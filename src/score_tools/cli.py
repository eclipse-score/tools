"""
Eclipse S-CORE convenience CLI: "score-tools".

Features:
- Lists known tool wrappers and their availability (Python package & CLI in PATH).
- Detects available package managers (uv, pipx, pip) and suggests install commands.
- Supports JSON output for scripting.

Usage examples:
- score-tools
- score-tools --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Any

from . import __version__


def _is_cmd_available(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _pkg_version(dist_name: str) -> str | None:
    try:
        return metadata.version(dist_name)
    except metadata.PackageNotFoundError:
        return None


@dataclass
class ToolStatus:
    name: str
    python: bool
    cli: bool


def _detect_tool_score_ruff() -> ToolStatus:
    # This is our wrapper CLI; version matches score-tools package version
    return ToolStatus(
        name="score-ruff",
        python=True,  # If this CLI runs, score-tools is importable
        cli=_is_cmd_available("score-ruff"),
    )


def _available_package_managers() -> list[str]:
    managers: list[str] = []
    if _is_cmd_available("uv"):
        managers.append("uv")
    if _is_cmd_available("pipx"):
        managers.append("pipx")
    if _is_cmd_available("pip"):
        managers.append("pip")
    return managers


def _suggest_installs(managers: list[str]) -> dict[str, list[str]]:
    # Provide suggestions for installing extras and running CLIs without activation
    suggestions: dict[str, list[str]] = {}

    if "uv" in managers:
        suggestions["uv"] = [
            "uv pip install 'score-tools[all]'",
            "uv run score-ruff --version",
            "uvx score-ruff --version",
        ]
    if "pipx" in managers:
        suggestions["pipx"] = [
            "pipx install 'score-tools[all]'",
            "score-ruff --version",
        ]
    if "pip" in managers:
        suggestions["pip"] = [
            "python -m pip install 'score-tools[all]'",
            ".venv/bin/activate",
            "score-ruff --version",
        ]
    return suggestions


def build_status() -> dict[str, Any]:
    tools = [
        _detect_tool_score_ruff(),
    ]
    managers = _available_package_managers()

    return {
        "score_tools_version": __version__,
        "tools": {t.name: asdict(t) for t in tools},
        "package_managers": managers,
        "suggest": _suggest_installs(managers),
    }


def _pretty_print_status(status: dict[str, Any]) -> None:
    print(f"score-tools {status['score_tools_version']}")
    print("")
    print("Embedded tools:")
    for name, data in status["tools"].items():
        installed = "yes" if (data.get("python") or data.get("cli")) else "no"
        print(f"  - {name:11} installed: {installed:3}")

    print("")
    if status["package_managers"]:
        print("Detected package managers: " + ", ".join(status["package_managers"]))
    else:
        print("No package managers detected in PATH.")

    if status["suggest"]:
        print("")
        print("Install and usage suggestions:")
        for mgr, cmds in status["suggest"].items():
            print(f"  [{mgr}]")
            for cmd in cmds:
                print(f"    $ {cmd}")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    parser = argparse.ArgumentParser(
        prog="score-tools",
        description="Eclipse S-CORE convenience CLI: list tools and show install tips.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON for scripting.",
    )
    args = parser.parse_args(argv)

    status = build_status()
    if args.json:
        sys.stdout.write(json.dumps(status, indent=2) + "\n")
    else:
        _pretty_print_status(status)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
