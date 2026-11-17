#!/usr/bin/env bash
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

# Wrapper around the Bazel binary used by bazel-compile-commands so that its
# internal `bazel info` calls do not inherit the consumer workspace's default
# `--platforms=@...//:...` option.
#
# bazel-compile-commands (kiron1/bazel-compile-commands) issues:
#     bazel info workspace
#     bazel info execution_root
#     bazel aquery ...
# to build compile_commands.json. `bazel info` inherits `build` options from
# .bazelrc (including a default `--config=...` that sets --platforms), but
# `bazel info` does not perform Bzlmod target-platform repository resolution.
# That makes the info calls fail with:
#     no such package '@@platforms_repo//':
#       The repository '@@platforms_repo' is not defined
# even though `bazel build`/`aquery` for the same workspace succeed.
#
# Clearing --platforms for the `info` subcommand avoids that target-platform
# repository resolution while leaving the target platform (and the resulting
# compile actions) untouched for the `aquery` call.
#
# The Bazel executable is taken from $BCC_BAZEL (set by `sca --bazel`), then
# $BAZEL, then bazelisk, then bazel.

set -euo pipefail

BAZEL_BIN="${BCC_BAZEL:-${BAZEL:-}}"

if [[ -z "${BAZEL_BIN}" ]]; then
    # Prefer bazelisk when present so a workspace-local .bazelversion is
    # respected; fall back to whatever `bazel` resolves to (which is bazelisk
    # on the GitHub Actions runners).
    BAZEL_BIN="$(command -v bazelisk 2>/dev/null || command -v bazel 2>/dev/null || true)"
fi

if [[ -z "${BAZEL_BIN}" ]]; then
    echo "ERROR: could not locate a Bazel executable" >&2
    exit 1
fi

# Locate the Bazel subcommand (the first argument that is not a flag).
subcommand=""
for arg in "$@"; do
    case "${arg}" in
        -*) continue ;;
        *)
            subcommand="${arg}"
            break
            ;;
    esac
done

if [[ "${subcommand}" == "info" ]]; then
    exec "${BAZEL_BIN}" "$@" --platforms=
fi

exec "${BAZEL_BIN}" "$@"
