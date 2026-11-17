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

"""Fill the pack cache from the coding-standards release assets.

``bazel run @score_tools//codeql:fetch_packs -- --cache-dir=/opt/codeql-packs``
downloads the ``coding-standards-codeql-packs.zip`` release asset of the pinned
version, verifies its sha256 and unpacks the pinned coding-standards packs into
``<cache>/<scope>/<name>/<version>/`` - the layout ``--pack-cache-dir`` (and
``$SCORE_CODEQL_PACK_CACHE``) expects.

The CodeQL registry is never contacted, so the cache can be built once (release
job, image build) and then consumed offline - also by a CLI that cannot read the
registry copy of the packs. The asset is byte-identical to what the registry
serves, and the packs are self-contained (they carry the libraries they were
built with). The asset helpers live in `packs`, because `ensure_packs` falls back
to the same release asset when `codeql pack download` fails.
"""

from __future__ import annotations

import argparse
import logging
import tempfile
import urllib.parse
import zipfile
from collections.abc import Sequence
from pathlib import Path

from score_codeql import bazel as bz
from score_codeql import config as cfg
from score_codeql import packs as pk
from score_codeql import runfiles as rf
from score_codeql.errors import ConfigError, ScaError
from score_codeql.logging_utils import setup_logging

# Packs that the CodeQL distribution ships in `<dist>/qlpacks`; they come with the
# pinned bundle, not with the coding-standards release asset.
CLI_PACKS = ("codeql/cpp-all", "codeql/cpp-queries")

ASSET_NAME = "coding-standards-codeql-packs.zip"
CHECKSUMS_NAME = pk.CHECKSUMS_NAME

# The asset handling lives in `packs`, because `sca` falls back to the release
# asset when `codeql pack download` cannot fetch a pinned pack. Re-exported here
# so this entry point stays the documented tool for building a pack cache.
download = pk.download
sha256_of = pk.sha256_of
parse_checksums = pk.parse_checksums
expected_sha256 = pk.expected_sha256
install_pack = pk.install_pack


def released_packs(pins: cfg.Pins) -> dict[str, str]:
    """``{name: version}`` of the pinned packs the release asset provides.

    The coding-standards packs are released together, so they must share one
    version: the asset carries exactly that release.
    """
    released = {n: v for n, v in pins.packs.items() if n not in CLI_PACKS}
    if not released:
        raise ConfigError(
            "packs.toml pins no pack that the coding-standards release provides "
            f"(only the bundles {', '.join(CLI_PACKS)})"
        )
    versions = sorted(set(released.values()))
    if len(versions) != 1:
        raise ConfigError(
            "the coding-standards packs must share one version (one release asset "
            f"carries them all), packs.toml pins {', '.join(versions)}"
        )
    return released


def obtain_asset(url: str, zip_path: str | None, asset_name: str, tmp: Path) -> Path:
    """The asset to install from: ``--zip`` if given, else a download in *tmp*."""
    if zip_path:
        path = bz.user_path(zip_path)
        if not path.is_file():
            raise ScaError(f"--zip {path} does not exist")
        return path
    target = tmp / asset_name
    download(url, target)
    return target


def run(args: argparse.Namespace) -> int:
    pins = cfg.load_pins(rf.resolve_env_path(rf.ENV_PACKS))
    released = released_packs(pins)
    pinned_version = next(iter(released.values()))
    version = args.version or pinned_version
    if version != pinned_version:
        raise ConfigError(
            f"--version {version} does not match the pinned coding-standards "
            f"version {pinned_version} (packs.toml)"
        )
    cache_dir = (
        bz.user_path(args.cache_dir) if args.cache_dir else pk.default_pack_cache()
    )
    asset_name = Path(urllib.parse.urlparse(args.asset_url).path).name or ASSET_NAME
    expected = expected_sha256(args.asset_url, asset_name, args.sha256)
    with tempfile.TemporaryDirectory() as tmp:
        asset = obtain_asset(args.asset_url, args.zip, asset_name, Path(tmp))
        actual = sha256_of(asset)
        if actual != expected:
            raise ScaError(
                f"{asset.name}: sha256 {actual} does not match the expected "
                f"{expected} (from {CHECKSUMS_NAME} or --sha256)"
            )
        try:
            archive = zipfile.ZipFile(asset)
        except zipfile.BadZipFile as exc:
            raise ScaError(f"{asset} is not a zip archive: {exc}") from exc
        with archive:
            for pack, pinned in sorted(released.items()):
                status, path = install_pack(archive, pack, pinned, cache_dir)
                print(f"{status} {pack}@{pinned} -> {path}")
    print(f"pack cache {cache_dir} is ready (no registry access)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fill the CodeQL pack cache from the coding-standards release "
        "asset (no registry access).",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        metavar="PATH",
        help="Pack cache to fill (default: $SCORE_CODEQL_PACK_CACHE or "
        "~/.cache/score-codeql/packs)",
    )
    parser.add_argument(
        "--zip",
        default=None,
        metavar="PATH",
        help="Use this already downloaded asset instead of downloading it",
    )
    parser.add_argument(
        "--asset-url",
        required=True,
        metavar="URL",
        help="Release asset URL (baked in by the Bazel target)",
    )
    parser.add_argument(
        "--sha256",
        default=None,
        metavar="HEX",
        help="Expected sha256 of the asset (default: the release's checksums.txt)",
    )
    parser.add_argument(
        "--version",
        default=None,
        metavar="VERSION",
        help="Release version to unpack; must match packs.toml",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except ScaError as exc:
        logging.error("%s", exc)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
