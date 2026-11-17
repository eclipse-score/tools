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

"""Pinned CodeQL pack handling.

Every referenced pack must resolve to exactly the pinned version:

* ``bundle``     - shipped inside the CodeQL distribution (``<dist>/qlpacks``)
* ``cached``     - already present in the pack cache directory
* ``downloaded`` - fetched with ``codeql pack download --dir=<cache> name@version``
* ``local``      - a query pack from a local directory (this repository or a consumer)

The cache layout is ``<cache>/<scope>/<name>/<version>/qlpack.yml``, so it can be
restored from a CI cache. ``$SCORE_CODEQL_PACK_CACHE`` or ``--pack-cache-dir``
selects the cache; ``fetch_packs`` fills it from the release assets, without the
registry. Other versions of a pinned pack are removed from the cache, because the
CLI refuses to resolve a pack that is present more than once. Registry
credentials (``GITHUB_TOKEN`` / ``CODEQL_REGISTRIES_AUTH``) are inherited from the
environment and never logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from score_codeql.config import PackRef, Pins
from score_codeql.errors import ConfigError, ScaError
from score_codeql.process import run_capture, run_cmd

_NAME_RE = re.compile(r"^name:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
_VERSION_RE = re.compile(r"^version:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)

ENV_PACK_CACHE = "SCORE_CODEQL_PACK_CACHE"


@dataclass(frozen=True)
class CliInfo:
    """Facts reported by ``codeql version --format=json``."""

    version: str
    distribution: Path


@dataclass(frozen=True)
class ResolvedPack:
    name: str
    version: str | None
    origin: str
    path: Path


def default_pack_cache(environ: Mapping[str, str] | None = None) -> Path:
    """Pack cache: ``$SCORE_CODEQL_PACK_CACHE`` or ``${XDG_CACHE_HOME:-~/.cache}/score-codeql/packs``.

    The environment variable lets a deployment or CI job point every run at a
    pre-populated cache (see ``fetch_packs``) without changing the targets or
    passing ``--pack-cache-dir``.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_PACK_CACHE, "").strip()
    if override:
        return Path(override).expanduser()
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "score-codeql" / "packs"


def get_cli_info(codeql: Path) -> CliInfo:
    """Ask the CLI for its version and distribution directory."""
    result = run_capture([str(codeql), "version", "--format=json"])
    try:
        data = json.loads(result.stdout)
        return CliInfo(str(data["version"]), Path(data["unpackedLocation"]))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ScaError(f"cannot parse `codeql version --format=json`: {exc}") from exc


def check_cli_version(info: CliInfo, pins: Pins) -> None:
    """The CLI must be exactly the pinned version."""
    if info.version != pins.codeql_version:
        raise ScaError(
            f"CodeQL CLI version {info.version} does not match the pinned version "
            f"{pins.codeql_version} (packs.toml)"
        )


def read_qlpack(directory: Path) -> tuple[str, str | None]:
    """``(name, version)`` from ``<directory>/qlpack.yml``."""
    path = directory / "qlpack.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    name = _NAME_RE.search(text)
    if name is None:
        raise ConfigError(f"{path} has no 'name:'")
    version = _VERSION_RE.search(text)
    return name.group(1), version.group(1) if version else None


def find_local_packs(directories: Iterable[Path]) -> dict[str, tuple[str | None, Path]]:
    """Map pack name -> ``(version, directory)`` for pack directories."""
    packs: dict[str, tuple[str | None, Path]] = {}
    for directory in directories:
        name, version = read_qlpack(directory)
        if name in packs and packs[name][1] != directory:
            raise ConfigError(
                f"local pack {name!r} found in two directories: "
                f"{packs[name][1]} and {directory}"
            )
        packs[name] = (version, directory)
    return packs


def _pack_dir(root: Path, name: str, version: str) -> Path:
    return root / name / version


def has_pack(directory: Path, version: str) -> bool:
    """``qlpack.yml`` in *directory* declares *version*."""
    qlpack = directory / "qlpack.yml"
    if not qlpack.is_file():
        return False
    try:
        return read_qlpack(directory)[1] == version
    except ConfigError:
        return False


def check_cache_shadowing(
    cache_dir: Path,
    distribution: Path,
    *,
    local_names: Iterable[str],
    allowed_external: Iterable[str],
) -> None:
    """Fail if the pack cache holds a pack that must not live there.

    The cache is passed to the CLI with ``--additional-packs``; a directory for a
    bundled or local pack, or for an unknown pack, silently shadows the real one
    and breaks query resolution (e.g. a stub ``codeql/cpp-queries``).
    """
    if not cache_dir.is_dir():
        return
    local = set(local_names)
    allowed = set(allowed_external)
    problems: list[str] = []
    for scope in sorted(cache_dir.iterdir()):
        if scope.name.startswith(".") or not scope.is_dir():
            continue
        for pack_dir in sorted(scope.iterdir()):
            if pack_dir.name.startswith(".") or not pack_dir.is_dir():
                continue
            name = f"{scope.name}/{pack_dir.name}"
            if (distribution / "qlpacks" / name).is_dir():
                reason = "is bundled with the CodeQL CLI"
            elif name in local:
                reason = "is a local pack"
            elif name not in allowed:
                reason = (
                    "is not an external pack listed in packs.toml or referenced "
                    "by the selected reports"
                )
            else:
                continue
            problems.append(f"  {pack_dir}: {name} {reason}")
    if problems:
        raise ScaError(
            f"the pack cache {cache_dir} contains directories that would shadow "
            "other packs and silently break query resolution; remove them:\n"
            + "\n".join(problems)
        )


