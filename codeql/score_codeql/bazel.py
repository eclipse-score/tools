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

"""Bazel invocation helpers, target-pattern handling and workspace discovery."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from score_codeql.errors import ScaError
from score_codeql.process import run_capture

DEFAULT_AUTO_EXCLUDE_DIRS = ("utests", "tests", "test", "mocks", "mock")
_BUILD_FILE_NAMES = ("BUILD", "BUILD.bazel")


def find_bazel(explicit: str | None = None) -> str:
    """Bazel executable: ``--bazel``, then ``$BAZEL``, then bazelisk, then bazel."""
    for configured in (explicit, os.environ.get("BAZEL")):
        if configured:
            found = shutil.which(configured)
            if not found:
                raise ScaError(f"Bazel executable {configured!r} was not found")
            return found
    for candidate in ("bazelisk", "bazel"):
        found = shutil.which(candidate)
        if found:
            return found
    raise ScaError("no Bazel executable found (tried bazelisk and bazel; use --bazel)")


def find_workspace(
    environ: dict[str, str] | None = None, cwd: Path | None = None
) -> Path:
    """Workspace root: ``BUILD_WORKSPACE_DIRECTORY`` or the nearest ``MODULE.bazel``."""
    env = os.environ if environ is None else environ
    explicit = env.get("BUILD_WORKSPACE_DIRECTORY")
    if explicit:
        return Path(explicit)
    start = Path(env.get("BUILD_WORKING_DIRECTORY") or cwd or Path.cwd()).resolve()
    for directory in (start, *start.parents):
        if any(
            (directory / marker).exists()
            for marker in ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel")
        ):
            return directory
    raise ScaError(
        f"no Bazel workspace (MODULE.bazel) found at or above {start}; "
        "run the analysis from inside the workspace"
    )


def user_path(value: str | Path) -> Path:
    """Resolve a user path against the directory ``bazel run`` was started in."""
    path = Path(value)
    if not path.is_absolute():
        path = Path(os.environ.get("BUILD_WORKING_DIRECTORY") or Path.cwd()) / path
    return Path(os.path.normpath(path))


def bazel_target_args(targets: Sequence[str]) -> list[str]:
    """Targets prefixed with ``--`` so negative patterns are not parsed as options."""
    return ["--", *targets] if targets else []


def split_bazel_targets(targets: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split into positive patterns and negative patterns (without the ``-``)."""
    items = list(targets)
    positive = [t for t in items if not t.startswith("-")]
    negative = [t[1:] for t in items if t.startswith("-")]
    return positive, negative


def negative_target_to_exclude_glob(target: str) -> str | None:
    """Map a *package-wide* negative pattern (``//foo`` or ``//foo/...``) to a glob.

    Exact labels (``//foo:bar``) cannot be mapped: compile commands do not record
    target ownership, so widening them would silently over-exclude siblings.
    """
    if not target.startswith("//"):
        return None
    label = target[2:]
    if ":" in label:
        return None
    if label == "...":
        return "/**"
    package = label[:-4] if label.endswith("/...") else label
    package = package.rstrip("/")
    if not package:
        return None
    return f"/{package}/**"


def build_flags(force_rebuild: bool) -> list[str]:
    """Flags for the materialising ``bazel build`` (see README, 'Build step')."""
    flags = [
        "--experimental_convenience_symlinks=normal",
        "--remote_download_outputs=all",
    ]
    if force_rebuild:
        # A warm action cache can skip creating tree artifacts and the execroot
        # `external/` forest that replay needs; force a fresh, non-uploaded build.
        flags += [
            f"--action_env=_SCORE_CODEQL_MATERIALIZE_RUN_ID={uuid.uuid4().hex}",
            "--noremote_upload_local_results",
            "--disk_cache=",
        ]
    return flags


