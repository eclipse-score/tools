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

from importlib.resources import files as _pkg_files

from score_tools import __version__

RuffConfigPath = Path | None


def _load_bundled_ruff_defaults() -> str:
    """Load the bundled Eclipse S-CORE Ruff defaults from the package."""
    try:
        return _pkg_files("score_tools.ruff").joinpath("defaults.toml").read_text(encoding="utf-8")
    except Exception as exc:
        sys.stderr.write(f"Failed to read bundled defaults: {exc}\n")
        raise


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


def _loads_toml(text: str) -> dict:
    # Lazy-import toml readers to avoid hard dependency unless ruff extra is installed.
    try:  # Python 3.11+
        import tomllib as _tomllib  # type: ignore[attr-defined]
    except Exception:
        try:
            import tomli as _tomllib  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError(
                "TOML reader not available. Install 'score-tools[ruff]' to enable merged config support."
            ) from exc
    return _tomllib.loads(text)


def _load_toml_file(path: Path) -> dict:
    try:  # Python 3.11+
        import tomllib as _tomllib  # type: ignore[attr-defined]
    except Exception:
        try:
            import tomli as _tomllib  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError(
                "TOML reader not available. Install 'score-tools[ruff]' to enable merged config support."
            ) from exc
    with path.open("rb") as f:
        return _tomllib.load(f)


def _extract_ruff_table(data: dict) -> dict:
    """Return a ruff-level config mapping regardless of file style.

    - If given a pyproject-style mapping with [tool.ruff], return that table.
    - Otherwise, assume it's already ruff.toml style (top-level settings).
    """
    tool = data.get("tool") if isinstance(data, dict) else None
    if isinstance(tool, dict) and isinstance(tool.get("ruff"), dict):
        return tool["ruff"]  # type: ignore[return-value]
    return data


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Deep-merge two dicts: values in overrides replace those in base.

    Lists and scalars are replaced entirely; nested dicts are merged recursively.
    """
    out: dict = dict(base)
    for k, v in overrides.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _write_merged_config(defaults_text: str, user_cfg_path: Path) -> Path:
    """Merge bundled defaults with user config and write a temp ruff.toml.

    The merged file is written in ruff.toml style (not pyproject), with user values
    taking precedence over bundled defaults.
    """
    defaults = _extract_ruff_table(_loads_toml(defaults_text))
    user_all = _load_toml_file(user_cfg_path)
    user = _extract_ruff_table(user_all)
    merged = _deep_merge(defaults, user)

    # Lazy import writer; if unavailable, signal to caller.
    try:
        import tomli_w  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "TOML writer not available. Install 'score-tools[ruff]' to enable merged config support."
        ) from exc

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".toml", prefix="score-ruff-merged-", delete=False) as tmp:
        tomli_w.dump(merged, tmp)
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
        # print bundled defaults
        try:
            defaults_text = _load_bundled_ruff_defaults()
            print(defaults_text.strip())
            return 0
        except Exception:
            return 1

    cwd = Path.cwd()
    user_cfg_path: RuffConfigPath = Path(parsed.config) if parsed.config else _find_user_ruff_config(cwd)

    if parsed.print_config:
        if user_cfg_path is None:
            print("Using Eclipse S-CORE default Ruff configuration (no project config found).")
        else:
            print(
                "Using merged configuration: S-CORE defaults overlaid with project configuration at: "
                f"{user_cfg_path}"
            )
        return 0

    if user_cfg_path is None:
        try:
            defaults_text = _load_bundled_ruff_defaults()
            cfg_path = _write_temp_config(defaults_text)
            return _run_ruff(["--config", str(cfg_path), *rest])
        except Exception:
            return 1

    # Merge defaults with user config (user overrides defaults). If TOML libs are
    # unavailable (base install without [ruff]), fail with a clear instruction so
    # behavior is deterministic and easy to diagnose.
    try:
        defaults_text = _load_bundled_ruff_defaults()
        merged_cfg = _write_merged_config(defaults_text, user_cfg_path)
        return _run_ruff(["--config", str(merged_cfg), *rest])
    except Exception as exc:
        sys.stderr.write(
            "Failed to merge S-CORE defaults with project config.\n"
            f"Reason: {exc}\n"
            "Install the ruff extra to enable merging: `pipx install 'score-tools[ruff]'`\n"
            "or `uv pip install 'score-tools[ruff]'`."
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
