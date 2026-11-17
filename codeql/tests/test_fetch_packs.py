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

"""`fetch_packs`: install the pinned packs from the release asset (no registry)."""

import hashlib
import tarfile
import zipfile
from pathlib import Path

import pytest
from score_codeql import config as cfg
from score_codeql import fetch_packs as fp
from score_codeql.errors import ConfigError

VERSION = "2.63.0"
OTHER_VERSION = "2.62.0"
PACKS = (
    "cert-c-coding-standards",
    "cert-cpp-coding-standards",
    "misra-cpp-coding-standards",
)

PACKS_TOML = f"""\
[codeql_cli]
version = "2.23.9"

[packs]
"codeql/cpp-queries" = "1.5.8"
"codeql/cpp-all" = "6.1.4"
"codeql/cert-cpp-coding-standards" = "{VERSION}"
"codeql/cert-c-coding-standards" = "{VERSION}"
"codeql/misra-cpp-coding-standards" = "{VERSION}"

[local]
"score/code-complexity-queries" = "1.0.0"
"""


@pytest.fixture
def pins_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "packs.toml"
    path.write_text(PACKS_TOML, encoding="utf-8")
    monkeypatch.setenv("SCORE_CODEQL_PACKS", str(path))
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pack_tgz(root: Path, name: str, version: str = VERSION) -> Path:
    """`<name>.tgz` with a pack root at its top level, like the release asset."""
    content = root / "content" / name
    (content / "codeql-suites").mkdir(parents=True)
    (content / "qlpack.yml").write_text(
        f"name: codeql/{name}\nversion: {version}\n", encoding="utf-8"
    )
    (content / "codeql-suites" / "cert.qls").write_text("", encoding="utf-8")
    # The release tarballs carry runner modes; the installer must not keep them.
    (content / "qlpack.yml").chmod(0o400)
    tgz = root / f"{name}.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        for path in sorted(content.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(content)))
    return tgz


def _asset(
    root: Path,
    packs: tuple[str, ...] = PACKS,
    version: str = VERSION,
    name: str = fp.ASSET_NAME,
) -> Path:
    asset = root / name
    with zipfile.ZipFile(asset, "w") as archive:
        for pack in packs:
            archive.write(_pack_tgz(root, pack, version), arcname=f"{pack}.tgz")
    return asset


def _run(asset: Path, cache: Path, *extra: str) -> int:
    return fp.main(
        [
            f"--asset-url={asset.as_uri()}",
            f"--zip={asset}",
            f"--cache-dir={cache}",
            f"--sha256={_sha256(asset)}",
            *extra,
        ]
    )


def _pack_dir(cache: Path, pack: str, version: str = VERSION) -> Path:
    return cache / "codeql" / pack / version


