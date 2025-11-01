"""Command-line interface for score-ruff (Ruff wrapper with Eclipse S-CORE defaults)."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

try:
    from importlib.resources import files as _pkg_files
except ImportError:  # Python < 3.9
    from importlib_resources import files as _pkg_files  # type: ignore[import-not-found,no-redef]

from score_tools import __version__

RuffConfigPath = Path | None


def _find_user_ruff_config(start: Path) -> RuffConfigPath:
    """Search upwards for a user Ruff config file."""
    toml_names = ("ruff.toml", ".ruff.toml")
    for parent in [start, *start.parents]:
        for name in toml_names:
            candidate = parent / name
            if candidate.is_file():
                return candidate
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8")
            except Exception:
                continue
            if "[tool.ruff]" in text or "[tool.ruff.lint]" in text:
                return pyproject
    return None


def _write_temp_config(toml_text: str) -> Path:
    """Write TOML text to a temporary file and return the path."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".toml",
        prefix="score-ruff-",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(toml_text)
        tmp.flush()
        return Path(tmp.name)


def _run_ruff(argv: Iterable[str]) -> int:
    args = ["ruff", *argv]
    try:
        # Ensure any prior prints are visible before replacing the process.
        with contextlib.suppress(Exception):
            sys.stdout.flush()
        os.execvp("ruff", args)  # type: ignore[attr-defined]
    except FileNotFoundError:
        sys.stderr.write(
            "ruff is not installed. You need to explicitly select it during installation, e.g. with `pipx install 'score-tools[ruff]'` or `uvx --from 'score-tools[ruff]' score-ruff`.\n"
        )
        return 127
    except Exception:
        # Fallback: try module runner
        try:
            import runpy

            sys.argv = args
            runpy.run_module("ruff", run_name="__main__")
            return 0
        except Exception as exc:  # pragma: no cover
            sys.stderr.write(f"Failed to run ruff: {exc}\n")
            return 1


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    p = argparse.ArgumentParser(
        description="score-ruff: A wrapper around Ruff with Eclipse S-CORE defaults",
        epilog="All other arguments are passed through to Ruff",
        add_help=False,
    )
    p.add_argument("-h", "--help", action="store_true", help="Show this help and Ruff's help, then exit")
    p.add_argument("-v", "--version", action="store_true", help="Show score-ruff and Ruff versions, then exit")
    p.add_argument("--config", type=str, help="Path to ruff.toml/.ruff.toml or pyproject.toml")
    p.add_argument(
        "--score-config", action="store_true", help="Print the bundled Eclipse S-CORE default config and exit"
    )
    p.add_argument("--print-config", action="store_true", help="Print the resolved config (path) and exit")

    parsed, rest = p.parse_known_args(argv)

    if parsed.help:
        print(p.format_help())
        print("\n--- Ruff help ---\n")
        return _run_ruff(["--help"])  # exits

    if parsed.version:
        print(f"score-ruff version {__version__}")
        return _run_ruff(["--version"])  # exits

    if parsed.score_config:
        try:
            defaults_text = _pkg_files("score_tools.ruff").joinpath("defaults.toml").read_text(encoding="utf-8")
        except Exception as exc:
            sys.stderr.write(f"Failed to read bundled defaults: {exc}\n")
            return 1
        print(defaults_text.strip())
        return 0

    cwd = Path.cwd()
    user_cfg_path: RuffConfigPath = Path(parsed.config) if parsed.config else _find_user_ruff_config(cwd)

    if parsed.print_config:
        if user_cfg_path is None:
            print("Using Eclipse S-CORE default Ruff configuration (no project config found).")
        else:
            print(f"Using project configuration at: {user_cfg_path}")
        return 0

    if user_cfg_path is None:
        try:
            defaults_text = _pkg_files("score_tools.ruff").joinpath("defaults.toml").read_text(encoding="utf-8")
        except Exception as exc:
            sys.stderr.write(f"Failed to read bundled defaults: {exc}\n")
            return 1
        cfg_path = _write_temp_config(defaults_text)
        return _run_ruff(["--config", str(cfg_path), *rest])

    return _run_ruff(["--config", str(user_cfg_path), *rest])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
