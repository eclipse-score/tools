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
"""Find the base branch run a pull request's coverage is compared against.

Used by actions/report/action.yml. The pull request's tests measure its head
commit, so the fitting base numbers are those of the merge base: the commit
the pull request branched off. Comparing with the newest base run instead
would mix in everything merged into the base branch since.

1. Ask GitHub for the merge base of the base branch and the head commit.
2. List the base branch runs that uploaded the report artifact.
3. Take the run of the merge base; without one, the newest run. The summary
   then says it compared with another commit (see cli.py --base-commit).

Reads from the environment::

    GH_TOKEN  REPO  BASE_BRANCH  ARTIFACT  HEAD_SHA  GITHUB_OUTPUT
    GITHUB_API_URL       set by GitHub; https://api.github.com if missing

Writes ``merge_base=<sha or empty>`` and, if a run was found,
``run_id=<id>`` to GITHUB_OUTPUT. Never fails because of GitHub: a pull request
summary without comparison is still useful.
"""

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

type Get = Callable[[str], Any]

# 100 artifacts per page. Ten pages reach back far enough for any open pull
# request; artifacts expire after 90 days by default anyway.
_PER_PAGE = 100
_MAX_PAGES = 10


def github_get(token: str, api_url: str) -> Get:
    """Return a function that GETs a GitHub REST API path and parses the JSON."""

    def get(path: str) -> Any:
        request = urllib.request.Request(
            f"{api_url.rstrip('/')}/{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    return get


def merge_base(get: Get, repo: str, base_branch: str, head_sha: str) -> str | None:
    """Return the commit ``head_sha`` branched off ``base_branch``; None if unknown."""
    basehead = urllib.parse.quote(f"{base_branch}...{head_sha}", safe="/.")
    try:
        return get(f"repos/{repo}/compare/{basehead}")["merge_base_commit"]["sha"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"warning: no merge base of {base_branch} and {head_sha}: {exc!r}")
        return None


def base_runs(
    get: Get, repo: str, artifact: str, base_branch: str
) -> list[dict[str, Any]]:
    """Return the workflow runs that uploaded ``artifact`` on ``base_branch``, newest first.

    The artifact name alone is no proof: a pull request can change the
    workflow and upload under that name, and a fork's branch can have the base
    branch's name. Only runs of this repository's own branches count.
    """
    runs: list[dict[str, Any]] = []
    name = urllib.parse.quote(artifact)
    for page in range(1, _MAX_PAGES + 1):
        artifacts = get(
            f"repos/{repo}/actions/artifacts?name={name}&per_page={_PER_PAGE}&page={page}"
        )["artifacts"]
        for item in artifacts:
            run = item.get("workflow_run") or {}
            if (
                not item.get("expired")
                and run.get("head_branch") == base_branch
                and run.get("repository_id") is not None
                and run.get("head_repository_id") == run.get("repository_id")
            ):
                runs.append({**run, "created_at": item["created_at"]})
        if len(artifacts) < _PER_PAGE:
            break
    return sorted(runs, key=lambda run: run["created_at"], reverse=True)


def choose(runs: list[dict[str, Any]], base_sha: str | None) -> dict[str, Any] | None:
    """Return the run of ``base_sha``; without one, the newest run."""
    for run in runs:
        if base_sha and run.get("head_sha") == base_sha:
            return run
    return runs[0] if runs else None


def main(env: Mapping[str, str] = os.environ, get: Get | None = None) -> int:
    if get is None:
        api_url = env.get("GITHUB_API_URL") or "https://api.github.com"
        get = github_get(env["GH_TOKEN"], api_url)
    repo, base_branch = env["REPO"], env["BASE_BRANCH"]

    base_sha = merge_base(get, repo, base_branch, env["HEAD_SHA"])
    try:
        runs = base_runs(get, repo, env["ARTIFACT"], base_branch)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"warning: cannot list {env['ARTIFACT']} artifacts: {exc!r}")
        runs = []
    run = choose(runs, base_sha)

    if run is None:
        print(f"No {env['ARTIFACT']} artifact of {base_branch}; no comparison.")
    else:
        print(
            f"Comparing with run {run['id']} of {base_branch} at {run.get('head_sha')}."
        )
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"merge_base={base_sha or ''}\n")
        if run is not None:
            output.write(f"run_id={run['id']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
