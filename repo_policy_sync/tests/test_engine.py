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
import subprocess

import pytest

from repo_policy_sync.engine import apply_policy, evaluate_policy
from repo_policy_sync.errors import RepoPolicySyncError
from repo_policy_sync.policy import BUNDLED_POLICY_DIRECTORY, load_policy
from repo_policy_sync.models import (
    AfterApplyCommand,
    BazelDependencyUpdate,
    BazelCondition,
    Change,
    EnsureLine,
    EnsureMinimumVersion,
    EnsureNoSuchFile,
    FileContainsCondition,
    Policy,
    ReplaceRegex,
    SynchronizeFile,
    SynchronizeBazelDependencies,
    SynchronizeDevcontainerVersion,
)


def _policy() -> Policy:
    return Policy(
        id="score-docs-as-code.gitignore-and-cleanup",
        title="Update docs files",
        description=None,
        bazel_condition=BazelCondition(("score_docs_as_code",)),
        ensure=(
            EnsureLine(Path(".gitignore"), "_build", ("/_build",)),
            EnsureLine(Path(".gitignore"), "ubproject.toml", ("/docs/ubproject.toml",)),
            EnsureNoSuchFile(Path("docs/ubproject.toml")),
        ),
    )


def test_apply_policy_replaces_legacy_lines_and_removes_file(tmp_path: Path) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        'module(name = "example")\nbazel_dep(name = "score_docs_as_code", version = "1.0")\n'
    )
    (tmp_path / ".gitignore").write_text(
        "/keep\n/_build\n/docs/ubproject.toml\n/_build\n"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/ubproject.toml").write_text("legacy\n")

    evaluation = apply_policy(tmp_path, _policy())

    assert len(evaluation.changes) == 3
    assert (tmp_path / ".gitignore").read_text() == "/keep\n_build\nubproject.toml\n"
    assert not (tmp_path / "docs/ubproject.toml").exists()
    assert apply_policy(tmp_path, _policy()).changes == ()


def test_policy_does_not_apply_without_direct_dependency(tmp_path: Path) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        'bazel_dep(name = "other", version = "1.0")\n'
    )
    evaluation = evaluate_policy(tmp_path, _policy())
    assert not evaluation.applies
    assert evaluation.changes == ()


def test_ensure_line_replaces_complete_line_glob_matches(tmp_path: Path) -> None:
    (tmp_path / "MODULE.bazel").write_text('bazel_dep(name = "score_docs_as_code")\n')
    (tmp_path / ".gitignore").write_text("prefix_build_suffix\n_build\n")
    policy = Policy(
        "example",
        "Example",
        None,
        BazelCondition(("score_docs_as_code",)),
        (EnsureLine(Path(".gitignore"), "_build", (), ("*_build*",)),),
    )

    apply_policy(tmp_path, policy)

    assert (tmp_path / ".gitignore").read_text() == "_build\n"
    assert apply_policy(tmp_path, policy).changes == ()


@pytest.mark.parametrize(
    ("operation", "path"),
    [
        (
            EnsureLine(Path("target"), "required", ()),
            Path("target"),
        ),
        (
            EnsureMinimumVersion(Path("target"), "8.6.0"),
            Path("target"),
        ),
        (
            ReplaceRegex(Path("target"), "legacy", "current"),
            Path("target"),
        ),
    ],
)
def test_path_operations_reject_directory_targets(
    tmp_path: Path, operation, path: Path
) -> None:
    (tmp_path / path).mkdir()

    with pytest.raises(RepoPolicySyncError, match="must be a file"):
        apply_policy(tmp_path, Policy("example", "Example", None, None, (operation,)))


def test_ensure_no_such_file_refuses_to_remove_a_directory(tmp_path: Path) -> None:
    (tmp_path / "MODULE.bazel").write_text('bazel_dep(name = "score_docs_as_code")\n')
    (tmp_path / "docs/ubproject.toml").mkdir(parents=True)

    with pytest.raises(RepoPolicySyncError, match="refusing to remove directory"):
        apply_policy(tmp_path, _policy())


def test_ensure_no_such_file_refuses_a_directory_during_evaluation(
    tmp_path: Path,
) -> None:
    (tmp_path / "MODULE.bazel").write_text('bazel_dep(name = "score_docs_as_code")\n')
    (tmp_path / "docs/ubproject.toml").mkdir(parents=True)

    with pytest.raises(RepoPolicySyncError, match="refusing to remove directory"):
        evaluate_policy(tmp_path, _policy())


