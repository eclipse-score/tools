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

from pathlib import Path

import pytest

from repo_policy_sync.src import cli
from repo_policy_sync.src.policy import BUNDLED_POLICY_DIRECTORY
from repo_policy_sync.src.runner import RunReport, RunSummary


def _empty_report() -> RunReport:
    return RunReport(
        summary=RunSummary(
            repositories=0,
            synchronized=0,
            sync_failures=0,
            skipped=0,
            evaluations=0,
            compliant=0,
            drifted=0,
            not_applicable=0,
            evaluation_failures=0,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
        ),
        outcomes=(),
    )


@pytest.mark.parametrize(
    "argv, message",
    [
        (("plan", "--org", "eclipse-score", "--recreate"), "unrecognized arguments"),
        (
            ("apply", "--org", "eclipse-score", "--recreate", "--policy", "example"),
            "exactly one --repo",
        ),
        (
            ("apply", "--org", "eclipse-score", "--recreate", "--repo", "example"),
            "exactly one --policy",
        ),
    ],
)
def test_recreate_requires_an_explicit_single_target(argv, message, capsys) -> None:
    with pytest.raises(SystemExit) as exit_code:
        cli.main(argv)

    assert exit_code.value.code == 2
    assert message in capsys.readouterr().err


def test_default_output_is_a_table(monkeypatch, capsys) -> None:
    report = RunReport(
        summary=RunSummary(
            repositories=1,
            synchronized=1,
            sync_failures=0,
            skipped=0,
            evaluations=1,
            compliant=0,
            drifted=1,
            not_applicable=0,
            evaluation_failures=0,
            pull_requests_created=0,
            pull_requests_updated=0,
            pull_requests_open=0,
            pull_requests_recreated=0,
        ),
        outcomes=(),
    )
    monkeypatch.setattr(cli, "load_policies", lambda _: ())
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--policy-dir",
                "repo_policy_sync/policies",
                "--quiet",
            )
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "📋 Policy evaluations" in captured.out


def test_all_reports_can_be_written_from_one_run(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = _empty_report()
    observed = {}
    monkeypatch.setattr(cli, "load_policies", lambda _: ())
    monkeypatch.setattr(
        cli,
        "run_policies",
        lambda **kwargs: observed.update(kwargs) or report,
    )
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--policy-dir",
                "repo_policy_sync/policies",
                "--json-output",
                str(json_path),
                "--markdown-output",
                str(markdown_path),
                "--quiet",
            )
        )
        == 0
    )

    assert '"schema_version": 2' in json_path.read_text(encoding="utf-8")
    assert "# Repository policy compliance" in markdown_path.read_text(encoding="utf-8")
    assert "📋 Policy evaluations" in capsys.readouterr().out
    assert observed["include_pull_request_status"] is True


def test_json_report_requests_pull_request_status(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = _empty_report()
    observed = {}
    monkeypatch.setattr(cli, "load_policies", lambda _: ())
    monkeypatch.setattr(
        cli,
        "run_policies",
        lambda **kwargs: observed.update(kwargs) or report,
    )
    json_path = tmp_path / "report.json"

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--policy-dir",
                "repo_policy_sync/policies",
                "--json-output",
                str(json_path),
                "--quiet",
            )
        )
        == 0
    )

    assert observed["include_pull_request_status"] is True
    capsys.readouterr()


def test_plan_requests_pull_request_status_for_terminal_report(
    monkeypatch, capsys
) -> None:
    observed = {}
    monkeypatch.setattr(cli, "load_policies", lambda _: ())
    monkeypatch.setattr(
        cli,
        "run_policies",
        lambda **kwargs: observed.update(kwargs) or _empty_report(),
    )

    assert cli.main(("plan", "--org", "eclipse-score", "--quiet")) == 0

    assert observed["include_pull_request_status"] is True
    capsys.readouterr()


