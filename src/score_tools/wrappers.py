# score_tools/_wrappers.py
from __future__ import annotations

import runpy
import sys


def ruff() -> None:
    # Executes ruff as if `python -m ruff`
    sys.argv[0] = "ruff"
    _ = runpy.run_module("ruff", run_name="__main__")


def pytest() -> None:
    sys.argv[0] = "pytest"
    _ = runpy.run_module("pytest", run_name="__main__")


def basedpyright() -> None:
    sys.argv[0] = "basedpyright"
    _ = runpy.run_module("basedpyright", run_name="__main__")
