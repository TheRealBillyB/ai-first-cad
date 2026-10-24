"""Windows path forms and the native-stdout redirect.

These run on Linux too. A drive letter, a UNC path, or a backslash ``..`` has
to be refused from the text, because opening that string on POSIX would not
see the escape a Windows machine would. The redirect is checked by pointing
``os.devnull`` at a real file: a hard-coded ``/dev/null`` would miss it.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import AICADError, DocumentError, GeometryError
from aicad.kernel import divert_native_stdio
from aicad.paths import require_session_file, session_path_escapes
from aicad.workspace import Workspace

BOX = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]

_STEP_ESCAPES = [
    r"C:\outside\hidden.step",
    "C:/outside/hidden.step",
    "C:hidden.step",
    r"\\server\share\hidden.step",
    "//server/share/hidden.step",
    r"\outside\hidden.step",
    r"\\?\C:\outside\hidden.step",
    r"\\.\C:\outside\hidden.step",
    r"\\?\UNC\server\share\hidden.step",
    r"..\outside\hidden.step",
    r"foo\..\..\outside\hidden.step",
    "../outside/hidden.step",
    "foo/../../outside/hidden.step",
    "/outside/hidden.step",
]

_RELATIVE_OK = [
    "imports/hidden.step",
    r"imports\hidden.step",
    "imports/nested/hidden.step",
]


def _session(victim: Path, step_file: str) -> Document:
    payload = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [{"name": "stolen", "source": "step", "step_file": step_file}],
    }
    (victim / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    return Document(victim)


def _outside_step(tmp_path: Path) -> Path:
    maker = tmp_path / "maker"
    maker.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    document = Document(maker)
    document.create_part_from_ops("box", BOX)
    step = outside / "hidden.step"
    document.export("box", step, "step")
    return step


def test_session_path_classifier_knows_windows_forms():
    for raw in _STEP_ESCAPES:
        assert session_path_escapes(raw), raw
    for raw in _RELATIVE_OK:
        assert session_path_escapes(raw) is False, raw
    assert session_path_escapes("parts/../hidden.step") is True


@pytest.mark.parametrize("step_file", _STEP_ESCAPES)
def test_step_file_windows_forms_are_not_imported(tmp_path, step_file):
    _outside_step(tmp_path)
    digest = hashlib.sha256(step_file.encode("utf-8")).hexdigest()[:16]
    victim = tmp_path / "victim" / digest
    victim.mkdir(parents=True)
    reopened = _session(victim, step_file)
    info = reopened.list_parts()[0]
    assert info.built is False
    assert info.volume_mm3 != pytest.approx(64)
    assert any("outside" in problem and "not read" in problem for problem in reopened.problems)
    # The refusal is from the text. Nothing was joined into the project tree.
    assert list(victim.rglob("hidden.step")) == []


def test_a_relative_step_inside_the_project_still_imports(tmp_path):
    step = _outside_step(tmp_path)
    project = tmp_path / "project"
    imports = project / "imports"
    imports.mkdir(parents=True)
    kept = imports / "hidden.step"
    kept.write_bytes(step.read_bytes())
    reopened = _session(project, "imports/hidden.step")
    assert reopened.list_parts()[0].built is True
    assert reopened.measure("stolen").volume_mm3 == pytest.approx(64)
    assert reopened.problems == []


@pytest.mark.skipif(os.name != "nt", reason="a backslash is a separator only on Windows")
def test_a_backslash_relative_step_imports_on_windows(tmp_path):
    step = _outside_step(tmp_path)
    project = tmp_path / "project"
    imports = project / "imports"
    imports.mkdir(parents=True)
    (imports / "hidden.step").write_bytes(step.read_bytes())
    reopened = _session(project, r"imports\hidden.step")
    assert reopened.measure("stolen").volume_mm3 == pytest.approx(64)


def test_a_symlinked_step_directory_outside_the_project_is_not_read(tmp_path):
    step = _outside_step(tmp_path)
    project = tmp_path / "project"
    link = project / "imports"
    project.mkdir()
    try:
        link.symlink_to(step.parent, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")
    reopened = _session(project, "imports/hidden.step")
    info = reopened.list_parts()[0]
    assert info.built is False
    assert any("outside" in problem for problem in reopened.problems)


def test_a_session_path_trims_like_a_windows_export(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "hidden.step"
    outside.write_bytes(b"step")
    for raw in (".. .", ".. ./hidden.step", "imports/.. .", "..../..../hidden.step", r"....\....\hidden.step"):
        with pytest.raises(DocumentError, match="outside"):
            require_session_file(project, raw, "STEP file", DocumentError)
    assert outside.read_bytes() == b"step"


def test_export_rejects_windows_escapes_and_keeps_paths_inside(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.execute("create_part_from_ops", {"name": "box", "ops": BOX})
    escapes = [
        r"C:\Temp\out.step",
        "C:/Temp/out.step",
        "C:out.step",
        r"\\server\share\out.step",
        "//server/share/out.step",
        r"\Temp\out.step",
        r"\\?\C:\Temp\out.step",
        r"foo\..\..\outside.step",
        "../outside.step",
        "exports/../../outside.step",
        ".. .",
        ".. ./hidden.step",
        "imports/.. .",
    ]
    for raw in escapes:
        with pytest.raises(GeometryError, match="outside"):
            workspace.execute(
                "export_part",
                {"name": "box", "format": "step", "path": raw},
            )
    assert list(tmp_path.rglob("*.step")) == []

    relative = workspace.execute(
        "export_part",
        {"name": "box", "format": "step", "path": "exports/kept.step"},
    )
    assert Path(relative["path"]).is_file()
    assert Path(relative["path"]).resolve().is_relative_to(tmp_path.resolve())

    inside = tmp_path / "exports" / "absolute.step"
    written = workspace.execute(
        "export_part",
        {"name": "box", "format": "step", "path": str(inside)},
    )
    assert Path(written["path"]).is_file()
    home = os.path.expanduser("~/aicad-not-in-the-project.step")
    with pytest.raises(AICADError, match="outside"):
        workspace.execute("export_part", {"name": "box", "format": "step", "path": "~/aicad-not-in-the-project.step"})
    assert not Path(home).exists()


def _c_printf(payload: bytes) -> None:
    if os.name == "nt":
        # ucrtbase does not export printf. _write is the Universal CRT write
        # to the same stdout descriptor the redirect replaces.
        libc = ctypes.CDLL("ucrtbase")
        libc._write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        libc._write.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(payload)
        libc._write(1, buffer, len(payload))
        return
    libc = ctypes.CDLL(None)
    libc.printf(payload)


def test_native_stdout_goes_to_os_devnull_and_comes_back(tmp_path, monkeypatch, capfd):
    sink = tmp_path / "null-device"
    sink.write_bytes(b"")
    monkeypatch.setattr(os, "devnull", str(sink))
    with divert_native_stdio(stderr=True):
        os.write(1, b"OUT")
        os.write(2, b"ERR")
        _c_printf(b"NATIVE")
    os.write(1, b"BACK")
    captured = capfd.readouterr()
    stored = sink.read_bytes()
    assert b"OUT" in stored
    assert b"ERR" in stored
    assert b"NATIVE" in stored
    assert b"BACK" not in stored
    assert "OUT" not in captured.out
    assert "NATIVE" not in captured.out
    assert "BACK" in captured.out
    assert "ERR" not in captured.err


def test_divert_leaves_stderr_alone_unless_asked(tmp_path, monkeypatch, capfd):
    sink = tmp_path / "null-device"
    sink.write_bytes(b"")
    monkeypatch.setattr(os, "devnull", str(sink))
    with divert_native_stdio():
        os.write(1, b"OUT")
        os.write(2, b"ERR")
    captured = capfd.readouterr()
    assert b"OUT" in sink.read_bytes()
    assert b"ERR" not in sink.read_bytes()
    assert "ERR" in captured.err
    assert "OUT" not in captured.out


def test_nested_divert_restores_the_outer_descriptor(capfd):
    with divert_native_stdio():
        with divert_native_stdio():
            os.write(1, b"INNER")
        os.write(1, b"OUTER")
    os.write(1, b"AFTER")
    captured = capfd.readouterr()
    assert "INNER" not in captured.out
    assert "OUTER" not in captured.out
    assert captured.out == "AFTER"


def test_divert_restores_stdout_when_the_body_raises(tmp_path, monkeypatch, capfd):
    sink = tmp_path / "null-device"
    sink.write_bytes(b"")
    monkeypatch.setattr(os, "devnull", str(sink))
    with pytest.raises(RuntimeError, match="boom"):
        with divert_native_stdio():
            os.write(1, b"HIDDEN")
            raise RuntimeError("boom")
    os.write(1, b"BACK")
    captured = capfd.readouterr()
    assert sink.read_bytes() == b"HIDDEN"
    assert captured.out == "BACK"