def test_replace_regex_applies_and_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "example.txt").write_text("legacy\nunchanged\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            ReplaceRegex(
                Path("example.txt"),
                "legacy",
                "current",
            ),
        ),
    )

    evaluation = apply_policy(tmp_path, policy)

    assert evaluation.changes == (Change(Path("example.txt"), "replace matching text"),)
    assert (tmp_path / "example.txt").read_text() == "current\nunchanged\n"
    assert apply_policy(tmp_path, policy).changes == ()


def test_ensure_minimum_version_upgrades_only_older_versions(tmp_path: Path) -> None:
    version_file = tmp_path / ".bazelversion"
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (EnsureMinimumVersion(Path(".bazelversion"), "8.6.0"),),
    )

    version_file.write_text("8.5.2\n")
    assert apply_policy(tmp_path, policy).changes == (
        Change(Path(".bazelversion"), "upgrade from '8.5.2' to '8.6.0'"),
    )
    assert version_file.read_text() == "8.6.0\n"

    version_file.write_text("8.6.1\n")
    assert apply_policy(tmp_path, policy).changes == ()
    assert version_file.read_text() == "8.6.1\n"


def test_synchronize_bazel_dependencies_migrates_modules_and_all_build_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        """bazel_dep(
    name = "score_platform",
    version = "0.6.3",
)
bazel_dep(name = "score_docs_as_code", version = "7.4.0")
bazel_dep(name = "score_process", version = "1.8.2")
"""
    )
    (tmp_path / "BUILD").write_text(
        'deps = ["@score_process//:api", "score_process_description"]\n'
    )
    nested = tmp_path / "nested" / "BUILD.bazel"
    nested.parent.mkdir()
    nested.write_text('load("@score_process//:defs.bzl", "score_rule")\n')
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeBazelDependencies(
                Path("MODULE.bazel"),
                (
                    BazelDependencyUpdate("score_platform", "0.7.0"),
                    BazelDependencyUpdate("score_docs_as_code", "8.0.0"),
                    BazelDependencyUpdate(
                        "score_process", "2.1.0", "score_process_description"
                    ),
                ),
            ),
        ),
    )

    evaluation = apply_policy(tmp_path, policy)

    assert [change.path for change in evaluation.changes] == [
        Path("MODULE.bazel"),
        Path("BUILD"),
        Path("nested/BUILD.bazel"),
    ]
    module = (tmp_path / "MODULE.bazel").read_text()
    assert 'name = "score_platform"' in module and 'version = "0.7.0"' in module
    assert 'name = "score_docs_as_code"' in module and 'version = "8.0.0"' in module
    assert (
        'name = "score_process_description"' in module and 'version = "2.1.0"' in module
    )
    assert "score_process_description" in (tmp_path / "BUILD").read_text()
    assert "score_process_description" in nested.read_text()
    assert "@score_process//" not in (tmp_path / "BUILD").read_text()
    assert "@score_process//" not in nested.read_text()
    assert apply_policy(tmp_path, policy).changes == ()


def test_synchronize_bazel_dependencies_skips_absent_optional_modules(
    tmp_path: Path,
) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        'bazel_dep(name = "score_platform", version = "0.6.3")\n'
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeBazelDependencies(
                Path("MODULE.bazel"),
                (
                    BazelDependencyUpdate("score_platform", "0.7.0", optional=True),
                    BazelDependencyUpdate("score_docs_as_code", "8.0.0", optional=True),
                ),
            ),
        ),
    )

    apply_policy(tmp_path, policy)

    assert (tmp_path / "MODULE.bazel").read_text() == (
        'bazel_dep(name = "score_platform", version = "0.7.0")\n'
    )


