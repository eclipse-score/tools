# DR1 - Repo Layout

## Decision

We use a tool-centric repo layout, where each tool owns its source code, tests, documentation, and Bazel integration. Tools may use different languages and build systems.

As it's not clear yet whether all future tools will be Python packages, we will not use a standard Python package layout.

## Options

### Option 1 - Tool Centric
*Each tool owns its source code, tests, documentation, and Bazel integration. Tools may use different languages and build systems.*

```
/pyproject.toml
/uv.lock
/cr_checker
    /README.md
    /BUILD
    /cr_checker.bzl
    /src
        /__init__.py
    /tests
        /test_core.py
/sync
    /README.md
    /src
        /__init__.py
    /tests
        /test_core.py
```

### Option 2 - Python
*All Python packages live below a shared src directory. Tests are grouped by package below a shared tests directory.*
**Not clear what to do with non-Python packages.**

```
/pyproject.toml
/uv.lock
/src
    /cr_checker
        /README.md
        /__init__.py
        /BUILD
        /cr_checker.bzl
    /sync
        /README.md
        /__init__.py
/tests
    /cr_checker
        /test_core.py
```
