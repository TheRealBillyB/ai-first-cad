"""Shared model files cannot write outside the project.

A project someone else sent is opened, and its STEP, STL, and 3MF files are
imported. A canary is a file named SANDBOX_CANARY.txt. These tests build that
file's contents into the shared models and check that opening or importing
them does not create it outside the project.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import DocumentError, GeometryError
from aicad.kernel import divert_native_stdio, export_step_file, import_step_file, make_from_ops
from aicad.workspace import Workspace

CANARY_NAME = "SANDBOX_CANARY.txt"
CANARY_TEXT = "CANARY_HIT\n"
_SECRET_SIZE = [7.0, 3.0, 1.0]
_SECRET_VOLUME = 21.0


@pytest.fixture
def layout(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setenv("TMP", str(scratch))
    monkeypatch.setenv("TEMP", str(scratch))
    secret = outside / "secret.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": _SECRET_SIZE}]), secret)
    return project, outside, scratch, secret


def _canary(outside: Path) -> Path:
    return outside / CANARY_NAME


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_canary_absent(outside: Path, scratch: Path) -> None:
    assert not _canary(outside).exists()
    assert not (Path.cwd() / CANARY_NAME).exists()
    for folder in (outside, scratch):
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if path.name == CANARY_NAME:
                raise AssertionError(f"{path} was written outside the project")
            if path.stat().st_size <= 2_000_000 and b"CANARY_HIT" in path.read_bytes():
                raise AssertionError(f"{path} contains the canary")


def _write_session(project: Path, parts: list[dict]) -> None:
    payload = {"units": "mm", "sketches": [], "assemblies": [], "parts": parts}
    (project / "session.json").write_text(json.dumps(payload), encoding="utf-8")


def _box_step(path: Path) -> None:
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [4, 4, 4]}]), path)


def _assembly_step(path: Path, first: str, second: str) -> None:
    """A two-part STEP assembly whose external names are ``first`` and ``second``."""
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from build123d import Box, Compound

    assembly = Compound(children=[Box(1, 1, 1), Box(1, 1, 1)])
    document = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    label = tool.AddShape(assembly.wrapped, True)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString("asm"))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    with divert_native_stdio(stderr=True):
        writer.Transfer(document, STEPControl_StepModelType.STEPControl_AsIs, "pre_")
        writer.Write(str(path))
    text = path.read_text(encoding="utf-8")
    # The longer token is replaced first so it is not partly rewritten.
    text = text.replace("pre_SOLID_1.stp", second).replace("pre_SOLID.stp", first)
    path.write_text(text, encoding="utf-8")


def _open(project: Path) -> Document:
    holder = project.parent / "holder"
    holder.mkdir(exist_ok=True)
    workspace = Workspace(holder)
    workspace.open_project(project)
    return workspace.document


def test_step_metadata_does_not_write_outside_the_project(layout):
    project, outside, scratch, _secret = layout
    canary = _canary(outside)
    step = project / "meta.step"
    _box_step(step)
    text = step.read_text(encoding="utf-8")
    text = text.replace("Open CASCADE Shape Model", str(canary), 1)
    text = text.replace(
        "('Open CASCADE Model')",
        f"('{CANARY_TEXT.strip()} {canary}')",
        1,
    )
    text = text.replace("PRODUCT('COMPOUND','COMPOUND'", f"PRODUCT('{canary}','{canary}'", 1)
    step.write_text(text, encoding="utf-8")
    _write_session(project, [{"name": "meta", "source": "step", "step_file": "meta.step"}])
    opened = _open(project)
    info = opened.list_parts()[0]
    assert info.built is True
    assert info.volume_mm3 == pytest.approx(64)
    opened.export("meta", project / "out.stl", "stl")
    opened.export("meta", project / "out.step", "step")
    _assert_canary_absent(outside, scratch)
    assert list(scratch.iterdir()) == []


def test_step_oversized_header_does_not_write_outside_the_project(layout):
    project, outside, scratch, _secret = layout
    step = project / "wide.step"
    _box_step(step)
    text = step.read_text(encoding="utf-8")
    text = text.replace("('Open CASCADE Model')", "('" + ("A" * 400_000) + "')", 1)
    step.write_text(text, encoding="utf-8")
    _write_session(project, [{"name": "wide", "source": "step", "step_file": "wide.step"}])
    _open(project)
    with pytest.raises(GeometryError):
        import_step_file(step)
    _assert_canary_absent(outside, scratch)
    assert list(scratch.iterdir()) == []


def test_step_external_file_outside_the_folder_is_not_opened(layout):
    project, outside, scratch, secret = layout
    canary = _canary(outside)
    before = _digest(secret)
    step = project / "extern.step"
    _assembly_step(step, "../outside/secret.step", str(canary))
    _write_session(project, [{"name": "extern", "source": "step", "step_file": "extern.step"}])
    opened = _open(project)
    info = opened.list_parts()[0]
    assert info.built is False
    assert any("outside its folder" in problem for problem in opened.problems)
    with pytest.raises(GeometryError, match="outside its folder"):
        import_step_file(step)
    assert _digest(secret) == before
    _assert_canary_absent(outside, scratch)


def test_step_encoded_external_file_outside_the_folder_is_not_opened(layout):
    project, outside, scratch, secret = layout
    before = _digest(secret)
    climb = "../outside/secret.step"
    utf16 = "\\X2\\" + "".join(f"{ord(char):04X}" for char in climb) + "\\X0\\"
    step = project / "encoded.step"
    _assembly_step(step, utf16, "missing-local.step")
    with pytest.raises(GeometryError, match="outside its folder"):
        import_step_file(step)
    byte_form = "".join("\\X\\" + f"{ord(char):02X}" for char in climb)
    other = project / "bytes.step"
    _assembly_step(other, byte_form, "missing-local.step")
    with pytest.raises(GeometryError, match="outside its folder"):
        import_step_file(other)
    assert _digest(secret) == before
    _assert_canary_absent(outside, scratch)


def test_step_newline_in_external_name_outside_the_folder_is_not_opened(layout):
    project, outside, scratch, secret = layout
    before = _digest(secret)
    step = project / "newline.step"
    _assembly_step(step, "../outside/secret.step", "missing-local.step")
    text = step.read_text(encoding="utf-8")
    text = text.replace("'../outside/secret.step'", "'.\n./outside/secret.step'")
    step.write_text(text, encoding="utf-8")
    with pytest.raises(GeometryError, match="outside its folder"):
        import_step_file(step)
    directive = project / "directive.step"
    _assembly_step(directive, "../outside/secret.step", "missing-local.step")
    text = directive.read_text(encoding="utf-8")
    text = text.replace("'../outside/secret.step'", "'.\\N\\./outside/secret.step'")
    directive.write_text(text, encoding="utf-8")
    with pytest.raises(GeometryError, match="outside its folder"):
        import_step_file(directive)
    assert _digest(secret) == before
    _assert_canary_absent(outside, scratch)


def test_step_split_external_name_outside_the_folder_is_not_opened(layout):
    project, outside, scratch, secret = layout
    before = _digest(secret)
    step = project / "split.step"
    _assembly_step(step, "../outside/secret.step", "missing-local.step")
    text = step.read_text(encoding="utf-8")
    text = text.replace("'../outside/secret.step'", "'..' '/outside/secret.step'")
    text = text.replace("DOCUMENT_FILE", "DOCUMENT_/* */FILE")
    step.write_text(text, encoding="utf-8")
    with pytest.raises(GeometryError, match="outside its folder"):
        import_step_file(step)
    assert _digest(secret) == before
    _assert_canary_absent(outside, scratch)


def test_step_external_file_in_the_same_folder_is_not_opened(layout):
    """A name in the same folder is not opened. Only the STEP file itself is read."""
    project, outside, scratch, _secret = layout
    sibling = project / "sibling.step"
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [5, 5, 5]}]), sibling)
    before = _digest(sibling)
    step = project / "local.step"
    _assembly_step(step, "sibling.step", "missing-local.step")
    _write_session(project, [{"name": "local", "source": "step", "step_file": "local.step"}])
    opened = _open(project)
    info = opened.list_parts()[0]
    assert info.built is False
    assert info.volume_mm3 != pytest.approx(125)
    with pytest.raises(GeometryError):
        import_step_file(step)
    assert _digest(sibling) == before
    assert not (project / "missing-local.step").exists()
    _assert_canary_absent(outside, scratch)


def test_stl_names_do_not_write_outside_the_project(layout):
    project, outside, scratch, _secret = layout
    canary = _canary(outside)
    ascii_stl = project / "name.stl"
    ascii_stl.write_text(
        f"solid {canary}\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        f"endsolid {canary}\n",
        encoding="utf-8",
    )
    binary = project / "count.stl"
    header = str(canary).encode("utf-8")[:80].ljust(80, b"\0")
    binary.write_bytes(header + struct.pack("<I", 100_000))
    _write_session(
        project,
        [
            {"name": "ascii_mesh", "source": "step", "step_file": "name.stl"},
            {"name": "binary_mesh", "source": "step", "step_file": "count.stl"},
        ],
    )
    opened = _open(project)
    assert all(part.built is False for part in opened.list_parts())
    for path in (ascii_stl, binary):
        with pytest.raises(GeometryError):
            import_step_file(path)
    _assert_canary_absent(outside, scratch)
    assert list(scratch.iterdir()) == []


def test_3mf_zip_entries_do_not_leave_the_project(layout):
    project, outside, scratch, _secret = layout
    canary = _canary(outside)
    from build123d import Box, Mesher

    mesh = project / "part.3mf"
    writer = Mesher()
    writer.add_shape(Box(2, 2, 2))
    writer.write(str(mesh))
    packed = project / "slip.3mf"
    with zipfile.ZipFile(mesh) as source, zipfile.ZipFile(packed, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("../outside/SANDBOX_CANARY.txt", CANARY_TEXT)
        target.writestr(str(canary).lstrip("/"), CANARY_TEXT)
        target.writestr("/" + str(canary).lstrip("/"), CANARY_TEXT)
    rels = project / "rels.3mf"
    with zipfile.ZipFile(rels, "w") as target:
        target.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'></Types>",
        )
        target.writestr(
            "_rels/.rels",
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            f"<Relationship Target='{canary}' Id='r1' "
            "Type='http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel'/>"
            "</Relationships>",
        )
        target.writestr(
            "3D/3dmodel.model",
            "<?xml version='1.0'?>"
            "<model unit='millimeter' xml:lang='en-US' "
            "xmlns='http://schemas.microsoft.com/3dmanufacturing/core/2015/02'>"
            f"<metadata name='Title'>{CANARY_TEXT.strip()}</metadata>"
            "<resources></resources><build></build></model>",
        )
    _write_session(
        project,
        [
            {"name": "pack", "source": "step", "step_file": "slip.3mf"},
            {"name": "rels", "source": "step", "step_file": "rels.3mf"},
        ],
    )
    opened = _open(project)
    assert all(part.built is False for part in opened.list_parts())
    for path in (packed, rels):
        with pytest.raises(GeometryError):
            import_step_file(path)
    _assert_canary_absent(outside, scratch)


def test_a_relative_step_path_stays_in_the_project(layout):
    project, outside, _scratch, secret = layout
    before = _digest(secret)
    document = Document(project)
    with pytest.raises(DocumentError, match="outside the project"):
        document.import_step("again", Path("..") / outside.name / secret.name)
    assert _digest(secret) == before
    assert not (project / "imports").exists()
    local = project / "local.step"
    _box_step(local)
    document.import_step("local", Path("nested") / ".." / "local.step")
    assert document.measure("local").watertight


def test_symlinked_step_outside_the_project_is_not_read(layout):
    project, outside, scratch, secret = layout
    before = _digest(secret)
    link = project / "linked.step"
    link.symlink_to(secret)
    _write_session(project, [{"name": "linked", "source": "step", "step_file": "linked.step"}])
    opened = _open(project)
    assert opened.list_parts()[0].built is False
    assert any("outside" in problem for problem in opened.problems)
    document = Document(project)
    with pytest.raises(DocumentError, match="outside the project"):
        document.import_step("again", link)
    assert _digest(secret) == before
    assert opened.list_parts()[0].volume_mm3 != pytest.approx(_SECRET_VOLUME)
    _assert_canary_absent(outside, scratch)