def test_synchronize_bazel_dependencies_adds_and_updates_git_override(
    tmp_path: Path,
) -> None:
    module_file = tmp_path / "MODULE.bazel"
    module_file.write_text('bazel_dep(name = "score_baselibs", version = "0.2.11")\n')
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeBazelDependencies(
                Path("MODULE.bazel"),
                (
                    BazelDependencyUpdate(
                        "score_baselibs",
                        "0.2.11",
                        override="bf0020fefef402642dcb0092832e03ba4267d739",
                        remote="https://github.com/eclipse-score/baselibs.git",
                    ),
                ),
            ),
        ),
    )

    evaluation = apply_policy(tmp_path, policy)

    assert evaluation.changes == (
        Change(
            Path("MODULE.bazel"),
            "synchronize Bazel dependency versions and module names",
        ),
    )
    assert module_file.read_text() == (
        'bazel_dep(name = "score_baselibs", version = "0.2.11")\n\n'
        "git_override(\n"
        '    module_name = "score_baselibs",\n'
        '    commit = "bf0020fefef402642dcb0092832e03ba4267d739",\n'
        '    remote = "https://github.com/eclipse-score/baselibs.git",\n'
        ")\n"
    )
    assert apply_policy(tmp_path, policy).changes == ()

    module_file.write_text(
        module_file.read_text().replace(
            "bf0020fefef402642dcb0092832e03ba4267d739", "old-commit"
        )
    )
    assert apply_policy(tmp_path, policy).changes == (
        Change(
            Path("MODULE.bazel"),
            "synchronize Bazel dependency versions and module names",
        ),
    )
    assert "old-commit" not in module_file.read_text()

    module_file.write_text(
        module_file.read_text().replace(
            '    remote = "https://github.com/eclipse-score/baselibs.git",\n', ""
        )
    )
    apply_policy(tmp_path, policy)
    assert (
        'remote = "https://github.com/eclipse-score/baselibs.git"'
        in module_file.read_text()
    )


def test_synchronize_bazel_dependencies_preserves_override_for_newer_version(
    tmp_path: Path,
) -> None:
    module_file = tmp_path / "MODULE.bazel"
    module_file.write_text(
        'bazel_dep(name = "score_baselibs", version = "0.2.12")\n\n'
        "git_override(\n"
        '    module_name = "score_baselibs",\n'
        '    commit = "newer-commit",\n'
        '    remote = "https://github.com/eclipse-score/baselibs.git",\n'
        ")\n"
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeBazelDependencies(
                Path("MODULE.bazel"),
                (
                    BazelDependencyUpdate(
                        "score_baselibs",
                        "0.2.11",
                        override="baseline-commit",
                        remote="https://github.com/eclipse-score/baselibs.git",
                    ),
                ),
            ),
        ),
    )

    assert apply_policy(tmp_path, policy).changes == ()
    assert 'version = "0.2.12"' in module_file.read_text()
    assert 'commit = "newer-commit"' in module_file.read_text()


def test_bazel_dependency_policy_preserves_newer_baselibs_override(
    tmp_path: Path,
) -> None:
    module_file = tmp_path / "MODULE.bazel"
    module_file.write_text(
        'bazel_dep(name = "score_platform", version = "0.6.3")\n'
        'bazel_dep(name = "score_baselibs", version = "0.2.12")\n\n'
        "git_override(\n"
        '    module_name = "score_baselibs",\n'
        '    commit = "newer-commit",\n'
        '    remote = "https://github.com/eclipse-score/baselibs.git",\n'
        ")\n"
    )
    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY / "score-bazel-dependency-alignment" / "policy.yml"
    )

    apply_policy(tmp_path, policy)

    module = module_file.read_text()
    assert 'version = "0.7.0"' in module
    assert 'version = "0.2.12"' in module
    assert 'commit = "newer-commit"' in module


def test_synchronize_bazel_dependencies_uses_configured_build_rename(
    tmp_path: Path,
) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        'bazel_dep(name = "legacy_module", version = "1.0.0")\n'
    )
    build = tmp_path / "BUILD"
    build.write_text('deps = ["@legacy_module//:api", "legacy_module_description"]\n')
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeBazelDependencies(
                Path("MODULE.bazel"),
                (BazelDependencyUpdate("legacy_module", "2.0.0", "current_module"),),
            ),
        ),
    )

    evaluation = apply_policy(tmp_path, policy)

    assert evaluation.changes == (
        Change(
            Path("MODULE.bazel"),
            "synchronize Bazel dependency versions and module names",
        ),
        Change(
            Path("BUILD"),
            "replace 'legacy_module' with 'current_module' in BUILD files",
        ),
    )
    assert 'name = "current_module"' in (tmp_path / "MODULE.bazel").read_text()
    assert 'version = "2.0.0"' in (tmp_path / "MODULE.bazel").read_text()
    assert "@current_module//:api" in build.read_text()
    assert "legacy_module_description" in build.read_text()


