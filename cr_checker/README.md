<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# CopyRight Checker

`cr_checker.py` is a tool designed to check if files contain a specified copyright header. It provides configurable logging, color-coded console output, and can handle large file sets efficiently. The script supports reading configuration files for custom copyright templates and can utilize memory-mapped file reading for better performance with large files. Tool itself can also append copyright header at the beginning of file if flag `--fix` is used.

## Features

- Checks files for specified copyright headers based on file extensions.
- When no explicit inputs are given, automatically discovers files via `git ls-files --cached --other --exclude-standard`, so it always operates on the full set of tracked and untracked-but-not-ignored files in the repository.
- Configurable logging, including color-coded output for easy visibility of log levels.
- Can use memory mapping for large file handling.
- Customizable file encoding.
- Automatically detects and skips past a shebang (`#!...`) line before looking for the header.
- Can append copyright headers.

## Requirements

- Python 3.12+
- `argparse`, `logging`, `os`, `sys`, `mmap`, `subprocess`, `tempfile`, and `pathlib` (standard library modules)
- `git` available on `PATH` (used to discover files when no explicit inputs are given)

## Usage

`cr_checker` is **not intended to be invoked directly**. It's meant to be wired into your module through one of the two supported integrations — [pre-commit](#how-to-pre-commit) or [Bazel](#how-to-bazel) — both described below under [Integrating `cr_checker` into your own module](#integrating-cr_checker-into-your-own-module). Both integrations run the exact same script; the flags below are simply the ones you pass through `args:` in your `pre-commit` hook config or through the matching parameters of the `copyright_checker` Bazel macro.

### Arguments

- **-t**, **--template-file**: Path to the template file that defines the copyright text for each file extension. Defaults to the bundled `templates.ini` next to the script.
- **-v**, **--verbose**: Enable debug-level logging.
- **-l**, **--log-file**: Path to a log file where logs will be saved. If not provided, logs will print to the console.
- **-e**, **--extensions**: List of file extensions to filter, e.g., `-e py cpp`. This list replaces (rather than extends) the built-in default list.
- **--encoding**: File encoding (default is utf-8).
- **--exclusion-file**: Path to a file listing paths (one per line, relative to the repository root) to exclude from the check.
- **--fix**: Setting script into fix mode where copyright header will be added to the files if it's missing from same.
- **inputs**: (Optional) Directories and/or files to check. Neither integration passes these explicitly for a full-repo check: `pre-commit` passes whichever files it decided to run against instead, and the Bazel macro never passes any, which makes the tool fall back to running `git ls-files --cached --other --exclude-standard` (resolved against `BUILD_WORKSPACE_DIRECTORY` if set, otherwise the current working directory) and checking everything that comes back.


### Template File Format

The template file should be in INI format, with each section representing a file extension and a section specifying the copyright text.
The copyright text can use format expressions to match the year and the author.

Example templates.ini:

```ini
[py,sh]
# Copyright (c) {year} {author}

[cpp,c,hpp, h]
// Copyright (c) {year} {author}
```

## Exit Codes

- 0: All files contain the required copyright text.
- 1: Some files are missing the required copyright text, have a duplicate/malformed header, or the exclusion file contains invalid entries.
- Other: Error encountered during file processing.


## Integrating `cr_checker` into your own module

There are two supported ways to run `cr_checker` against your own repository: as a `pre-commit` hook, or as a Bazel target. Both rely on the same default behavior described above: if you don't tell it which files to look at, it discovers them itself.

### How-to: pre-commit

This repository ships a [`.pre-commit-hooks.yaml`](../.pre-commit-hooks.yaml) that defines a `copyright` hook (`language: script`, running `cr_checker.py --fix`), so any repository can pull it in as a remote `pre-commit` hook.

1. In your module's `.pre-commit-config.yaml`, add this repository as a hook source:

   ```yaml
   repos:
     - repo: https://github.com/eclipse-score/tools
       rev: <tag-or-commit-sha>          # pin to a specific commit/tag of this repo
       hooks:
         - id: copyright
           args:
             # - --exclusion-file=copyright_exclusions.txt   # optional
             # - --template-file=templates.ini             # optional, provide your own header templates
             # - --extensions=py cpp h                       # optional, overrides the built-in list
   ```

2. Any `args` you add here are appended after the hard-coded `--fix` from the hook definition, and the hook runs with your repository as the working directory, so relative paths (like `copyright_exclusions.txt` above) are resolved against your repo root.

3. `pre-commit` itself decides which files to pass to the hook (the changed/staged files by default, or every tracked file with `pre-commit run --all-files`), so in normal `pre-commit` usage `cr_checker` checks exactly the files `pre-commit` hands it — the "no inputs → scan the whole repo via `git ls-files`" fallback described above mainly matters when you invoke the script directly or via Bazel (see below) without passing any files.

4. Run `pre-commit run --all-files` once after adding the hook to confirm your repo is clean, then let it run on every commit as usual.

### How-to: Bazel

1. Declare a dependency on this repository's Bazel module in your `MODULE.bazel`:

   ```python
   bazel_dep(name = "score_tools", version = "<version>")
   ```

   If this module isn't available from the Bazel Central Registry yet, add the registry that hosts it to your `.bazelrc` first, e.g.:

   ```python
   common --registry=https://raw.githubusercontent.com/eclipse-score/bazel_registry/main/
   common --registry=https://bcr.bazel.build
   ```

2. In the `BUILD` file where you want the check available (typically your repo root), load and instantiate the `copyright_checker` macro:

   ```python
   load("@score_tools//cr_checker:cr_checker.bzl", "copyright_checker")

   copyright_checker(
       name = "copyright_check",
       # template: optional, path to your own templates.ini; omit to use the bundled default templates.
       # template = "//:templates.ini",
       # exclusion = "//:copyright_exclusions.txt",   # optional, list of files to ignore
       # extensions = ["py", "cpp", "h"],             # optional, overrides the built-in list
       visibility = ["//visibility:public"],
   )
   ```

   This defines two runnable targets plus convenience aliases:
   - `bazel test //:copyright_check.check` (aliased as `bazel run //:copyright-check`) — reports missing/duplicate headers.
   - `bazel run //:copyright_check.fix` (aliased as `bazel run //:copyright-fix`) — inserts missing headers.

3. There is no `srcs` parameter anymore — the macro doesn't take a file list at all. Every invocation shells out to `git ls-files --cached --other --exclude-standard` against your workspace and filters the result by `extensions`, so it always covers the whole repository (minus whatever `exclusion` lists).

4. Because of that, **use `bazel run`, not `bazel build`/`bazel test`**: the tool relies on the `BUILD_WORKSPACE_DIRECTORY` environment variable to find your real workspace root, and Bazel only sets that variable for `run`. It also needs `git` available on `PATH` at run time.

#### Parameters

- **name**: Unique identifier for the rule.
- **visibility**: Defines which targets can access this rule.
- **template** (optional): Path to the copyright header template. Defaults to the tool's bundled `templates.ini` if omitted.
- **exclusion** (optional): Path to the project-specific exclusion file.
- **extensions** (optional): List of file extensions to filter files. Defaults to the tool's built-in list.
- **debug** (optional): Enables verbose logging for debugging.
- **fix** (optional): Automatically applies fixes instead of just reporting issues.
- **target_compatible_with** (optional): Standard Bazel platform-compatibility constraint list.
