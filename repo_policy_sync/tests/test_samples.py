# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

import json
from pathlib import Path
from shutil import copytree

from repo_policy_sync.models import (
    FileContainsAnyCondition,
    FileContainsCondition,
    Policy,
    Repository,
)
from repo_policy_sync.samples import collect_samples


class FakeSampleClient:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.repositories = (
            Repository("docs-repo", "main"),
            Repository("archived-repo", "main", archived=True),
        )
        self.authenticated = False

    def ensure_authenticated(self) -> None:
        self.authenticated = True

    def list_repositories(self, *, org: str) -> tuple[Repository, ...]:
        return self.repositories

    def sync_default_branch(
        self, *, repository: str, branch: str, destination: Path
    ) -> None:
        copytree(self.source, destination)


def test_collect_samples_uses_policy_when_and_writes_inventory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    workflow = source / ".github/workflows/docs.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: Documentation\njobs:\n  docs:\n    uses: example/cicd/docs.yml@ref\n"
    )
    output = tmp_path / "samples"
    policy = Policy(
        id="docs-policy",
        title="Docs",
        description=None,
        bazel_condition=None,
        ensure=(),
        file_contains_any_condition=FileContainsAnyCondition(
            (
                FileContainsCondition(
                    Path(".github/workflows/*.yml"),
                    r"uses:\s+example/cicd/docs\.yml@",
                ),
            )
        ),
    )
    client = FakeSampleClient(source)

    report = collect_samples(
        client=client,
        org="example",
        policies=(policy,),
        repository_names=(),
        checkout_cache_directory=tmp_path / "cache",
        output_directory=output,
        sync_workers=1,
        progress=lambda _: None,
    )

    sample = output / "docs-policy/docs-repo/before/.github/workflows/docs.yml"
    assert client.authenticated
    assert report.cases[0].repository == "docs-repo"
    assert sample.read_text() == workflow.read_text()
    inventory = json.loads((output / "inventory.json").read_text())
    assert inventory["schema_version"] == 1
    assert inventory["cases"][0]["files"] == [
        {"path": ".github/workflows/docs.yml", "size": workflow.stat().st_size}
    ]