def test_synchronize_bazel_dependencies_does_not_change_unconfigured_build_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        'bazel_dep(name = "score_platform", version = "0.6.3")\n'
    )
    build = tmp_path / "BUILD"
    build.write_text('deps = ["@score_process//:api"]\n')
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeBazelDependencies(
                Path("MODULE.bazel"),
                (BazelDependencyUpdate("score_platform", "0.7.0"),),
            ),
        ),
    )

    evaluation = apply_policy(tmp_path, policy)

    assert evaluation.changes == (
        Change(
            Path("MODULE.bazel"),
            "synchronize Bazel dependency versions and module names",
        ),
    )
    assert build.read_text() == 'deps = ["@score_process//:api"]\n'


def test_bazel_dependency_policy_does_not_trigger_on_build_reference_alone(
    tmp_path: Path,
) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        """module(name = "example")
bazel_dep(name = "score_platform", version = "0.7.0")
bazel_dep(name = "score_docs_as_code", version = "8.0.0")
bazel_dep(name = "score_process_description", version = "2.1.1")
"""
    )
    nested = tmp_path / "subproject" / "BUILD"
    nested.parent.mkdir()
    nested.write_text('deps = ["@score_process//:api"]\n')

    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY / "score-bazel-dependency-alignment" / "policy.yml"
    )

    evaluation = evaluate_policy(tmp_path, policy)

    assert evaluation.applies is False
    assert evaluation.changes == ()


def test_minimal_bazel_module_policy_handles_inline_metadata(tmp_path: Path) -> None:
    (tmp_path / "MODULE.bazel").write_text(
        'module(name = "score_sbom", version = "0.0.1")\n'
    )
    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY / "minimal-bazel-module-declaration" / "policy.yml"
    )

    evaluation = apply_policy(tmp_path, policy)

    assert len(evaluation.changes) == 1
    assert evaluation.changes[0].path == Path("MODULE.bazel")
    assert evaluation.changes[0].description == "replace matching text"
    assert (tmp_path / "MODULE.bazel").read_text() == 'module(name = "score_sbom")\n'


def test_synchronize_file_replaces_contents_and_makes_the_target_executable(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".devcontainer/run-tool"
    target.parent.mkdir()
    target.write_text("outdated\n")
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeFile(
                Path(".devcontainer/run-tool"), "#!/usr/bin/env bash\ncurrent\n", True
            ),
        ),
    )

    evaluation = apply_policy(tmp_path, policy)

    assert evaluation.changes == (
        Change(
            Path(".devcontainer/run-tool"), "synchronize contents and make executable"
        ),
    )
    assert target.read_text() == "#!/usr/bin/env bash\ncurrent\n"
    assert target.stat().st_mode & 0o111
    assert apply_policy(tmp_path, policy).changes == ()


def test_synchronize_workflow_inserts_missing_name_and_preserves_jobs(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".github/workflows/docs.yml"
    target.parent.mkdir(parents=True)
    target.write_text(
        "# local header\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  local:\n"
        "    runs-on: ubuntu-latest\n"
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeFile(
                path=Path(".github/workflows/docs.yml"),
                contents=(
                    "name: Documentation CI\n"
                    "on:\n"
                    "  pull_request:\n"
                    "jobs:\n"
                    "  docs:\n"
                    "    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@ref\n"
                ),
                preserve_reusable_workflow_refs=(
                    (
                        "eclipse-score/cicd-workflows/.github/workflows/docs.yml",
                        (0, 0, 3),
                    ),
                ),
                preserve_workflow_content=True,
            ),
        ),
    )

    apply_policy(tmp_path, policy)

    result = target.read_text()
    assert result.startswith("# local header\nname: Documentation CI\n")
    assert "  local:\n" in result
    assert "  docs:\n" in result


def test_synchronize_workflow_rejects_job_id_collision(tmp_path: Path) -> None:
    target = tmp_path / ".github/workflows/docs.yml"
    target.parent.mkdir(parents=True)
    target.write_text(
        "name: Local\non: [push]\njobs:\n  docs:\n    runs-on: ubuntu-latest\n"
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeFile(
                path=Path(".github/workflows/docs.yml"),
                contents=(
                    "name: Documentation CI\n"
                    "on: [push]\n"
                    "jobs:\n"
                    "  docs:\n"
                    "    uses: eclipse-score/cicd-workflows/.github/workflows/docs.yml@ref\n"
                ),
                preserve_reusable_workflow_refs=(
                    (
                        "eclipse-score/cicd-workflows/.github/workflows/docs.yml",
                        (0, 0, 3),
                    ),
                ),
                preserve_workflow_content=True,
            ),
        ),
    )

    with pytest.raises(RepoPolicySyncError, match="job with that ID already exists"):
        apply_policy(tmp_path, policy)


