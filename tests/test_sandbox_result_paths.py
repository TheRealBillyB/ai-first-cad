"""A script result cannot name a file outside the project.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the project.
A result file that is a link is not read. Geometry and pictures named by the
result are opened only when they are regular files inside the project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from build123d import Box, export_step

from aicad.errors import ScriptError
from aicad.sandbox import (
    _payload_from_result,
    _project_regular_file,
    allow_design_scripts,
    run_design_script,
    run_part_script,
    sandbox_supported,
)


CANARY_NAME = "SANDBOX_CANARY.txt"
CANARY_TEXT = "CANARY_HIT\n"


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    monkeypatch.delenv("AICAD_SETTINGS", raising=False)
    monkeypatch.delenv("AICAD_PLUGIN_CONSENT", raising=False)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    return tmp_path


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are not available: {exc}")


def _decoy_result(outside: Path) -> Path:
    path = outside / "result.json"
    path.write_text(
        json.dumps(
            {
                "ok": True,
                "stdout": "CANARY_HIT",
                "error": None,
                "errors": [],
                "changed_parts": [],
                "deleted_parts": [],
                "checks": [],
                "views": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_result_symlink_is_not_read(tmp_path):
    job = tmp_path / "job"
    outside = tmp_path / "outside"
    job.mkdir()
    outside.mkdir()
    decoy = _decoy_result(outside)
    before = decoy.read_bytes()
    _symlink(job / "result.json", decoy)
    with pytest.raises(ScriptError, match="link"):
        _payload_from_result(job, 0, "", "", memory_killed=False)
    assert decoy.read_bytes() == before
    assert b"CANARY_HIT" in before


def test_result_hard_link_is_not_read(tmp_path):
    job = tmp_path / "job"
    outside = tmp_path / "outside"
    job.mkdir()
    outside.mkdir()
    decoy = _decoy_result(outside)
    before = decoy.read_bytes()
    try:
        os.link(decoy, job / "result.json")
    except OSError as exc:
        pytest.skip(f"hard links are not available: {exc}")
    with pytest.raises(ScriptError, match="link"):
        _payload_from_result(job, 0, "", "", memory_killed=False)
    assert decoy.read_bytes() == before


def test_picture_link_is_not_opened(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    canary = outside / CANARY_NAME
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    link = project / "view.png"
    _symlink(link, canary)
    with pytest.raises(ScriptError, match="link"):
        _project_regular_file(project, link, "The script picture")
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT
    try:
        os.link(canary, project / "shared.png")
    except OSError:
        return
    with pytest.raises(ScriptError, match="link"):
        _project_regular_file(project, project / "shared.png", "The script picture")
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


def test_script_result_symlink_is_not_returned(tmp_path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    decoy = _decoy_result(outside)
    before = decoy.read_bytes()
    allow_design_scripts(project)
    script = (
        "import aicad.sandbox as sandbox\n"
        "job = sandbox.os.environ['AICAD_JOB']\n"
        f"sandbox.os.symlink({str(decoy)!r}, sandbox.os.path.join(job, 'result.json'))\n"
        "print('hello')\n"
    )
    try:
        payload = run_design_script(project, script)
    except ScriptError as exc:
        assert "CANARY_HIT" not in str(exc)
    else:
        assert "CANARY_HIT" not in json.dumps(payload)
    assert decoy.read_bytes() == before


def test_part_geometry_outside_the_project_is_not_opened(tmp_path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    step = outside / "box.step"
    export_step(Box(7, 3, 1), step)
    before = step.read_bytes()
    # The runner writes result.json with Path.write_text on every system.
    # On Windows the runner is not the __main__ module.
    script = (
        "import json\n"
        "import pathlib\n"
        f"outside_step = {str(step)!r}\n"
        "real = pathlib.Path.write_text\n"
        "def fake(self, data, *args, **kwargs):\n"
        "    if self.name == 'result.json':\n"
        "        payload = json.loads(data)\n"
        "        if isinstance(payload, dict) and 'step' in payload:\n"
        "            payload['step'] = outside_step\n"
        "            data = json.dumps(payload) + '\\n'\n"
        "    return real(self, data, *args, **kwargs)\n"
        "pathlib.Path.write_text = fake\n"
        "part = Box(2, 2, 2)\n"
    )
    with pytest.raises(ScriptError, match="outside the project"):
        run_part_script(script, project)
    assert step.read_bytes() == before


def test_picture_outside_the_project_is_not_returned(tmp_path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    canary = outside / CANARY_NAME
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    allow_design_scripts(project)
    script = (
        "import json\n"
        "import pathlib\n"
        f"canary = {str(canary)!r}\n"
        "real = pathlib.Path.write_text\n"
        "def fake(self, data, *args, **kwargs):\n"
        "    if self.name == 'result.json':\n"
        "        payload = json.loads(data)\n"
        "        if isinstance(payload, dict):\n"
        "            payload['views'] = [{'name': 'iso', 'path': canary, 'width': 1, 'height': 1}]\n"
        "            data = json.dumps(payload) + '\\n'\n"
        "    return real(self, data, *args, **kwargs)\n"
        "pathlib.Path.write_text = fake\n"
        "print('hello')\n"
    )
    with pytest.raises(ScriptError, match="outside the project"):
        run_design_script(project, script)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT
