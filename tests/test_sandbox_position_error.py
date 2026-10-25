"""Position-error strength-check pictures stay inside the project.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT. The solver stand-in
writes that file in the directory it was given, which is where the pictures
would be written.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError

CANARY_NAME = "SANDBOX_CANARY.txt"
CANARY_TEXT = "CANARY_HIT\n"


def _document(tmp_path: Path) -> tuple[Path, Path, Document]:
    project = tmp_path / "proj"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    document = Document(project)
    document.create_part_from_ops(
        "base",
        [{"op": "add", "shape": "box", "size": [20, 10, 6], "center": [0, 0, 3]}],
    )
    return project, outside, document


def _watch(monkeypatch) -> list[Path | None]:
    calls: list[Path | None] = []

    def fake(shape, setup, out_dir=None, **kwargs):
        destination = None if out_dir is None else Path(out_dir)
        calls.append(destination)
        if destination is not None:
            destination.mkdir(parents=True, exist_ok=True)
            (destination / CANARY_NAME).write_text(CANARY_TEXT, encoding="utf-8")
        raise GeometryError("picture directory recorded")

    monkeypatch.setattr("aicad.fea.solve_part", fake)
    return calls


def _budget(instance: str) -> dict:
    return {
        "tool": instance,
        "tolerance_mm": 1.0,
        "links": {instance: {"fea": {"material": "steel", "fixes": [], "loads": []}}},
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _arm(document: Document, instance: str) -> None:
    document.create_assembly(
        {"name": "arm", "instances": [{"name": instance, "part": "base"}], "joints": []}
    )


@pytest.mark.parametrize(
    "instance_for",
    ["absolute", "climb", "backslash", "drive", "trimmed_parent", "reserved"],
)
def test_strength_check_pictures_cannot_leave_the_project(tmp_path, monkeypatch, instance_for):
    project, outside, document = _document(tmp_path)
    canary = outside / CANARY_NAME
    picture_root = project / "renders" / "assemblies" / "arm" / "position-error"
    if instance_for == "absolute":
        instance = str(outside)
    elif instance_for == "climb":
        instance = Path(os.path.relpath(outside, picture_root)).as_posix()
        assert ".." in instance
    elif instance_for == "backslash":
        instance = "..\\..\\..\\..\\..\\outside"
    elif instance_for == "drive":
        instance = "C:outside"
    elif instance_for == "trimmed_parent":
        instance = ".. ."
    else:
        instance = "CON"
    _arm(document, instance)
    calls = _watch(monkeypatch)
    match = "reserved" if instance_for == "reserved" else "outside the project"
    with pytest.raises(GeometryError, match=match):
        document.check_position_error("arm", _budget(instance))
    assert calls == []
    assert not canary.exists()
    assert list(outside.iterdir()) == []
    assert list(project.rglob(CANARY_NAME)) == []


def test_a_picture_directory_that_is_a_link_is_not_used(tmp_path, monkeypatch):
    project, outside, document = _document(tmp_path)
    canary = outside / CANARY_NAME
    picture_root = project / "renders" / "assemblies" / "arm" / "position-error"
    picture_root.mkdir(parents=True)
    link = picture_root / "base"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a symlink")
    _arm(document, "base")
    calls = _watch(monkeypatch)
    with pytest.raises(GeometryError, match="outside the project"):
        document.check_position_error("arm", _budget("base"))
    assert calls == []
    assert not canary.exists()
    assert list(outside.iterdir()) == []


def test_a_link_name_still_gets_a_picture_directory_inside_the_project(tmp_path, monkeypatch):
    project, outside, document = _document(tmp_path)
    canary = outside / CANARY_NAME
    _arm(document, "base")
    calls = _watch(monkeypatch)
    with pytest.raises(GeometryError, match="picture directory recorded"):
        document.check_position_error("arm", _budget("base"))
    assert len(calls) == 1
    destination = calls[0]
    assert destination is not None
    assert _inside(destination, project)
    assert destination.name == "base"
    assert (destination / CANARY_NAME).read_text(encoding="utf-8") == CANARY_TEXT
    assert not canary.exists()
    assert list(outside.iterdir()) == []
