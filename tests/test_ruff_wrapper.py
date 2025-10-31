"""Tests for score-ruff wrapper."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from score_tools.ruff import cli as score_ruff


def run_and_capture(argv: list[str]) -> tuple[int, str]:
    """Run score-ruff with argv and capture stdout."""
    buf = StringIO()
    with redirect_stdout(buf):
        code = score_ruff.main(argv)
    return code, buf.getvalue()


def test_score_config_prints_defaults() -> None:
    """Test that --score-config prints bundled defaults."""
    code, out = run_and_capture(["--score-config"])
    assert code == 0
    assert "[lint]" in out or "[format]" in out
    assert "Eclipse S-CORE" in out


def test_print_config_without_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that --print-config reports using defaults when no user config is found."""
    # Run in a temp directory with no config present
    monkeypatch.chdir(tmp_path)
    code, out = run_and_capture(["--print-config"])
    assert code == 0
    assert "Eclipse S-CORE default Ruff configuration" in out


def test_print_config_with_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that --print-config detects pyproject.toml with [tool.ruff]."""
    # Create a minimal pyproject with [tool.ruff]
    pyproj = tmp_path / "pyproject.toml"
    pyproj.write_text(
        """
[tool.ruff]
line-length = 88
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    code, out = run_and_capture(["--print-config"])
    assert code == 0
    # Should report using the project configuration
    assert str(pyproj) in out


def test_print_config_with_ruff_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that --print-config detects ruff.toml."""
    ruff_toml = tmp_path / "ruff.toml"
    ruff_toml.write_text("line-length = 100\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code, out = run_and_capture(["--print-config"])
    assert code == 0
    assert str(ruff_toml) in out


def test_print_config_with_dot_ruff_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that --print-config detects .ruff.toml."""
    ruff_toml = tmp_path / ".ruff.toml"
    ruff_toml.write_text("line-length = 100\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code, out = run_and_capture(["--print-config"])
    assert code == 0
    assert str(ruff_toml) in out


def test_find_config_in_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that config search walks up parent directories."""
    # Create config in parent
    pyproj = tmp_path / "pyproject.toml"
    pyproj.write_text("[tool.ruff]\nline-length = 88\n", encoding="utf-8")

    # Run from subdirectory
    subdir = tmp_path / "src" / "mypackage"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    code, out = run_and_capture(["--print-config"])
    assert code == 0
    assert str(pyproj) in out


def test_explicit_config_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that --config flag overrides config discovery."""
    # Create two configs
    default_cfg = tmp_path / "ruff.toml"
    default_cfg.write_text("line-length = 88\n", encoding="utf-8")

    custom_cfg = tmp_path / "custom-ruff.toml"
    custom_cfg.write_text("line-length = 100\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    code, out = run_and_capture(["--print-config", "--config", str(custom_cfg)])
    assert code == 0
    assert str(custom_cfg) in out
    assert str(default_cfg) not in out
