from __future__ import annotations

import json

import score_tools
from score_tools import cli as score_cli


def test_score_tools_json_structure(capsys) -> None:
    code = score_cli.main(["--json"])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["score_tools_version"] == score_tools.__version__
    # Known tools present
    tools = data["tools"]
    assert "score-ruff" in tools
    # Package managers is a list (may be empty on CI)
    assert isinstance(data["package_managers"], list)


def test_score_tools_pretty_runs(capsys) -> None:
    code = score_cli.main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "tools:" in out
    assert "score-ruff" in out
