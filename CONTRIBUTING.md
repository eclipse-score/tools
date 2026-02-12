# Contributing to score-tools

Thanks for your interest in contributing! This project is part of [Eclipse S-CORE](https://projects.eclipse.org/projects/automotive.score) and aims to provide thin wrappers around common developer tools with S-CORE defaults.

## Getting started

- Python 3.12 or 3.13
- Create a virtual environment and install dev deps:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,ruff]'
```

## Running tests

```bash
pytest -q
```

## Making changes

- Keep wrappers thin and predictable.
- Prefer conservative defaults. Project configs should always take precedence when present.
- Avoid hard dependencies in the base package; use extras like `[ruff]` and `[all]`.

## Releasing

- Bump `__version__` in `src/score_tools/__init__.py` and `pyproject.toml` as appropriate.
- Build and publish via your preferred workflow (e.g., `hatch build` or `python -m build` followed by `twine upload`).

## Code of Conduct

This project follows the Contributor Covenant. See `CODE_OF_CONDUCT.md`.

## License

By contributing to this project, you agree that your contributions will be licensed under the Apache License 2.0.