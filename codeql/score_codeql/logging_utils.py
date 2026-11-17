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

"""Shared logging setup: ``WARNING:``/``ERROR:`` prefixes, plain INFO/DEBUG."""

from __future__ import annotations

import logging
import sys


class LevelPrefixFormatter(logging.Formatter):
    """Emit a WARNING:/ERROR: prefix (coloured on a TTY); nothing for INFO/DEBUG."""

    def __init__(self, fmt: str = "%(message)s", *, color: bool = False) -> None:
        super().__init__(fmt)
        warn = "\033[93mWARNING:\033[0m " if color else "WARNING: "
        err = "\033[91mERROR:\033[0m " if color else "ERROR: "
        self._prefix = {
            logging.WARNING: warn,
            logging.ERROR: err,
            logging.CRITICAL: err,
        }

    def format(self, record: logging.LogRecord) -> str:
        return self._prefix.get(record.levelno, "") + super().format(record)


def setup_logging(verbose: bool = False, *, quiet_default: bool = False) -> None:
    """Configure the root logger on stderr.

    ``verbose`` selects DEBUG; otherwise INFO (or WARNING when ``quiet_default``).
    """
    if verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING if quiet_default else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(LevelPrefixFormatter(color=sys.stderr.isatty()))
    logging.basicConfig(level=level, handlers=[handler], force=True)
