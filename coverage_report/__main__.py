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

"""Entry point for `python -m coverage_report` and the Bazel `py_binary`.

Uses an absolute import so the CLI module keeps its imports intact when this
file runs as `__main__`.
"""

import sys

from coverage_report.src.cli import main

if __name__ == "__main__":
    sys.exit(main())
