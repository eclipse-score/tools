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

"""Cargo.toml and Cargo.lock parser."""

from __future__ import annotations

from dataclasses import dataclass, field
import tomllib
from typing import Any


@dataclass
class DeclaredDependency:
    """Represents a dependency declared in Cargo.toml."""

    name: str
    version_req: str | None = None
    features: list[str] = field(default_factory=list)
    is_workspace: bool = False
    is_git: bool = False
    is_path: bool = False
    dep_type: str = "normal"  # "normal", "dev", "build"
    git_url: str | None = None
    git_rev: str | None = None
    alias: str | None = None


@dataclass
class CargoTomlPackage:
    """Represents a Rust package or workspace parsed from Cargo.toml."""

    name: str
    version: str | None = None
    path: str = ""
    is_workspace_root: bool = False
    dependencies: list[DeclaredDependency] = field(default_factory=list)


@dataclass
class CargoLockPackage:
    """Represents a resolved package from Cargo.lock."""

    name: str
    version: str
    source: str | None = None
    dependencies: list[str] = field(default_factory=list)


def _parse_dep_spec(name: str, spec: Any, dep_type: str) -> DeclaredDependency:
    """Parses a dependency entry which can be a version string or a table."""
    if isinstance(spec, str):
        return DeclaredDependency(
            name=name,
            version_req=spec,
            dep_type=dep_type,
        )

    if isinstance(spec, dict):
        actual_name = str(spec.get("package", name))
        alias = name if actual_name != name else None
        version_req = spec.get("version")
        features = list(spec.get("features", []))
        is_workspace = spec.get("workspace", False) is True
        git_url = spec.get("git")
        git_rev = spec.get("rev") or spec.get("tag") or spec.get("branch")
        is_git = bool(git_url or "git" in spec)
        is_path = "path" in spec

        return DeclaredDependency(
            name=actual_name,
            version_req=str(version_req) if version_req is not None else None,
            features=features,
            is_workspace=is_workspace,
            is_git=is_git,
            is_path=is_path,
            dep_type=dep_type,
            git_url=str(git_url) if git_url is not None else None,
            git_rev=str(git_rev) if git_rev is not None else None,
            alias=alias,
        )

    return DeclaredDependency(name=name, dep_type=dep_type)


def parse_cargo_toml(content: str, path: str = "") -> CargoTomlPackage:
    """Parses Cargo.toml content into a CargoTomlPackage dataclass."""
    data = tomllib.loads(content)
    package_data = data.get("package", {})

    is_workspace_root = "workspace" in data and not package_data
    pkg_name = package_data.get("name")
    if not pkg_name:
        pkg_name = "workspace_root" if is_workspace_root else "unknown"

    pkg_version = package_data.get("version")

    dependencies: list[DeclaredDependency] = []

    # Parse dependencies sections
    dep_sections = [
        ("dependencies", "normal"),
        ("dev-dependencies", "dev"),
        ("build-dependencies", "build"),
    ]

    for section_name, dep_type in dep_sections:
        deps_dict = data.get(section_name, {})
        if isinstance(deps_dict, dict):
            for dep_name, dep_spec in deps_dict.items():
                dependencies.append(_parse_dep_spec(dep_name, dep_spec, dep_type))

    # Parse [workspace.dependencies]
    workspace_data = data.get("workspace", {})
    if isinstance(workspace_data, dict):
        ws_deps = workspace_data.get("dependencies", {})
        if isinstance(ws_deps, dict):
            for dep_name, dep_spec in ws_deps.items():
                dependencies.append(_parse_dep_spec(dep_name, dep_spec, "workspace"))

    # Parse target-specific dependencies [target.'...'.dependencies]
    target_data = data.get("target", {})
    if isinstance(target_data, dict):
        for _target_triple, target_val in target_data.items():
            if isinstance(target_val, dict):
                for section_name, dep_type in dep_sections:
                    deps_dict = target_val.get(section_name, {})
                    if isinstance(deps_dict, dict):
                        for dep_name, dep_spec in deps_dict.items():
                            dependencies.append(
                                _parse_dep_spec(dep_name, dep_spec, dep_type)
                            )

    return CargoTomlPackage(
        name=pkg_name,
        version=str(pkg_version) if pkg_version is not None else None,
        path=path,
        is_workspace_root=is_workspace_root,
        dependencies=dependencies,
    )


def parse_cargo_lock(content: str, path: str = "") -> list[CargoLockPackage]:
    """Parses Cargo.lock content into a list of CargoLockPackage dataclasses."""
    data = tomllib.loads(content)
    packages_data = data.get("package", [])

    packages: list[CargoLockPackage] = []
    for pkg in packages_data:
        name = pkg.get("name", "")
        version = str(pkg.get("version", ""))
        source = pkg.get("source")
        raw_deps = pkg.get("dependencies", [])
        clean_deps: list[str] = []
        for dep in raw_deps:
            # Dependencies in Cargo.lock can be "package_name version" or just "package_name"
            clean_deps.append(dep.split(" ")[0])

        packages.append(
            CargoLockPackage(
                name=name,
                version=version,
                source=source,
                dependencies=clean_deps,
            )
        )

    return packages
