"""Gmsh writes its step file outside the project.

The marker is CANARY_HIT. The process temporary directory may be the project.
The step file still is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicad import fea, kernel

_MARKER = "CANARY_HIT"


def test_a_mesh_step_file_stays_outside_the_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    (project / "marker.txt").write_text(_MARKER + "\n", encoding="utf-8")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(project))
    monkeypatch.chdir(project)
    seen: dict[str, Path] = {}

    def record(_shape, path):
        seen["path"] = Path(path)
        raise RuntimeError("stop")

    monkeypatch.setattr(kernel, "export_step_file", record)
    shape = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10]}])
    with pytest.raises(RuntimeError, match="stop"):
        fea._mesh_attempt(shape, 2.0, 1, algorithm=1, optimize=0, occ_fix=False)
    path = seen["path"].resolve()
    assert path != project.resolve()
    assert project.resolve() not in path.parents
    assert (project / "marker.txt").read_text(encoding="utf-8") == _MARKER + "\n"
