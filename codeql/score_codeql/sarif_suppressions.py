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

"""Annotate SARIF suppressions with the text of the in-source justification.

``interpret-results`` marks results as ``kind: "inSource"`` when it finds a
``// codeql[rule-id]`` comment but leaves ``justification`` empty. This module
reads the comment on the line directly above the result and stores its trailing
text (``// codeql[rule-id] <justification>``) as the suppression justification.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from score_codeql.logging_utils import setup_logging

# Mirrors CodeQL's own detection (\bcodeql\s*\[...\]); accepts // and /* */ comments.
_COMMENT_RE = re.compile(
    r"^\s*"
    r"(?://|/\*)\s*"
    r"codeql\s*\["
    r"[^\]]*"
    r"\]"
    r"\s*"
    r"(.*?)"
    r"\s*"
    r"(?:\*/)?\s*$"
)
_RULE_ID_RE = re.compile(r"codeql\s*\[([^\]]*)\]")


def _uri_to_path(uri: str, source_root: Path) -> Path:
    if uri.startswith("file://"):
        return Path(unquote(uri[len("file://") :]))
    return source_root / unquote(uri)


def _build_artifact_map(
    artifacts: list[dict[str, Any]], source_root: Path
) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for index, artifact in enumerate(artifacts):
        uri = artifact.get("location", {}).get("uri")
        if uri:
            result[index] = _uri_to_path(uri, source_root)
    return result


def _primary_location(result: dict[str, Any]) -> dict[str, Any] | None:
    locations = result.get("locations", [])
    if not locations:
        return None
    return locations[0].get("physicalLocation")


def _resolve_source_file(
    physical_location: dict[str, Any],
    artifact_paths: dict[int, Path],
    source_root: Path,
) -> Path | None:
    artifact_loc = physical_location.get("artifactLocation", {})
    index = artifact_loc.get("index")
    if index is not None and index in artifact_paths:
        return artifact_paths[index]
    uri = artifact_loc.get("uri")
    return _uri_to_path(uri, source_root) if uri else None


def _read_line(path: Path, line_num: int) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for current, line in enumerate(fh, start=1):
                if current == line_num:
                    return line.rstrip("\n\r")
                if current > line_num:
                    break
    except OSError:
        pass
    return None


def extract_justification(
    source_file: Path, start_line: int, rule_id: str
) -> str | None:
    """Justification of the ``codeql[rule_id]`` comment directly above *start_line*."""
    if start_line < 2:
        return None
    comment_line = _read_line(source_file, start_line - 1)
    if comment_line is None:
        return None
    match = _COMMENT_RE.match(comment_line)
    if match is None:
        return None
    id_match = _RULE_ID_RE.search(comment_line)
    if id_match is None or id_match.group(1).strip() != rule_id.strip():
        logging.debug(
            "Rule ID mismatch in suppression comment at %s:%d",
            source_file,
            start_line - 1,
        )
        return None
    justification = re.sub(r"^[-–—*•]\s*", "", match.group(1).strip()).strip()
    return justification or None


def annotate_sarif(sarif_path: Path, source_root: Path) -> int:
    """Annotate suppressed results in place; return a process exit code."""
    try:
        with sarif_path.open("r", encoding="utf-8") as fh:
            sarif = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logging.error("Failed to read SARIF file %s: %s", sarif_path, exc)
        return 1
    if not isinstance(sarif, dict):
        logging.error("Expected a JSON object at the root of %s", sarif_path)
        return 1

    annotated = 0
    suppressed = 0
    for run in sarif.get("runs", []):
        artifact_paths = _build_artifact_map(run.get("artifacts", []), source_root)
        for result in run.get("results", []):
            in_source = [
                s
                for s in result.get("suppressions") or []
                if s.get("kind") == "inSource"
            ]
            if not in_source:
                continue
            suppressed += 1
            location = _primary_location(result)
            if location is None:
                logging.warning(
                    "Suppressed result for rule %r has no valid location",
                    result.get("ruleId"),
                )
                continue
            source_file = _resolve_source_file(location, artifact_paths, source_root)
            if source_file is None or not source_file.is_file():
                logging.warning(
                    "Cannot resolve source file for suppressed result (rule %r, URI: %s)",
                    result.get("ruleId"),
                    location.get("artifactLocation", {}).get("uri", "<unknown>"),
                )
                continue
            start_line = location.get("region", {}).get("startLine")
            if start_line is None:
                logging.warning(
                    "Suppressed result for rule %r has no startLine",
                    result.get("ruleId"),
                )
                continue
            justification = extract_justification(
                source_file, start_line, result.get("ruleId", "")
            )
            if justification:
                for suppression in in_source:
                    suppression["justification"] = justification
                annotated += 1

    try:
        with sarif_path.open("w", encoding="utf-8") as fh:
            json.dump(sarif, fh, indent=2)
    except OSError as exc:
        logging.error("Failed to write SARIF file %s: %s", sarif_path, exc)
        return 1
    logging.info(
        "Annotated %d of %d suppressed result(s) with justification text.",
        annotated,
        suppressed,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Populate the justification of in-source suppressions in a CodeQL "
            "SARIF file from the codeql[...] comment in the source."
        )
    )
    parser.add_argument("sarif_file", type=Path, help="SARIF file to annotate")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="Root for relative URIs (default: directory of the SARIF file)",
    )
    args = parser.parse_args(argv)
    setup_logging()
    if not args.sarif_file.exists():
        logging.error("SARIF file not found: %s", args.sarif_file)
        return 1
    return annotate_sarif(args.sarif_file, args.source_root or args.sarif_file.parent)


if __name__ == "__main__":
    raise SystemExit(main())