def _pack_problems(directory: Path, version: str, paths: Iterable[str]) -> list[str]:
    """What is wrong with the pack in *directory* (empty list if it is usable)."""
    if not has_pack(directory, version):
        return [f"qlpack.yml with version {version} is missing"]
    return [f"{p} is missing" for p in paths if not (directory / p).exists()]


def remove_other_versions(cache_dir: Path, name: str, version: str) -> None:
    """Delete every other version of *name* from the pack cache.

    The cache is passed to the CLI with ``--additional-packs``, which offers all
    versions found below it. From 2.26 on the CLI fails with "Referenced pack
    '<name>' was found in multiple locations" instead of picking the pinned one,
    so a cache that survived a version bump would break every run. The cache is
    disposable (it is re-downloaded on demand), so stale versions are removed.
    """
    pack_root = cache_dir / name
    if not pack_root.is_dir():
        return
    for entry in sorted(pack_root.iterdir()):
        if entry.name == version or entry.is_symlink() or not entry.is_dir():
            continue
        logging.info(
            "Removing other cached version %s@%s from %s", name, entry.name, cache_dir
        )
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            raise ScaError(
                f"cannot remove the stale pack version {entry} from the cache: the "
                f"CodeQL CLI refuses to resolve a pack that exists in more than one "
                f"version ({exc})"
            ) from exc


def ensure_packs(
    codeql: Path,
    refs: Sequence[PackRef],
    *,
    cache_dir: Path,
    distribution: Path,
    local_packs: Mapping[str, tuple[str | None, Path]],
    listed_external: Iterable[str] = (),
) -> list[ResolvedPack]:
    """Make every referenced pack available at its pinned version; fail otherwise.

    Besides the pinned version, every path referenced by *refs* (suites, queries)
    must exist in the pack. An incomplete or corrupt cached pack is removed and
    downloaded again once; bundled and local packs are never modified.
    """
    check_cache_shadowing(
        cache_dir,
        distribution,
        local_names=local_packs,
        allowed_external=[
            *listed_external,
            *(r.name for r in refs if r.name not in local_packs),
        ],
    )
    paths: dict[tuple[str, str | None], list[str]] = {}
    for ref in refs:
        paths.setdefault((ref.name, ref.version), [])
        if ref.path:
            paths[(ref.name, ref.version)].append(ref.path)

    resolved: list[ResolvedPack] = []
    for (name, version), wanted in paths.items():
        ref_text = f"{name}@{version}" if version else name
        if name in local_packs:
            local_version, directory = local_packs[name]
            if version and local_version and version != local_version:
                raise ConfigError(
                    f"local pack {name} has version {local_version} but "
                    f"{ref_text} is requested"
                )
            missing = [p for p in wanted if not (directory / p).exists()]
            if missing:
                raise ScaError(
                    f"local pack {name} ({directory}) lacks: {', '.join(missing)}"
                )
            resolved.append(ResolvedPack(name, local_version, "local", directory))
            continue
        if not version:
            raise ConfigError(f"pack reference {name} has no version")
        bundled = _pack_dir(distribution / "qlpacks", name, version)
        if has_pack(bundled, version):
            problems = _pack_problems(bundled, version, wanted)
            if problems:
                raise ScaError(
                    f"bundled pack {ref_text} ({bundled}): {'; '.join(problems)}"
                )
            resolved.append(ResolvedPack(name, version, "bundle", bundled))
            continue
        cached = _pack_dir(cache_dir, name, version)
        origin = "cached"
        problems = (
            _pack_problems(cached, version, wanted) if cached.exists() else ["absent"]
        )
        if problems and cached.exists():
            logging.warning(
                "Cached pack %s is incomplete (%s); removing %s and downloading again",
                ref_text,
                "; ".join(problems),
                cached,
            )
            shutil.rmtree(cached)
        if problems:
            logging.info("Downloading CodeQL pack %s into %s", ref_text, cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            run_cmd([str(codeql), "pack", "download", f"--dir={cache_dir}", ref_text])
            origin = "downloaded"
            problems = _pack_problems(cached, version, wanted)
            if problems:
                raise ScaError(
                    f"pack {ref_text} was downloaded into {cached} but is unusable: "
                    f"{'; '.join(problems)}"
                )
        resolved.append(ResolvedPack(name, version, origin, cached))
        remove_other_versions(cache_dir, name, version)
    return resolved


def additional_packs_value(cache_dir: Path, local_dirs: Iterable[Path]) -> str:
    """Value of ``--additional-packs``: the pack cache plus local pack directories."""
    return os.pathsep.join([str(cache_dir), *(str(d) for d in local_dirs)])


def pins_json(pins: Pins, cli: CliInfo, resolved: Iterable[ResolvedPack]) -> dict:
    """Content of ``pins.json`` (pinned versions plus what was actually resolved)."""
    data = pins.as_json()
    data["codeql_cli_actual"] = cli.version
    data["resolved"] = [
        {
            "pack": f"{p.name}@{p.version}" if p.version else p.name,
            "origin": p.origin,
            "path": str(p.path),
        }
        for p in resolved
    ]
    return data


def write_pins_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
