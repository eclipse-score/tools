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

from pathlib import Path

import pytest
from score_codeql import config as cfg
from score_codeql.errors import ConfigError


def _load(config_env: dict[str, Path], *extra: Path) -> cfg.ReportsConfig:
    return cfg.load_reports(config_env["SCORE_CODEQL_REPORTS"], extra)


def test_parse_pack_ref_splits_name_version_path() -> None:
    ref = cfg.parse_pack_ref("codeql/cpp-queries@1.10.0:codeql-suites/x.qls")
    assert (ref.name, ref.version, ref.path) == (
        "codeql/cpp-queries",
        "1.10.0",
        "codeql-suites/x.qls",
    )
    assert str(ref) == "codeql/cpp-queries@1.10.0:codeql-suites/x.qls"
    assert ref.pack == "codeql/cpp-queries@1.10.0"


def test_parse_pack_ref_local_and_unversioned() -> None:
    local = cfg.parse_pack_ref(
        "score/code-complexity-queries@1.0.0:suites/thresholds.qls"
    )
    assert local.name == "score/code-complexity-queries"
    bare = cfg.parse_pack_ref("codeql/cpp-queries:AlertSuppression.ql")
    assert bare.version is None and bare.path == "AlertSuppression.ql"
    assert cfg.parse_pack_ref("codeql/cpp-all@12.4.0").path is None


@pytest.mark.parametrize("bad", ["cpp-queries@1.0.0", "", "a/b c@1", "a/b@:x"])
def test_parse_pack_ref_rejects_garbage(bad: str) -> None:
    with pytest.raises(ConfigError):
        cfg.parse_pack_ref(bad)


def test_default_reports_and_presets_load(config_env: dict[str, Path]) -> None:
    config = _load(config_env)
    assert list(config.reports) == ["base", "safety", "complexity"]
    assert config.presets["QM"] == ["base", "complexity"]


def test_select_preset_then_explicit_reports_in_order(
    config_env: dict[str, Path],
) -> None:
    config = _load(config_env)
    assert cfg.select_reports(config, "QM", ["safety", "base"]) == [
        "base",
        "complexity",
        "safety",
    ]


def test_select_defaults_to_all_reports(config_env: dict[str, Path]) -> None:
    config = _load(config_env)
    assert cfg.select_reports(config) == ["base", "safety", "complexity"]


def test_select_unknown_report_and_preset(config_env: dict[str, Path]) -> None:
    config = _load(config_env)
    with pytest.raises(ConfigError, match="unknown report 'nope'"):
        cfg.select_reports(config, None, ["nope"])
    with pytest.raises(ConfigError, match="unknown safety level/preset 'SIL4'"):
        cfg.select_reports(config, "SIL4")


def test_consumer_file_adds_replaces_and_overrides_presets(
    config_env: dict[str, Path], tmp_path: Path
) -> None:
    consumer = tmp_path / "consumer.toml"
    consumer.write_text(
        """
[reports.extra]
packs = ["codeql/cpp-queries@1.10.0:AlertSuppression.ql"]

[reports.complexity]
packs = ["score/code-complexity-queries@1.0.0:suites/other.qls"]

[presets]
QM = ["base", "extra"]
""",
        encoding="utf-8",
    )
    config = _load(config_env, consumer)
    assert "extra" in config.reports
    assert config.reports["complexity"] == [
        "score/code-complexity-queries@1.0.0:suites/other.qls"
    ]
    assert config.presets["QM"] == ["base", "extra"]
    assert config.presets["ASIL"] == ["safety", "complexity"]


def test_later_consumer_file_wins(config_env: dict[str, Path], tmp_path: Path) -> None:
    first, second = tmp_path / "a.toml", tmp_path / "b.toml"
    first.write_text('[reports.x]\npacks = ["a/b@1.0.0"]\n', encoding="utf-8")
    second.write_text('[reports.x]\npacks = ["c/d@1.0.0"]\n', encoding="utf-8")
    assert _load(config_env, first, second).reports["x"] == ["c/d@1.0.0"]


