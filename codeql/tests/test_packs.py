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

import json
from pathlib import Path

import pytest
from score_codeql import config as cfg
from score_codeql import packs as pk
from score_codeql.errors import ConfigError, ScaError


def _calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _pack(root: Path, name: str, version: str, files: tuple[str, ...] = ()) -> Path:
    directory = root / name / version
    directory.mkdir(parents=True)
    (directory / "qlpack.yml").write_text(f"name: {name}\nversion: {version}\n")
    for rel in files:
        (directory / rel).parent.mkdir(parents=True, exist_ok=True)
        (directory / rel).write_text("")
    return directory


def _ref(text: str) -> cfg.PackRef:
    return cfg.parse_pack_ref(text)


def test_cli_info_and_version_pin(
    fake_codeql: Path, tmp_path: Path, config_env: dict
) -> None:
    info = pk.get_cli_info(fake_codeql)
    assert info.version == "2.27.4"
    assert info.distribution == tmp_path / "dist"
    pins = cfg.load_pins(config_env["SCORE_CODEQL_PACKS"])
    pk.check_cli_version(info, pins)
    with pytest.raises(
        ScaError, match="2.26.0 does not match the pinned version 2.27.4"
    ):
        pk.check_cli_version(pk.CliInfo("2.26.0", info.distribution), pins)


def test_cli_info_rejects_garbage(tmp_path: Path) -> None:
    script = tmp_path / "codeql"
    script.write_text("#!/bin/sh\necho not-json\n")
    script.chmod(0o755)
    with pytest.raises(ScaError, match="cannot parse"):
        pk.get_cli_info(script)


