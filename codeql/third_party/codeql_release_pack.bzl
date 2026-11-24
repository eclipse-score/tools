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
"""Repository rule that vendors a pre-compiled CodeQL coding-standards pack.

Every github/codeql-coding-standards release publishes a
`coding-standards-codeql-packs.zip` asset with one pre-compiled pack per
standard (`.qlx` queries, suites and all library dependencies under
`.codeql/libraries/`). Using it avoids compiling queries (which does not work in
Bazel's sandbox) and keeps the analysis pinned by sha256.
"""

def _codeql_release_pack_impl(repository_ctx):
    repository_ctx.download(
        url = repository_ctx.attr.urls,
        output = "packs.zip",
        sha256 = repository_ctx.attr.sha256,
    )
    repository_ctx.extract(archive = "packs.zip", output = "_packs")
    repository_ctx.extract(
        archive = "_packs/" + repository_ctx.attr.pack_tarball,
        output = "pack",
    )
    repository_ctx.delete("packs.zip")
    repository_ctx.delete("_packs")
    repository_ctx.file(
        "BUILD.bazel",
        """\
filegroup(
    name = "pack",
    srcs = glob(["pack/**", "pack/.codeql/**"], allow_empty = False),
    visibility = ["//visibility:public"],
)

exports_files(["pack/qlpack.yml"])
""",
    )

codeql_release_pack = repository_rule(
    implementation = _codeql_release_pack_impl,
    doc = "Downloads and unpacks one pre-compiled coding-standards CodeQL pack.",
    attrs = {
        "pack_tarball": attr.string(
            mandatory = True,
            doc = "Name of the per-standard .tgz inside the zip, e.g. misra-cpp-coding-standards.tgz.",
        ),
        "sha256": attr.string(
            mandatory = True,
            doc = "Expected sha256 of the downloaded zip.",
        ),
        "urls": attr.string_list(
            mandatory = True,
            doc = "URL(s) of the coding-standards-codeql-packs.zip release asset.",
        ),
    },
)
