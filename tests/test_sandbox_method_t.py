"""Shared files and scripts stop at a fixed cap.

A zip or 3MF stored in the project is not extracted. A setting cannot raise
the script caps.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from aicad.blueprint import BlueprintError, _MAX_BYTES, open_blueprint, png_size
from aicad.document import Document
from aicad.errors import GeometryError, ScriptError
from aicad.sandbox import (
    CPU_SECONDS,
    MEMORY_BYTES,
    SCRIPT_MAX_CHARS,
    WALL_SECONDS,
    cpu_seconds,
    memory_bytes,
    run_part_script,
    wall_seconds,
)

_CANARY_TEXT = "CANARY_HIT\n"


def test_script_caps_cannot_be_raised(monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "999999")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "999999")
    monkeypatch.setenv("AICAD_SCRIPT_MEMORY_MB", "999999")
    assert wall_seconds() == WALL_SECONDS
    assert cpu_seconds() == CPU_SECONDS
    assert memory_bytes() == MEMORY_BYTES


def test_a_huge_blueprint_and_png_are_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sheet = project / "sheet.png"
    with sheet.open("wb") as handle:
        handle.seek(_MAX_BYTES)
        handle.write(b"x")
    with pytest.raises(BlueprintError, match="too large"):
        open_blueprint(sheet, project)
    header = project / "wide.png"
    header.write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (100_001).to_bytes(4, "big") + (1).to_bytes(4, "big"))
    with pytest.raises(GeometryError):
        png_size(header)


def test_an_archive_in_the_project_is_not_extracted(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    project = tmp_path / "project"
    project.mkdir()
    archive = project / "model.3mf"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../SANDBOX_CANARY.txt", _CANARY_TEXT)
        bundle.writestr("nested.zip", b"PK\x03\x04")
    Document(project)
    assert not canary.exists()
    assert not (project / "SANDBOX_CANARY.txt").exists()
    assert list(project.iterdir()) == [archive]


def test_a_script_past_the_character_cap_is_not_run(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    project = tmp_path / "project"
    project.mkdir()
    script = "part = Box(1, 1, 1)\n" + ("# pad\n" * (SCRIPT_MAX_CHARS // 6 + 2))
    assert len(script) > SCRIPT_MAX_CHARS
    with pytest.raises(ScriptError):
        run_part_script(script, project)
    assert not canary.exists()
