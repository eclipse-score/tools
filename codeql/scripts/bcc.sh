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

# Launcher for the pinned bazel-compile-commands (kiron1/bazel-compile-commands).
#
# Usage:
#   bcc.sh --output-dir <dir> -- <arguments for bazel-compile-commands>
#
# Environment (absolute paths, provided by the `sca` binary from Bazel runfiles):
#   SCORE_CODEQL_BCC                 binary built for glibc >= 2.38 (release zip)
#   SCORE_CODEQL_BCC_DEB             Ubuntu 22.04 (jammy) .deb used when glibc < 2.38
#   SCORE_CODEQL_BCC_BAZEL_WRAPPER   bcc_bazel_wrapper.sh, passed with -B
#   SCORE_CODEQL_BCC_OVERRIDE        optional explicit path to a binary; wins over
#                                    the pinned ones (no implicit PATH lookup)
#
# The release binary requires glibc >= 2.38. On older hosts (e.g. Ubuntu 22.04,
# glibc 2.35) the last jammy release (a .deb requiring glibc 2.34) is unpacked
# with `dpkg-deb -x` into <output-dir>/bin. Nothing is downloaded at runtime.
#
# <output-dir> is only used as scratch space for the unpacked .deb. Where
# compile_commands.json is written is decided by the arguments after `--`
# (bazel-compile-commands --output <path>).

set -euo pipefail

usage() {
    echo "Usage: bcc.sh --output-dir <dir> -- <bazel-compile-commands args...>" >&2
}

output_dir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            output_dir="$2"
            shift 2
            ;;
        --output-dir=*)
            output_dir="${1#--output-dir=}"
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [[ -z "${output_dir}" ]]; then
    usage
    exit 2
fi

if [[ -z "${SCORE_CODEQL_BCC_BAZEL_WRAPPER:-}" ]]; then
    echo "ERROR: SCORE_CODEQL_BCC_BAZEL_WRAPPER is not set" >&2
    exit 1
fi

# version_gte CURRENT REQUIRED — exit 0 if CURRENT >= REQUIRED (compared with
# GNU sort -V). An empty/undetectable version is treated as "older".
version_gte() {
    local current="$1" required="$2"
    [[ -n "${current}" ]] || return 1
    [[ "$(printf '%s\n%s\n' "${current}" "${required}" | sort -V | head -n1)" == "${required}" ]]
}

host_glibc_version() {
    local version
    version="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}' || true)"
    if [[ -z "${version}" ]]; then
        version="$(ldd --version 2>/dev/null | awk 'NR==1 {print $NF}' || true)"
    fi
    printf '%s' "${version}"
}

if [[ -n "${SCORE_CODEQL_BCC_OVERRIDE:-}" ]]; then
    bcc_bin="${SCORE_CODEQL_BCC_OVERRIDE}"
    if [[ ! -x "${bcc_bin}" ]]; then
        echo "ERROR: SCORE_CODEQL_BCC_OVERRIDE='${bcc_bin}' is not an executable file" >&2
        exit 1
    fi
elif version_gte "$(host_glibc_version)" "2.38"; then
    bcc_bin="${SCORE_CODEQL_BCC:-}"
    if [[ -z "${bcc_bin}" || ! -x "${bcc_bin}" ]]; then
        echo "ERROR: SCORE_CODEQL_BCC does not point to an executable (got '${bcc_bin}')" >&2
        exit 1
    fi
else
    deb="${SCORE_CODEQL_BCC_DEB:-}"
    if [[ -z "${deb}" || ! -f "${deb}" ]]; then
        echo "ERROR: SCORE_CODEQL_BCC_DEB does not point to a file (got '${deb}')" >&2
        exit 1
    fi
    if ! command -v dpkg-deb &>/dev/null; then
        echo "ERROR: dpkg-deb is required to unpack the jammy bazel-compile-commands package" \
            "on hosts with glibc < 2.38 (install 'dpkg' or use a Debian/Ubuntu host)." >&2
        exit 1
    fi
    bcc_bin="${output_dir}/bin/usr/bin/bazel-compile-commands"
    if [[ ! -x "${bcc_bin}" ]]; then
        mkdir -p "${output_dir}/bin"
        dpkg-deb -x "${deb}" "${output_dir}/bin"
    fi
fi

exec "${bcc_bin}" -B "${SCORE_CODEQL_BCC_BAZEL_WRAPPER}" "$@"
