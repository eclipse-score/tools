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

"""Repository-level consistency: pins agree everywhere, no forbidden branding.

Checks against files owned by the Bazel side are skipped when the file is absent.
"""

import re
from pathlib import Path

import pytest
from score_codeql import config as cfg

CODEQL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODEQL_DIR.parent


def _need(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    return path


def _bzl_constants() -> dict[str, str]:
    text = _need(CODEQL_DIR / "versions.bzl").read_text(encoding="utf-8")
    return dict(re.findall(r'^([A-Z_0-9]+)\s*=\s*"([^"]*)"', text, re.MULTILINE))


@pytest.fixture(scope="module")
def pins() -> cfg.Pins:
    return cfg.load_pins(_need(CODEQL_DIR / "config" / "packs.toml"))


def test_versions_bzl_matches_packs_toml(pins: cfg.Pins) -> None:
    bzl = _bzl_constants()
    assert bzl["CODEQL_CLI_VERSION"] == pins.codeql_version
    assert bzl["CODEQL_CPP_QUERIES_VERSION"] == pins.packs["codeql/cpp-queries"]
    assert bzl["CODEQL_CPP_ALL_VERSION"] == pins.packs["codeql/cpp-all"]
    for pack, version in pins.packs.items():
        if pack.endswith("-coding-standards"):
            assert version == bzl["CODING_STANDARDS_VERSION"], pack
    assert (
        bzl["CODE_COMPLEXITY_PACK_VERSION"]
        == pins.local["score/code-complexity-queries"]
    )
    assert bzl["DIAGNOSTICS_PACK_VERSION"] == pins.local["score/codeql-sca-diagnostics"]


def test_bundle_url_contains_the_pinned_cli_version(pins: cfg.Pins) -> None:
    assert (
        f"codeql-bundle-v{pins.codeql_version}" in _bzl_constants()["CODEQL_BUNDLE_URL"]
    )


def test_module_bazel_carries_the_same_artifacts() -> None:
    module = _need(REPO_ROOT / "MODULE.bazel").read_text(encoding="utf-8")
    bzl = _bzl_constants()
    for key in (
        "CODEQL_BUNDLE_URL",
        "CODEQL_BUNDLE_SHA256",
        "BCC_URL",
        "BCC_SHA256",
        "BCC_JAMMY_URL",
        "BCC_JAMMY_SHA256",
    ):
        assert bzl[key] in module, key


def test_reports_toml_refs_are_pinned_consistently(pins: cfg.Pins) -> None:
    reports = cfg.load_reports(_need(CODEQL_DIR / "config" / "reports.toml"))
    by_report = cfg.refs_of(reports, list(reports.reports))
    cfg.validate_refs(by_report, pins)
    versions = {
        ref.name: ref.version
        for refs in by_report.values()
        for r in [refs]
        for ref in r
    }
    for name, version in versions.items():
        if name in pins.packs:
            assert version == pins.packs[name]
    assert set(reports.presets) >= {"QM", "ASIL"}


def test_readme_pin_table_agrees(pins: cfg.Pins) -> None:
    readme = _need(CODEQL_DIR / "README.md").read_text(encoding="utf-8")
    rows = [line for line in readme.splitlines() if line.startswith("|")]
    assert any("CodeQL bundle" in r and pins.codeql_version in r for r in rows)
    for pack, version in pins.packs.items():
        matching = [r for r in rows if f"`{pack}`" in r]
        assert matching, f"{pack} missing from the README pin table"
        assert all(version in r for r in matching), pack


def test_local_qlpacks_match_pins(pins: cfg.Pins) -> None:
    for directory in ("code-complexity", "diagnostics"):
        qlpack = _need(CODEQL_DIR / "queries" / directory / "qlpack.yml")
        text = qlpack.read_text(encoding="utf-8")
        name = re.search(r"^name:\s*(\S+)", text, re.MULTILINE)
        version = re.search(r"^version:\s*(\S+)", text, re.MULTILINE)
        assert name and version
        assert pins.local[name.group(1)] == version.group(1)
        assert f"codeql/cpp-all: {pins.packs['codeql/cpp-all']}" in text


def test_complexity_and_diagnostics_packs_depend_only_on_bundled_libraries() -> None:
    for directory in ("code-complexity", "diagnostics"):
        text = _need(CODEQL_DIR / "queries" / directory / "qlpack.yml").read_text(
            encoding="utf-8"
        )
        assert "codeql/cpp-queries" not in text


COMPLEXITY_PACK = CODEQL_DIR / "queries" / "code-complexity"

# Documented thresholds of the two safety profiles (`README.md`,
# `queries/code-complexity/lib/Thresholds.qll`).
EXPECTED_THRESHOLDS = {
    "Qm": {
        "linesOfCodePerFunction": 200,
        "linesOfCodePerFile": 4000,
        "cyclomaticComplexityPerFunction": 20,
        "parametersPerFunction": 7,
        "publicInterfacesPerComponent": 10,
    },
    "Asil": {
        "linesOfCodePerFunction": 100,
        "linesOfCodePerFile": 2000,
        "cyclomaticComplexityPerFunction": 10,
        "parametersPerFunction": 5,
        "publicInterfacesPerComponent": 3,
    },
}

# `@problem.severity` of the complexity queries. The public-interface metric is
# advisory (CodeQL maps `recommendation` to the SARIF level `note`), everything
# else is a warning.
DEFAULT_COMPLEXITY_SEVERITY = "warning"
EXPECTED_SEVERITIES = {"PublicInterfacesPerComponent": "recommendation"}


@pytest.fixture(scope="module")
def reports() -> cfg.ReportsConfig:
    return cfg.load_reports(_need(CODEQL_DIR / "config" / "reports.toml"))


def _suite_dirs(suite: Path) -> list[str]:
    return re.findall(
        r"^\s*-\s*queries:\s*(\S+)\s*$", suite.read_text(encoding="utf-8"), re.MULTILINE
    )


def _complexity_suites(reports: cfg.ReportsConfig) -> dict[str, Path]:
    """Suite file per complexity report."""
    suites = {}
    for name, refs in reports.reports.items():
        if "complexity" not in name:
            continue
        assert len(refs) == 1, name
        path = cfg.parse_pack_ref(refs[0]).path
        assert path, name
        suites[name] = _need(COMPLEXITY_PACK / path)
    return suites


def test_presets_select_the_profile_specific_reports(
    reports: cfg.ReportsConfig,
) -> None:
    assert reports.presets == {
        "QM": ["codeql-report", "codeql-report-complexity-qm"],
        "ASIL": ["codeql-report-safety", "codeql-report-complexity-asil"],
    }


def test_each_preset_runs_alert_suppression(reports: cfg.ReportsConfig) -> None:
    for preset, names in reports.presets.items():
        refs = [ref for name in names for ref in reports.reports[name]]
        assert any(ref.endswith(":AlertSuppression.ql") for ref in refs), preset


def test_complexity_suites_cover_both_profiles(reports: cfg.ReportsConfig) -> None:
    suites = _complexity_suites(reports)
    assert set(suites) == {
        "codeql-report-complexity-qm",
        "codeql-report-complexity-asil",
    }
    for name, suite in suites.items():
        profile = name.rsplit("-", 1)[-1]
        directories = _suite_dirs(suite)
        assert f"src/{profile}" in directories, name
        # Profile-independent checks (goto statements, recursion).
        assert "src/common" in directories, name
        for directory in directories:
            path = COMPLEXITY_PACK / directory
            assert path.is_dir(), path
            assert list(path.glob("*.ql")), path


def test_profile_modules_define_the_documented_thresholds() -> None:
    text = _need(COMPLEXITY_PACK / "lib" / "Thresholds.qll").read_text(encoding="utf-8")
    modules = {
        name: {
            key: int(value)
            for key, value in re.findall(
                r"int (\w+)\(\)\s*\{\s*result\s*=\s*(\d+)\s*\}", body
            )
        }
        for name, body in re.findall(r"module (\w+) \{(.*?)\n\}", text, re.DOTALL)
    }
    assert modules == EXPECTED_THRESHOLDS


def test_profile_queries_read_the_matching_threshold_module() -> None:
    for profile, module in (("qm", "Qm"), ("asil", "Asil")):
        queries = sorted((COMPLEXITY_PACK / "src" / profile).glob("*.ql"))
        assert queries
        for query in queries:
            assert f"{module}::" in query.read_text(encoding="utf-8"), query


def test_complexity_query_ids_are_unique() -> None:
    ids: dict[str, Path] = {}
    for query in sorted(COMPLEXITY_PACK.rglob("*.ql")):
        match = re.search(r"@id\s+(\S+)", query.read_text(encoding="utf-8"))
        assert match, query
        assert match.group(1) not in ids, f"{query} reuses {match.group(1)}"
        ids[match.group(1)] = query


def test_complexity_query_ids_are_namespaced_by_profile() -> None:
    def query_id(query: Path) -> str:
        match = re.search(r"@id\s+(\S+)", query.read_text(encoding="utf-8"))
        assert match, query
        return match.group(1)

    for profile in ("qm", "asil"):
        for query in sorted((COMPLEXITY_PACK / "src" / profile).glob("*.ql")):
            assert query_id(query).startswith(f"score/cpp/{profile}/"), query
    for query in sorted((COMPLEXITY_PACK / "src" / "common").glob("*.ql")):
        assert re.fullmatch(r"score/cpp/[^/]+", query_id(query)), query


def test_complexity_query_severities() -> None:
    """Only the advisory public-interface check is a `recommendation`."""

    def severity(query: Path) -> str:
        match = re.search(
            r"@problem\.severity\s+(\S+)", query.read_text(encoding="utf-8")
        )
        assert match, query
        return match.group(1)

    for query in sorted(COMPLEXITY_PACK.rglob("*.ql")):
        expected = EXPECTED_SEVERITIES.get(query.stem, DEFAULT_COMPLEXITY_SEVERITY)
        assert severity(query) == expected, f"{query}: {severity(query)} != {expected}"


def test_readme_documents_both_complexity_reports() -> None:
    readme = _need(CODEQL_DIR / "README.md").read_text(encoding="utf-8")
    for name in ("codeql-report-complexity-qm", "codeql-report-complexity-asil"):
        assert f"`{name}`" in readme, name


# The forbidden words are assembled so that this file does not contain them itself.
_FORBIDDEN = re.compile("(?i)" + "e" + "tas|" + "v" + "sps")
_SKIPPED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def test_no_forbidden_branding_in_any_file() -> None:
    offenders = []
    for path in CODEQL_DIR.rglob("*"):
        if not path.is_file() or _SKIPPED_PARTS & set(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _FORBIDDEN.search(text) or _FORBIDDEN.search(path.name):
            offenders.append(str(path.relative_to(CODEQL_DIR)))
    assert offenders == []


def test_every_python_file_has_the_eclipse_header() -> None:
    missing = []
    for path in CODEQL_DIR.rglob("*.py"):
        if _SKIPPED_PARTS & set(path.parts):
            continue
        head = path.read_text(encoding="utf-8")[:700]
        if (
            "Contributors to the Eclipse Foundation" not in head
            or "Apache-2.0" not in head
        ):
            missing.append(str(path.relative_to(CODEQL_DIR)))
    assert missing == []