@pytest.mark.parametrize(
    "text, message",
    [
        ("[bogus]\nx = 1\n", "unknown top-level key"),
        ('[reports.a]\npacks = ["x/y@1.0.0"]\ncolour = "red"\n', "unknown key"),
        ("[reports.a]\n", "missing 'packs'"),
        ("[reports.a]\npacks = []\n", "empty 'packs'"),
        ('[reports.a]\npacks = "x/y@1.0.0"\n', "list of strings"),
        ('[reports.a]\npacks = ["not a ref"]\n', "invalid pack reference"),
        (
            '[reports.a]\npacks = ["x/y@1.0.0"]\n[presets]\nQM = ["ghost"]\n',
            "unknown report",
        ),
    ],
)
def test_invalid_reports_file_is_rejected(
    config_env: dict[str, Path], tmp_path: Path, text: str, message: str
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        _load(config_env, bad)


def test_invalid_toml_is_a_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("= not toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        cfg.load_reports_file(bad)


def test_union_refs_deduplicates_in_order(config_env: dict[str, Path]) -> None:
    config = _load(config_env)
    refs = cfg.union_refs(cfg.refs_of(config, ["base", "safety"]))
    assert [str(r) for r in refs] == [
        "codeql/cpp-queries@1.10.0:codeql-suites/cpp-code-scanning.qls",
        "codeql/cert-cpp-coding-standards@2.62.0:codeql-suites/cert-cpp-l1.qls",
        "codeql/misra-cpp-coding-standards@2.62.0:codeql-suites/misra-cpp-required.qls",
    ]


def test_load_pins(config_env: dict[str, Path]) -> None:
    pins = cfg.load_pins(config_env["SCORE_CODEQL_PACKS"])
    assert pins.codeql_version == "2.27.4"
    assert pins.packs["codeql/cpp-queries"] == "1.10.0"
    assert pins.as_json()["local_packs"]["score/code-complexity-queries"] == "1.0.0"


@pytest.mark.parametrize(
    "text, message",
    [
        ('[packs]\n"a/b" = "1.0.0"\n', r"\[codeql_cli\]"),
        ('[codeql_cli]\nversion = "latest"\n', "not an exact version"),
        (
            '[codeql_cli]\nversion = "2.27.4"\n[packs]\n"a/b" = "^1.0"\n',
            "not an exact version",
        ),
        ('[codeql_cli]\nversion = "2.27.4"\n[extra]\n', "unknown top-level key"),
        (
            '[codeql_cli]\nversion = "2.27.4"\n[packs]\n"a/b" = "1.0.0"\n[local]\n"a/b" = "1.0.0"\n',
            "both",
        ),
    ],
)
def test_invalid_pins_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "packs.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        cfg.load_pins(path)


def _validate(
    config_env: dict[str, Path], reports: dict[str, list[str]], extra=()
) -> None:
    pins = cfg.load_pins(config_env["SCORE_CODEQL_PACKS"])
    config = cfg.ReportsConfig(reports=reports)
    cfg.validate_refs(cfg.refs_of(config, list(reports)), pins, extra)


def test_validate_accepts_pinned_refs(config_env: dict[str, Path]) -> None:
    config = _load(config_env)
    pins = cfg.load_pins(config_env["SCORE_CODEQL_PACKS"])
    cfg.validate_refs(cfg.refs_of(config, list(config.reports)), pins)


def test_validate_rejects_unpinned_external_ref(config_env: dict[str, Path]) -> None:
    with pytest.raises(ConfigError, match="has no version"):
        _validate(config_env, {"r": ["codeql/cert-cpp-coding-standards:x.qls"]})


def test_validate_rejects_version_mismatch(config_env: dict[str, Path]) -> None:
    with pytest.raises(ConfigError, match="does not match the pinned version 2.62.0"):
        _validate(config_env, {"r": ["codeql/cert-cpp-coding-standards@2.61.0:x.qls"]})
    with pytest.raises(ConfigError, match="pinned local pack version 1.0.0"):
        _validate(config_env, {"r": ["score/code-complexity-queries@9.9.9:x.qls"]})


def test_validate_allows_unversioned_local_and_extra_local(
    config_env: dict[str, Path],
) -> None:
    _validate(
        config_env,
        {"r": ["score/code-complexity-queries:suites/thresholds.qls", "acme/own:q.ql"]},
        extra=["acme/own"],
    )


def test_validate_allows_unlisted_pack_with_explicit_version(
    config_env: dict[str, Path],
) -> None:
    _validate(config_env, {"r": ["acme/external@3.1.4:q.ql"]})
