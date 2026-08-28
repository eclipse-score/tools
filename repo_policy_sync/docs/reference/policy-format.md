<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Policy format reference

Each bundled policy has its own directory:

```text
policies/
  example-policy/
    policy.yml
    example-case/
      before/
      after/
```

`policy.yml` is the policy definition. Each example case is an executable
before/after repository tree: the fixture test applies the real policy to
`before/`, compares the result with `after/`, and confirms that `after/` is
already compliant. Keep cases small and name them for the behavior they show.

The policy directory is scanned recursively for `policy.yml` files. Every
policy must use the directory layout above so its directory name can provide
its ID.

See the [bundled policy overview](../../policies/README.md) for the policies
shipped with this repository and their intended lifecycle.

## Policy schema

```yaml
title: "chore(docs): remove legacy score_docs_as_code configuration"
description: Replace legacy documentation configuration.
legacy_names: [old-policy-name]

when:
  bazel:
    direct_module_dependencies: [score_docs_as_code]

ensure:
  - type: ensure_line
    path: .gitignore
    line: _build
    replace_line_globs: ["*_build*"]
    rationale: Legacy generated-build entries are no longer used.

  - type: remove_file
    path: docs/ubproject.toml
```

`title` and a non-empty `ensure` list are required. `description`,
`legacy_names`, and `when` are optional. `legacy_names` is a list of former
policy IDs whose pull-request markers are still recognized for this policy.
Pull requests are located through the `repo-policy-sync` label and then matched
by marker, so this also supports historical branch names and the former
`repo-sync` marker format. New pull requests use the current directory name
and marker.
Paths must be non-empty and
repository-relative; absolute paths and paths containing `..` are rejected.

The policy directory name is the current, stable policy ID. It determines the
policy-owned branch name and pull-request marker. When a policy is renamed,
list the former directory name in `legacy_names` so an existing policy branch
or pull request can be updated in place.

IDs are normalized to lowercase branch slugs by replacing non-alphanumeric
characters with hyphens. The loaded policy catalogue must not contain two
different IDs with the same normalized slug (for example, `foo_bar` and
`foo-bar`).

## Follow-up commands

`after_apply` runs a command after the policy has changed a repository. Each
command runs only when its `when_file_exists` path exists. Commands are lists,
not shell strings, and run from the repository root. Their conditional file is
included in the planned changes and commit, so generated files can be reviewed
in the policy pull request.

```yaml
after_apply:
  - command: [bazel, mod, deps]
    when_file_exists: MODULE.bazel.lock
    when_path_changed: MODULE.bazel
    description: Regenerate MODULE.bazel.lock with `bazel mod deps`.
```

`when_path_changed` is optional. When present, the command is planned and run
only if the policy changed that repository-relative path. This avoids
regenerating derived files after unrelated policy changes. Forced follow-up
commands still run when their conditional file exists.

## Conditions

`when.file_exists` requires one repository-relative file to exist.

`when.bazel.direct_module_dependencies` requires every listed module to be a
direct `bazel_dep(name = "…")` declaration in `MODULE.bazel`. The optional
`when.bazel.any_direct_module_dependencies` field requires at least one of its
listed modules to be direct, which is useful during module renames.

The optional `when.bazel.any_direct_module_conditions` field accepts a
non-empty list of version comparisons such as
`score_platform < 0.7.0`. The list is combined with OR: one matching
condition is enough. Supported operators are `<`, `<=`, `==`, `!=`, `>=`, and
`>`, and versions must use `major.minor.patch` form.

```yaml
when:
  bazel:
    direct_module_dependencies: [score_platform, score_docs_as_code]
    any_direct_module_dependencies: [score_process, score_process_description]
    any_direct_module_conditions:
      - score_platform < 0.7.0
      - score_docs_as_code < 8.0.0
      - score_process_description < 2.1.1
```

`when.file_contains` requires a repository-relative UTF-8 text file to match a
Python regular expression. It accepts `path` and `pattern` fields. Multiple
conditions are combined with AND; `when.file_contains_any` accepts a non-empty
list of the same conditions and matches when at least one does. A path may also
contain `*`, `?`, or `[` glob syntax; for example, `**/BUILD` checks every
matching file. A repository that does not match is neither an error nor a
change.

## Built-in operations

The [built-in operations catalogue](../../operations/README.md) provides a
quick overview of every supported operation. This section remains the
authoritative reference for their schemas and detailed behavior.

Every operation accepts an optional `rationale` string. When that operation
changes a repository, the generated pull request renders the rationale as a
nested bullet below the corresponding change. Keep it concise and explain why
the change is safe or necessary.

### `ensure_line`

```yaml
- type: ensure_line
  path: .gitignore
  line: _build
  replace_lines: [/_build]
  replace_line_globs: ["*_build*"]
```

Ensures one exact UTF-8 text line occurs exactly once. It removes exact
`replace_lines`, whole-line glob matches from `replace_line_globs`, and
duplicates of `line`. It inserts the desired line at the first removed or
existing match; without one, it appends the line. A missing file is created.
Globs use `*`, `?`, and `[...]` and match complete raw lines; comments and
whitespace have no special meaning.

### `remove_file`

```yaml
- type: remove_file
  path: docs/obsolete.toml
```

Deletes a file when it exists. A missing file is compliant. The operation
refuses to delete a directory.

### `replace_regex`

```yaml
- type: replace_regex
  path: MODULE.bazel
  pattern: 'legacy = "([^"]+)"'
  replacement: 'current = "\1"'
```

Applies Python `re.sub` semantics to the complete UTF-8 text file: every
non-overlapping match is replaced, with no implicit flags. Use inline regex
flags such as `(?s)` when needed. Capture groups in `replacement` use `\1`
syntax; invalid replacement backreferences are rejected while the policy is
loaded. A missing file, no match, or replacement that produces identical text
is compliant. Prefer narrow patterns and cover every intended layout with an
executable example.

### `ensure_minimum_version`

```yaml
- type: ensure_minimum_version
  path: .bazelversion
  minimum_version: 8.6.0
```

Ensures an existing `major.minor.patch` version is at least the specified
numeric version. Lower versions are replaced with `minimum_version`; equal and
higher versions are unchanged. A missing file is compliant. A file containing
any other format is rejected so the policy cannot accidentally overwrite an
unknown version scheme.
