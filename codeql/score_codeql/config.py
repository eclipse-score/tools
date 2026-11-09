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

"""Report/preset definitions (``reports.toml``) and pinned versions (``packs.toml``).

``reports.toml``::

    [reports.<name>]
    packs = ["codeql/cpp-queries@1.10.0:codeql-suites/cpp-code-scanning.qls", ...]

    [presets]
    QM = ["<report>", ...]

``packs.toml``::

    [codeql_cli]
    version = "2.27.4"

    [packs]
    "codeql/cpp-queries" = "1.10.0"

    [local]
    "score/code-complexity-queries" = "1.0.0"
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from score_codeql.errors import ConfigError

_PACK_REF_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"(?:@(?P<version>[^:@\s]+))?"
    r"(?::(?P<path>\S+))?$"
)
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")

_TOP_LEVEL_REPORT_KEYS = {"reports", "presets"}
_REPORT_KEYS = {"packs", "description"}
_TOP_LEVEL_PIN_KEYS = {"codeql_cli", "packs", "local"}


@dataclass(frozen=True)
class PackRef:
    """A query reference ``name[@version][:path]``."""

    name: str
    version: str | None
    path: str | None

    def __str__(self) -> str:
        text = self.name
        if self.version:
            text += f"@{self.version}"
        if self.path:
            text += f":{self.path}"
        return text

    @property
    def pack(self) -> str:
        """``name@version`` (or just ``name`` for an unversioned local pack)."""
        return f"{self.name}@{self.version}" if self.version else self.name


def parse_pack_ref(ref: str) -> PackRef:
    """Split ``name@version:path`` into its parts."""
    match = _PACK_REF_RE.match(ref.strip())
    if match is None:
        raise ConfigError(
            f"invalid pack reference {ref!r}; expected 'scope/name@version:path'"
        )
    return PackRef(match["name"], match["version"], match["path"])


@dataclass
class ReportsConfig:
    """Report name -> pack reference strings, and preset name -> report names."""

    reports: dict[str, list[str]] = field(default_factory=dict)
    presets: dict[str, list[str]] = field(default_factory=dict)


def _expect_str_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return list(value)


def parse_reports(data: Mapping[str, object], source: str) -> ReportsConfig:
    """Validate parsed TOML *data* and convert it to a ``ReportsConfig``."""
    unknown = set(data) - _TOP_LEVEL_REPORT_KEYS
    if unknown:
        raise ConfigError(
            f"{source}: unknown top-level key(s): {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(sorted(_TOP_LEVEL_REPORT_KEYS))})"
        )
    config = ReportsConfig()
    reports = data.get("reports", {})
    if not isinstance(reports, dict):
        raise ConfigError(f"{source}: [reports] must be a table")
    for name, body in reports.items():
        if not isinstance(body, dict):
            raise ConfigError(f"{source}: [reports.{name}] must be a table")
        bad = set(body) - _REPORT_KEYS
        if bad:
            raise ConfigError(
                f"{source}: [reports.{name}] has unknown key(s): "
                f"{', '.join(sorted(bad))}"
            )
        if "packs" not in body:
            raise ConfigError(f"{source}: [reports.{name}] is missing 'packs'")
        packs = _expect_str_list(body["packs"], f"{source}: reports.{name}.packs")
        if not packs:
            raise ConfigError(f"{source}: [reports.{name}] has an empty 'packs' list")
        for ref in packs:
            parse_pack_ref(ref)
        config.reports[name] = packs
    presets = data.get("presets", {})
    if not isinstance(presets, dict):
        raise ConfigError(f"{source}: [presets] must be a table")
    for name, members in presets.items():
        config.presets[name] = _expect_str_list(members, f"{source}: presets.{name}")
    return config


def load_reports_file(path: Path) -> ReportsConfig:
    """Load and validate one ``reports.toml``-style file."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read reports file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return parse_reports(data, str(path))


def merge_reports(base: ReportsConfig, *overrides: ReportsConfig) -> ReportsConfig:
    """Merge configs: new names are added, same names (reports/presets) replaced."""
    merged = ReportsConfig(dict(base.reports), dict(base.presets))
    for override in overrides:
        merged.reports.update(override.reports)
        merged.presets.update(override.presets)
    return merged


def validate_presets(config: ReportsConfig) -> None:
    """Every preset member must be a defined report."""
    if not config.reports:
        raise ConfigError("no reports are defined")
    for preset, members in config.presets.items():
        missing = [m for m in members if m not in config.reports]
        if missing:
            raise ConfigError(
                f"preset {preset!r} references unknown report(s): {', '.join(missing)}"
            )