def test_synchronize_workflow_preserves_publish_permissions_on_matching_job(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".github/workflows/docs-publish.yml"
    target.parent.mkdir(parents=True)
    target.write_text(
        "name: Publish Documentation\n"
        "on: [workflow_run]\n"
        "jobs:\n"
        "  publish:\n"
        "    uses: eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml@v0.0.2\n"
        "    with:\n"
        "      deployment_type: custom"
    )
    policy = Policy(
        "example",
        "Example",
        None,
        None,
        (
            SynchronizeFile(
                path=Path(".github/workflows/docs-publish.yml"),
                contents=(
                    "name: Publish Documentation\n"
                    "on:\n"
                    "  workflow_run:\n"
                    "    workflows: [Documentation CI]\n"
                    "jobs:\n"
                    "  docs-publish:\n"
                    "    uses: eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml@ref\n"
                    "    permissions:\n"
                    "      contents: write\n"
                    "      pages: write\n"
                ),
                preserve_reusable_workflow_refs=(
                    (
                        "eclipse-score/cicd-workflows/.github/workflows/docs-publish.yml",
                        (0, 0, 3),
                    ),
                ),
                preserve_workflow_content=True,
            ),
        ),
    )

    apply_policy(tmp_path, policy)

    result = target.read_text()
    assert "contents: write\n" in result
    assert "pages: write\n" in result
    assert "deployment_type: custom\n    permissions:" in result
    assert "deployment_type: custom\n" in result


def test_after_apply_regenerates_existing_conditional_file(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "MODULE.bazel.lock").write_text("old lock\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"),
                Path("MODULE.bazel.lock"),
                "Regenerate the lock file.",
            ),
        ),
    )
    calls: list[tuple[tuple[str, ...], Path]] = []

    def run(command, *, cwd, check, capture_output, text):
        calls.append((tuple(command), cwd))
        assert capture_output is True
        assert text is True

    monkeypatch.setattr("repo_policy_sync.engine.subprocess.run", run)

    evaluation = evaluate_policy(tmp_path, policy)
    applied = apply_policy(tmp_path, policy)

    assert [change.path for change in evaluation.changes] == [
        Path(".bazelversion"),
        Path("MODULE.bazel.lock"),
    ]
    assert applied == evaluation
    assert calls == [(("bazel", "mod", "deps"), tmp_path)]


def test_force_after_apply_runs_for_an_already_compliant_policy(
    tmp_path: Path, monkeypatch
) -> None:
    lock_file = tmp_path / "MODULE.bazel.lock"
    lock_file.write_text("old lock\n")
    (tmp_path / ".bazelversion").write_text("8.6.0\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureMinimumVersion(Path(".bazelversion"), "8.6.0"),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"), Path("MODULE.bazel.lock"), "Regenerate lock."
            ),
        ),
    )

    def run(command, *, cwd, check, capture_output, text):
        assert command == ("bazel", "mod", "deps")
        assert cwd == tmp_path
        assert capture_output is True
        assert text is True
        lock_file.write_text("new lock\n")

    monkeypatch.setattr("repo_policy_sync.engine.subprocess.run", run)

    applied = apply_policy(tmp_path, policy, force_after_apply=True)

    assert applied.changes == (Change(Path("MODULE.bazel.lock"), "Regenerate lock."),)
    assert lock_file.read_text() == "new lock\n"


def test_after_apply_failure_redacts_credentials(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "MODULE.bazel.lock").write_text("old lock\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps", "--token", "ghp_secret_value_12345"),
                Path("MODULE.bazel.lock"),
                "Regenerate the lock file.",
            ),
        ),
    )

    def run(*_, **__):
        raise subprocess.CalledProcessError(
            1,
            ["bazel", "mod", "deps"],
            stderr="authorization: Bearer ghp_secret_value_12345\n",
            output="password=another-secret\n",
        )

    monkeypatch.setattr("repo_policy_sync.engine.subprocess.run", run)

    with pytest.raises(RepoPolicySyncError) as error:
        apply_policy(tmp_path, policy)

    message = str(error.value)
    assert "ghp_secret_value_12345" not in message
    assert "another-secret" not in message
    assert "[REDACTED]" in message


