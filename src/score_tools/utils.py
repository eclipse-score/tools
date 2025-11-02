"""Common helper utilities for tool detection and version checks.

These functions intentionally avoid hard dependencies. Where richer parsing is
useful (PEP 440 specifiers), we try to import `packaging` and fall back to
conservative behavior if it's not available.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib import metadata


def is_cli_available(cmd: str) -> bool:
    """Return True if a command is found in PATH."""
    return shutil.which(cmd) is not None


def dist_version(dist_name: str) -> str | None:
    """Return installed distribution version, or None if not installed."""
    try:
        return metadata.version(dist_name)
    except metadata.PackageNotFoundError:
        return None


def version_satisfies(dist_name: str, specifier: str) -> tuple[bool, str | None, str | None]:
    """Check whether an installed dist satisfies a version specifier.

    Returns (ok, installed_version, reason_if_not_ok).

    - If the distribution is not installed, returns (False, None, "not installed").
    - If `packaging` is available, uses SpecifierSet for accurate evaluation.
    - Without `packaging`, supports only exact equality (==) checks; otherwise
      returns (True, version, None) to avoid false negatives.
    """
    ver = dist_version(dist_name)
    if ver is None:
        return False, None, "not installed"

    try:
        from packaging.specifiers import SpecifierSet  # type: ignore
        from packaging.version import Version  # type: ignore

        ok = Version(ver) in SpecifierSet(specifier)
        return (ok, ver, None if ok else f"{ver} does not satisfy '{specifier}'")
    except Exception:
        # Fallback: support only '==X' exact match; otherwise assume ok.
        spec = specifier.strip()
        if spec.startswith("=="):
            expected = spec[2:].strip()
            ok = ver == expected
            return (ok, ver, None if ok else f"{ver} != {expected}")
        return True, ver, None


@dataclass
class ToolStatus:
    name: str
    installed: bool  # fully ready to use (all required extras installed)
    details: dict[str, dict[str, object]]


def is_installed(tool: str, extra: str) -> ToolStatus:
    """Generic check that all requirements for an extra are satisfied.

    Returns (all_ok, details) where details maps requirement names to:
    {"required": str, "installed": str|None, "ok": bool, "reason": str|None}

    This avoids hard-coding dependency names by reading the installed
    distribution's "Requires-Dist" entries and selecting those tied to the
    given extra (respecting environment markers like python_version).
    """
    try:
        from packaging.markers import default_environment  # type: ignore
        from packaging.requirements import Requirement  # type: ignore
    except Exception:
        return ToolStatus(
            name=tool,
            installed=False,
            details={
                "packaging": {
                    "required": "packaging",
                    "installed": None,
                    "ok": False,
                    "reason": "packaging not available to parse extras; install 'packaging' to enable this check",
                }
            },
        )

    dist = metadata.distribution("score-tools")
    requires = dist.requires or []
    env: dict[str, str] = {k: str(v) for k, v in default_environment().items()}
    env["extra"] = str(extra)

    selected: list[Requirement] = []
    for req_str in requires:
        try:
            req = Requirement(req_str)
        except Exception:
            continue
        # Only consider requirements whose marker matches current env and extra
        if req.marker is None or req.marker.evaluate(env):
            selected.append(req)

    details: dict[str, dict[str, object]] = {}
    all_ok = True
    for req in selected:
        name = req.name
        spec = req.specifier
        inst = dist_version(name)
        if inst is None:
            details[name] = {"required": str(spec), "installed": None, "ok": False, "reason": "not installed"}
            all_ok = False
            continue
        ok = spec.contains(inst, prereleases=True) if spec else True
        details[name] = {
            "required": str(spec),
            "installed": inst,
            "ok": ok,
            "reason": None if ok else "version mismatch",
        }
        if not ok:
            all_ok = False

    return ToolStatus(
        name=tool,
        installed=all_ok,
        details=details,
    )
