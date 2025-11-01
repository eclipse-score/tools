from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_module(mod: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", mod, *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )


def test_score_tools_cli_pretty_blackbox() -> None:
    proc = run_module("score_tools.cli")
    assert proc.returncode == 0
    assert "score-tools" in proc.stdout
    assert ("Embedded tools:" in proc.stdout) or ("Detected tools:" in proc.stdout)
    assert "score-ruff" in proc.stdout


def test_score_tools_cli_json_blackbox() -> None:
    proc = run_module("score_tools.cli", "--json")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "score_tools_version" in data
    assert "tools" in data
    assert isinstance(data["tools"], dict)
    assert "score-ruff" in data["tools"]


def test_score_ruff_cli_version_blackbox() -> None:
    proc = run_module("score_tools.ruff.cli", "--version")
    # We always print our wrapper version; ruff may not be installed
    assert "score-ruff version" in proc.stdout
    assert proc.returncode in (0, 127)
    if proc.returncode == 127:
        assert "ruff is not installed" in proc.stderr


def test_score_ruff_cli_score_config_blackbox() -> None:
    proc = run_module("score_tools.ruff.cli", "--score-config")
    assert proc.returncode == 0
    assert "S-CORE Ruff defaults" in proc.stdout
    # Basic shape of a ruff config
    assert "[lint]" in proc.stdout or "line-length" in proc.stdout


def test_score_ruff_cli_print_config_default_blackbox(tmp_path: Path) -> None:
    proc = run_module("score_tools.ruff.cli", "--print-config", cwd=tmp_path)
    assert proc.returncode == 0
    assert "default Ruff configuration" in proc.stdout


def test_score_ruff_cli_print_config_project_blackbox(tmp_path: Path) -> None:
    pyproj = tmp_path / "pyproject.toml"
    pyproj.write_text("[tool.ruff]\nline-length = 88\n", encoding="utf-8")
    proc = run_module("score_tools.ruff.cli", "--print-config", cwd=tmp_path)
    assert proc.returncode == 0
    assert str(pyproj) in proc.stdout
