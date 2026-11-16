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

"""Parser and model for eclipse-score/score-crates reference dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class ScoreCrateSpec:
    """Represents a crate.spec definition from score-crates MODULE.bazel."""

    package: str
    version: str | None = None
    features: list[str] = field(default_factory=list)
    git: str | None = None
    rev: str | None = None


@dataclass
class ScoreCratesReference:
    """Container for score-crates defined packages and aliases."""

    crates: dict[str, ScoreCrateSpec] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def contains_crate(self, name: str) -> bool:
        """Checks if a crate is defined in score-crates (by name, normalized name, or alias)."""
        normalized = name.replace("-", "_")
        return (
            name in self.crates
            or normalized in self.crates
            or name in self.aliases
            or normalized in self.aliases
        )

    def get_spec(
        self, name: str, visited: set[str] | None = None
    ) -> ScoreCrateSpec | None:
        """Retrieves spec for a crate name or alias with cycle detection."""
        if visited is None:
            visited = set()
        if name in visited:
            return None
        visited.add(name)

        normalized = name.replace("-", "_")
        if name in self.crates:
            return self.crates[name]
        if normalized in self.crates:
            return self.crates[normalized]
        # Check alias
        if name in self.aliases:
            target = self.aliases[name].split(":")[-1]
            return self.get_spec(target, visited)
        if normalized in self.aliases:
            target = self.aliases[normalized].split(":")[-1]
            return self.get_spec(target, visited)
        return None

    def get_version(self, name: str) -> str | None:
        """Retrieves declared version for a crate."""
        spec = self.get_spec(name)
        return spec.version if spec else None


def _extract_function_calls(content: str, func_name: str) -> list[str]:
    """Extracts argument bodies for calls to func_name(...) handling comments, strings, and nesting."""
    calls: list[str] = []
    i = 0
    n = len(content)
    in_string: str | None = None
    escape = False

    while i < n:
        ch = content[i]

        if in_string:
            if escape:
                escape = False
                i += 1
            elif ch == "\\":
                escape = True
                i += 1
            elif in_string.startswith('"""') and content.startswith('"""', i):
                in_string = None
                i += 3
            elif in_string.startswith("'''") and content.startswith("'''", i):
                in_string = None
                i += 3
            elif ch == in_string:
                in_string = None
                i += 1
            else:
                i += 1
            continue

        # In code (outside string literals)
        if ch == "#":
            next_nl = content.find("\n", i)
            if next_nl == -1:
                break
            i = next_nl + 1
            continue

        if content.startswith('"""', i):
            in_string = '"""'
            i += 3
            continue
        if content.startswith("'''", i):
            in_string = "'''"
            i += 3
            continue
        if ch in ('"', "'"):
            in_string = ch
            i += 1
            continue

        # Check for candidate func_name(...) at word boundary
        is_word_boundary = (i == 0) or (
            not (content[i - 1].isalnum() or content[i - 1] == "_")
        )
        if is_word_boundary and content.startswith(func_name, i):
            j = i + len(func_name)
            while j < n and content[j].isspace():
                j += 1
            if j < n and content[j] == "(":
                # Genuine function call found; extract balanced arguments
                start_args = j + 1
                k = start_args
                depth = 1
                arg_in_string: str | None = None
                arg_escape = False

                while k < n and depth > 0:
                    c = content[k]
                    if arg_in_string:
                        if arg_escape:
                            arg_escape = False
                            k += 1
                        elif c == "\\":
                            arg_escape = True
                            k += 1
                        elif arg_in_string.startswith('"""') and content.startswith(
                            '"""', k
                        ):
                            arg_in_string = None
                            k += 3
                        elif arg_in_string.startswith("'''") and content.startswith(
                            "'''", k
                        ):
                            arg_in_string = None
                            k += 3
                        elif c == arg_in_string:
                            arg_in_string = None
                            k += 1
                        else:
                            k += 1
                        continue

                    # Inside call arguments, outside string literals
                    if c == "#":
                        next_nl = content.find("\n", k)
                        if next_nl == -1:
                            break
                        k = next_nl + 1
                        continue

                    if content.startswith('"""', k):
                        arg_in_string = '"""'
                        k += 3
                        continue
                    if content.startswith("'''", k):
                        arg_in_string = "'''"
                        k += 3
                        continue
                    if c in ('"', "'"):
                        arg_in_string = c
                        k += 1
                        continue

                    if c == "(":
                        depth += 1
                        k += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            calls.append(content[start_args:k])
                            k += 1
                            break
                        k += 1
                    else:
                        k += 1

                # Continue scanning after the function call
                i = k
                continue

        i += 1

    return calls


def parse_score_crates_module_bazel(content: str) -> dict[str, ScoreCrateSpec]:
    """Parses crate.spec(...) calls from score-crates MODULE.bazel."""
    specs: dict[str, ScoreCrateSpec] = {}

    for block in _extract_function_calls(content, "crate.spec"):
        # Strip comments so comments don't interfere with parameter extraction
        clean_lines = [line.split("#")[0] for line in block.splitlines()]
        clean_block = "\n".join(clean_lines)

        pkg_match = re.search(r'package\s*=\s*["\']([^"\']+)["\']', clean_block)
        if not pkg_match:
            continue
        package = pkg_match.group(1)

        ver_match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', clean_block)
        version = ver_match.group(1) if ver_match else None

        git_match = re.search(r'git\s*=\s*["\']([^"\']+)["\']', clean_block)
        git = git_match.group(1) if git_match else None

        rev_match = re.search(r'rev\s*=\s*["\']([^"\']+)["\']', clean_block)
        rev = rev_match.group(1) if rev_match else None

        features: list[str] = []
        features_match = re.search(r"features\s*=\s*\[(.*?)\]", clean_block, re.DOTALL)
        if features_match:
            raw_feats = features_match.group(1)
            features = re.findall(r'["\']([^"\']+)["\']', raw_feats)

        specs[package] = ScoreCrateSpec(
            package=package,
            version=version,
            features=features,
            git=git,
            rev=rev,
        )

    return specs


def parse_score_crates_build(content: str) -> dict[str, str]:
    """Parses alias(...) definitions from score-crates BUILD."""
    aliases: dict[str, str] = {}

    for block in _extract_function_calls(content, "alias"):
        clean_lines = [line.split("#")[0] for line in block.splitlines()]
        clean_block = "\n".join(clean_lines)

        name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', clean_block)
        actual_match = re.search(r'actual\s*=\s*["\']([^"\']+)["\']', clean_block)

        if name_match and actual_match:
            aliases[name_match.group(1)] = actual_match.group(1)

    return aliases


def build_score_crates_reference(
    module_bazel_content: str, build_content: str = ""
) -> ScoreCratesReference:
    """Creates a ScoreCratesReference from MODULE.bazel and optional BUILD contents."""
    crates = parse_score_crates_module_bazel(module_bazel_content)
    aliases = parse_score_crates_build(build_content) if build_content else {}
    return ScoreCratesReference(crates=crates, aliases=aliases)
