"""A plugin export stays inside the project.

The canary is SANDBOX_CANARY.txt. step and stl are still refused before the
plugin is imported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad.document import Document
from aicad.plugins import PluginError, export_with


class _Loaded:
    name = "toy"
    publisher = "Toy"
    permissions = frozenset()

    def _exporter(self, _format):
        return self

    def call(self, _view, _options):
        return b"note\n"


def test_a_plugin_export_cannot_leave_the_project(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    document = Document(project)
    loaded = {"count": 0}

    def load(*_args, **_kwargs):
        loaded["count"] += 1
        return _Loaded()

    monkeypatch.setattr("aicad.plugins.load", load)
    monkeypatch.setattr("aicad.plugins._part_view", lambda *_args, **_kwargs: (None, None))

    with pytest.raises(PluginError, match="built in"):
        export_with("toy", "step", "box", outside, document=document)
    with pytest.raises(PluginError, match="built in"):
        export_with("toy", "stl", "box", "../outside/SANDBOX_CANARY.txt", document=document)
    assert loaded["count"] == 0

    with pytest.raises(PluginError, match="outside the project"):
        export_with("toy", "note", "box", outside, document=document)
    with pytest.raises(PluginError, match="outside the project"):
        export_with("toy", "note", "box", "../outside/SANDBOX_CANARY.txt", document=document)
    with pytest.raises(PluginError, match="outside the project"):
        export_with("toy", "note", "box", "folder\\..\\..\\outside\\SANDBOX_CANARY.txt", document=document)
    for reserved in ("NUL", "COM1", "exports/CON/box.txt", "CONOUT$.txt", "CONIN$"):
        with pytest.raises(PluginError, match="reserved"):
            export_with("toy", "note", "box", reserved, document=document)
    assert not outside.exists()
    assert loaded["count"] == 0
    assert not (project / "NUL").exists()
    assert not (project / "CONOUT$.txt").exists()

    written = export_with("toy", "note", "box", "exports/box.txt", document=document)
    path = Path(written["path"])
    assert path.read_bytes() == b"note\n"
    assert path.resolve().is_relative_to(project.resolve())
    inside = project / "exports" / "named.txt"
    named = export_with("toy", "note", "box", inside, document=document)
    assert Path(named["path"]) == inside.resolve()
    assert loaded["count"] == 2
