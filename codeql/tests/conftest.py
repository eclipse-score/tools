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

"""Shared fixtures: tiny config files and a fake ``codeql`` executable."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

REPORTS_TOML = """\
[reports.base]
packs = [
  "codeql/cpp-queries@1.10.0:codeql-suites/cpp-code-scanning.qls",
  "codeql/cert-cpp-coding-standards@2.62.0:codeql-suites/cert-cpp-l1.qls",
]

[reports.safety]
packs = [
  "codeql/cpp-queries@1.10.0:codeql-suites/cpp-code-scanning.qls",
  "codeql/misra-cpp-coding-standards@2.62.0:codeql-suites/misra-cpp-required.qls",
]

[reports.complexity]
packs = ["score/code-complexity-queries@1.0.0:suites/thresholds.qls"]

[presets]
QM = ["base", "complexity"]
ASIL = ["safety", "complexity"]
"""

PACKS_TOML = """\
[codeql_cli]
version = "2.27.4"

[packs]
"codeql/cpp-queries" = "1.10.0"
"codeql/cert-cpp-coding-standards" = "2.62.0"
"codeql/misra-cpp-coding-standards" = "2.62.0"

[local]
"score/code-complexity-queries" = "1.0.0"
"score/codeql-sca-diagnostics" = "1.0.0"
"""

# Files every fake "downloaded" pack contains (the suites the tests reference).
FAKE_PACK_FILES = [
    "codeql-suites/cert-cpp-l1.qls",
    "codeql-suites/cert-c-l1.qls",
    "codeql-suites/misra-cpp-required.qls",
    "codeql-suites/cpp-code-scanning.qls",
    "a.qls",
    "b.qls",
    "x.qls",
    "x",
]

FAKE_CODEQL = """\
#!{python}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
log = os.environ.get("FAKE_CODEQL_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\\n")
fail = os.environ.get("FAKE_CODEQL_FAIL", "")
if fail and fail in " ".join(args):
    print("boom", file=sys.stderr)
    sys.exit(3)
if args[:1] == ["version"]:
    print(json.dumps({{
        "version": os.environ.get("FAKE_CODEQL_VERSION", "2.27.4"),
        "unpackedLocation": os.environ["FAKE_CODEQL_DIST"],
    }}))
elif args[:2] == ["pack", "download"]:
    target = next(a.split("=", 1)[1] for a in args if a.startswith("--dir="))
    name, version = args[-1].split("@")
    pack = Path(target) / name / version
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "qlpack.yml").write_text(f"name: {{name}}\\nversion: {{version}}\\n")
    for rel in json.loads(os.environ.get("FAKE_CODEQL_FILES", "[]")):
        (pack / rel).parent.mkdir(parents=True, exist_ok=True)
        (pack / rel).write_text("")
elif args[:2] == ["resolve", "queries"]:
    print(os.environ.get("FAKE_CODEQL_QUERIES", "[]"))
elif args[:2] == ["bqrs", "decode"]:
    print(os.environ.get("FAKE_CODEQL_BQRS", "{{}}"))
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from the real ``~/.cache`` (and the real pack cache)."""
    home = tmp_path / "home"
    (home / ".cache").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    from score_codeql import packs

    cache = packs.default_pack_cache()
    assert tmp_path in cache.parents, (
        f"default pack cache escapes the test dir: {cache}"
    )
    return home


@pytest.fixture
def fake_codeql(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Executable fake CLI; calls are logged to ``tmp_path/calls.jsonl``."""
    dist = tmp_path / "dist"
    (dist / "qlpacks").mkdir(parents=True)
    script = tmp_path / "codeql"
    script.write_text(FAKE_CODEQL.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CODEQL_DIST", str(dist))
    monkeypatch.setenv("FAKE_CODEQL_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.delenv("FAKE_CODEQL_FAIL", raising=False)
    monkeypatch.delenv("FAKE_CODEQL_VERSION", raising=False)
    monkeypatch.setenv("FAKE_CODEQL_FILES", json.dumps(FAKE_PACK_FILES))
    return script


@pytest.fixture
def config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Config files in ``tmp_path`` exported through the ``SCORE_CODEQL_*`` variables."""
    cfg = tmp_path / "cfg"
    for sub in ("code-complexity", "diagnostics"):
        (cfg / sub).mkdir(parents=True)
    paths = {
        "SCORE_CODEQL_REPORTS": cfg / "reports.toml",
        "SCORE_CODEQL_PACKS": cfg / "packs.toml",
        "SCORE_CODEQL_COMPLEXITY_QLPACK": cfg / "code-complexity" / "qlpack.yml",
        "SCORE_CODEQL_DIAGNOSTICS_QLPACK": cfg / "diagnostics" / "qlpack.yml",
    }
    suites = cfg / "code-complexity" / "suites"
    suites.mkdir()
    (suites / "thresholds.qls").write_text("", encoding="utf-8")
    paths["SCORE_CODEQL_REPORTS"].write_text(REPORTS_TOML, encoding="utf-8")
    paths["SCORE_CODEQL_PACKS"].write_text(PACKS_TOML, encoding="utf-8")
    paths["SCORE_CODEQL_COMPLEXITY_QLPACK"].write_text(
        "name: score/code-complexity-queries\nversion: 1.0.0\n", encoding="utf-8"
    )
    paths["SCORE_CODEQL_DIAGNOSTICS_QLPACK"].write_text(
        "name: score/codeql-sca-diagnostics\nversion: 1.0.0\n", encoding="utf-8"
    )
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))
    return paths