def test_bundled_pack_is_not_downloaded(fake_codeql: Path, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _pack(dist / "qlpacks", "codeql/cpp-queries", "1.10.0", ("AlertSuppression.ql",))
    resolved = pk.ensure_packs(
        fake_codeql, [_ref("codeql/cpp-queries@1.10.0:AlertSuppression.ql")],
        cache_dir=tmp_path / "cache", distribution=dist, local_packs={},
    )  # fmt: skip
    assert [(p.origin, p.version) for p in resolved] == [("bundle", "1.10.0")]
    assert not any(c[:2] == ["pack", "download"] for c in _calls(tmp_path))


def test_external_pack_is_downloaded_with_exact_version_once(
    fake_codeql: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    refs = [
        _ref("codeql/cert-cpp-coding-standards@2.62.0:a.qls"),
        _ref("codeql/cert-cpp-coding-standards@2.62.0:b.qls"),
    ]
    resolved = pk.ensure_packs(
        fake_codeql,
        refs,
        cache_dir=cache,
        distribution=tmp_path / "dist",
        local_packs={},
    )
    assert [p.origin for p in resolved] == ["downloaded"]
    downloads = [c for c in _calls(tmp_path) if c[:2] == ["pack", "download"]]
    assert downloads == [
        [
            "pack",
            "download",
            f"--dir={cache}",
            "codeql/cert-cpp-coding-standards@2.62.0",
        ]
    ]
    assert resolved[0].path == cache / "codeql/cert-cpp-coding-standards/2.62.0"


def test_cached_pack_is_reused_without_network(
    fake_codeql: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _pack(cache, "codeql/cert-c-coding-standards", "2.62.0", ("x.qls",))
    resolved = pk.ensure_packs(
        fake_codeql, [_ref("codeql/cert-c-coding-standards@2.62.0:x.qls")],
        cache_dir=cache, distribution=tmp_path / "dist", local_packs={},
    )  # fmt: skip
    assert resolved[0].origin == "cached"
    assert not any(c[:2] == ["pack", "download"] for c in _calls(tmp_path))


def test_cache_with_wrong_version_triggers_download(
    fake_codeql: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    stale = _pack(cache, "codeql/cert-c-coding-standards", "2.62.0")
    (stale / "qlpack.yml").write_text(
        "name: codeql/cert-c-coding-standards\nversion: 2.61.0\n"
    )
    resolved = pk.ensure_packs(
        fake_codeql, [_ref("codeql/cert-c-coding-standards@2.62.0:x.qls")],
        cache_dir=cache, distribution=tmp_path / "dist", local_packs={},
    )  # fmt: skip
    assert resolved[0].origin == "downloaded"


def test_failed_download_is_fatal_and_verified(
    fake_codeql: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEQL_FAIL", "pack download")
    with pytest.raises(ScaError, match="command failed"):
        pk.ensure_packs(
            fake_codeql, [_ref("a/b@1.0.0:x")], cache_dir=tmp_path / "c",
            distribution=tmp_path / "dist", local_packs={},
        )  # fmt: skip


def test_download_that_does_not_produce_the_pack_is_detected(tmp_path: Path) -> None:
    noop = tmp_path / "codeql"
    noop.write_text("#!/bin/sh\nexit 0\n")
    noop.chmod(0o755)
    with pytest.raises(ScaError, match="unusable"):
        pk.ensure_packs(
            noop, [_ref("a/b@1.0.0:x")], cache_dir=tmp_path / "c",
            distribution=tmp_path / "dist", local_packs={},
        )  # fmt: skip


def test_local_packs_are_found_by_name(fake_codeql: Path, tmp_path: Path) -> None:
    pack = tmp_path / "mine"
    pack.mkdir()
    (pack / "qlpack.yml").write_text(
        "name: acme/own\nversion: 0.2.0\ndependencies:\n  a/b: 1\n"
    )
    (pack / "q.ql").write_text("")
    (pack / "r.ql").write_text("")
    local = pk.find_local_packs([pack])
    assert local == {"acme/own": ("0.2.0", pack)}
    resolved = pk.ensure_packs(
        fake_codeql, [_ref("acme/own:q.ql"), _ref("acme/own@0.2.0:r.ql")],
        cache_dir=tmp_path / "c", distribution=tmp_path / "dist", local_packs=local,
    )  # fmt: skip
    assert [(p.origin, p.path) for p in resolved] == [("local", pack)] * 2
    with pytest.raises(ConfigError, match="0.3.0 is requested"):
        pk.ensure_packs(
            fake_codeql, [_ref("acme/own@0.3.0:q.ql")], cache_dir=tmp_path / "c",
            distribution=tmp_path / "dist", local_packs=local,
        )  # fmt: skip


def test_unversioned_external_ref_is_rejected(
    fake_codeql: Path, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="has no version"):
        pk.ensure_packs(
            fake_codeql, [_ref("a/b:x.ql")], cache_dir=tmp_path / "c",
            distribution=tmp_path / "dist", local_packs={},
        )  # fmt: skip


def test_read_qlpack_and_duplicates(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    for directory in (one, two):
        directory.mkdir()
        (directory / "qlpack.yml").write_text("name: 'acme/own'\nversion: \"1.0.0\"\n")
    assert pk.read_qlpack(one) == ("acme/own", "1.0.0")
    with pytest.raises(ConfigError, match="two directories"):
        pk.find_local_packs([one, two])
    (one / "qlpack.yml").write_text("version: 1.0.0\n")
    with pytest.raises(ConfigError, match="no 'name:'"):
        pk.read_qlpack(one)
    with pytest.raises(ConfigError, match="cannot read"):
        pk.read_qlpack(tmp_path / "none")


def test_additional_packs_value_and_default_cache(tmp_path: Path) -> None:
    assert pk.additional_packs_value(Path("/c"), [Path("/a"), Path("/b")]) == "/c:/a:/b"
    assert pk.default_pack_cache({"XDG_CACHE_HOME": "/x"}) == Path(
        "/x/score-codeql/packs"
    )
    assert pk.default_pack_cache({}).parts[-3:] == (".cache", "score-codeql", "packs")


def test_pins_json_records_resolution(tmp_path: Path, config_env: dict) -> None:
    pins = cfg.load_pins(config_env["SCORE_CODEQL_PACKS"])
    resolved = [
        pk.ResolvedPack("codeql/cpp-queries", "1.10.0", "bundle", Path("/b")),
        pk.ResolvedPack("acme/own", None, "local", Path("/l")),
    ]
    data = pk.pins_json(pins, pk.CliInfo("2.27.4", Path("/d")), resolved)
    assert data["codeql_cli"] == data["codeql_cli_actual"] == "2.27.4"
    assert data["resolved"][0] == {
        "pack": "codeql/cpp-queries@1.10.0",
        "origin": "bundle",
        "path": "/b",
    }
    assert data["resolved"][1]["pack"] == "acme/own"
    target = tmp_path / "out" / "pins.json"
    pk.write_pins_json(target, data)
    assert json.loads(target.read_text())["packs"]["codeql/cpp-queries"] == "1.10.0"


def _ensure(codeql, tmp_path, refs, **overrides):
    options = dict(
        cache_dir=tmp_path / "cache",
        distribution=tmp_path / "dist",
        local_packs={},
        listed_external=["codeql/cert-cpp-coding-standards"],
    )
    options.update(overrides)
    return pk.ensure_packs(codeql, [_ref(r) for r in refs], **options)


def test_stub_shadowing_a_bundled_pack_in_the_cache_is_rejected(
    fake_codeql: Path, tmp_path: Path
) -> None:
    _pack(tmp_path / "dist" / "qlpacks", "codeql/cpp-queries", "1.10.0", ("a.qls",))
    stub = _pack(tmp_path / "cache", "codeql/cpp-queries", "1.10.0")
    with pytest.raises(ScaError, match="would shadow") as info:
        _ensure(fake_codeql, tmp_path, ["codeql/cpp-queries@1.10.0:a.qls"])
    message = str(info.value)
    assert str(stub.parent) in message and "bundled with the CodeQL CLI" in message
    assert not any(c[:2] == ["pack", "download"] for c in _calls(tmp_path))


def test_cache_entries_for_local_or_unlisted_packs_are_rejected(
    fake_codeql: Path, tmp_path: Path
) -> None:
    _pack(tmp_path / "cache", "score/code-complexity-queries", "1.0.0")
    _pack(tmp_path / "cache", "random/thing", "1.0.0")
    local = {"score/code-complexity-queries": ("1.0.0", tmp_path / "cx")}
    with pytest.raises(ScaError) as info:
        _ensure(fake_codeql, tmp_path, [], local_packs=local)
    message = str(info.value)
    assert "is a local pack" in message
    assert "random/thing is not an external pack listed in packs.toml" in message


def test_listed_unreferenced_and_referenced_unlisted_cache_entries_are_fine(
    fake_codeql: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _pack(cache, "codeql/misra-cpp-coding-standards", "2.62.0")
    _pack(cache, "acme/consumer", "3.0.0", ("q.ql",))
    (cache / ".hidden").mkdir()
    (cache / "stray-file").write_text("")
    resolved = _ensure(
        fake_codeql,
        tmp_path,
        ["acme/consumer@3.0.0:q.ql"],
        listed_external=["codeql/misra-cpp-coding-standards"],
    )
    assert resolved[0].origin == "cached"


def test_incomplete_cached_pack_is_removed_and_downloaded_again(
    fake_codeql: Path, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    pack = _pack(cache, "codeql/cert-cpp-coding-standards", "2.62.0")
    (pack / "leftover.txt").write_text("partial")
    resolved = _ensure(
        fake_codeql,
        tmp_path,
        ["codeql/cert-cpp-coding-standards@2.62.0:codeql-suites/cert-cpp-l1.qls"],
    )
    assert resolved[0].origin == "downloaded"
    assert (pack / "codeql-suites" / "cert-cpp-l1.qls").is_file()
    assert not (pack / "leftover.txt").exists()
    assert sum(c[:2] == ["pack", "download"] for c in _calls(tmp_path)) == 1


def test_redownload_that_is_still_incomplete_fails_with_the_missing_path(
    fake_codeql: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEQL_FILES", "[]")
    with pytest.raises(ScaError, match="unusable: nope.qls is missing"):
        _ensure(
            fake_codeql, tmp_path, ["codeql/cert-cpp-coding-standards@2.62.0:nope.qls"]
        )


def test_missing_suite_in_bundled_or_local_pack_is_an_error_without_changes(
    fake_codeql: Path, tmp_path: Path
) -> None:
    bundled = _pack(tmp_path / "dist" / "qlpacks", "codeql/cpp-queries", "1.10.0")
    with pytest.raises(ScaError, match="bundled pack.*suite.qls is missing"):
        _ensure(fake_codeql, tmp_path, ["codeql/cpp-queries@1.10.0:suite.qls"])
    assert bundled.is_dir()
    pack = tmp_path / "mine"
    pack.mkdir()
    local = {"acme/own": ("1.0.0", pack)}
    with pytest.raises(ScaError, match="lacks: q.ql"):
        _ensure(fake_codeql, tmp_path, ["acme/own:q.ql"], local_packs=local)


def test_wrong_version_in_cache_is_replaced(fake_codeql: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    pack = _pack(cache, "codeql/cert-cpp-coding-standards", "2.62.0", ("a.qls",))
    (pack / "qlpack.yml").write_text(
        "name: codeql/cert-cpp-coding-standards\nversion: 2.0.0\n"
    )
    resolved = _ensure(
        fake_codeql, tmp_path, ["codeql/cert-cpp-coding-standards@2.62.0:a.qls"]
    )
    assert resolved[0].origin == "downloaded"


def test_tests_cannot_touch_the_real_cache(isolated_home: Path, tmp_path: Path) -> None:
    import os

    assert Path(os.environ["HOME"]) == isolated_home
    assert tmp_path in pk.default_pack_cache().parents
    assert tmp_path in Path("~").expanduser().parents
