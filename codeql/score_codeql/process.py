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

"""Subprocess helpers (never uses a shell; never logs the environment)."""

from __future__ import annotations

import logging
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from score_codeql.errors import ScaError


def _fmt(args: Sequence[str]) -> str:
    return shlex.join(str(a) for a in args)


def run_cmd(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Run *args*, streaming its output; raise ``ScaError`` on failure."""
    logging.debug("$ %s", _fmt(args))
    try:
        subprocess.run(
            [str(a) for a in args],
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ScaError(f'executable not found: "{_fmt(args)}" ({exc})') from exc
    except subprocess.CalledProcessError as exc:
        raise ScaError(
            f'command failed: "{_fmt(args)}" (rc={exc.returncode})',
            exc.returncode or 1,
        ) from exc


def run_capture(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *args* capturing stdout/stderr; raise ``ScaError`` on a non-zero exit."""
    logging.debug("$ %s", _fmt(args))
    try:
        return subprocess.run(
            [str(a) for a in args],
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ScaError(f'executable not found: "{_fmt(args)}" ({exc})') from exc
    except subprocess.TimeoutExpired as exc:
        raise ScaError(f'command timed out: "{_fmt(args)}"') from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise ScaError(
            f'command failed: "{_fmt(args)}" (rc={exc.returncode})'
            + (f": {detail}" if detail else ""),
            exc.returncode or 1,
        ) from exc
