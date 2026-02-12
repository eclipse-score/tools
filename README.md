# score-tools

[![CI](https://github.com/eclipse-score/tools/actions/workflows/installer-and-tests.yml/badge.svg?branch=main)](https://github.com/eclipse-score/tools/actions/workflows/installer-and-tests.yml)

Eclipse S-CORE tool wrappers and defaults for a consistent, batteries-included developer experience.

Today this includes:

- score-ruff — a thin wrapper around Ruff that applies S-CORE defaults when no project config is present.

Planned: additional wrappers following the same pattern.

## Install

Use one of the following:

```bash
# With pip (extras to pull tools)
pip install 'score-tools[ruff]'

# With pipx
pipx install 'score-tools[ruff]'

# With uv
uv pip install 'score-tools[ruff]'

# Run-once without installing (uvx)
# Note: use --from to include extras so Ruff is available
uvx --from 'score-tools[ruff]' score-ruff --version
```

Python 3.10–3.13 are supported.

## Usage

score-ruff will:

- Use your project's config if it finds one (ruff.toml/.ruff.toml or pyproject.toml with [tool.ruff]).
- Otherwise, apply S-CORE's default Ruff configuration bundled in the package.

Examples:

```bash
# Lint the current directory with defaults if no config is present
score-ruff

# Target a specific file or directory
score-ruff src/

# Print the bundled defaults
score-ruff --score-config

# Print which config would be used
score-ruff --print-config

# Show Ruff help through the wrapper
score-ruff --help
```

## Extras

- ruff — installs Ruff alongside the wrapper
- all — currently an alias for ruff (future tools will be included here)

Install specific tools via extras, e.g.:

```bash
pipx install 'score-tools[ruff]'
```

## Development

Clone the repository and set up your development environment:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e '.[dev,all]'
pytest
```

## License

This project is part of [Eclipse S-CORE](https://projects.eclipse.org/projects/automotive.score) and is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
