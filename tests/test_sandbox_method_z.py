"""Windows path forms do not name a different file from the one that was checked.

The marker outside the project is SANDBOX_CANARY.txt containing CANARY_HIT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.paths import is_reserved_device_name, require_export_path

_CANARY_TEXT = "CANARY_HIT\n"
_BOX = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]


def test_reserved_device_names_are_refused():
    for name in ("CON", "NUL", "COM1", "COM1.txt", "aux.txt", "LPT9.step"):
        assert is_reserved_device_name(name)


def test_stream_short_long_and_trailing_names_are_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    refused = (
        "exports/model.step:hidden",
        "exports/model.step:hidden:$DATA",
        "exports/MODELS~1.STP",
        "exports/SECRET~1",
        "exports/box.step.",
        "exports/box.step ",
        "a" * 200,
        "\\\\server\\share\\box.step",
        "\\\\?\\C:\\box.step",
    )
    for raw in refused:
        with pytest.raises(GeometryError):
            require_export_path(project, raw, "Export path", GeometryError)


def test_a_case_only_name_is_the_same_file(tmp_path):
    project = tmp_path / "project"
    exports = project / "exports"
    exports.mkdir(parents=True)
    existing = exports / "Box.step"
    existing.write_text("solid\n", encoding="utf-8")
    with pytest.raises(GeometryError, match="case"):
        require_export_path(project, "exports/box.step", "Export path", GeometryError)
    assert existing.read_text(encoding="utf-8") == "solid\n"


def test_an_export_does_not_write_the_marker_through_a_windows_alias(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    document = Document(project)
    document.create_part_from_ops("base", _BOX)
    for raw in ("exports/model.step:hidden", "exports/MODELS~1.STP", "exports/box.step.", "NUL.txt"):
        with pytest.raises(GeometryError):
            document.export("base", raw, "step")
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert not any(project.rglob("SANDBOX_CANARY.txt"))
