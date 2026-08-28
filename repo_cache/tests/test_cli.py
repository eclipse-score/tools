# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

import json
from pathlib import Path

from repo_cache.src import cli as cli_module
from repo_cache.src.errors import RepoCacheError
from repo_cache.src.models import Repository
from repo_cache.src.sync import SyncOutcome, SyncReport


def test_list_prints_active_repositories(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli_module,
        "list_repositories",
        lambda *, org: (
            Repository("alpha", "main"),
            Repository("retired", None, archived=True),
        ),
    )

    exit_code = cli_module.main(["list", "--org", "acme"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "alpha" in captured.out
    assert "retired" not in captured.out


def test_list_json_includes_archived_repositories_when_requested(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        cli_module,
        "list_repositories",
        lambda *, org: (Repository("retired", None, archived=True),),
    )

    exit_code = cli_module.main(
        ["list", "--org", "acme", "--include-archived", "--json"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == [
        {"name": "retired", "default_branch": None, "archived": True}
    ]


def test_sync_reports_failures_with_a_nonzero_exit_code(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repository = Repository("broken", "main")
    report = SyncReport(
        org="acme",
        cache_dir=tmp_path,
        outcomes=(
            SyncOutcome(repository, tmp_path / "acme" / "broken", "checkout failed"),
        ),
    )
    monkeypatch.setattr(cli_module, "sync_org", lambda **_: report)

    exit_code = cli_module.main(
        ["sync", "--org", "acme", "--cache-dir", str(tmp_path), "--quiet"]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "broken: checkout failed" in captured.err


def test_sync_succeeds_with_zero_exit_code(monkeypatch, tmp_path: Path) -> None:
    repository = Repository("alpha", "main")
    report = SyncReport(
        org="acme",
        cache_dir=tmp_path,
        outcomes=(SyncOutcome(repository, tmp_path / "acme" / "alpha", None),),
    )
    monkeypatch.setattr(cli_module, "sync_org", lambda **_: report)

    exit_code = cli_module.main(
        ["sync", "--org", "acme", "--cache-dir", str(tmp_path), "--quiet"]
    )

    assert exit_code == 0


def test_main_reports_repo_cache_errors_without_a_traceback(
    monkeypatch, capsys
) -> None:
    def raise_error(*, org: str) -> None:
        raise RepoCacheError("authentication failed")

    monkeypatch.setattr(cli_module, "list_repositories", raise_error)

    exit_code = cli_module.main(["list", "--org", "acme"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "authentication failed" in captured.err