def resolve_labels_to_source_globs(
    workspace: Path, labels: Sequence[str], bazel: str
) -> list[str]:
    """Map negative exact labels to workspace-relative source-file globs.

    ``bazel-compile-commands`` cannot forward negative patterns to ``aquery``, so
    exact labels are expanded with ``bazel query`` (aliases, filegroups and the
    outputs of generating rules) and excluded by path instead.
    """
    if not labels:
        return []
    queries: list[tuple[str, str]] = [(" + ".join(labels), "target")]
    globs: list[str] = []
    seen: set[tuple[str, str]] = set()
    while queries:
        expression, mode = queries.pop()
        try:
            result = run_capture(
                [bazel, "query", expression, "--output=label_kind"], cwd=workspace
            )
        except ScaError as exc:
            raise ScaError(
                f"failed to resolve source files for negative label(s) "
                f"{', '.join(labels)}: {exc}",
                exc.exit_code,
            ) from exc
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            kind, label = line.rsplit(" ", 1)
            if not label.startswith("//") or (mode, label) in seen:
                continue
            seen.add((mode, label))
            if kind == "alias rule":
                queries.append((f'labels("actual", {label})', mode))
            elif mode == "target" and kind not in ("source file", "generated file"):
                queries.append((f'labels("srcs", {label})', "src"))
            elif mode == "src" and kind == "filegroup rule":
                queries.append((f'labels("srcs", {label})', "src"))
            elif mode == "src" and kind not in ("source file", "generated file"):
                # A generating rule referenced from srcs: its outputs are compiled.
                queries.append((f'labels("outs", {label})', "out"))
            else:
                package, _, file_name = label[2:].partition(":")
                if not file_name:
                    continue
                globs.append(f"/{package}/{file_name}" if package else f"/{file_name}")
    return globs


def _info(bazel: str, key: str, workspace: Path) -> Path:
    # --platforms= keeps a default target platform from leaking into `bazel info`,
    # which cannot resolve Bzlmod target-platform repositories.
    result = run_capture([bazel, "info", key, "--platforms="], cwd=workspace)
    value = result.stdout.strip()
    if not value:
        raise ScaError(f"Bazel returned an empty {key}")
    return Path(value)


def get_execution_root(bazel: str, workspace: Path) -> Path:
    return _info(bazel, "execution_root", workspace)


def get_output_base(bazel: str, workspace: Path) -> Path:
    return _info(bazel, "output_base", workspace)


def _read_bazelignore(workspace: Path) -> set[str]:
    path = workspace / ".bazelignore"
    entries: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.add(os.path.normpath(line))
    return entries


def auto_exclude_targets(
    workspace: Path,
    exclude_dirs: Iterable[str] = DEFAULT_AUTO_EXCLUDE_DIRS,
    skip_dirs: Iterable[Path] = (),
) -> list[str]:
    """Negative patterns for every Bazel package living in a test/mock directory.

    The walk never follows symlinks and prunes ``bazel-*`` convenience symlinks,
    ``.git``, *skip_dirs* (e.g. the output directory) and ``.bazelignore`` entries.
    """
    names = set(exclude_dirs)
    ignored = _read_bazelignore(workspace)
    skipped = {Path(p).resolve() for p in skip_dirs}
    found: set[str] = set()
    for current, dirs, files in os.walk(workspace, followlinks=False):
        here = Path(current)
        rel_here = os.path.relpath(here, workspace)
        keep: list[str] = []
        for name in dirs:
            child = here / name
            rel_child = os.path.normpath(
                name if rel_here == "." else os.path.join(rel_here, name)
            )
            if (
                name == ".git"
                or name.startswith("bazel-")
                or child.is_symlink()
                or child.resolve() in skipped
                or rel_child in ignored
            ):
                continue
            keep.append(name)
        dirs[:] = keep
        if (
            here.name in names
            and rel_here != "."
            and any(b in files for b in _BUILD_FILE_NAMES)
        ):
            found.add(f"-//{Path(rel_here).as_posix()}/...")
    if found:
        logging.info("Auto-excluded %d test/mock package(s) from analysis", len(found))
    return sorted(found)