def load_reports(default: Path, extra: Iterable[Path] = ()) -> ReportsConfig:
    """Load the default file and merge consumer files on top (in order)."""
    merged = merge_reports(
        load_reports_file(default), *(load_reports_file(p) for p in extra)
    )
    validate_presets(merged)
    return merged


def select_reports(
    config: ReportsConfig,
    preset: str | None = None,
    names: Sequence[str] | None = None,
) -> list[str]:
    """Report names to run: preset members first, then explicit ``--report`` names.

    Without a preset and without names every defined report runs.
    """
    selected: list[str] = []
    if preset:
        if preset not in config.presets:
            raise ConfigError(
                f"unknown safety level/preset {preset!r}; available presets: "
                f"{', '.join(sorted(config.presets)) or '<none>'}"
            )
        selected.extend(config.presets[preset])
    for name in names or ():
        if name not in config.reports:
            raise ConfigError(
                f"unknown report {name!r}; available reports: "
                f"{', '.join(config.reports)}"
            )
        if name not in selected:
            selected.append(name)
    return selected or list(config.reports)


def refs_of(config: ReportsConfig, names: Iterable[str]) -> dict[str, list[PackRef]]:
    """Parsed pack references per selected report."""
    return {n: [parse_pack_ref(r) for r in config.reports[n]] for n in names}


def union_refs(by_report: Mapping[str, Sequence[PackRef]]) -> list[PackRef]:
    """All references across reports, de-duplicated, order preserved."""
    seen: set[PackRef] = set()
    result: list[PackRef] = []
    for refs in by_report.values():
        for ref in refs:
            if ref not in seen:
                seen.add(ref)
                result.append(ref)
    return result


@dataclass(frozen=True)
class Pins:
    """Pinned CodeQL CLI and pack versions."""

    codeql_version: str
    packs: dict[str, str]
    local: dict[str, str]

    def as_json(self) -> dict[str, object]:
        return {
            "codeql_cli": self.codeql_version,
            "packs": dict(self.packs),
            "local_packs": dict(self.local),
        }


def _check_version(value: object, where: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.match(value):
        raise ConfigError(f"{where}: {value!r} is not an exact version (e.g. 1.2.3)")
    return value


def load_pins(path: Path) -> Pins:
    """Load and validate ``packs.toml``."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read pins file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    unknown = set(data) - _TOP_LEVEL_PIN_KEYS
    if unknown:
        raise ConfigError(
            f"{path}: unknown top-level key(s): {', '.join(sorted(unknown))}"
        )
    cli = data.get("codeql_cli")
    if not isinstance(cli, dict) or set(cli) != {"version"}:
        raise ConfigError(f"{path}: [codeql_cli] must contain exactly 'version'")
    version = _check_version(cli["version"], f"{path}: codeql_cli.version")
    tables: dict[str, dict[str, str]] = {}
    for table in ("packs", "local"):
        body = data.get(table, {})
        if not isinstance(body, dict):
            raise ConfigError(f"{path}: [{table}] must be a table")
        tables[table] = {
            name: _check_version(v, f"{path}: {table}.{name}")
            for name, v in body.items()
        }
    overlap = set(tables["packs"]) & set(tables["local"])
    if overlap:
        raise ConfigError(
            f"{path}: pack(s) in both [packs] and [local]: {', '.join(sorted(overlap))}"
        )
    return Pins(version, tables["packs"], tables["local"])


def validate_refs(
    by_report: Mapping[str, Sequence[PackRef]],
    pins: Pins,
    extra_local: Iterable[str] = (),
) -> None:
    """Enforce version pinning of every pack reference.

    * Local packs (``[local]`` or discovered in ``--additional-pack-dir``) may omit
      the version; if given it must match the pin.
    * Every other pack needs an explicit version, which must equal ``[packs]`` when
      the pack is listed there.
    """
    local_names = set(pins.local) | set(extra_local)
    for report, refs in by_report.items():
        for ref in refs:
            if ref.name in local_names:
                pinned = pins.local.get(ref.name)
                if ref.version and pinned and ref.version != pinned:
                    raise ConfigError(
                        f"report {report!r}: {ref} does not match the pinned local "
                        f"pack version {pinned}"
                    )
                continue
            if not ref.version:
                raise ConfigError(
                    f"report {report!r}: pack reference {ref} has no version; "
                    "external packs must be pinned as 'name@version:path'"
                )
            pinned = pins.packs.get(ref.name)
            if pinned and ref.version != pinned:
                raise ConfigError(
                    f"report {report!r}: {ref} does not match the pinned version "
                    f"{pinned} from packs.toml"
                )
