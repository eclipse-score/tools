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

"""Export the rules (queries) of every pack reference of the selected reports as CSV.

For each reference ``codeql resolve queries --format=json`` is evaluated against the
pinned packs (bundle, pack cache, local packs). One ``queries.csv`` per reference is
written to ``<output-dir>/<name>/`` with one row per query, relative to the pack root
and without the ``.ql`` suffix.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path

from score_codeql import bazel as bz
from score_codeql import config as cfg
from score_codeql import packs as pk
from score_codeql import runfiles as rf
from score_codeql.cli import consumer_inputs
from score_codeql.errors import ScaError
from score_codeql.logging_utils import setup_logging
from score_codeql.process import run_capture


def output_name(ref: cfg.PackRef) -> str:
    """Directory name for *ref*: last pack path component plus suite/query stem."""
    pack = ref.name.rsplit("/", 1)[-1]
    if ref.path:
        stem = Path(ref.path).stem
        return re.sub(r"[^A-Za-z0-9._-]+", "_", f"{pack}-{stem}")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", pack)


def relative_rule(query_path: str, pack_root: Path) -> str:
    """Query path relative to *pack_root*, without the ``.ql`` suffix."""
    absolute = Path(query_path).resolve()
    try:
        relative = absolute.relative_to(pack_root.resolve())
    except ValueError:
        relative = absolute
    return str(relative.with_suffix("")) if relative.suffix == ".ql" else str(relative)


def resolve_queries(codeql: Path, ref: cfg.PackRef, additional: str) -> list[str]:
    result = run_capture(
        [
            str(codeql),
            "resolve",
            "queries",
            "--format=json",
            f"--additional-packs={additional}",
            str(ref),
        ]
    )
    text = result.stdout.strip()
    try:
        queries = json.loads(text[text.index("[") : text.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ScaError(f"no JSON array in resolve output for {ref}: {exc}") from exc
    return [str(q) for q in queries]


def write_rules_csv(path: Path, rules: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["query"])
        for rule in sorted(rules):
            writer.writerow([rule])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="list_rules", allow_abbrev=False, description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--output-dir", default=None, help="default: <workspace>/_codeql_sca/rules"
    )
    parser.add_argument("--safety-level", default=None)
    parser.add_argument("-r", "--report", action="append", default=None, metavar="NAME")
    parser.add_argument("--reports-file", action="append", default=[], metavar="PATH")
    parser.add_argument(
        "--additional-pack-dir", action="append", default=[], metavar="PATH"
    )
    parser.add_argument(
        "--additional-pack-qlpack", action="append", default=[], metavar="LOCATION"
    )
    parser.add_argument("--pack-cache-dir", default=None)
    parser.add_argument("--codeql-cli", default=None, metavar="PATH")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    pins = cfg.load_pins(rf.resolve_env_path(rf.ENV_PACKS))
    reports_files, extra_dirs = consumer_inputs(args)
    reports = cfg.load_reports(rf.resolve_env_path(rf.ENV_REPORTS), reports_files)
    selected = cfg.select_reports(reports, args.safety_level, args.report)
    by_report = cfg.refs_of(reports, selected)
    local_dirs = [rf.resolve_pack_dir(rf.ENV_COMPLEXITY_QLPACK), *extra_dirs]
    local_packs = pk.find_local_packs(local_dirs)
    cfg.validate_refs(by_report, pins, local_packs.keys())

    codeql = rf.resolve_codeql(args.codeql_cli)
    info = pk.get_cli_info(codeql)
    pk.check_cli_version(info, pins)
    cache_dir = (
        bz.user_path(args.pack_cache_dir)
        if args.pack_cache_dir
        else pk.default_pack_cache()
    )
    refs = cfg.union_refs(by_report)
    resolved = {
        (p.name, p.version): p
        for p in pk.ensure_packs(
            codeql,
            refs,
            cache_dir=cache_dir,
            distribution=info.distribution,
            local_packs=local_packs,
            listed_external=pins.packs,
        )
    }
    additional = pk.additional_packs_value(cache_dir, local_dirs)

    output_dir = (
        bz.user_path(args.output_dir)
        if args.output_dir
        else bz.find_workspace() / "_codeql_sca" / "rules"
    )
    used: set[str] = set()
    for ref in refs:
        name = output_name(ref)
        while name in used:
            name += "_"
        used.add(name)
        pack = resolved.get((ref.name, ref.version)) or resolved[(ref.name, None)]
        rules = [
            relative_rule(q, pack.path)
            for q in resolve_queries(codeql, ref, additional)
        ]
        target = output_dir / name / "queries.csv"
        write_rules_csv(target, rules)
        print(f"{name}: {len(rules)} rules -> {target}")
    return 0


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
