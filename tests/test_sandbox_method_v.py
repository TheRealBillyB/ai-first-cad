"""Exports and the session record do not copy the environment or the account name.

The values below are markers. They are not part of the model.
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.workspace import Workspace

_MARKER = "CANARY_ENV_MARKER_VALUE"
_TOKEN = "sk-canarysecretvalue"
_BOX = [{"op": "add", "shape": "box", "size": [8, 4, 2]}]


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_CANARY_MARKER", _MARKER)
    monkeypatch.setenv("AICAD_API_KEY", _TOKEN)
    return tmp_path


def _secrets() -> list[str]:
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    values = [_MARKER, _TOKEN]
    if home:
        values.append(home)
    user = getpass.getuser()
    if user and user not in {"Author", "unknown"}:
        values.append(user)
    return values


def _assert_clean(blob: bytes) -> None:
    text = blob.decode("latin-1", errors="replace")
    for secret in _secrets():
        assert secret not in text


def test_step_stl_urdf_and_mjcf_omit_the_environment(tmp_path, config_home):
    document = Document(tmp_path / "project")
    document.create_part_from_ops("base", _BOX)
    step = document.export("base", "exports/base.step", "step")
    stl = document.export("base", "exports/base.stl", "stl")
    document.create_assembly(
        {
            "name": "arm",
            "instances": [
                {
                    "name": "base",
                    "part": "base",
                    "placement": {"position": [0, 0, 0], "rotation_deg": [0, 0, 0]},
                }
            ],
            "joints": [],
        }
    )
    report = document.export_robot("arm")
    blobs = [step.read_bytes(), stl.read_bytes()]
    for key in ("urdf", "mjcf"):
        blobs.append(Path(report[key]).read_bytes())
    for blob in blobs:
        _assert_clean(blob)


def test_a_session_record_omits_the_environment(tmp_path, config_home):
    workspace = Workspace(tmp_path / "project")
    workspace.execute("session_record", {"action": "start"}, source="ui")
    workspace.execute("create_part_from_ops", {"name": "base", "ops": _BOX}, source="ui")
    saved = (workspace.root / "records" / "session.json").read_bytes()
    _assert_clean(saved)
    assert _MARKER not in json.dumps(workspace.state())