def test_recreate_selects_only_the_requested_bundled_policy(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    report = _empty_report()
    loaded_paths = []
    observed = {}
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(
        cli,
        "run_policies",
        lambda **kwargs: observed.update(kwargs) or report,
    )

    assert (
        cli.main(
            (
                "apply",
                "--org",
                "eclipse-score",
                "--repo",
                "reference_integration",
                "--policy",
                "minimum-bazel-version",
                "--recreate",
                "--quiet",
            )
        )
        == 0
    )

    assert [path.resolve() for path in loaded_paths] == [
        BUNDLED_POLICY_DIRECTORY / "minimum-bazel-version" / "policy.yml"
    ]
    assert observed["recreate"] is True


def test_recreate_does_not_load_unrelated_local_policies(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    policy_directory = tmp_path / "policies"
    target = policy_directory / "target" / "policy.yml"
    target.parent.mkdir(parents=True)
    target.write_text("not loaded here\n")
    unrelated = policy_directory / "unrelated" / "policy.yml"
    unrelated.parent.mkdir()
    unrelated.write_text("this is invalid policy YAML: [\n")
    report = _empty_report()
    loaded_paths = []
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "apply",
                "--org",
                "eclipse-score",
                "--repo",
                "reference_integration",
                "--policy-dir",
                str(policy_directory),
                "--policy",
                "target",
                "--recreate",
                "--quiet",
            )
        )
        == 0
    )

    assert loaded_paths == [target]


def test_bundled_policies_do_not_require_a_local_policy_directory(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    report = _empty_report()
    loaded_paths = []
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "etas-eng",
                "--repo",
                "vsps_product",
                "--quiet",
            )
        )
        == 0
    )
    assert [path.parent.name for path in loaded_paths] == [
        path.name
        for path in sorted(
            BUNDLED_POLICY_DIRECTORY.iterdir(), key=lambda path: str(path)
        )
        if path.is_dir()
    ]

    loaded_paths.clear()
    assert (
        cli.main(
            (
                "plan",
                "--org",
                "etas-eng",
                "--repo",
                "vsps_product",
                "--exclude-policy",
                "minimum-bazel-version",
                "--quiet",
            )
        )
        == 0
    )
    assert all(path.parent.name != "minimum-bazel-version" for path in loaded_paths)


def test_local_policies_can_be_excluded_by_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    local_policy = tmp_path / "policies" / "local-policy" / "policy.yml"
    local_policy.parent.mkdir(parents=True)
    local_policy.write_text("not loaded here\n")
    report = _empty_report()
    loaded_paths = []
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--policy-dir",
                str(local_policy.parent.parent),
                "--exclude-policy",
                "local-policy",
                "--quiet",
            )
        )
        == 0
    )

    assert local_policy not in loaded_paths
    assert any(path.parent.name == "minimum-bazel-version" for path in loaded_paths)


def test_bundled_policy_selected_from_bundled_directory_is_not_loaded_twice(
    monkeypatch,
) -> None:
    report = _empty_report()
    loaded_paths = []
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--repo",
                "reference_integration",
                "--policy-dir",
                "repo_policy_sync/policies",
                "--policy",
                "minimum-bazel-version",
                "--quiet",
            )
        )
        == 0
    )

    assert [path.resolve() for path in loaded_paths] == [
        BUNDLED_POLICY_DIRECTORY / "minimum-bazel-version" / "policy.yml"
    ]


def test_policy_selection_loads_only_the_selected_bundled_policy(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    report = _empty_report()
    loaded_paths = []
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--policy",
                "minimum-bazel-version",
                "--quiet",
            )
        )
        == 0
    )

    assert loaded_paths == [
        BUNDLED_POLICY_DIRECTORY / "minimum-bazel-version" / "policy.yml"
    ]


def test_policy_selection_loads_only_the_selected_local_policy(
    monkeypatch, tmp_path: Path
) -> None:
    policy_directory = tmp_path / "policies"
    selected = policy_directory / "selected" / "policy.yml"
    selected.parent.mkdir(parents=True)
    selected.write_text(
        """title: Selected
ensure:
  - type: remove_file
    path: obsolete-file
""",
        encoding="utf-8",
    )
    unrelated = policy_directory / "unrelated" / "policy.yml"
    unrelated.parent.mkdir()
    unrelated.write_text("this policy is not selected\n", encoding="utf-8")
    report = _empty_report()
    loaded_paths = []
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(cli, "run_policies", lambda **_: report)

    assert (
        cli.main(
            (
                "plan",
                "--org",
                "eclipse-score",
                "--policy-dir",
                str(policy_directory),
                "--policy",
                "selected",
                "--quiet",
            )
        )
        == 0
    )

    assert loaded_paths == [selected]


