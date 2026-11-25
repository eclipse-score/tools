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

"""Locate runfiles passed in by the Bazel macro.

Every runfile arrives as an environment variable holding a Bazel
``rlocationpath``. It is resolved with ``python.runfiles.Runfiles`` (rules_python).
Outside Bazel (unit tests, local debugging) the same variable may hold an ordinary
file-system path instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from score_codeql.errors import ScaError

ENV_CLI = "SCORE_CODEQL_CLI"
ENV_BCC = "SCORE_CODEQL_BCC"
ENV_BCC_DEB = "SCORE_CODEQL_BCC_DEB"
ENV_BCC_OVERRIDE = "SCORE_CODEQL_BCC_OVERRIDE"
ENV_BCC_SH = "SCORE_CODEQL_BCC_SH"
ENV_BCC_BAZEL_WRAPPER = "SCORE_CODEQL_BCC_BAZEL_WRAPPER"
ENV_WRAPPER_SRC = "SCORE_CODEQL_WRAPPER_SRC"
ENV_REPORTS = "SCORE_CODEQL_REPORTS"
ENV_PACKS = "SCORE_CODEQL_PACKS"
ENV_COMPLEXITY_QLPACK = "SCORE_CODEQL_COMPLEXITY_QLPACK"
ENV_DIAGNOSTICS_QLPACK = "SCORE_CODEQL_DIAGNOSTICS_QLPACK"


def _create_runfiles():
    try:
        from python.runfiles import Runfiles  # type: ignore[import-not-found]
    except ImportError:
        return None
    return Runfiles.Create()


def locate(value: str, *, base: Path | None = None, runfiles=None) -> Path | None:
    """Existing file for *value*: absolute path, runfile location, or relative to *base*."""
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    rf = runfiles if runfiles is not None else _create_runfiles()
    if rf is not None:
        located = rf.Rlocation(value)
        if located and Path(located).exists():
            return Path(located)
    if base is not None and (base / candidate).exists():
        return base / candidate
    return candidate.absolute() if base is None and candidate.exists() else None


def resolve_env_path(
    name: str,
    environ: Mapping[str, str] | None = None,
    *,
    runfiles=None,
) -> Path:
    """Return the existing file/directory referenced by environment variable *name*."""
    env = os.environ if environ is None else environ
    value = env.get(name, "").strip()
    if not value:
        raise ScaError(
            f"required environment variable {name} is not set; it is provided by "
            "the Bazel target (run the analysis via `bazel run`, or export the "
            "variable with a file path when running outside Bazel)"
        )
    located = locate(value, runfiles=runfiles)
    if located is None:
        raise ScaError(
            f"{name}={value!r} does not resolve to an existing file "
            "(neither a runfile location nor a file-system path)"
        )
    return located


def resolve_option_path(value: str, option: str, base: Path) -> Path:
    """Resolve an option value that is a runfile location or a path relative to *base*."""
    located = locate(value, base=base)
    if located is None:
        raise ScaError(
            f"{option} {value!r} does not exist (neither a runfile location nor "
            f"a path relative to {base})"
        )
    return located


def resolve_pack_dir(name: str, environ: Mapping[str, str] | None = None) -> Path:
    """Resolve a ``*_QLPACK`` variable (path of ``qlpack.yml``) to the pack directory."""
    return resolve_env_path(name, environ).parent


def resolve_codeql(
    override: str | None = None, environ: Mapping[str, str] | None = None
) -> Path:
    """Return the CodeQL launcher: ``--codeql-cli`` wins, then ``SCORE_CODEQL_CLI``."""
    if override:
        path = Path(override)
        if not path.exists():
            raise ScaError(f"--codeql-cli {override!r} does not exist")
        return path.absolute()
    return resolve_env_path(ENV_CLI, environ)


def bcc_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Absolute paths for the variables consumed by ``scripts/bcc.sh``."""
    env = os.environ if environ is None else environ
    resolved: dict[str, str] = {}
    if not (env.get(ENV_BCC) or env.get(ENV_BCC_DEB) or env.get(ENV_BCC_OVERRIDE)):
        raise ScaError(
            f"none of {ENV_BCC}, {ENV_BCC_DEB} or {ENV_BCC_OVERRIDE} is set; a "
            "bazel-compile-commands binary is required"
        )
    for name in (ENV_BCC, ENV_BCC_DEB):
        if env.get(name):
            resolved[name] = str(resolve_env_path(name, environ))
    resolved[ENV_BCC_BAZEL_WRAPPER] = str(
        resolve_env_path(ENV_BCC_BAZEL_WRAPPER, environ)
    )
    return resolved