def test_installs_the_pinned_packs_into_the_cache_layout(
    tmp_path: Path, pins_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asset = _asset(tmp_path)
    cache = tmp_path / "cache"

    assert _run(asset, cache, f"--version={VERSION}") == 0

    for pack in PACKS:
        directory = _pack_dir(cache, pack)
        assert (directory / "qlpack.yml").is_file(), pack
        assert (directory / "codeql-suites" / "cert.qls").is_file(), pack
        # Readable for the user that runs the analysis, whatever the asset says.
        assert (directory / "qlpack.yml").stat().st_mode & 0o444 == 0o444, pack
        assert directory.stat().st_mode & 0o555 == 0o555, pack
    out = capsys.readouterr().out
    assert out.count("installed ") == len(PACKS)
    assert "no registry access" in out


def test_bundled_packs_are_not_installed(tmp_path: Path, pins_file: Path) -> None:
    asset = _asset(tmp_path)
    cache = tmp_path / "cache"

    assert _run(asset, cache) == 0

    assert not (cache / "codeql" / "cpp-queries").exists()
    assert not (cache / "codeql" / "cpp-all").exists()


def test_released_packs_excludes_the_bundled_packs(pins_file: Path) -> None:
    released = fp.released_packs(cfg.load_pins(pins_file))
    assert released == {f"codeql/{pack}": VERSION for pack in PACKS}


def test_mixed_coding_standards_versions_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "packs.toml"
    path.write_text(
        PACKS_TOML.replace(
            f'"codeql/cert-c-coding-standards" = "{VERSION}"',
            f'"codeql/cert-c-coding-standards" = "{OTHER_VERSION}"',
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SCORE_CODEQL_PACKS", str(path))

    with pytest.raises(ConfigError, match="must share one version"):
        fp.released_packs(cfg.load_pins(path))


def test_checksum_mismatch_fails_before_writing(
    tmp_path: Path, pins_file: Path
) -> None:
    asset = _asset(tmp_path)
    cache = tmp_path / "cache"

    code = fp.main(
        [
            f"--asset-url={asset.as_uri()}",
            f"--zip={asset}",
            f"--cache-dir={cache}",
            f"--sha256={'0' * 64}",
        ]
    )

    assert code == 1
    assert not (cache / "codeql").exists()


def test_second_run_reports_present_and_keeps_the_pack(
    tmp_path: Path, pins_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asset = _asset(tmp_path)
    cache = tmp_path / "cache"
    assert _run(asset, cache) == 0
    marker = _pack_dir(cache, PACKS[0]) / "marker"
    marker.write_text("keep", encoding="utf-8")
    capsys.readouterr()

    assert _run(asset, cache) == 0

    assert marker.read_text(encoding="utf-8") == "keep"
    assert capsys.readouterr().out.count("present ") == len(PACKS)


def test_prunes_other_versions_of_a_pinned_pack(
    tmp_path: Path, pins_file: Path
) -> None:
    asset = _asset(tmp_path)
    cache = tmp_path / "cache"
    stale = _pack_dir(cache, PACKS[0], OTHER_VERSION)
    stale.mkdir(parents=True)
    (stale / "qlpack.yml").write_text(
        f"name: codeql/{PACKS[0]}\nversion: {OTHER_VERSION}\n", encoding="utf-8"
    )

    assert _run(asset, cache) == 0

    assert not stale.exists()
    assert _pack_dir(cache, PACKS[0]).is_dir()


def test_missing_pack_in_the_asset_is_reported(tmp_path: Path, pins_file: Path) -> None:
    asset = _asset(tmp_path, packs=PACKS[:2])
    cache = tmp_path / "cache"

    assert _run(asset, cache) == 1

    assert not _pack_dir(cache, PACKS[2]).exists()


def test_pack_with_the_wrong_version_is_rejected(
    tmp_path: Path, pins_file: Path
) -> None:
    asset = _asset(tmp_path, version=OTHER_VERSION)
    cache = tmp_path / "cache"

    assert _run(asset, cache) == 1

    assert not _pack_dir(cache, PACKS[0]).exists()


def test_version_must_match_the_pins(tmp_path: Path, pins_file: Path) -> None:
    asset = _asset(tmp_path)

    assert _run(asset, tmp_path / "cache", f"--version={OTHER_VERSION}") == 1


def test_downloads_the_asset_and_reads_the_published_checksums(
    tmp_path: Path, pins_file: Path
) -> None:
    asset = _asset(tmp_path)
    (tmp_path / fp.CHECKSUMS_NAME).write_text(
        f"{_sha256(asset)}  {fp.ASSET_NAME}\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"

    code = fp.main([f"--asset-url={asset.as_uri()}", f"--cache-dir={cache}"])

    assert code == 0
    assert (_pack_dir(cache, PACKS[1]) / "qlpack.yml").is_file()


def test_checksums_without_the_asset_entry_fail(
    tmp_path: Path, pins_file: Path
) -> None:
    asset = _asset(tmp_path)
    (tmp_path / fp.CHECKSUMS_NAME).write_text(
        f"{'a' * 64}  other.zip\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"

    code = fp.main([f"--asset-url={asset.as_uri()}", f"--cache-dir={cache}"])

    assert code == 1
    assert not (cache / "codeql").exists()


def test_parse_checksums_ignores_unknown_lines() -> None:
    sums = fp.parse_checksums(
        "\n".join(
            [
                f"{'a' * 64}  first.zip",
                f"{'b' * 64} *starred.zip",
                "# a comment",
                "c" * 64,
                "not-a-checksum  other.zip",
            ]
        )
    )
    assert sums == {"first.zip": "a" * 64, "starred.zip": "b" * 64}


def test_missing_zip_file_is_reported(tmp_path: Path, pins_file: Path) -> None:
    code = fp.main(
        [
            "--asset-url=https://example.invalid/coding-standards-codeql-packs.zip",
            f"--zip={tmp_path / 'absent.zip'}",
            f"--sha256={'a' * 64}",
            f"--cache-dir={tmp_path / 'cache'}",
        ]
    )

    assert code == 1
    assert not (tmp_path / "cache").exists()
