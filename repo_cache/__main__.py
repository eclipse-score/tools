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

"""Entry point for `python -m repo_cache` and the Bazel `py_binary`.

Delegates to `repo_cache.src.cli` via an absolute import so the CLI module
keeps its relative imports intact even when this file is executed directly
as `__main__` (which strips its own package context).
"""

import sys

from repo_cache.src.cli import main

if __name__ == "__main__":
    sys.exit(main())