def test_after_apply_failure_without_output_has_a_fallback_message(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "MODULE.bazel.lock").write_text("old lock\n")
    policy = Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=None,
        ensure=(EnsureLine(Path(".bazelversion"), "8.6.0", ()),),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"),
                Path("MODULE.bazel.lock"),
                "Regenerate the lock file.",
            ),
        ),
    )

    def run(*_, **__):
        raise subprocess.CalledProcessError(1, ["bazel", "mod", "deps"])

    monkeypatch.setattr("repo_policy_sync.engine.subprocess.run", run)

    with pytest.raises(RepoPolicySyncError, match=r"command failed \(exit status 1\)"):
        apply_policy(tmp_path, policy)


def _devcontainer_policy(*, with_guard: bool = False) -> Policy:
    return Policy(
        id="example",
        title="Example",
        description=None,
        bazel_condition=BazelCondition(("score_devcontainer",)),
        ensure=(
            SynchronizeDevcontainerVersion(
                Path(".devcontainer/Dockerfile"),
                Path("MODULE.bazel"),
                "ghcr.io/eclipse-score/devcontainer",
                "score_devcontainer",
            ),
        ),
        after_apply=(
            AfterApplyCommand(
                ("bazel", "mod", "deps"),
                Path("MODULE.bazel.lock"),
                "Regenerate lock.",
                Path("MODULE.bazel") if with_guard else None,
            ),
        ),
        file_contains_condition=FileContainsCondition(
            Path(".devcontainer/Dockerfile"),
            r"(?m)^\s*FROM\s+ghcr\.io/eclipse-score/devcontainer:",
        ),
    )


def _write_devcontainer_files(
    tmp_path: Path, docker_version: str, module_version: str
) -> None:
    (tmp_path / ".devcontainer").mkdir(exist_ok=True)
    (tmp_path / ".devcontainer/Dockerfile").write_text(
        f"FROM ghcr.io/eclipse-score/devcontainer:v{docker_version} AS development\nRUN echo ready\n"
    )
    (tmp_path / "MODULE.bazel").write_text(
        'module(name = "example")\n\n'
        "bazel_dep(\n"
        f'    version = "{module_version}",\n'
        '    name = "score_devcontainer",\n'
        ")\n"
    )


def test_devcontainer_policy_updates_only_the_lower_version(tmp_path: Path) -> None:
    _write_devcontainer_files(tmp_path, "1.9.0", "1.8.4")

    evaluation = apply_policy(tmp_path, _devcontainer_policy())

    assert evaluation.changes == (
        Change(Path("MODULE.bazel"), "align version from '1.8.4' to '1.9.0'"),
    )
    assert 'version = "1.9.0"' in (tmp_path / "MODULE.bazel").read_text()
    assert (
        (tmp_path / ".devcontainer/Dockerfile")
        .read_text()
        .endswith("AS development\nRUN echo ready\n")
    )


def test_devcontainer_policy_updates_dockerfile_when_bazel_is_higher(
    tmp_path: Path,
) -> None:
    _write_devcontainer_files(tmp_path, "1.8.4", "1.9.0")

    evaluation = apply_policy(tmp_path, _devcontainer_policy())

    assert evaluation.changes == (
        Change(
            Path(".devcontainer/Dockerfile"), "align version from '1.8.4' to '1.9.0'"
        ),
    )
    assert (
        "FROM ghcr.io/eclipse-score/devcontainer:v1.9.0 AS development"
        in (tmp_path / ".devcontainer/Dockerfile").read_text()
    )


def test_devcontainer_policy_is_not_applicable_without_target_base_image(
    tmp_path: Path,
) -> None:
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer/Dockerfile").write_text("FROM ubuntu:24.04\n")
    (tmp_path / "MODULE.bazel").write_text(
        'bazel_dep(name = "score_devcontainer", version = "1.9.0")\n'
    )

    assert evaluate_policy(tmp_path, _devcontainer_policy()).applies is False


