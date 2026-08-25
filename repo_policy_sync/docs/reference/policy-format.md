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

when:
  bazel:
    direct_module_dependencies: [score_docs_as_code]

ensure:
  - type: ensure_line
    path: .gitignore
    line: _build
    replace_line_globs: ["*_build*"]
    rationale: Legacy generated-build entries are no longer used.

  - type: ensure_no_such_file
    path: docs/ubproject.toml
```

`title` and a non-empty `ensure` list are required. `description` and `when`
are optional. Paths must be non-empty and
repository-relative; absolute paths and paths containing `..` are rejected.

The policy directory name is the current, stable policy ID. It determines the
policy-owned branch name and pull-request marker. Renaming a policy is a
breaking change in the first version: update its callers and any existing
policy branch or pull request to the new ID before rollout.

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
`>`, and versions must use `major.minor.patch` form. A dependency rename
configured with `replacement_name` is an implicit legacy trigger, so the old
module name does not need to be repeated in this condition list.

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

### `ensure_no_such_file`

```yaml
- type: ensure_no_such_file
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

### `synchronize_workflow`

```yaml
- type: synchronize_workflow
  source: docs.yml
  reusable_workflow: eclipse-score/cicd-workflows/.github/workflows/docs.yml
  minimum_version: 0.0.3
  required_triggers:
    - pull_request
    - push
    - merge_group
    - release
    - workflow_dispatch
  workflow_run:
    path: .github/workflows/docs-publish.yml
    source: docs-publish.yml
```

Finds exactly one workflow below `.github/workflows` that calls the configured
reusable workflow. The existing workflow remains the structural source of
truth: its top-level `name`, local jobs, and job `with` values are preserved.
Missing trigger events are copied from the policy asset, and the reusable job
and permissions are synchronized. A lower reusable-workflow version is updated
to the policy asset's ref; a higher version or unknown immutable ref is
preserved.

When `workflow_run` is configured, its workflow is synchronized in the same
operation. The `on.workflow_run.workflows` value is derived from the selected
build workflow's actual top-level `name`, so renaming or preserving a local
workflow name cannot silently break the workflow-run companion.

The operation rejects zero or multiple matching build workflows and rejects
workflow assets that omit one of the declared `required_triggers`.

### `synchronize_file`

```yaml
- type: synchronize_file
  path: .devcontainer/run-tool
  source: run-tool
  executable: true
```

Synchronizes a repository-relative UTF-8 file with a checked-in UTF-8 source
asset located relative to `policy.yml`. Missing or changed files are replaced;
when `executable: true` is set, the target is also made executable. This keeps
policy runs deterministic: update the checked-in source asset when intentionally
adopting a newer upstream version.

For reusable GitHub workflow assets, `preserve_reusable_workflow_refs` can keep
an existing ref when it is at least the policy baseline or cannot be safely
ordered. A lower semantic version is replaced by the ref in the source asset;
branches and unknown immutable refs are preserved conservatively:

```yaml
preserve_reusable_workflow_refs:
  - workflow: eclipse-score/cicd-workflows/.github/workflows/docs.yml
    minimum_version: 0.0.3
```

Policies that set `preserve_workflow_content: true` merge the standard
workflow envelope into an existing workflow while retaining local jobs and
their `with` parameters. The source asset is authoritative for top-level
`permissions`: an explicit block is copied into the target, while an omitted
block removes any existing top-level permissions. Jobs needing different
permissions must declare them at job level.

### `synchronize_devcontainer_version`

```yaml
- type: synchronize_devcontainer_version
  dockerfile: .devcontainer/Dockerfile
  module_file: MODULE.bazel
  image: ghcr.io/eclipse-score/devcontainer
  module_name: score_devcontainer
```

Synchronizes one Docker `FROM image:vX.Y.Z` instruction with one direct
`bazel_dep(name = "module_name", version = "X.Y.Z")` declaration. It retains
the higher numeric version and rewrites only the lower version text. Missing,
duplicate, or unsupported declarations raise an error rather than modifying an
ambiguous repository.

### `ensure_bazel_dependency`

```yaml
- type: ensure_bazel_dependency
  dockerfile: .devcontainer/Dockerfile
  module_file: MODULE.bazel
  image: ghcr.io/eclipse-score/devcontainer
  module_name: score_devcontainer
```

Ensures that `MODULE.bazel` contains one valid direct dependency for the module
named by `module_name`. The dependency version is read from the single
`FROM image:vX.Y.Z` instruction in `dockerfile`. A missing dependency is added;
an existing valid dependency is left for a separate synchronization policy.
Duplicate, malformed, or unsupported declarations raise an error.

### `synchronize_bazel_dependencies`

```yaml
- type: synchronize_bazel_dependencies
  module_file: MODULE.bazel
  dependencies:
    - name: score_platform
      version: 0.7.0
      optional: true
    - name: score_process
      replacement_name: score_process_description
      version: 2.1.0
      optional: true
    - name: score_baselibs
      version: 0.2.11
      override: bf0020fefef402642dcb0092832e03ba4267d739
      remote: https://github.com/eclipse-score/baselibs.git
    build_file_names: [BUILD, BUILD.bazel]
```

Synchronizes the listed direct `bazel_dep` declarations to at least their
target versions. A dependency with `replacement_name` is renamed and moved to
the target version. Set `optional: true` for dependencies that are only used
by some repositories; absent optional dependencies are skipped. Every matching
BUILD file is scanned recursively and legacy references to the old module name
are renamed as well. Missing required, duplicate, or malformed configured
dependencies raise an error. `override` together with `remote` adds or updates
a matching `git_override(module_name = "...", commit = "...", remote = "...")`
declaration. Overrides for absent optional dependencies are skipped.

### `migrate_devcontainer_json`

```yaml
- type: migrate_devcontainer_json
  sources: [.devcontainer.json, .devcontainer/devcontainer.json]
  destination: .devcontainer/devcontainer.json
  dockerfile: .devcontainer/Dockerfile
  image: ghcr.io/eclipse-score/devcontainer
  dockerfile_comment: "# Use Dockerfile to get dependabot version bumps after new image is released"
  copyright_header_source: copyright-header
  copyright_header_organization: eclipse-score
```

Moves an image-based devcontainer configuration containing exactly one
`image: image:vX.Y.Z` entry to `destination`, replaces the image entry with a
`build` entry pointing to the Dockerfile, and removes the source file. Existing
Dockerfiles or destination files with different contents and unsupported image
tags are rejected rather than overwritten. When `copyright_header_organization`
is set, the configured header from `copyright_header_source` is added only when
the policy is applied to `copyright_header_organization`. The Dockerfile
comment is configured with `dockerfile_comment` so the operation does not
assume a particular organization or tooling convention.
