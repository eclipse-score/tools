<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Coverage Report

Merge the LCOV reports of several test suites and show, as a pull request
comment, how coverage changed against the base branch. No external service,
no GitHub App: everything runs in your own workflows.

Reads LCOV only, so it works for every language whose coverage tool writes
LCOV (coverage.py `coverage lcov`, `bazel coverage`, llvm-cov, gcov/lcov).
Zero non-stdlib Python dependencies.

## What a pull request gets

- Lines and branches per test suite and combined, with the change (Δ)
  against the base branch at the commit the pull request started from (its
  merge base). If that run is missing, against the newest base run, and the
  comment says so.
- Collapsed: files whose coverage changed (biggest drop first), per package,
  and files no test runs at all.
- One comment, updated on every push.

Coverage informs; it does not gate. Nothing here fails a build because
coverage went down.

## CLI

```bash
uvx --from "git+https://github.com/eclipse-score/tools#subdirectory=coverage_report" \
    score-coverage-report "Unit tests=coverage-unit.lcov" "Scenarios=out/docs-*.lcov" \
    --json coverage.json --markdown summary.md --compare-with main/coverage.json
```

Each positional argument is one suite, `LABEL=PATH`. `PATH` is an LCOV file,
a directory (searched recursively for `*.lcov` and `*.info`) or a glob; all
reports of a suite are merged. A suite without reports shows as "no data",
never as 0%.

| Option | Effect |
| --- | --- |
| `--json FILE` | Write the numbers. Store this on the base branch; pull requests compare against it. |
| `--markdown FILE` | Write the summary. |
| `--compare-with FILE` | JSON of the base branch; adds the Δ columns. Missing or unreadable: a note, not an error. |
| `--base-commit SHA` | Commit the comparison is meant against (the merge base). A JSON of another commit gets a note. |
| `--title`, `--note`, `--base-name` | Heading, extra lines below the totals (repeatable), name of the base branch. |
| `--package-depth N` | Group files by at most `N` leading directories (default: full directory). |
| `--commit SHA` | Commit the reports belong to (default: `git rev-parse HEAD`). |

A file counts in every suite's denominator, even if only one suite lists it;
see [Producing the reports](#producing-the-reports-run-tests-however-you-need).

With Bazel: `bazel run @score_tools//coverage_report:score-coverage-report -- ...`.

## Producing the reports: run tests however you need

The tool never runs tests. Each test run, started any way you like, writes
LCOV; each run becomes one suite. Runs can be in different jobs, use different
commands, or measure code in subprocesses, as long as they end in LCOV files.

Example: docs-as-code measures its Python code in two very different runs.

| Suite | How it runs | Becomes LCOV with |
| --- | --- | --- |
| Unit tests | `coverage run -m pytest src tools ...` | `coverage combine && coverage lcov -o coverage-unit.lcov` |
| docs.bzl scenarios | pytest starts Bazel, Bazel starts Sphinx; each Sphinx process records coverage through `COVERAGE_PROCESS_START` | `coverage combine && coverage lcov -o coverage-docs-bzl-<suite>.lcov` (one per matrix job) |

Each job uploads its LCOV file as an artifact (`coverage-lcov-unit`,
`coverage-lcov-docs-bzl-cached`, `coverage-lcov-docs-bzl-runtime`), and the
report gets two suites; the glob merges both docs.bzl jobs into one suite:

```text
Unit tests=coverage-lcov/coverage-lcov-unit
docs.bzl scenarios=coverage-lcov/coverage-lcov-docs-bzl-*
```

Other producers work the same: `bazel coverage --combined_report=lcov`
(`bazel-out/_coverage/_coverage_report.dat`, pass the file path), llvm-cov
`export -format=lcov`, or `lcov --capture`.

Two things to get right:

- **Same paths.** Suites are merged by the `SF:` path. All runs must write
  the same path for the same file (e.g. repository-relative `src/app.py`);
  otherwise one file counts twice.
- **One suite lists every file.** Files no run lists are invisible. Let one
  run list all source files, e.g. coverage.py with `source` set, so files no
  test imports show up at 0% in every suite.

## GitHub Actions

Two composite actions. `report` runs in your test workflow; `comment` runs in
a separate `workflow_run` workflow, because pull requests from forks only get
a read-only token.

### 1. In the test workflow

Upload the LCOV files of each test job as artifacts, then add one job:

```yaml
coverage_report:
  needs: [unit_tests, scenario_tests]
  if: ${{ !cancelled() }}
  # Coverage informs, it does not gate.
  continue-on-error: true
  runs-on: ubuntu-24.04
  permissions:
    contents: read
    actions: read # find the base branch's report
  steps:
    - uses: actions/download-artifact@v8
      with:
        pattern: coverage-lcov-*
        path: lcov
    - uses: eclipse-score/tools/coverage_report/actions/report@main
      with:
        title: Python coverage
        suites: |
          Unit tests=lcov/coverage-lcov-unit
          Scenarios=lcov/coverage-lcov-scenarios-*
```

The workflow must also run on pushes to the base branch: those runs upload
the numbers pull requests compare against (`coverage-report-base` artifact).

| Input | Default | |
| --- | --- | --- |
| `suites` | – | One `LABEL=PATH` per line, as on the CLI. |
| `title` | `Coverage` | Heading. |
| `notes` | – | Lines below the totals. |
| `package-depth` | – | As `--package-depth`. |
| `base-branch` | default branch | Branch whose pushes store the comparison numbers. |
| `artifact-name` | `coverage-report` | Must match the `comment` action's. |

The summary also goes to the job summary of every run.

### 2. The comment workflow

```yaml
name: Coverage Comment
on:
  workflow_run:
    workflows: ["PR Checks"] # the workflow that runs the report action
    types: [completed]
permissions:
  contents: read
jobs:
  comment:
    if: >-
      github.event.workflow_run.event == 'pull_request' &&
      github.event.workflow_run.conclusion != 'cancelled'
    runs-on: ubuntu-24.04
    permissions:
      actions: read # download the report
      pull-requests: write # create or update the comment
    steps:
      - uses: eclipse-score/tools/coverage_report/actions/comment@main
```

GitHub runs `workflow_run` workflows from the default branch only: the
comment appears once this file is merged.

The action never checks out or runs pull request code. It only posts the
uploaded Markdown, and only on the pull request whose repository, branch and
commit match the triggering run.

## Limits

- One coverage comment per pull request: a second `report` would replace the
  first one's comment.
- The comparison needs a base branch run whose artifact has not expired
  (90 days by default). Without one, the summary says so.
- If the merge base's run has not finished yet (or never ran, e.g. a skipped
  push), the pull request compares with the newest base run instead; the
  comment says so and names the commit it used. Push again later to get the
  exact comparison.