def test_config_values_are_overridden_by_explicit_cli_values(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[score-repo-policy-sync]
org = "config-org"
repos = ["config-repo"]
policy_dirs = []
exclude_policies = ["minimum-bazel-version"]
recreate = false
allow_dirty_pr = true
quiet = true
cache_dir = "config-cache"
sync_workers = 2
policy_workers = 3
""",
        encoding="utf-8",
    )
    loaded_paths = []
    observed = {}
    monkeypatch.setattr(
        cli, "load_policies", lambda paths: loaded_paths.extend(paths) or ()
    )
    monkeypatch.setattr(
        cli,
        "run_policies",
        lambda **kwargs: observed.update(kwargs) or _empty_report(),
    )

    assert (
        cli.main(
            (
                "apply",
                "--config",
                str(config_path),
                "--org",
                "cli-org",
                "--repo",
                "cli-repo",
                "--no-allow-dirty-pr",
                "--exclude-policy",
                "docs-as-code-gitignore",
                "--no-quiet",
                "--cache-dir",
                "cli-cache",
                "--sync-workers",
                "7",
                "--policy-workers",
                "8",
            )
        )
        == 0
    )
    assert observed["org"] == "cli-org"
    assert observed["repository_names"] == ("cli-repo",)
    assert observed["apply"] is True
    assert observed["allow_dirty_pr"] is False
    assert observed["sync_workers"] == 7
    assert observed["policy_workers"] == 8
    assert observed["checkout_cache_directory"] == Path("cli-cache")
    assert observed["include_pull_request_status"] is False
    assert all(path.parent.name != "docs-as-code-gitignore" for path in loaded_paths)
    assert any(path.parent.name == "minimum-bazel-version" for path in loaded_paths)


def test_configurable_defaults_are_left_unset_for_config_merging() -> None:
    args = cli.create_parser().parse_args(("plan", "--org", "eclipse-score"))

    assert args.sync_workers is None
    assert args.policy_workers is None


def test_policy_directory_defaults_to_current_working_directory() -> None:
    args = cli.create_parser().parse_args(("plan", "--org", "eclipse-score"))

    assert args.policy_dir is None
    assert args.config is None
    assert args.exclude_policy is None


def test_dirty_pull_requests_can_be_enabled() -> None:
    args = cli.create_parser().parse_args(
        ("apply", "--org", "eclipse-score", "--allow-dirty-pr")
    )

    assert args.allow_dirty_pr is True


def test_policy_directory_can_be_repeated() -> None:
    args = cli.create_parser().parse_args(
        (
            "plan",
            "--org",
            "eclipse-score",
            "--policy-dir",
            "policies",
            "--policy-dir",
            "shared-policies",
        )
    )

    assert args.policy_dir == [Path("policies"), Path("shared-policies")]


def test_removed_policy_directory_alias_is_rejected() -> None:
    with pytest.raises(SystemExit) as exit_code:
        cli.create_parser().parse_args(
            ("plan", "--org", "eclipse-score", "--policy-directory", "policies")
        )

    assert exit_code.value.code == 2


def test_policies_can_be_excluded_by_name() -> None:
    args = cli.create_parser().parse_args(
        (
            "plan",
            "--org",
            "eclipse-score",
            "--exclude-policy",
            "minimum-bazel-version",
        )
    )

    assert args.exclude_policy == ["minimum-bazel-version"]


def test_help_groups_options_by_frequency(capsys) -> None:
    with pytest.raises(SystemExit) as exit_code:
        cli.create_parser().parse_args(("--help",))

    assert exit_code.value.code == 0
    help_text = capsys.readouterr().out
    assert "plan" in help_text
    assert "apply" in help_text
    assert "collect-samples" not in help_text

    with pytest.raises(SystemExit) as exit_code:
        cli.create_parser().parse_args(("apply", "--help"))

    assert exit_code.value.code == 0
    help_text = capsys.readouterr().out
    assert (
        help_text.index("Typical:")
        < help_text.index("Rare:")
        < help_text.index("Debugging only:")
    )
    _, debugging_help = help_text.split("Debugging only:", 1)
    assert "--recreate" in help_text
    assert "--cache-dir" in debugging_help
