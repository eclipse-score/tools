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

"""Small subprocess boundary for pre-authenticated gh and Git commands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .errors import CommandError, redact_sensitive_text
from .models import Change, Policy, policy_branch_slug

TOOL_SLUG = "repo-policy-sync"
AUTOMATION_LABELS = ("automation", TOOL_SLUG)
AUTOMATION_LABEL_COLOR = "EDEDED"
_PRE_COMMIT_ENVIRONMENT_KEYS = {
    "CI",
    "LANG",
    "PATH",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
    "LOGNAME",
}


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    expected_head_oid: str | None = None
    warnings: tuple[str, ...] = ()
    branch: str = ""
    merged_at: str | None = None
    body: str | None = None
    mergeable: str | None = None


@dataclass(frozen=True)
class PolicyPullRequestStatus:
    """The relevant current and historical PRs for one repository policy."""

    open: PullRequest | None = None
    merged: PullRequest | None = None


@dataclass(frozen=True)
class CommitResult:
    """The published commit and any pre-commit failure allowed by the caller."""

    head_oid: str
    pre_commit_failure: str | None = None


class GitHubCli:
    """Run the minimal gh/Git command set required by this tool."""

    def find_open_pull_request(
        self,
        *,
        repository: str,
        branches: tuple[str, ...],
        policy_id: str,
        legacy_policy_ids: tuple[str, ...] = (),
    ) -> PullRequest | None:
        """Find one PR owned by the policy."""

        pull_requests = self._find_policy_pull_requests(
            repository=repository,
            branches=branches,
            policy_id=policy_id,
            legacy_policy_ids=legacy_policy_ids,
            state="open",
        )
        if len(pull_requests) > 1:
            urls = ", ".join(pull_request.url for pull_request in pull_requests)
            raise CommandError(
                f"multiple open pull requests match policy {policy_id} in {repository}: {urls}"
            )
        return pull_requests[0] if pull_requests else None

    def find_policy_pull_request_status(
        self,
        *,
        repository: str,
        branches: tuple[str, ...],
        policy_id: str,
        legacy_policy_ids: tuple[str, ...] = (),
    ) -> PolicyPullRequestStatus:
        """Find the open PR and latest merged PR owned by a repository policy."""

        open_pull_requests = self._find_policy_pull_requests(
            repository=repository,
            branches=branches,
            policy_id=policy_id,
            legacy_policy_ids=legacy_policy_ids,
            state="open",
        )
        if len(open_pull_requests) > 1:
            urls = ", ".join(pull_request.url for pull_request in open_pull_requests)
            raise CommandError(
                f"multiple open pull requests match policy {policy_id} in {repository}: {urls}"
            )
        merged_pull_requests = self._find_policy_pull_requests(
            repository=repository,
            branches=branches,
            policy_id=policy_id,
            legacy_policy_ids=legacy_policy_ids,
            state="merged",
        )
        latest_merged = max(
            merged_pull_requests,
            key=lambda pull_request: (
                pull_request.merged_at or "",
                pull_request.number,
            ),
            default=None,
        )
        return PolicyPullRequestStatus(
            open=open_pull_requests[0] if open_pull_requests else None,
            merged=latest_merged,
        )

    def _find_policy_pull_requests(
        self,
        *,
        repository: str,
        branches: tuple[str, ...],
        policy_id: str,
        legacy_policy_ids: tuple[str, ...],
        state: str,
    ) -> tuple[PullRequest, ...]:
        """Find policy-owned PRs in one GitHub state across its branch."""

        owned: list[PullRequest] = []
        accepted_markers = {
            _policy_marker(identifier) for identifier in (policy_id, *legacy_policy_ids)
        }
        fields = (
            "number,url,body,mergedAt"
            if state == "merged"
            else "number,url,body,mergeable"
        )
        for branch in branches:
            output = self._run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repository,
                    "--head",
                    branch,
                    "--state",
                    state,
                    "--json",
                    fields,
                ]
            )
            try:
                pull_requests = json.loads(output)
            except json.JSONDecodeError as exc:
                raise CommandError(
                    f"gh returned invalid pull-request JSON for {repository}"
                ) from exc
            if not isinstance(pull_requests, list):
                raise CommandError(
                    f"gh returned invalid pull-request JSON for {repository}"
                )
            for pull_request in pull_requests:
                if not isinstance(pull_request, dict):
                    raise CommandError(
                        f"gh returned invalid pull-request JSON for {repository}"
                    )
                raw_body = pull_request.get("body", "")
                body = "" if raw_body is None else raw_body
                if not isinstance(body, str):
                    raise CommandError(
                        f"gh returned invalid pull-request JSON for {repository}"
                    )
                if not any(marker in body for marker in accepted_markers):
                    if state == "merged":
                        # Merged history may contain an unrelated PR from a
                        # previous branch user; only an open PR can block reuse.
                        continue
                    raise CommandError(
                        f"refusing to reuse {repository} branch {branch}: its {state} pull request "
                        f"is not owned by policy {policy_id}"
                    )
                number = pull_request.get("number")
                url = pull_request.get("url")
                if not isinstance(number, int) or not isinstance(url, str):
                    raise CommandError(
                        f"gh returned invalid pull-request JSON for {repository}"
                    )
                merged_at = pull_request.get("mergedAt")
                if merged_at is not None and not isinstance(merged_at, str):
                    raise CommandError(
                        f"gh returned invalid pull-request JSON for {repository}"
                    )
                mergeable = pull_request.get("mergeable")
                if mergeable is not None and not isinstance(mergeable, str):
                    raise CommandError(
                        f"gh returned invalid pull-request JSON for {repository}"
                    )
                owned.append(
                    PullRequest(
                        number=number,
                        url=url,
                        expected_head_oid=_policy_head_marker_from_body(body),
                        branch=branch,
                        merged_at=merged_at,
                        body=body,
                        mergeable=mergeable,
                    )
                )
        return tuple(owned)

    def switch_to_policy_branch(
        self, *, checkout: Path, branch: str, exists_remotely: bool
    ) -> None:
        if exists_remotely:
            self._run(["git", "-C", str(checkout), "fetch", "origin", branch])
            self._run(
                ["git", "-C", str(checkout), "switch", "-C", branch, "FETCH_HEAD"]
            )
        else:
            # A cached checkout can retain a local branch from a failed run.
            # It is disposable, so recreate that branch from the freshly synced
            # default branch instead of failing because the name already exists.
            self._run(["git", "-C", str(checkout), "switch", "-C", branch])

    def recreate_policy_branch(self, *, checkout: Path, branch: str) -> None:
        """Start a policy branch again from the already-synced default branch."""

        self._run(["git", "-C", str(checkout), "switch", "-C", branch])

    def verify_policy_branch_head(
        self, *, checkout: Path, branch: str, expected_head_oid: str
    ) -> None:
        """Refuse to alter a policy branch whose head changed outside this tool."""

        output = self._run(
            ["git", "-C", str(checkout), "ls-remote", "origin", f"refs/heads/{branch}"]
        )
        actual_head_oid = output.split(maxsplit=1)[0] if output.strip() else ""
        if actual_head_oid != expected_head_oid:
            raise CommandError(
                f"refusing to modify policy branch {branch}: expected {expected_head_oid}, "
                f"found {actual_head_oid or 'no remote branch'}"
            )

    def commit_and_push(
        self,
        *,
        checkout: Path,
        branch: str,
        policy: Policy,
        changes: tuple[Change, ...],
        allow_dirty_pr: bool = False,
    ) -> CommitResult:
        paths = tuple(dict.fromkeys(str(change.path) for change in changes))
        self._run(["git", "-C", str(checkout), "add", "-A", "--", *paths])
        pre_commit_ran, pre_commit_failure = self._run_pre_commit(
            checkout=checkout, paths=paths, allow_dirty_pr=allow_dirty_pr
        )
        if pre_commit_ran:
            self._run(["git", "-C", str(checkout), "add", "-A", "--", *paths])
        self._run(["git", "-C", str(checkout), "commit", "-m", policy.title])
        self._run(
            ["git", "-C", str(checkout), "push", "--set-upstream", "origin", branch]
        )
        return CommitResult(
            head_oid=self._run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"]
            ).strip(),
            pre_commit_failure=pre_commit_failure,
        )

    def run_pre_commit(
        self, *, checkout: Path, paths: tuple[str, ...] | None = None
    ) -> bool:
        """Run every configured pre-commit hook before publishing policy changes.

        A non-zero result is allowed one retry because formatter hooks commonly
        fix files and use their first run to report that they changed them.
        The caller stages those fixes after this method returns.
        """

        if not (checkout / ".pre-commit-config.yaml").is_file():
            return False
        if paths is not None and not paths:
            return False
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in _PRE_COMMIT_ENVIRONMENT_KEYS or key.startswith("LC_")
        }
        with tempfile.TemporaryDirectory(prefix=f"{TOOL_SLUG}-pre-commit-") as home:
            environment.update(
                {
                    "HOME": home,
                    "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                    "GH_CONFIG_DIR": str(Path(home) / ".gh"),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
            command = ["pre-commit", "run", "--all-files"]
            if paths is not None:
                command = ["pre-commit", "run", "--files", *paths]
            self._run(command, cwd=checkout, env=environment)
        return True

    def _run_pre_commit(
        self, *, checkout: Path, paths: tuple[str, ...], allow_dirty_pr: bool
    ) -> tuple[bool, str | None]:
        """Run pre-commit twice when needed so formatter fixes can be published cleanly."""

        existing_paths = tuple(path for path in paths if (checkout / path).exists())
        if not existing_paths:
            return False, None
        try:
            ran = self.run_pre_commit(checkout=checkout, paths=existing_paths)
        except CommandError:
            self._run(["git", "-C", str(checkout), "add", "-A", "--", *paths])
            try:
                ran = self.run_pre_commit(checkout=checkout, paths=existing_paths)
            except CommandError as exc:
                if not allow_dirty_pr:
                    raise
                return True, str(exc)
            # The first attempt ran, so its formatting changes must be staged
            # even if the configuration disappears before the retry.
            return True, None
        return ran, None

    def has_changes(self, *, checkout: Path, changes: tuple[Change, ...]) -> bool:
        paths = tuple(dict.fromkeys(str(change.path) for change in changes))
        if not paths:
            return False
        return bool(
            self._run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "status",
                    "--short",
                    "--untracked-files=all",
                    "--",
                    *paths,
                ]
            ).strip()
        )

    def commit_and_force_push(
        self,
        *,
        checkout: Path,
        branch: str,
        expected_head_oid: str,
        policy: Policy,
        changes: tuple[Change, ...],
        allow_dirty_pr: bool = False,
    ) -> CommitResult:
        paths = tuple(dict.fromkeys(str(change.path) for change in changes))
        self._run(["git", "-C", str(checkout), "add", "-A", "--", *paths])
        pre_commit_ran, pre_commit_failure = self._run_pre_commit(
            checkout=checkout, paths=paths, allow_dirty_pr=allow_dirty_pr
        )
        if pre_commit_ran:
            self._run(["git", "-C", str(checkout), "add", "-A", "--", *paths])
        self._run(["git", "-C", str(checkout), "commit", "-m", policy.title])
        self._run(
            [
                "git",
                "-C",
                str(checkout),
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected_head_oid}",
                "--set-upstream",
                "origin",
                branch,
            ]
        )
        return CommitResult(
            head_oid=self._run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"]
            ).strip(),
            pre_commit_failure=pre_commit_failure,
        )

    def create_pull_request(
        self,
        *,
        repository: str,
        base: str,
        branch: str,
        policy: Policy,
        changes: tuple[Change, ...],
        head_oid: str,
        draft: bool = False,
    ) -> PullRequest:
        self._ensure_automation_labels(repository=repository)
        create_command = [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            base,
            "--head",
            branch,
            "--title",
            policy.title,
            "--body",
            _pull_request_body(policy, changes, head_oid=head_oid),
        ]
        if draft:
            create_command.insert(3, "--draft")
        output = self._run(create_command).strip()
        if not output:
            raise CommandError(f"gh did not return a pull-request URL for {repository}")
        warnings: list[str] = []
        for label in AUTOMATION_LABELS:
            try:
                self._run(["gh", "pr", "edit", output, "--add-label", label])
            except CommandError as exc:
                warnings.append(f"label {label!r} was not applied: {exc}")
        return PullRequest(number=0, url=output, warnings=tuple(warnings))

    def _ensure_automation_labels(self, *, repository: str) -> None:
        """Create the labels applied to generated pull requests when they are absent."""

        output = self._run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"/repos/{repository}/labels?per_page=100",
            ]
        )
        try:
            pages = json.loads(output)
        except json.JSONDecodeError as exc:
            raise CommandError(
                f"gh returned invalid label JSON for {repository}"
            ) from exc
        if not isinstance(pages, list):
            raise CommandError(f"gh returned invalid label JSON for {repository}")

        existing_labels: set[str] = set()
        for page in pages:
            if not isinstance(page, list):
                raise CommandError(f"gh returned invalid label JSON for {repository}")
            for label in page:
                if not isinstance(label, dict):
                    raise CommandError(
                        f"gh returned invalid label JSON for {repository}"
                    )
                name = label.get("name")
                if not isinstance(name, str) or not name:
                    raise CommandError(
                        f"gh returned invalid label JSON for {repository}"
                    )
                existing_labels.add(name)

        for label in AUTOMATION_LABELS:
            if label not in existing_labels:
                self._run(
                    [
                        "gh",
                        "api",
                        "--method",
                        "POST",
                        f"/repos/{repository}/labels",
                        "-f",
                        f"name={label}",
                        "-f",
                        f"color={AUTOMATION_LABEL_COLOR}",
                    ]
                )

    def update_pull_request(
        self,
        *,
        repository: str,
        pull_request: PullRequest,
        policy: Policy,
        changes: tuple[Change, ...],
        head_oid: str,
        failure: str | None = None,
    ) -> None:
        """Keep an existing policy-owned pull request's explanation current."""

        self._run(
            [
                "gh",
                "pr",
                "edit",
                pull_request.url,
                "--repo",
                repository,
                "--title",
                policy.title,
                "--body",
                _pull_request_body(policy, changes, head_oid=head_oid, failure=failure),
            ]
        )

    def close_pull_request(self, *, repository: str, pull_request: PullRequest) -> None:
        """Close a policy-owned pull request after ownership is verified."""

        # A closed generated PR no longer needs its policy branch. Removing it
        # prevents stale branch contents from being mistaken for current work.
        self._run(
            [
                "gh",
                "pr",
                "close",
                pull_request.url,
                "--repo",
                repository,
                "--delete-branch",
            ]
        )

    def mark_pull_request_draft(
        self, *, repository: str, pull_request: PullRequest
    ) -> None:
        """Keep a pull request in draft state until its dirty changes are fixed."""

        self._run(
            ["gh", "pr", "ready", pull_request.url, "--repo", repository, "--undo"]
        )

    def comment_on_pull_request(
        self, *, repository: str, pull_request: PullRequest, failure: str
    ) -> None:
        """Explain why a dirty draft pull request was created."""

        self._run(
            [
                "gh",
                "pr",
                "comment",
                pull_request.url,
                "--repo",
                repository,
                "--body",
                _pre_commit_failure_comment(failure),
            ]
        )

    @staticmethod
    def _run(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            raise CommandError(
                f"required command is unavailable: {command[0]}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or "command failed"
            raise CommandError(f"{' '.join(command[:3])}: {detail}") from exc
        return result.stdout


def policy_branch(policy_id: str) -> str:
    """Map a stable policy identifier to a safe, deterministic branch name."""

    slug = policy_branch_slug(policy_id)
    if not slug:
        raise ValueError(f"policy ID cannot produce a branch name: {policy_id!r}")
    return f"{TOOL_SLUG}/{slug}"


def policy_branches(policy: Policy) -> tuple[str, ...]:
    """Return current and legacy branches that own this policy's PR."""

    return tuple(
        dict.fromkeys(
            policy_branch(identifier)
            for identifier in (policy.id, *policy.legacy_names)
        )
    )


def _policy_marker(policy_id: str) -> str:
    return f"<!-- {TOOL_SLUG}-policy: {policy_id} -->"


def _policy_head_marker(head_oid: str) -> str:
    return f"<!-- {TOOL_SLUG}-head: {head_oid} -->"


def _policy_head_marker_from_body(body: str) -> str | None:
    match = re.search(rf"<!-- {re.escape(TOOL_SLUG)}-head: ([0-9a-f]{{40}}) -->", body)
    return match.group(1) if match else None


def _pull_request_body(
    policy: Policy,
    changes: tuple[Change, ...],
    *,
    head_oid: str,
    failure: str | None = None,
) -> str:
    """Build the concise, policy-centred pull-request template."""

    description = (
        policy.description
        or "Applies the repository policy described by this pull request."
    )
    change_lines = "\n".join(
        f"- `{change.path}`: {change.description}"
        + (f"\n  - {change.rationale}" if change.rationale else "")
        for change in changes
    )
    template = (
        files("repo_policy_sync")
        .joinpath("templates/pull_request.md")
        .read_text(encoding="utf-8")
    )
    values = {
        "policy_marker": _policy_marker(policy.id),
        "policy_head_marker": _policy_head_marker(head_oid),
        "policy_id": policy.id,
        "policy_description": description,
        "policy_trigger": _policy_trigger(policy, changes),
        "changes": change_lines,
        "failure_section": _failure_section(failure),
    }
    for key, value in values.items():
        template = template.replace(f"{{{{ {key} }}}}", value)
    return template


def _failure_section(failure: str | None) -> str:
    if failure is None:
        return ""
    failure = redact_sensitive_text(failure)
    return (
        "\n## Automation failure\n\n"
        "SCORE Repository Policy Sync could not apply this policy and closed this pull request.\n\n"
        f"```text\n{failure}\n```\n"
    )


def _pre_commit_failure_comment(failure: str) -> str:
    failure = redact_sensitive_text(failure)
    return (
        "SCORE Repository Policy Sync created this draft pull request because pre-commit "
        "still failed after an automatic formatting-fix retry. Please fix the failure "
        "before marking it ready.\n\n"
        f"```text\n{failure}\n```"
    )


def _policy_trigger(policy: Policy, changes: tuple[Change, ...]) -> str:
    paths = tuple(dict.fromkeys(change.path for change in changes))
    targets = ", ".join(f"`{path}`" for path in paths)
    reasons: list[str] = []
    file_exists_condition = policy.file_exists_condition
    if file_exists_condition is not None:
        reasons.append(f"`{file_exists_condition.path}` exists")
    file_condition = policy.file_contains_condition
    if file_condition is not None:
        reasons.append(
            f"`{file_condition.path}` matches this policy's file-content condition"
        )
    file_any_condition = policy.file_contains_any_condition
    if file_any_condition is not None:
        paths = ", ".join(
            f"`{condition.path}`" for condition in file_any_condition.conditions
        )
        reasons.append(f"one of {paths} matches this policy's file-content condition")
    bazel_condition = policy.bazel_condition
    if bazel_condition is not None:
        # Describe both the required group and the alternative group in the PR body.
        dependencies = ", ".join(
            f"`{dependency}`"
            for dependency in bazel_condition.direct_module_dependencies
        )
        if dependencies:
            reasons.append(
                f"`MODULE.bazel` declares the required direct Bazel dependency or dependencies: {dependencies}"
            )
        any_dependencies = ", ".join(
            f"`{dependency}`"
            for dependency in bazel_condition.any_direct_module_dependencies
        )
        if any_dependencies:
            reasons.append(
                f"`MODULE.bazel` declares at least one of these direct Bazel dependencies: {any_dependencies}"
            )
    if reasons:
        return f"This repository matches this policy because {' and '.join(reasons)}."
    return f"This policy applies to configuration in {targets}."
