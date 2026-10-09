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

"""Secure GitHub API client for repository and file discovery."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any


class GitHubClient:
    """Client for interacting with GitHub REST API securely without token exposure."""

    def __init__(
        self, token: str | None = None, api_base_url: str = "https://api.github.com"
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self._token = token or self._discover_token()
        # Mapping of (owner, repo) -> {submodule_path: (sub_owner, sub_repo, commit_sha)}
        self._submodules: dict[tuple[str, str], dict[str, tuple[str, str, str]]] = {}

    def __repr__(self) -> str:
        return f"GitHubClient(authenticated={bool(self._token)})"

    def __str__(self) -> str:
        return f"GitHubClient(authenticated={bool(self._token)})"

    @staticmethod
    def _discover_token() -> str | None:
        """Finds token in environment variables or via gh CLI fallback."""
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            return token.strip()

        # Try gh CLI if available
        if shutil.which("gh"):
            try:
                res = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass

        return None

    def _request(self, endpoint: str) -> Any:
        """Executes a GET request against GitHub API."""
        url = f"{self.api_base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "score-rust-dependency-audit",
        }
        if self._token:
            headers["Authorization"] = f"token {self._token}"

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Handle rate limiting or not found without leaking tokens
            if e.code == 404:
                return None
            if e.code in (401, 403):
                raise RuntimeError(
                    f"GitHub API access denied (HTTP {e.code}). "
                    "Ensure GITHUB_TOKEN is set with adequate permissions."
                ) from None
            raise RuntimeError(
                f"GitHub API request failed with HTTP {e.code}"
            ) from None
        except Exception as e:
            raise RuntimeError(f"Network error querying GitHub API: {e}") from None

    def get_organization_repositories(
        self, org: str, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        """Retrieves list of repositories for an organization."""
        repos: list[dict[str, Any]] = []
        page = 1
        per_page = 100

        while True:
            endpoint = f"orgs/{org}/repos?type=all&per_page={per_page}&page={page}"
            batch = self._request(endpoint)
            if not batch or not isinstance(batch, list):
                break

            for r in batch:
                if not include_archived and r.get("archived", False):
                    continue
                repos.append(r)

            if len(batch) < per_page:
                break
            page += 1

        return repos

    def _fetch_tree_entries(
        self, owner: str, repo: str, branch_or_sha: str
    ) -> list[dict[str, Any]]:
        """Fetches git tree entries, falling back to manual subtree traversal if truncated."""
        endpoint = f"repos/{owner}/{repo}/git/trees/{branch_or_sha}?recursive=1"
        data = self._request(endpoint)
        if not data or "tree" not in data:
            return []

        # If not truncated, return all entries directly
        if not data.get("truncated", False):
            return list(data.get("tree", []))

        # If truncated, fall back to walking tree nodes level-by-level
        entries: list[dict[str, Any]] = []
        root_data = self._request(f"repos/{owner}/{repo}/git/trees/{branch_or_sha}")
        if not root_data or "tree" not in root_data:
            return list(data.get("tree", []))

        queue: list[tuple[dict[str, Any], str]] = [
            (item, "") for item in root_data.get("tree", [])
        ]
        while queue:
            item, prefix = queue.pop(0)
            item_type = item.get("type")
            item_path = (
                f"{prefix}/{item['path']}".lstrip("/")
                if prefix
                else item.get("path", "")
            )
            item_sha = item.get("sha", "")

            if item_type == "tree":
                sub_tree = self._request(f"repos/{owner}/{repo}/git/trees/{item_sha}")
                if sub_tree and "tree" in sub_tree:
                    for sub_item in sub_tree.get("tree", []):
                        queue.append((sub_item, item_path))
            else:
                entries.append(
                    {
                        "path": item_path,
                        "type": item_type,
                        "sha": item_sha,
                        "mode": item.get("mode", ""),
                    }
                )

        return entries

    @staticmethod
    def _parse_gitmodules(content: str) -> dict[str, str]:
        """Extracts path -> url mapping from .gitmodules content."""
        mapping: dict[str, str] = {}
        curr_path = None
        curr_url = None
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("[submodule"):
                if curr_path and curr_url:
                    mapping[curr_path] = curr_url
                curr_path = None
                curr_url = None
            elif line.startswith("path"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    curr_path = parts[1].strip()
            elif line.startswith("url"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    curr_url = parts[1].strip()
        if curr_path and curr_url:
            mapping[curr_path] = curr_url
        return mapping

    @staticmethod
    def _parse_github_url(
        url: str, fallback_owner: str
    ) -> tuple[str | None, str | None]:
        """Parses git URL into (owner, repo)."""
        u = url.strip()
        if u.startswith("../"):
            repo_name = u[3:].rstrip("/").removesuffix(".git")
            return fallback_owner, repo_name
        if "github.com" in u:
            if ":" in u and not u.startswith("http"):
                u = u.split(":")[-1]
            elif "github.com/" in u:
                u = u.split("github.com/")[-1]
            parts = u.rstrip("/").removesuffix(".git").split("/")
            if len(parts) >= 2:
                return parts[-2], parts[-1]
        return None, None

    def get_repo_git_tree(
        self, owner: str, repo: str, branch: str = "main"
    ) -> list[str]:
        """Fetches recursive tree paths for a repository including submodules."""
        tree_entries = self._fetch_tree_entries(owner, repo, branch)
        if not tree_entries:
            return []

        paths: list[str] = []
        commit_entries: list[dict[str, Any]] = []
        has_gitmodules = False

        for item in tree_entries:
            item_type = item.get("type")
            path = item.get("path", "")
            if item_type == "blob":
                paths.append(path)
                if path == ".gitmodules":
                    has_gitmodules = True
            elif item_type == "commit":
                commit_entries.append(item)

        # Resolve submodules if present
        if commit_entries and has_gitmodules:
            gitmodules_content = self.get_file_content(
                owner, repo, ".gitmodules", branch=branch
            )
            if gitmodules_content:
                submodule_urls = self._parse_gitmodules(gitmodules_content)
                sub_map: dict[str, tuple[str, str, str]] = {}
                for item in commit_entries:
                    sub_path = item.get("path", "")
                    sub_sha = item.get("sha", "")
                    url = submodule_urls.get(sub_path)
                    if url:
                        sub_owner, sub_repo = self._parse_github_url(
                            url, fallback_owner=owner
                        )
                        if sub_owner and sub_repo:
                            sub_map[sub_path] = (sub_owner, sub_repo, sub_sha)
                            try:
                                sub_entries = self._fetch_tree_entries(
                                    sub_owner, sub_repo, sub_sha
                                )
                                for sub_item in sub_entries:
                                    if sub_item.get("type") == "blob":
                                        full_sub_p = (
                                            f"{sub_path}/{sub_item.get('path', '')}"
                                        )
                                        paths.append(full_sub_p)
                            except Exception:
                                pass
                if sub_map:
                    self._submodules[(owner, repo)] = sub_map

        return paths

    def _fetch_file_content(
        self, owner: str, repo: str, path: str, branch_or_sha: str
    ) -> str | None:
        """Helper to fetch raw file content from GitHub raw or REST API."""
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch_or_sha}/{path}"
        headers = {
            "User-Agent": "score-rust-dependency-audit",
        }
        if self._token:
            headers["Authorization"] = f"token {self._token}"

        for attempt in range(3):
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
                time.sleep(0.5 * (attempt + 1))
            except Exception:
                time.sleep(0.5 * (attempt + 1))

        # Fallback to GitHub REST API /repos/{owner}/{repo}/contents/{path}
        try:
            data = self._request(
                f"repos/{owner}/{repo}/contents/{path}?ref={branch_or_sha}"
            )
            if data and isinstance(data, dict) and "content" in data:
                raw_bytes = base64.b64decode(data["content"])
                return raw_bytes.decode("utf-8")
        except Exception:
            pass

        return None

    def get_file_content(
        self, owner: str, repo: str, path: str, branch: str = "main"
    ) -> str | None:
        """Retrieves content of a file from a repository (or submodule) with retry and API fallback."""
        sub_info = self._submodules.get((owner, repo), {})
        for sub_path, (sub_owner, sub_repo, sub_sha) in sub_info.items():
            if path == sub_path or path.startswith(f"{sub_path}/"):
                rel_path = path[len(sub_path) + 1 :]
                return self._fetch_file_content(sub_owner, sub_repo, rel_path, sub_sha)

        return self._fetch_file_content(owner, repo, path, branch)
