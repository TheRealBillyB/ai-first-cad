"""A helper process cannot read a file outside the project.

The marker is CANARY_HIT. The helper is a child of the confined script.
It can read a file in the project and cannot read the marker outside it.
"""

from __future__ import annotations

import pytest

from aicad.sandbox import os_probe, sandbox_supported

_MARKER = "CANARY_HIT"


def test_a_helper_process_cannot_read_outside_the_project(tmp_path):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    probe = os_probe(project, helper=True)
    assert probe.get("helper_read") == "closed"
    assert probe.get("read_outside") != "opened"
    assert _MARKER not in str(probe)
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
