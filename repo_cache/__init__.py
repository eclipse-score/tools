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

"""SCORE Repository Cache component."""

from .src.cache import default_cache_directory as default_cache_directory
from .src.checkout import restore_synced_default_branch as restore_synced_default_branch
from .src.checkout import sync_default_branch as sync_default_branch
from .src.errors import CommandError as CommandError
from .src.errors import RepoCacheError as RepoCacheError
from .src.github import ensure_authenticated as ensure_authenticated
from .src.github import list_repositories as list_repositories
from .src.models import Repository as Repository
from .src.sync import DEFAULT_SYNC_WORKERS as DEFAULT_SYNC_WORKERS
from .src.sync import SyncOutcome as SyncOutcome
from .src.sync import SyncReport as SyncReport
from .src.sync import sync_org as sync_org
