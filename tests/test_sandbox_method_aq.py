"""A STEP import reads the bytes it was given.

Replacing a temporary name does not change the solid. The marker file outside
the project stays as it was written. Nothing is sent to another machine.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from OCP.STEPControl import STEPControl_Reader

from aicad.kernel import export_step_file, import_step_bytes, make_from_ops
from aicad.mesh import volume_mm3

_MARKER = "CANARY_HIT\n"


def _box_bytes(folder: Path) -> bytes:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [2, 3, 4]}]), path)
    return path.read_bytes()


def _marker(tmp_path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    return marker


def test_a_swapped_temporary_name_is_not_the_solid(tmp_path, monkeypatch):
    marker = _marker(tmp_path)
    data = _box_bytes(tmp_path / "source")
    original = tempfile.NamedTemporaryFile

    def swapping(*args, **kwargs):
        handle = original(*args, **kwargs)
        real_close = handle.close

        def close():
            name = Path(handle.name)
            real_close()
            name.unlink(missing_ok=True)
            name.symlink_to(marker)

        handle.close = close
        return handle

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", swapping)
    solid = import_step_bytes(tmp_path / "project" / "box.step", data)
    assert abs(volume_mm3(solid) - 24) < 1e-6
    assert marker.read_text(encoding="utf-8") == "untouched\n"


def test_the_reader_does_not_open_a_path(tmp_path, monkeypatch):
    marker = _marker(tmp_path)
    data = _box_bytes(tmp_path / "source")
    opened: list[str] = []

    def wrapped(self, filename, *args):
        opened.append(str(filename))
        marker.write_text(_MARKER, encoding="utf-8")
        raise AssertionError("the STEP reader opened a path")

    monkeypatch.setattr(STEPControl_Reader, "ReadFile", wrapped)
    solid = import_step_bytes(tmp_path / "project" / "box.step", data)
    assert opened == []
    assert abs(volume_mm3(solid) - 24) < 1e-6
    assert marker.read_text(encoding="utf-8") == "untouched\n"
    shown = ""
    try:
        import_step_bytes(tmp_path / "project" / "box.step", b"CANARY_HIT\n")
    except Exception as exc:
        shown = str(exc)
    assert "CANARY_HIT" not in shown
    assert marker.read_text(encoding="utf-8") == "untouched\n"
