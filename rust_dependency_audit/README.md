<!-- ----------------------------------------------------------------------------
  Copyright (c) 2026 Contributors to the Eclipse Foundation

  See the NOTICE file(s) distributed with this work for additional
  information regarding copyright ownership.

  This program and the accompanying materials are made available under the
  terms of the Apache License Version 2.0 which is available at
  https://www.apache.org/licenses/LICENSE-2.0

  SPDX-License-Identifier: Apache-2.0
----------------------------------------------------------------------------- -->

# Rust Dependency Audit

Audit Rust dependencies across repositories in a GitHub organization and cross-reference
them with the central source of truth: [`eclipse-score/score-crates`](https://github.com/eclipse-score/score-crates).

Talks to GitHub through the REST API or `gh` CLI, with zero non-stdlib Python dependencies,
producing reports in **HTML (GitHub Pages)**, **JSON**, and **Markdown**.

## Features

- **Organization-wide Scan**: Iterates through all repositories in the organization (defaults to `eclipse-score`).
- **Rust Manifest Discovery**: Detects all `Cargo.toml` and `Cargo.lock` files across repository subdirectories and workspaces.
- **Dependency Classification**:
  - `MANAGED`: Defined in `score-crates` with matching version.
  - `VERSION_MISMATCH`: Defined in `score-crates`, but the repository specifies a different version.
  - `UNMANAGED`: Direct external crate not yet onboarded into `score-crates`.
- **Interactive GitHub Pages Dashboard**: Self-contained HTML report with live filtering, search, metrics, and collapsible breakdowns.
- **Zero Token Leakage**: Credentials (`GITHUB_TOKEN` / `GH_TOKEN`) are strictly masked and never stored, logged, or included in report artifacts.

## CLI Usage

```bash
# Run organization scan against eclipse-score and output to site/
uv run score-rust-dependency-audit --org eclipse-score --output-dir site/

# Run against a different organization or reference repository
uv run score-rust-dependency-audit --org my-org --reference-repo my-org/score-crates --output-dir public/

# Offline / Local scan mode (scan local directories of clones)
uv run score-rust-dependency-audit --local-scan-dir /path/to/repos --local-reference-dir /path/to/score-crates --output-dir site/
```

### Command Line Options

| Option | Default | Description |
| :--- | :--- | :--- |
| `--org` | `eclipse-score` | GitHub organization to audit |
| `--reference-repo` | `eclipse-score/score-crates` | Central repository containing approved crates (`MODULE.bazel` / `BUILD`) |
| `--output-dir` | `site` | Directory to save `index.html`, `rust_dependency_audit.json`, and `rust_dependency_audit.md` |
| `--include-archived` | `False` | Include archived repositories in the audit |
| `--markdown-output` | `None` | Custom path to save Markdown report |
| `--json-output` | `None` | Custom path to save JSON report |
| `--html-output` | `None` | Custom path to save HTML report |
| `--local-scan-dir` | `None` | Scan local directory of repositories instead of GitHub API |
| `--local-reference-dir` | `None` | Directory containing local `MODULE.bazel` and `BUILD` of reference repository |

## Library Usage

```python
from rust_dependency_audit.src.auditor import audit_organization
from rust_dependency_audit.src.reporter import generate_html_report, generate_json_report, generate_markdown_report
from rust_dependency_audit.src.score_crates import build_score_crates_reference

# Load reference and perform audit
ref = build_score_crates_reference(module_bazel_text, build_text)
report = audit_organization(repos_data, ref)

html_page = generate_html_report(report)
```

## GitHub Pages Deployment

To automatically run the audit and host the interactive report on GitHub Pages, use the workflow defined in `.github/workflows/rust-dependency-audit.yml`.
