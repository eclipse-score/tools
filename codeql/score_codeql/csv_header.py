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

"""Ensure a CodeQL CSV report starts with the expected header row."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from score_codeql.logging_utils import setup_logging

CSV_HEADER = (
    '"Rule","Description","Severity","Message","Path",'
    '"StartLine","StartColumn","EndLine","EndColumn"'
)


def ensure_csv_header(csv_path: Path) -> int:
    """Prepend the header if missing; return a process exit code."""
    if not csv_path.exists():
        logging.error("CSV file not found: %s", csv_path)
        return 1
    try:
        original = csv_path.read_text(encoding="utf-8")
        first_line = original.split("\n", 1)[0].rstrip("\r")
        if first_line == CSV_HEADER:
            return 0
        csv_path.write_text(f"{CSV_HEADER}\n{original}", encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logging.error("failed to update CSV file '%s': %s", csv_path, exc)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepend the expected CodeQL CSV header, if it is missing."
    )
    parser.add_argument("csv_file", type=Path, help="Path to the CSV file to fix")
    args = parser.parse_args(argv)
    setup_logging()
    return ensure_csv_header(args.csv_file)


if __name__ == "__main__":
    raise SystemExit(main())
