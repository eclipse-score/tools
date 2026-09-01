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
# PRs created by the previous implementation used this slug in their body and
# branch names.  Keep accepting it while the label remains the stable tool
# ownership signal.
LEGACY_TOOL_SLUG = "repo-sync"
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
    closed_at: str | None = None
    body: str | None = None
    mergeable: str | None = None


@dataclass(frozen=True)
class PolicyPullRequestStatus:
    """The relevant current and historical PRs for one repository policy."""

    open: PullRequest | None = None
    merged: PullRequest | None = None
    closed: PullRequest | None = None


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
        """Find the open, latest merged, and latest closed PR owned by a policy."""

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
        # `gh pr list --state closed` returns every non-open PR, merged ones
        # included (GitHub's PR "state" only distinguishes open/closed; merged
        # is a separate flag). One query covers both merged and closed history,
        # classified below by whether `mergedAt` is set.
        resolved_pull_requests = self._find_policy_pull_requests(
            repository=repository,
            branches=branches,
            policy_id=policy_id,
            legacy_policy_ids=legacy_policy_ids,
            state="closed",
        )
        merged_pull_requests = tuple(
            pull_request
            for pull_request in resolved_pull_requests
            if pull_request.merged_at is not None
        )
        closed_pull_requests = tuple(
            pull_request
            for pull_request in resolved_pull_requests
            if pull_request.merged_at is None
        )
        latest_merged = max(
            merged_pull_requests,
            key=lambda pull_request: (
                pull_request.merged_at or "",
                pull_request.number,
            ),
            default=None,
        )
        latest_closed = max(
            closed_pull_requests,
            key=lambda pull_request: (
                pull_request.closed_at or "",
                pull_request.number,
            ),
            default=None,
        )
        return PolicyPullRequestStatus(
            open=open_pull_requests[0] if open_pull_requests else None,
            merged=latest_merged,
            closed=latest_closed,
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
        """Find policy-owned PRs in one GitHub state using the tool label."""

        owned: list[PullRequest] = []
        accepted_markers = _policy_markers((policy_id, *legacy_policy_ids))
        if state == "closed":
            fields = "number,url,body,headRefName,mergedAt,closedAt"
        else:
            fields = "number,url,body,headRefName,mergeable"
        output = self._run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--label",
                TOOL_SLUG,
                "--state",
                state,
                "--limit",
                "1000",
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
            branch = pull_request.get("headRefName")
            if not isinstance(body, str) or not isinstance(branch, str) or not branch:
                raise CommandError(
                    f"gh returned invalid pull-request JSON for {repository}"
                )
            if not any(marker in body for marker in accepted_markers):
                if state == "open" and branch in branches:
                    raise CommandError(
                        f"refusing to reuse {repository} branch {branch}: its {state} pull request "
                        f"is not owned by policy {policy_id}"
                    )
                # A labelled PR for another policy is expected in a repository
                # that is managed by the tool; it is not ours to reuse.
                continue
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
            closed_at = pull_request.get("closedAt")
            if closed_at is not None and not isinstance(closed_at, str):
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
                    closed_at=closed_at,
                    body=body,
                    mergeable=mergeable,
                )
            )
        if state == "open":
            self._verify_no_unlabeled_branch_collision(
                repository=repository,
                branches=branches,
                owned_branches={pull_request.branch for pull_request in owned},
                policy_id=policy_id,
            )
        return tuple(owned)

    def _verify_no_unlabeled_branch_collision(
        self,
        *,
        repository: str,
        branches: tuple[str, ...],
        owned_branches: set[str],
        policy_id: str,
    ) -> None:
        """Refuse to reuse an expected branch whose open PR lacks the tool label.

        The `--label` lookup above cannot see a PR that never got the tool
        label: a maintainer's own PR on the branch, or a tool-created PR whose
        label application failed. Without this check either would be silently
        invisible and a later apply could push over it or attempt a duplicate
        pull request instead of refusing safely.
        """

        for branch in branches:
            if branch in owned_branches:
                continue
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
                    "open",
                    "--json",
                    "number",
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
            if pull_requests:
                raise CommandError(
                    f"refusing to reuse {repository} branch {branch}: its open pull "
                    f"request is not owned by policy {policy_id}"
                )

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
        # The tool label is the sole ownership signal `_find_policy_pull_requests`
        # relies on, so a PR missing it would be invisible to every later run
        # and could be duplicated or overwritten. Only the cosmetic "automation"
        # label may fail without aborting.
        warnings: list[str] = []
        pull_request = PullRequest(number=_pull_request_number(output), url=output)
        for label in AUTOMATION_LABELS:
            if label == TOOL_SLUG:
                self._add_pull_request_label(
                    repository=repository, pull_request=pull_request, label=label
                )
                continue
            try:
                self._add_pull_request_label(
                    repository=repository, pull_request=pull_request, label=label
                )
            except CommandError as exc:
                warnings.append(f"label {label!r} was not applied: {exc}")
        return PullRequest(
            number=pull_request.number, url=output, warnings=tuple(warnings)
        )

    def _add_pull_request_label(
        self, *, repository: str, pull_request: PullRequest, label: str
    ) -> None:
        """Add a label through REST with the cross-repository bot's repo scope.

        GitHub CLI's pull-request commands are GraphQL-backed and require
        ``read:org`` for organization-owned repositories. The bot deliberately
        has only the narrower ``repo`` scope, which is sufficient for this
        REST endpoint.
        """

        self._run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"/repos/{repository}/issues/{pull_request.number}/labels",
                "-f",
                f"labels[]={label}",
            ]
        )

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

        # GitHub CLI's pull-request edit command is GraphQL-backed and needs
        # read:org for organization-owned repositories. The cross-repository
        # bot deliberately has only repo scope, which is sufficient here.
        self._run(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"/repos/{repository}/pulls/{pull_request.number}",
                "-f",
                f"title={policy.title}",
                "-f",
                f"body={_pull_request_body(policy, changes, head_oid=head_oid, failure=failure)}",
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


def _policy_markers(policy_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Return current and historical ownership markers for policy IDs."""

    return tuple(
        f"<!-- {tool_slug}-policy: {policy_id} -->"
        for tool_slug in (TOOL_SLUG, LEGACY_TOOL_SLUG)
        for policy_id in policy_ids
    )


def _policy_head_marker(head_oid: str) -> str:
    return f"<!-- {TOOL_SLUG}-head: {head_oid} -->"


def _policy_head_marker_from_body(body: str) -> str | None:
    tool_slugs = "|".join(
        re.escape(tool_slug) for tool_slug in (TOOL_SLUG, LEGACY_TOOL_SLUG)
    )
    match = re.search(rf"<!-- (?:{tool_slugs})-head: ([0-9a-f]{{40}}) -->", body)
    return match.group(1) if match else None


def _pull_request_number(url: str) -> int:
    match = re.search(r"/pull/(\d+)/?$", url)
    if match is None:
        raise CommandError(f"gh did not return a valid pull-request URL: {url}")
    return int(match.group(1))


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
    value_exists_condition = policy.value_exists_condition
    if value_exists_condition is not None:
        value_binding = next(
            (
                binding
                for binding in policy.values
                if binding.name == value_exists_condition.name
            ),
            None,
        )
        if value_binding is None:
            reasons.append(
                f"the policy value `{value_exists_condition.name}` is available"
            )
        else:
            source = value_binding.source
            reasons.append(
                f"the policy value `{value_exists_condition.name}` is available "
                f"because `{source.dockerfile}` contains exactly one "
                f"`{source.image}:vX.Y.Z` FROM instruction"
            )
    if reasons:
        return f"This repository matches this policy because {' and '.join(reasons)}."
    return f"This policy applies to configuration in {targets}."