def test_devcontainer_policy_is_not_applicable_without_direct_dependency(
    tmp_path: Path,
) -> None:
    _write_devcontainer_files(tmp_path, "1.8.4", "1.9.0")
    (tmp_path / "MODULE.bazel").write_text(
        'bazel_dep(name = "other", version = "1.9.0")\n'
    )

    assert evaluate_policy(tmp_path, _devcontainer_policy()).applies is False


def test_devcontainer_standardization_is_not_applicable_without_module_file(
    tmp_path: Path,
) -> None:
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer/Dockerfile").write_text(
        "FROM ghcr.io/eclipse-score/devcontainer:v1.9.0\n"
    )
    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY / "score-devcontainer-standardization" / "policy.yml"
    )

    assert evaluate_policy(tmp_path, policy).applies is False


def test_devcontainer_policy_rejects_unsupported_or_duplicate_declarations(
    tmp_path: Path,
) -> None:
    _write_devcontainer_files(tmp_path, "1.9", "1.8.4")
    dockerfile = tmp_path / ".devcontainer/Dockerfile"
    module_file = tmp_path / "MODULE.bazel"
    before_dockerfile = dockerfile.read_text()
    before_module = module_file.read_text()

    with pytest.raises(RepoPolicySyncError, match="vX.Y.Z"):
        apply_policy(tmp_path, _devcontainer_policy())

    assert dockerfile.read_text() == before_dockerfile
    assert module_file.read_text() == before_module

    _write_devcontainer_files(tmp_path, "1.9.0", "1.8.4")
    module_file.write_text(
        module_file.read_text()
        + 'bazel_dep(name = "score_devcontainer", version = "1.9.0")\n'
    )
    before_module = module_file.read_text()
    with pytest.raises(RepoPolicySyncError, match="exactly one bazel_dep"):
        apply_policy(tmp_path, _devcontainer_policy())
    assert module_file.read_text() == before_module

    _write_devcontainer_files(tmp_path, "1.9.0", "1.8.4")
    module_file.write_text('bazel_dep(name = "score_devcontainer")\n')
    before_module = module_file.read_text()
    with pytest.raises(RepoPolicySyncError, match="must declare version"):
        apply_policy(tmp_path, _devcontainer_policy())
    assert module_file.read_text() == before_module


def test_after_apply_changed_path_guard_only_regenerates_lock_after_module_change(
    tmp_path: Path, monkeypatch
) -> None:
    lock_file = tmp_path / "MODULE.bazel.lock"
    calls: list[tuple[str, ...]] = []

    def run(command, *, cwd, check, capture_output, text):
        calls.append(tuple(command))
        assert capture_output is True
        assert text is True

    monkeypatch.setattr("repo_policy_sync.engine.subprocess.run", run)
    _write_devcontainer_files(tmp_path, "1.9.0", "1.8.4")
    lock_file.write_text("old lock\n")
    applied = apply_policy(tmp_path, _devcontainer_policy(with_guard=True))

    assert [change.path for change in applied.changes] == [
        Path("MODULE.bazel"),
        Path("MODULE.bazel.lock"),
    ]
    assert calls == [("bazel", "mod", "deps")]

    calls.clear()
    _write_devcontainer_files(tmp_path, "1.8.4", "1.9.0")
    apply_policy(tmp_path, _devcontainer_policy(with_guard=True))

    assert calls == []


def test_devcontainer_migration_adds_copyright_only_for_eclipse_score(
    tmp_path: Path,
) -> None:
    policy = load_policy(
        BUNDLED_POLICY_DIRECTORY
        / "score-devcontainer-dockerfile-migration"
        / "policy.yml"
    )

    for organization, expected_copyright in ((None, False), ("eclipse-score", True)):
        repository = tmp_path / (organization or "other-org")
        repository.mkdir()
        (repository / ".devcontainer.json").write_text(
            '{\n  "image": "ghcr.io/eclipse-score/devcontainer:v1.9.0"\n}\n'
        )

        apply_policy(repository, policy, organization=organization)

        dockerfile = (repository / ".devcontainer/Dockerfile").read_text()
        assert (
            dockerfile.startswith(
                "# *******************************************************************************\n"
            )
            is expected_copyright
        )
        assert "# Use Dockerfile to get dependabot version bumps" in dockerfile
        assert not (repository / ".devcontainer.json").exists()
        config = (repository / ".devcontainer/devcontainer.json").read_text()
        assert '"dockerfile": "Dockerfile"' in config
        assert '"context"' not in config
