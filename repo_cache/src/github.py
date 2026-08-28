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

"""Organization authentication and repository listing via the `gh` CLI."""

from __future__ import annotations

import json

from .command import run_command
from .errors import CommandError
from .models import Repository


def ensure_authenticated() -> None:
    """Verify `gh` is authenticated before any organization-wide operation."""

    run_command(["gh", "auth", "status"])


def list_repositories(*, org: str) -> tuple[Repository, ...]:
    """List every repository in an organization, including its default branch."""

    output = run_command(
        ["gh", "api", "--paginate", "--slurp", f"/orgs/{org}/repos?per_page=100"]
    )
    try:
        pages = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CommandError(f"gh returned invalid repository JSON for {org}") from exc
    if not isinstance(pages, list):
        raise CommandError(f"gh returned invalid repository JSON for {org}")

    repositories: list[Repository] = []
    for page in pages:
        if not isinstance(page, list):
            raise CommandError(f"gh returned invalid repository JSON for {org}")
        for raw in page:
            repositories.append(_parse_repository(raw, org=org))
    return tuple(repositories)


def _parse_repository(raw: object, *, org: str) -> Repository:
    if not isinstance(raw, dict):
        raise CommandError(f"gh returned invalid repository JSON for {org}")

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise CommandError(f"gh returned a repository without a valid name for {org}")

    default_branch = raw.get("default_branch")
    if default_branch is not None and not isinstance(default_branch, str):
        raise CommandError(f"gh returned an invalid default branch for {org}/{name}")

    archived = raw.get("archived", False)
    if not isinstance(archived, bool):
        raise CommandError(f"gh returned an invalid archived state for {org}/{name}")

    return Repository(name=name, default_branch=default_branch, archived=archived)
