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

from pathlib import Path

import pytest

from repo_policy_sync.engine import apply_policy
from repo_policy_sync.errors import RepoPolicySyncError
from repo_policy_sync.models import Policy, SynchronizeWorkflow


def test_synchronize_workflow_preserves_name_and_updates_workflow_run(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    build = workflows / "custom-docs.yml"
    build.write_text(
        "name: Repository Docs\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  build:\n"
        "    uses: example/cicd/docs.yml@v0.0.2\n"
    )
    workflow_run = workflows / "docs-publish.yml"
    workflow_run.write_text(
        "name: Publish\n"
        "on:\n"
        "  workflow_run:\n"
        "    workflows: [Documentation]\n"
        "jobs:\n"
        "  publish:\n"
        "    uses: example/cicd/publish.yml@v0.0.2\n"
    )
    operation = SynchronizeWorkflow(
        source=Path("docs.yml"),
        contents=(
            "name: Documentation\n"
            "permissions:\n"
            "  contents: read\n"
            "on:\n"
            "  pull_request:\n"
            "    types: [opened, synchronize]\n"
            "jobs:\n"
            "  docs-build:\n"
            "    uses: example/cicd/docs.yml@source\n"
            "    permissions:\n"
            "      contents: read\n"
            "      actions: write\n"
        ),
        reusable_workflow="example/cicd/docs.yml",
        minimum_version=(0, 0, 3),
        required_triggers=("pull_request",),
        workflow_run_path=Path(".github/workflows/docs-publish.yml"),
        workflow_run_contents=(
            "name: Publish Documentation\n"
            "on:\n"
            "  workflow_run:\n"
            "    workflows: [Documentation]\n"
            "jobs:\n"
            "  docs-publish:\n"
            "    uses: example/cicd/publish.yml@source\n"
        ),
    )

    apply_policy(tmp_path, Policy("example", "Example", None, None, (operation,)))

    build_result = build.read_text()
    workflow_run_result = workflow_run.read_text()
    assert "name: Repository Docs\n" in build_result
    assert "  pull_request:\n" in build_result
    assert "example/cicd/docs.yml@source" in build_result
    assert (
        "    permissions:\n      contents: read\n      actions: write\n" in build_result
    )
    assert 'workflows: ["Repository Docs"]' in workflow_run_result
    assert "example/cicd/publish.yml@source" in workflow_run_result


def test_synchronize_workflow_rejects_ambiguous_selection(tmp_path: Path) -> None:
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    for name in ("first.yml", "second.yml"):
        (workflows / name).write_text(
            "name: Docs\n"
            "on: [push]\n"
            "jobs:\n"
            "  docs:\n"
            "    uses: example/cicd/docs.yml@v0.0.3\n"
        )
    operation = SynchronizeWorkflow(
        source=Path("docs.yml"),
        contents=(
            "name: Docs\n"
            "on:\n"
            "  push:\n"
            "jobs:\n"
            "  docs:\n"
            "    uses: example/cicd/docs.yml@source\n"
        ),
        reusable_workflow="example/cicd/docs.yml",
        minimum_version=(0, 0, 3),
        required_triggers=("push",),
    )

    with pytest.raises(RepoPolicySyncError, match="only one workflow"):
        apply_policy(tmp_path, Policy("example", "Example", None, None, (operation,)))
