# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of score-tools
- Support for Python 3.12 and 3.13
- Ruff wrapper with Eclipse S-CORE defaults
  - Automatic config discovery (ruff.toml, .ruff.toml, pyproject.toml)
  - Bundled Eclipse S-CORE Ruff defaults
- Installable via pip/pipx/uvx with extras: `[ruff]`, `[all]`
- CLI flags: `--score-config`, `--print-config`, `--help`, `--version`
- Type hints (PEP 561 py.typed marker)
- Comprehensive test suite with pytest
- CI/CD with GitHub Actions (multi-OS, multi-Python version)
- Pre-commit hooks configuration
- Apache-2.0 license
- Documentation (README, CONTRIBUTING, CODE_OF_CONDUCT)

[Unreleased]: https://github.com/eclipse-score/tools/compare/...HEAD
