"""A plan, a plugin manifest, and a command-line file are not read by path after a check.

The marker outside the project is SANDBOX_CANARY.txt containing CANARY_HIT.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aicad.cli import _read_named_file
from aicad.errors import AICADError
from aicad.plan import PlanError, PlanStore
from aicad.plugins import _read_manifest_bytes

_CANARY_TEXT = "CANARY_HIT\n"


def _canary(directory: Path) -> Path:
    path = directory / "SANDBOX_CANARY.txt"
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def _link(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this machine cannot create a symlink")


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    return tmp_path


def test_a_plan_link_is_not_opened(tmp_path, config_home):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    project = tmp_path / "project"
    plans = project / "plans"
    plans.mkdir(parents=True)
    _link(plans / "shelf.json", canary)
    store = PlanStore(project)
    with pytest.raises(PlanError):
        store.get("shelf")
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_plugin_manifest_link_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    manifest = tmp_path / "aicad.plugin.json"
    _link(manifest, canary)
    with pytest.raises(Exception):
        text = _read_manifest_bytes(manifest)
        assert _CANARY_TEXT not in text
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_command_line_file_link_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = _canary(outside)
    script = tmp_path / "script.py"
    _link(script, canary)
    with pytest.raises(AICADError):
        text = _read_named_file(script)
        assert _CANARY_TEXT not in text
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_regular_command_line_file_is_read(tmp_path):
    script = tmp_path / "script.py"
    script.write_bytes(b"part = Box(1, 1, 1)\r\n")
    assert _read_named_file(script) == "part = Box(1, 1, 1)\n"
    payload = tmp_path / "ops.json"
    payload.write_text(json.dumps([{"op": "add"}]), encoding="utf-8")
    assert json.loads(_read_named_file(payload)) == [{"op": "add"}]
