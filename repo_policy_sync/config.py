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

"""TOML configuration for Repository Policy Sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from .errors import RepoPolicySyncError

DEFAULT_CONFIG_FILE = Path("score-repo-policy-sync.toml")
CONFIG_SECTION = "score-repo-policy-sync"


@dataclass(frozen=True)
class PolicySyncConfig:
    """Command settings loaded from TOML."""

    org: str | None = None
    policies: tuple[str, ...] | None = None
    repositories: tuple[str, ...] | None = None
    policy_directories: tuple[Path, ...] | None = None
    exclude_bundled_policies: tuple[str, ...] = ()
    recreate: bool | None = None
    allow_dirty_pr: bool | None = None
    quiet: bool | None = None
    cache_directory: Path | None = None
    sync_workers: int | None = None
    policy_workers: int | None = None


def load_config(path: Path | None = None) -> PolicySyncConfig:
    """Load the optional TOML configuration file.

    When no path is supplied, ``score-repo-policy-sync.toml`` is loaded if it
    exists in the current working directory. Paths in the file are resolved
    relative to that file.
    """

    config_path = path or DEFAULT_CONFIG_FILE
    if not config_path.is_file():
        if path is None:
            return PolicySyncConfig()
        raise RepoPolicySyncError(f"configuration file does not exist: {config_path}")
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except OSError as exc:
        raise RepoPolicySyncError(
            f"could not read configuration {config_path}: {exc}"
        ) from exc
    except UnicodeError as exc:
        raise RepoPolicySyncError(
            f"could not decode configuration {config_path} as UTF-8: {exc}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise RepoPolicySyncError(
            f"invalid TOML in configuration {config_path}: {exc}"
        ) from exc

    unexpected_sections = set(raw) - {CONFIG_SECTION}
    if unexpected_sections:
        names = ", ".join(sorted(unexpected_sections))
        raise RepoPolicySyncError(
            f"configuration {config_path}: unexpected sections: {names}"
        )
    section = raw.get(CONFIG_SECTION, {})
    if not isinstance(section, dict):
        raise RepoPolicySyncError(
            f"configuration {config_path}: [{CONFIG_SECTION}] must be a table"
        )
    unexpected = set(section) - {
        "org",
        "policies",
        "repos",
        "policy_dirs",
        "exclude_bundled_policies",
        "recreate",
        "allow_dirty_pr",
        "quiet",
        "cache_dir",
        "sync_workers",
        "policy_workers",
    }
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise RepoPolicySyncError(
            f"configuration {config_path}: unexpected fields: {names}"
        )

    org = _optional_string(section, "org", config_path)
    policies = _optional_string_list(section, "policies", config_path)
    repositories = _optional_string_list(section, "repos", config_path)

    policy_directories = None
    if "policy_dirs" in section:
        policy_directories = tuple(
            _string_list(section["policy_dirs"], "policy_dirs", config_path)
        )
        policy_directories = tuple(
            _resolve_path(config_path, directory) for directory in policy_directories
        )
    exclude_bundled_policies = tuple(
        _string_list(
            section.get("exclude_bundled_policies", []),
            "exclude_bundled_policies",
            config_path,
        )
    )
    recreate = _optional_bool(section, "recreate", config_path)
    allow_dirty_pr = _optional_bool(section, "allow_dirty_pr", config_path)
    quiet = _optional_bool(section, "quiet", config_path)
    cache_directory = None
    if "cache_dir" in section:
        cache_directory = _resolve_path(
            config_path,
            _string_value(section["cache_dir"], "cache_dir", config_path),
        )
    sync_workers = _optional_positive_int(section, "sync_workers", config_path)
    policy_workers = _optional_positive_int(section, "policy_workers", config_path)
    return PolicySyncConfig(
        org=org,
        policies=policies,
        repositories=repositories,
        policy_directories=policy_directories,
        exclude_bundled_policies=exclude_bundled_policies,
        recreate=recreate,
        allow_dirty_pr=allow_dirty_pr,
        quiet=quiet,
        cache_directory=cache_directory,
        sync_workers=sync_workers,
        policy_workers=policy_workers,
    )


def _optional_string(section: dict[str, Any], field: str, source: Path) -> str | None:
    if field not in section:
        return None
    return _string_value(section[field], field, source)


def _string_value(raw: Any, field: str, source: Path) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise RepoPolicySyncError(
            f"configuration {source}: {field} must be a non-empty string"
        )
    return raw


def _optional_string_list(
    section: dict[str, Any], field: str, source: Path
) -> tuple[str, ...] | None:
    if field not in section:
        return None
    return tuple(_string_list(section[field], field, source))


def _string_list(raw: Any, field: str, source: Path) -> tuple[str, ...]:
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        raise RepoPolicySyncError(
            f"configuration {source}: {field} must be a list of non-empty strings"
        )
    return tuple(raw)


def _optional_bool(section: dict[str, Any], field: str, source: Path) -> bool | None:
    if field not in section:
        return None
    raw = section[field]
    if not isinstance(raw, bool):
        raise RepoPolicySyncError(f"configuration {source}: {field} must be a boolean")
    return raw


def _optional_positive_int(
    section: dict[str, Any], field: str, source: Path
) -> int | None:
    if field not in section:
        return None
    raw = section[field]
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise RepoPolicySyncError(
            f"configuration {source}: {field} must be a positive integer"
        )
    return raw


def _resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else config_path.parent / path
