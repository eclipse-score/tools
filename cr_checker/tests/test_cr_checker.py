# *******************************************************************************
# Copyright (c) 2024 Contributors to the Eclipse Foundation
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

# unit tests for the shebang handling in the cr_checker module
from __future__ import annotations

import logging
import importlib.util
import pytest
from datetime import datetime
from pathlib import Path


LOGGER = logging.getLogger(__name__)


# load the cr_checker module
def load_cr_checker_module():
    module_path = Path(__file__).resolve().parents[1] / "tool" / "cr_checker.py"
    spec = importlib.util.spec_from_file_location("cr_checker_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load cr_checker module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# load the license template
def load_template(extension: str) -> str:
    cr_checker = load_cr_checker_module()
    template_file = Path(__file__).resolve().parents[1] / "tool" / "templates.ini"
    templates = cr_checker.load_templates(template_file)
    return templates[extension]


# test that offset matches the length of the shebang line including trailing newlines
def test_detect_shebang_offset_counts_trailing_newlines(tmp_path):
    cr_checker = load_cr_checker_module()
    script = tmp_path / "script.py"
    script.write_text(
        "#!/usr/bin/env python3\n\nprint('hi')\n",
        encoding="utf-8",
    )

    offset = cr_checker.detect_shebang_offset(script, "utf-8")

    assert offset == len("#!/usr/bin/env python3\n\n".encode("utf-8"))


@pytest.fixture(
    params=[
        "cpp",
        "c",
        "h",
        "hpp",
        "py",
        "sh",
        "bzl",
        "ini",
        "yml",
        "yaml",
        "BUILD",
        "bazel",
        "rs",
        "rst",
    ]
)
def prepare_test_with_header(request, tmp_path: Path) -> tuple:
    extension = request.param
    test_file = tmp_path / ("file." + extension)
    header_template = load_template(extension)
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    test_file.write_text(
        header + "some content\n",
        encoding="utf-8",
    )
    return test_file, extension, header_template


@pytest.fixture(
    params=[
        "cpp",
        "c",
        "h",
        "hpp",
        "py",
        "sh",
        "bzl",
        "ini",
        "yml",
        "yaml",
        "BUILD",
        "bazel",
        "rs",
        "rst",
    ]
)
def prepare_test_no_header(request, tmp_path: Path) -> tuple:
    extension = request.param
    test_file = tmp_path / ("file." + extension)
    header_template = load_template(extension)
    test_file.write_text(
        "some content\n",
        encoding="utf-8",
    )
    return test_file, extension, header_template, tmp_path


def test_process_files_detects_header(prepare_test_with_header):
    cr_checker = load_cr_checker_module()
    test_file, extension, header_template = prepare_test_with_header

    results = cr_checker.process_files(
        files=[test_file],
        templates={extension: header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0


def test_process_files_detects_missing_header(prepare_test_no_header):
    cr_checker = load_cr_checker_module()
    test_file, extension, header_template, tmp_path = prepare_test_no_header

    results = cr_checker.process_files(
        files=[test_file],
        templates={extension: header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 1


def test_process_files_inserts_missing_header(prepare_test_no_header):
    cr_checker = load_cr_checker_module()
    test_file, extension, header_template, tmp_path = prepare_test_no_header

    results = cr_checker.process_files(
        files=[test_file],
        templates={extension: header_template},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 1
    assert results["fixed"] == 1
    expected_header = header_template.format(year=datetime.now().year)
    assert test_file.read_text(encoding="utf-8").startswith(expected_header)


def test_process_files_skips_exclusion_with_missing_header(prepare_test_no_header):
    cr_checker = load_cr_checker_module()
    test_file, extension, header_template, tmp_path = prepare_test_no_header

    results = cr_checker.process_files(
        files=[test_file],
        templates={extension: header_template},
        fix=False,
        exclusion={str(test_file)},
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0


# test that process_files function validates a license header after the shebang line
def test_process_files_accepts_header_after_shebang(tmp_path):
    cr_checker = load_cr_checker_module()
    script = tmp_path / "script.py"
    header_template = load_template("py")
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    script.write_text(
        "#!/usr/bin/env python3\n" + header + "print('hi')\n",
        encoding="utf-8",
    )

    results = cr_checker.process_files(
        files=[script],
        templates={"py": header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0


# test that process_files function fixes a missing license header after the shebang line
def test_process_files_fix_inserts_header_after_shebang(tmp_path):
    cr_checker = load_cr_checker_module()
    script = tmp_path / "script.py"
    script.write_text(
        "#!/usr/bin/env python3\nprint('hi')\n",
        encoding="utf-8",
    )
    header_template = load_template("py")
    current_year = datetime.now().year

    results = cr_checker.process_files(
        files=[script],
        templates={"py": header_template},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["fixed"] == 1
    assert results["no_copyright"] == 1
    expected_header = header_template.format(year=current_year)
    assert script.read_text(encoding="utf-8") == (
        "#!/usr/bin/env python3\n" + expected_header + "\n" + "print('hi')\n"
    )


# test that process_files function validates a license header without the shebang line
def test_process_files_accepts_header_without_shebang(tmp_path):
    cr_checker = load_cr_checker_module()
    script = tmp_path / "script.py"
    header_template = load_template("py")
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    script.write_text(header + "print('hi')\n", encoding="utf-8")

    results = cr_checker.process_files(
        files=[script],
        templates={"py": header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0


# test that process_files function fixes a missing license header without the shebang
def test_process_files_fix_inserts_header_without_shebang(tmp_path):
    cr_checker = load_cr_checker_module()
    script = tmp_path / "script.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    header_template = load_template("py")
    current_year = datetime.now().year

    results = cr_checker.process_files(
        files=[script],
        templates={"py": header_template},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["fixed"] == 1
    assert results["no_copyright"] == 1
    expected_header = header_template.format(year=current_year)
    assert (
        script.read_text(encoding="utf-8") == expected_header + "\n" + "print('hi')\n"
    )


# test that border lines with different fill characters are accepted (flexible matching)
def test_process_files_accepts_flexible_border(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.cpp"
    current_year = datetime.now().year
    # Use '/' fill chars instead of '*' for border lines
    header = (
        "/////////////////////////////////////////////////////////////////////////////////////\n"
        f" * Copyright (c) {current_year} Author\n"
        " *\n"
        " * See the NOTICE file(s) distributed with this work for additional\n"
        " * information regarding copyright ownership.\n"
        " *\n"
        " * This program and the accompanying materials are made available under the\n"
        " * terms of the Apache License Version 2.0 which is available at\n"
        " * https://www.apache.org/licenses/LICENSE-2.0\n"
        " *\n"
        " * SPDX-"
        "License-Identifier: Apache-2.0\n"
        " /////////////////////////////////////////////////////////////////////////////////////\n"
    )
    test_file.write_text(header + "int main() {}\n", encoding="utf-8")
    header_template = load_template("cpp")

    results = cr_checker.process_files(
        files=[test_file],
        templates={"cpp": header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0


# test that a blank line after the header does not cause a check failure
def test_process_files_accepts_header_with_trailing_blank_line(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    header_template = load_template("py")
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    test_file.write_text(header + "\nsome content\n", encoding="utf-8")

    results = cr_checker.process_files(
        files=[test_file],
        templates={"py": header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0


# test that fix_copyright inserts a blank line after the header
def test_process_files_fix_inserts_trailing_blank_line(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    test_file.write_text("some content\n", encoding="utf-8")
    header_template = load_template("py")
    current_year = datetime.now().year

    cr_checker.process_files(
        files=[test_file],
        templates={"py": header_template},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    expected_header = header_template.format(year=current_year)
    assert test_file.read_text(encoding="utf-8").startswith(expected_header + "\n")


# test that has_duplicate_copyright detects a header that appears twice
def test_has_duplicate_copyright_detects_duplicate(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    header_template = load_template("py")
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    test_file.write_text(header + header + "some content\n", encoding="utf-8")

    result = cr_checker.has_duplicate_copyright(
        test_file, header_template, False, "utf-8", 0
    )

    assert result is True


# test that has_duplicate_copyright returns False for a single header
def test_has_duplicate_copyright_single_header(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    header_template = load_template("py")
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    test_file.write_text(header + "some content\n", encoding="utf-8")

    result = cr_checker.has_duplicate_copyright(
        test_file, header_template, False, "utf-8", 0
    )

    assert result is False


# test that process_files counts duplicate headers separately from missing headers
def test_process_files_detects_duplicate_header(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    header_template = load_template("py")
    current_year = datetime.now().year
    header = header_template.format(year=current_year)
    test_file.write_text(header + header + "some content\n", encoding="utf-8")

    results = cr_checker.process_files(
        files=[test_file],
        templates={"py": header_template},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["duplicate_copyright"] == 1
    assert results["no_copyright"] == 0


# test that has_duplicate_copyright detects two headers with different year ranges
def test_has_duplicate_copyright_detects_different_year_ranges(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    header_template = load_template("py")
    header1 = header_template.format(year="2026")
    header2 = header_template.format(year="2024-2026")
    test_file.write_text(header1 + header2 + "some content\n", encoding="utf-8")

    result = cr_checker.has_duplicate_copyright(
        test_file, header_template, False, "utf-8", 0
    )

    assert result is True


# test that an exclusion.txt file sitting in the root directory is respected:
# the listed file is neither flagged nor altered
def test_exclusion_file_respected_at_root(tmp_path):
    cr_checker = load_cr_checker_module()
    header_template = load_template("py")

    test_file = tmp_path / "file.py"
    original_content = "some content\n"
    test_file.write_text(original_content, encoding="utf-8")

    exclusion_file = tmp_path / "exclusion.txt"
    exclusion_file.write_text(str(test_file), encoding="utf-8")

    exclusion, valid = cr_checker.load_exclusion(exclusion_file)
    assert valid is True
    assert exclusion == {str(test_file)}

    results = cr_checker.process_files(
        files=[test_file],
        templates={"py": header_template},
        fix=True,
        exclusion=exclusion,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0
    assert results["fixed"] == 0
    assert test_file.read_text(encoding="utf-8") == original_content


# test that an exclusion.txt file sitting in a deeply nested directory is respected:
# the listed file is neither flagged nor altered
def test_exclusion_file_respected_in_nested_directory(tmp_path):
    cr_checker = load_cr_checker_module()
    header_template = load_template("py")

    nested_dir = tmp_path / "a" / "b" / "c" / "d"
    nested_dir.mkdir(parents=True)

    test_file = nested_dir / "file.py"
    original_content = "some content\n"
    test_file.write_text(original_content, encoding="utf-8")

    exclusion_file = nested_dir / "exclusion.txt"
    exclusion_file.write_text(str(test_file), encoding="utf-8")

    exclusion, valid = cr_checker.load_exclusion(exclusion_file)
    assert valid is True
    assert exclusion == {str(test_file)}

    results = cr_checker.process_files(
        files=[test_file],
        templates={"py": header_template},
        fix=True,
        exclusion=exclusion,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0
    assert results["fixed"] == 0
    assert test_file.read_text(encoding="utf-8") == original_content


# test that a glob entry in the exclusion file is expanded recursively:
# `.claude/**/*` covers direct children as well as arbitrarily nested files,
# and a literal entry in the same file keeps working alongside it
def test_exclusion_file_expands_glob_pattern(tmp_path, monkeypatch):
    cr_checker = load_cr_checker_module()
    header_template = load_template("py")

    workspace_dir = tmp_path / "workspace"
    nested_dir = workspace_dir / ".claude" / "skills" / "some_skill" / "scripts"
    nested_dir.mkdir(parents=True)
    (workspace_dir / ".claude" / "agents").mkdir()
    (workspace_dir / "tool").mkdir()

    original_content = "some content\n"
    nested_file = nested_dir / "fix_titles.py"
    direct_child = workspace_dir / ".claude" / "settings.py"
    agent_file = workspace_dir / ".claude" / "agents" / "reviewer.py"
    literal_file = workspace_dir / "tool" / "generated.py"
    outside_file = workspace_dir / "tool" / "checked.py"
    for excluded in (nested_file, direct_child, agent_file, literal_file, outside_file):
        excluded.write_text(original_content, encoding="utf-8")

    exclusion_file = workspace_dir / "exclusion.txt"
    exclusion_file.write_text(
        "# AI - Stuff\n.claude/**/*\n\ntool/generated.py\n",
        encoding="utf-8",
    )

    execroot = tmp_path / "execroot"
    execroot.mkdir()
    monkeypatch.chdir(execroot)
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(workspace_dir))

    exclusion, valid = cr_checker.load_exclusion(exclusion_file)

    assert valid is True
    excluded_files = sorted(item for item in exclusion if Path(item).is_file())
    assert excluded_files == sorted(
        str(path) for path in (nested_file, direct_child, agent_file, literal_file)
    )
    assert str(outside_file) not in exclusion

    results = cr_checker.process_files(
        files=[nested_file, direct_child, agent_file, literal_file, outside_file],
        templates={"py": header_template},
        fix=True,
        exclusion=exclusion,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 1
    assert results["fixed"] == 1
    for skipped in (nested_file, direct_child, agent_file, literal_file):
        assert skipped.read_text(encoding="utf-8") == original_content
    skipped_header_inserted = outside_file.read_text(encoding="utf-8")
    assert skipped_header_inserted.startswith(
        header_template.format(year=datetime.now().year)
    )


# test the pathway where a glob pattern does not match anything
def test_exclusion_file_glob_without_match_is_invalid(tmp_path, monkeypatch, caplog):
    cr_checker = load_cr_checker_module()

    workspace_dir = tmp_path / "workspace"
    nested_dir = workspace_dir / ".claude" / "skills" / "some_skill" / "scripts"
    nested_dir.mkdir(parents=True)

    original_content = "some content\n"
    nested_file = nested_dir / "fix_titles.py"
    nested_file.write_text(original_content, encoding="utf-8")

    exclusion_file = workspace_dir / "exclusion.txt"
    exclusion_file.write_text(
        "# This doesn't exists \n.not_here/**/*\n\n.claude/**/*\n",
        encoding="utf-8",
    )

    execroot = tmp_path / "execroot"
    execroot.mkdir()
    monkeypatch.chdir(execroot)
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(workspace_dir))

    with caplog.at_level(logging.WARNING):
        _, valid = cr_checker.load_exclusion(exclusion_file)
    assert "Exclusion pattern .not_here/**/* matched nothing." in caplog.text

    assert valid is False


# test that a workspace-relative exclusion entry (as produced by e.g. `git ls-files`)
# is still resolved correctly when the process's cwd is not the workspace root, which
# is what happens under `bazel run`/`bazel test` (BUILD_WORKSPACE_DIRECTORY is set to
# the workspace root while the cwd is the execroot/runfiles directory)
def test_exclusion_file_respected_under_bazel_run_cwd(tmp_path, monkeypatch):
    cr_checker = load_cr_checker_module()
    header_template = load_template("py")

    workspace_dir = tmp_path / "workspace"
    nested_dir = workspace_dir / ".claude" / "skills" / "some_skill" / "scripts"
    nested_dir.mkdir(parents=True)

    test_file = nested_dir / "fix_titles.py"
    original_content = "some content\n"
    test_file.write_text(original_content, encoding="utf-8")

    relative_entry = ".claude/skills/some_skill/scripts/fix_titles.py"
    exclusion_file = workspace_dir / "exclusion.txt"
    exclusion_file.write_text(relative_entry, encoding="utf-8")

    execroot = tmp_path / "execroot"
    execroot.mkdir()
    monkeypatch.chdir(execroot)
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(workspace_dir))

    exclusion, valid = cr_checker.load_exclusion(exclusion_file)
    assert valid is True
    assert exclusion == {str(test_file)}

    collected_files = cr_checker.collect_inputs([relative_entry], exts=["py"])
    assert collected_files == [test_file]

    results = cr_checker.process_files(
        files=collected_files,
        templates={"py": header_template},
        fix=True,
        exclusion=exclusion,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["no_copyright"] == 0
    assert results["fixed"] == 0
    assert test_file.read_text(encoding="utf-8") == original_content


def find_header(tmp_path, name, content, template_ext=None):
    cr_checker = load_cr_checker_module()
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    ext = template_ext or path.suffix[1:]
    max_lines = len(load_template(ext).splitlines()) + 5
    offset = cr_checker.detect_shebang_offset(path, "utf-8")
    return cr_checker.find_existing_header(path, "utf-8", offset, max_lines)


# wrong wording ("Apache License 2.0") in every template style is found in full,
# including the closing border line, and the code after it is not included
@pytest.mark.parametrize("ext", ["py", "cpp", "rs", "md", "rst", "puml", "BUILD"])
def test_find_existing_header_finds_full_malformed_header(tmp_path, ext):
    header = (
        load_template(ext)
        .format(year=2023)
        .replace("Apache License Version 2.0", "Apache License 2.0")
    )
    name = "BUILD" if ext == "BUILD" else f"file.{ext}"
    result = find_header(tmp_path, name, header + "some content\n", ext)
    assert result == (len(header), header)


def test_find_existing_header_skips_shebang(tmp_path):
    header = "# Copyright (c) 2023 X\n# SPDX-License-Identifier: MIT\n"
    result = find_header(
        tmp_path, "s.py", "#!/usr/bin/env python3\n" + header + "x = 1\n"
    )
    assert result == (len(header), header)


def test_find_existing_header_short_header_without_border(tmp_path):
    header = "# Copyright (c) 2023 X\n# SPDX-License-Identifier: MIT\n"
    assert find_header(tmp_path, "a.py", header + "x = 1\n") == (len(header), header)


def test_find_existing_header_code_line_with_fill_chars_after_spdx_is_not_eaten(
    tmp_path,
):
    header = "# Copyright (c) 2023 X\n# SPDX-License-Identifier: MIT\n"
    result = find_header(tmp_path, "a.py", header + "print('=====')\n")
    assert result == (len(header), header)


def test_find_existing_header_code_before_header_returns_none(tmp_path):
    content = "x = 1\n# Copyright (c) 2023 X\n# SPDX-License-Identifier: MIT\n"
    assert find_header(tmp_path, "a.py", content) is None


def test_find_existing_header_spdx_beyond_max_lines_returns_none(tmp_path):
    content = (
        "# Copyright (c) 2023 X\n" + "#\n" * 30 + "# SPDX-License-Identifier: MIT\n"
    )
    assert find_header(tmp_path, "a.py", content) is None


def test_find_existing_header_spdx_without_copyright_returns_none(tmp_path):
    assert (
        find_header(tmp_path, "a.py", "# SPDX-License-Identifier: MIT\nx = 1\n") is None
    )


def test_find_existing_header_no_header_returns_none(tmp_path):
    assert find_header(tmp_path, "a.py", "x = 1\n") is None


def test_find_existing_header_header_only_file(tmp_path):
    header = "# Copyright (c) 2023 X\n# SPDX-License-Identifier: MIT"
    assert find_header(tmp_path, "a.py", header) == (len(header), header)


MIT_HEADER = "# Copyright (c) 2023 Example\n# SPDX-License-Identifier: MIT\n"


def wrong_wording_header(extension: str) -> str:
    return (
        load_template(extension)
        .format(year=2023)
        .replace("Apache License Version 2.0", "Apache License 2.0")
    )


def run_main(cr_checker, monkeypatch, *args):
    # main() calls configure_logging, which clears all root logger handlers
    # (including pytest's caplog handler), so it is disabled here
    monkeypatch.setattr(cr_checker, "configure_logging", lambda *a, **k: None)
    return cr_checker.main([*args])


# a header with different wording is counted as wrong, not as missing or ok
@pytest.mark.parametrize("extension", ["py", "cpp", "rs", "rst", "BUILD"])
def test_process_files_counts_wrong_copyright(tmp_path, extension):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / ("BUILD" if extension == "BUILD" else f"file.{extension}")
    content = wrong_wording_header(extension) + "some content\n"
    test_file.write_text(content, encoding="utf-8")

    results = cr_checker.process_files(
        files=[test_file],
        templates={extension: load_template(extension)},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["wrong_copyright"] == 1
    assert results["no_copyright"] == 0
    assert results["fixed"] == 0
    assert test_file.read_text(encoding="utf-8") == content


def test_process_files_logs_wrong_copyright_as_error(tmp_path, caplog):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    test_file.write_text(MIT_HEADER + "x = 1\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cr_checker.process_files(
            files=[test_file],
            templates={"py": load_template("py")},
            fix=False,
            use_mmap=False,
            encoding="utf-8",
        )

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Wrong copyright format" in errors[0].getMessage()
    assert str(test_file) in errors[0].getMessage()


def test_process_files_correct_header_is_not_wrong(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    test_file.write_text(
        load_template("py").format(year=2023) + "x = 1\n", encoding="utf-8"
    )

    results = cr_checker.process_files(
        files=[test_file],
        templates={"py": load_template("py")},
        fix=False,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["wrong_copyright"] == 0


# a header with a different license (e.g. third-party MIT code) is never
# replaced, also not with --fix
def test_process_files_fix_keeps_different_license(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    content = MIT_HEADER + "x = 1\n"
    test_file.write_text(content, encoding="utf-8")

    results = cr_checker.process_files(
        files=[test_file],
        templates={"py": load_template("py")},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["wrong_copyright"] == 1
    assert results["fixed"] == 0
    assert test_file.read_text(encoding="utf-8") == content


# regression test for #25 / #70: wrong header used to exit 0
@pytest.mark.parametrize("fix_args", [[], ["--fix"]])
def test_main_returns_1_for_wrong_copyright(tmp_path, monkeypatch, fix_args):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    content = MIT_HEADER + "x = 1\n"
    test_file.write_text(content, encoding="utf-8")

    assert run_main(cr_checker, monkeypatch, *fix_args, str(test_file)) == 1
    assert test_file.read_text(encoding="utf-8") == content


def test_main_returns_0_for_correct_copyright(tmp_path, monkeypatch):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    test_file.write_text(
        load_template("py").format(year=2023) + "x = 1\n", encoding="utf-8"
    )

    assert run_main(cr_checker, monkeypatch, str(test_file)) == 0


# --fix replaces a wrong-format header with the same license by the template,
# keeps the old year and leaves the rest of the file untouched
@pytest.mark.parametrize("ext", ["py", "cpp", "rs", "md", "rst", "puml", "BUILD"])
def test_process_files_fix_replaces_wrong_copyright(tmp_path, ext):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / ("BUILD" if ext == "BUILD" else f"file.{ext}")
    rest = "\nsome content\n"
    test_file.write_text(wrong_wording_header(ext) + rest, encoding="utf-8")

    results = cr_checker.process_files(
        files=[test_file],
        templates={ext: load_template(ext)},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert results["wrong_copyright"] == 1
    assert results["fixed"] == 1
    assert test_file.read_text(encoding="utf-8") == (
        load_template(ext).format(year=2023) + rest
    )


def test_process_files_fix_replaces_wrong_copyright_after_shebang(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "script.py"
    shebang = "#!/usr/bin/env python3\n"
    test_file.write_text(
        shebang + wrong_wording_header("py") + "x = 1\n", encoding="utf-8"
    )

    cr_checker.process_files(
        files=[test_file],
        templates={"py": load_template("py")},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert test_file.read_text(encoding="utf-8") == (
        shebang + load_template("py").format(year=2023) + "x = 1\n"
    )


def test_process_files_fix_keeps_year_range(tmp_path):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    header = wrong_wording_header("py").replace("2023", "2021-2023")
    test_file.write_text(header + "x = 1\n", encoding="utf-8")

    cr_checker.process_files(
        files=[test_file],
        templates={"py": load_template("py")},
        fix=True,
        use_mmap=False,
        encoding="utf-8",
    )

    assert test_file.read_text(encoding="utf-8").startswith(
        load_template("py").format(year="2021-2023")
    )


def test_main_fix_wrong_copyright_then_passes(tmp_path, monkeypatch):
    cr_checker = load_cr_checker_module()
    test_file = tmp_path / "file.py"
    test_file.write_text(wrong_wording_header("py") + "x = 1\n", encoding="utf-8")

    # first run fixes the file and reports it (pre-commit convention)
    assert run_main(cr_checker, monkeypatch, "--fix", str(test_file)) == 1
    # second run sees a correct header
    assert run_main(cr_checker, monkeypatch, str(test_file)) == 0
