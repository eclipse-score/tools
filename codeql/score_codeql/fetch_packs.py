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
built with).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
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
CHECKSUMS_NAME = "checksums.txt"
_TIMEOUT = 120

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_of(path: Path) -> str:
    """Hex sha256 of the file at *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(text: str) -> dict[str, str]:
    """``{file name: sha256}`` from a ``sha256sum``-style file."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and _SHA256_RE.match(fields[0].lower()):
            sums[fields[1].lstrip("*")] = fields[0].lower()
    return sums


def download(url: str, target: Path) -> None:
    """Download *url* to *target* (no credentials; the asset is public)."""
    logging.info("Downloading %s", url)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
            with target.open("wb") as fh:
                shutil.copyfileobj(response, fh)
    except OSError as exc:
        raise ScaError(f"cannot download {url}: {exc}") from exc


def checksums_url(asset_url: str) -> str:
    """``checksums.txt`` published next to the asset."""
    return urllib.parse.urljoin(asset_url, CHECKSUMS_NAME)


def expected_sha256(asset_url: str, asset_name: str, sha256: str | None) -> str:
    """Pinned checksum of the asset, from ``--sha256`` or the release's checksums."""
    if sha256:
        return sha256.lower()
    url = checksums_url(asset_url)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / CHECKSUMS_NAME
        download(url, path)
        try:
            sums = parse_checksums(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ScaError(f"cannot read {url}: {exc}") from exc
    if asset_name not in sums:
        raise ScaError(f"{url} has no sha256 for {asset_name}")
    return sums[asset_name]


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


def asset_member(archive: zipfile.ZipFile, pack: str) -> zipfile.ZipInfo:
    """The ``<pack>.tgz`` member of the asset that holds *pack*."""
    wanted = f"{pack.rsplit('/', 1)[-1]}.tgz"
    matches = [
        info for info in archive.infolist() if Path(info.filename).name == wanted
    ]
    if len(matches) != 1:
        raise ScaError(
            f"the release asset must contain exactly one {wanted}, it contains "
            f"{len(matches)}"
        )
    return matches[0]


def _normalize_modes(root: Path) -> None:
    """Readable directories (0755) and files (0644) below *root*.

    The tarballs in the release asset are built on a CI runner and may carry
    restrictive modes; the cache is read by the user (or CI job) that runs the
    analysis, so make it readable regardless.
    """
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            continue
        path.chmod(0o755 if path.is_dir() else 0o644)


def install_pack(
    archive: zipfile.ZipFile, pack: str, version: str, cache_dir: Path
) -> tuple[str, Path]:
    """Unpack *pack* into the cache; ``("present"|"installed", pack directory)``."""
    target = cache_dir / pack / version
    if pk.has_pack(target, version):
        pk.remove_other_versions(cache_dir, pack, version)
        return "present", target
    member = asset_member(archive, pack)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Unpack next to the target and rename, so an interrupted run never
        # leaves a half-written pack that would later look usable.
        staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=target.parent))
    except OSError as exc:
        raise ScaError(
            f"cannot write the pack cache {cache_dir}: {exc} (use a writable "
            "--cache-dir or $SCORE_CODEQL_PACK_CACHE)"
        ) from exc
    try:
        try:
            with (
                archive.open(member) as raw,
                tarfile.open(fileobj=raw, mode="r:gz") as tar,
            ):
                tar.extractall(staging, filter="data")
        except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
            raise ScaError(f"cannot unpack {member.filename}: {exc}") from exc
        name, found = pk.read_qlpack(staging)
        if (name, found) != (pack, version):
            raise ScaError(
                f"{member.filename} contains {name}@{found}, expected {pack}@{version}"
            )
        _normalize_modes(staging)
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    pk.remove_other_versions(cache_dir, pack, version)
    return "installed", target


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
