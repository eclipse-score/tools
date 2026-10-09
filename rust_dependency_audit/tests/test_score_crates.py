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

from rust_dependency_audit.src.score_crates import (
    parse_score_crates_module_bazel,
    parse_score_crates_build,
    build_score_crates_reference,
)

SAMPLE_MODULE_BAZEL = """
module(
    name = "score_crates",
    version = "0.0.7",
)

crate = use_extension("@rules_rust//crate_universe:extensions.bzl", "crate")

crate.spec(
    package = "futures",
    version = "0.3.31",
)
crate.spec(
    package = "libc",
    # Exact pin (not a caret/minimum requirement):
    version = "=0.2.186",
)
crate.spec(
    features = ["derive"],
    package = "clap",
    version = "4.5.4",
)
crate.spec(
    git = "https://github.com/qorix-group/iceoryx2.git",
    package = "iceoryx2-qnx8",
    rev = "9f5622f554de48a7a296e1a5a71200b01e35a502",
)
"""

SAMPLE_BUILD = """
alias(
    name = "clap",
    actual = "@crate_index//:clap",
    visibility = ["//visibility:public"],
)

alias(
    name = "futures",
    actual = "@crate_index//:futures",
    visibility = ["//visibility:public"],
)

alias(
    name = "iceoryx2_qnx8",
    actual = "@crate_index//:iceoryx2-qnx8",
    visibility = ["//visibility:public"],
)
"""


def test_parse_score_crates_module_bazel():
    specs = parse_score_crates_module_bazel(SAMPLE_MODULE_BAZEL)
    assert len(specs) == 4

    assert "futures" in specs
    assert specs["futures"].version == "0.3.31"
    assert specs["futures"].features == []

    assert "libc" in specs
    assert specs["libc"].version == "=0.2.186"

    assert "clap" in specs
    assert specs["clap"].version == "4.5.4"
    assert specs["clap"].features == ["derive"]

    assert "iceoryx2-qnx8" in specs
    assert specs["iceoryx2-qnx8"].git == "https://github.com/qorix-group/iceoryx2.git"
    assert specs["iceoryx2-qnx8"].rev == "9f5622f554de48a7a296e1a5a71200b01e35a502"


def test_parse_score_crates_build():
    aliases = parse_score_crates_build(SAMPLE_BUILD)
    assert len(aliases) == 3
    assert aliases["clap"] == "@crate_index//:clap"
    assert aliases["futures"] == "@crate_index//:futures"
    assert aliases["iceoryx2_qnx8"] == "@crate_index//:iceoryx2-qnx8"


def test_build_score_crates_reference():
    ref = build_score_crates_reference(SAMPLE_MODULE_BAZEL, SAMPLE_BUILD)
    assert ref.contains_crate("clap")
    assert ref.contains_crate("iceoryx2-qnx8")
    assert not ref.contains_crate("nonexistent")
    assert ref.get_version("futures") == "0.3.31"
