"""Shared files with lookalike paths, or nested archives, stay inside the project.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. Opening the project,
importing the asset, or using a relative path from that content must not
create it outside the project.
"""

from __future__ import annotations

import io
import json
import shutil
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import DocumentError, GeometryError
from aicad.paths import require_export_path, require_session_file
from aicad.studio import open_project as open_in_studio
from aicad.workspace import Workspace

CANARY = "SANDBOX_CANARY.txt"
CANARY_TEXT = "CANARY_HIT\n"
BOX = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]

# Each value is a project-relative path. Compatibility lookalikes fold to ".."
# or a separator under NFKC. The others are separator, bidi, or Windows forms.
LOOKALIKES = [
    "\uff0e\uff0e/\uff0e\uff0e/hidden.step",
    "\uff0e\uff0e\uff0f\uff0e\uff0e\uff0fhidden.step",
    "\u2025/\u2025/hidden.step",
    "\u2024\u2024/\u2024\u2024/hidden.step",
    "parts\u2215..\u2215..\u2215hidden.step",
    "parts\u2044..\u2044..\u2044hidden.step",
    "parts\uff3c..\uff3c..\uff3chidden.step",
    "parts\u00a5..\u00a5..\u00a5hidden.step",
    "imports/\u202e../\u202e../hidden.step",
    "imports/\u202e..\u202c/hidden.step",
    "imports/\u2066..\u2069/hidden.step",
    ".\u200b./.\u200b./hidden.step",
    "..\u00a0/..\u00a0/hidden.step",
    "..\u3000/..\u3000/hidden.step",
    "C\uff1a\\Windows\\hidden.step",
    ".. /.. /hidden.step",
    ".. ./hidden.step",
    ".../.../hidden.step",
    "NUL",
    "COM\u00b9.step",
    "imports/hidden.step:SANDBOX_CANARY.txt",
    "%2e%2e/%2e%2e/hidden.step",
]
LOOKALIKE_IDS = [
    "fullwidth-dots",
    "fullwidth-slash",
    "two-dot-leader",
    "one-dot-leader",
    "division-slash",
    "fraction-slash",
    "fullwidth-backslash",
    "yen-sign",
    "rtl-override",
    "rtl-component",
    "bidi-isolate",
    "zero-width",
    "nbsp-parent",
    "ideographic-space",
    "fullwidth-drive",
    "trailing-space-parent",
    "trailing-dot-parent",
    "three-dots",
    "device-name",
    "device-superscript",
    "alternate-data-stream",
    "percent-encoding",
]


@pytest.fixture(scope="module")
def box_step(tmp_path_factory):
    folder = tmp_path_factory.mktemp("box")
    document = Document(folder / "maker")
    document.create_part_from_ops("box", BOX)
    step = folder / "hidden.step"
    document.export("box", step, "step")
    return step


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _files(folder: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if not folder.exists():
        return found
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            found[path.relative_to(folder).as_posix()] = b"link:" + Path(path).readlink().as_posix().encode()
            continue
        if path.is_file():
            found[path.relative_to(folder).as_posix()] = path.read_bytes()
    return found


def _link(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this machine cannot create a symlink")


def _write_session(project: Path, field: str, value: str) -> None:
    if field == "step_file":
        part = {"name": "shared", "source": "step", "step_file": value}
    else:
        part = {
            "name": "shared",
            "source": "script",
            "script": "part = Box(1, 1, 1)\n",
            "script_sha256": "0" * 64,
            "geometry_file": value,
        }
    payload = {"units": "mm", "sketches": [], "assemblies": [], "parts": [part]}
    project.mkdir(parents=True, exist_ok=True)
    (project / "session.json").write_text(json.dumps(payload), encoding="utf-8")


def _assert_no_canary(tmp_path: Path) -> None:
    assert list(tmp_path.rglob(CANARY)) == []


def _open_shared(project: Path, value: str) -> Document:
    try:
        resolved = require_session_file(project, value, "step", DocumentError)
    except DocumentError:
        resolved = None
    else:
        assert resolved.is_relative_to(project.resolve())
    before = _files(project)
    document = Document(project)
    info = document.list_parts()[0]
    assert info.built is False
    assert info.volume_mm3 != pytest.approx(64)
    assert _files(project) == before
    _assert_no_canary(project.parent)
    return document


def _inner_zip(outside: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../" + CANARY, CANARY_TEXT)
        archive.writestr("../../" + CANARY, CANARY_TEXT)
        archive.writestr("../../../" + CANARY, CANARY_TEXT)
        archive.writestr(str(outside / CANARY), CANARY_TEXT)
        archive.writestr("..\\" + CANARY, CANARY_TEXT)
        archive.writestr("\uff0e\uff0e/\uff0e\uff0e/" + CANARY, CANARY_TEXT)
        link = zipfile.ZipInfo("nested-link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../" + CANARY)
    return buffer.getvalue()


def _outer_zip(outside: Path, *, symlink: bool, nested: bool) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("readme.txt", "shared asset\n")
        if nested:
            archive.writestr("aux/inner.zip", _inner_zip(outside))
        if symlink:
            unix = zipfile.ZipInfo("3D/unix-link")
            unix.create_system = 3
            unix.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(unix, "../" + CANARY)
            windows = zipfile.ZipInfo("3D/windows-link")
            windows.create_system = 0
            windows.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(windows, "..\\" + CANARY)
    return buffer.getvalue()


@pytest.mark.parametrize("field", ["step_file", "geometry_file"])
@pytest.mark.parametrize("step_file", LOOKALIKES, ids=LOOKALIKE_IDS)
def test_lookalike_shared_paths_do_not_escape(tmp_path, box_step, field, step_file):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    shutil.copy(box_step, tmp_path / "hidden.step")
    _write_session(project, field, step_file)
    _open_shared(project, step_file)
    try:
        exported = require_export_path(project, step_file, "Export path", GeometryError)
    except GeometryError:
        exported = None
    else:
        assert exported.is_relative_to(project.resolve())
    assert (tmp_path / "hidden.step").read_bytes() == box_step.read_bytes()


def test_a_parent_segment_in_a_shared_step_is_not_read(tmp_path, box_step):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    shutil.copy(box_step, tmp_path / "hidden.step")
    _write_session(project, "step_file", "../hidden.step")
    _open_shared(project, "../hidden.step")


def test_a_lookalike_folder_link_is_not_followed_for_a_shared_step(tmp_path, box_step, isolated_config):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    shutil.copy(box_step, outside / "hidden.step")
    folder = project / "\u0456mports"
    _link(folder, outside)
    raw = "\u0456mports/hidden.step"
    _write_session(project, "step_file", raw)
    _open_shared(project, raw)
    assert (outside / "hidden.step").read_bytes() == box_step.read_bytes()
    home = Workspace(tmp_path / "here")
    home.execute("open_project", {"path": str(project)})
    assert home.document.list_parts()[0].built is False
    _assert_no_canary(tmp_path)


def test_nfc_and_nfd_names_do_not_alias_a_link_out_of_the_project(tmp_path, box_step):
    import unicodedata

    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    shutil.copy(box_step, outside / "hidden.step")
    nfc = "caf\u00e9"
    nfd = unicodedata.normalize("NFD", nfc)
    _link(project / nfc, outside)
    _write_session(project, "step_file", f"{nfd}/hidden.step")
    document = _open_shared(project, f"{nfd}/hidden.step")
    # The composed name is the link. Asking for it directly is refused.
    _write_session(project, "step_file", f"{nfc}/hidden.step")
    reopened = Document(project)
    assert reopened.list_parts()[0].built is False
    assert document.list_parts()[0].built is False
    assert (outside / "hidden.step").read_bytes() == box_step.read_bytes()
    _assert_no_canary(tmp_path)


def test_a_unicode_name_inside_the_project_still_imports(tmp_path, box_step):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    folder = project / "imports" / "\u96f6\u4ef6"
    folder.mkdir(parents=True)
    shutil.copy(box_step, folder / "hidden.step")
    _write_session(project, "step_file", "imports/\u96f6\u4ef6/hidden.step")
    document = Document(project)
    assert document.list_parts()[0].built is True
    assert document.measure("shared").volume_mm3 == pytest.approx(64)
    assert document.problems == []
    _assert_no_canary(tmp_path)


def test_relative_lookalike_exports_stay_inside_the_project(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    document = Document(project)
    document.create_part_from_ops("box", BOX)
    for raw in LOOKALIKES:
        if raw in {"NUL", "COM\u00b9.step"}:
            continue
        try:
            written = document.export("box", raw, "step")
        except GeometryError:
            continue
        assert written.resolve().is_relative_to(project.resolve())
    _assert_no_canary(tmp_path)


def test_export_through_a_lookalike_folder_link_does_not_write_outside(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    document = Document(project)
    document.create_part_from_ops("box", BOX)
    _link(project / "\u0456mports", outside)
    raw = "\u0456mports/" + CANARY
    with pytest.raises(GeometryError, match="outside"):
        document.export("box", raw, "step")
    workspace = Workspace(project)
    with pytest.raises(GeometryError, match="outside"):
        workspace.execute("export_part", {"name": "box", "format": "step", "path": raw})
    assert _files(outside) == {}
    _assert_no_canary(tmp_path)


def test_import_copy_does_not_follow_a_folder_link_outside(tmp_path, box_step):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    _link(project / "imports", outside)
    document = Document(project)
    with pytest.raises(GeometryError, match="outside"):
        document.import_step("kept", box_step)
    assert _files(outside) == {}
    assert not (outside / "kept.step").exists()
    _assert_no_canary(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows trims a trailing dot or space on the last segment")
@pytest.mark.parametrize("raw", [".. .", ".. ./hidden.step", "imports/.. ."])
def test_windows_trimmed_last_segment_does_not_leave_the_project(tmp_path, box_step, raw):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    shutil.copy(box_step, tmp_path / "hidden.step")
    _write_session(project, "step_file", raw)
    _open_shared(project, raw)
    document = Document(project)
    document.create_part_from_ops("box", BOX)
    try:
        written = document.export("box", raw, "step")
    except GeometryError:
        written = None
    if written is not None:
        assert written.resolve().is_relative_to(project.resolve())
    _assert_no_canary(tmp_path)


@pytest.mark.parametrize("container", ["part.3mf", "project.zip"])
@pytest.mark.parametrize(
    "shape",
    ["nested", "symlink", "mixed"],
    ids=["nested-zip", "symlink-entries", "mixed-entries"],
)
def test_nested_archive_in_a_shared_project_is_not_unpacked(tmp_path, container, shape, isolated_config):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    payload = _outer_zip(outside, nested=shape in {"nested", "mixed"}, symlink=shape in {"symlink", "mixed"})
    (project / container).write_bytes(payload)
    _write_session(project, "step_file", container)
    before = _files(project)
    _open_shared(project, container)
    assert _files(project) == before
    home = Workspace(tmp_path / "here")
    open_in_studio(home, project)
    assert home.document.list_parts()[0].built is False
    assert _files(project) == before
    assert _files(outside) == {}
    for path in project.rglob("*"):
        if path.is_symlink():
            assert path.resolve().is_relative_to(project.resolve())


def test_importing_a_shared_3mf_does_not_unpack_a_nested_archive(tmp_path, isolated_config):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    project.mkdir()
    archive = project / "part.3mf"
    archive.write_bytes(_outer_zip(outside, nested=True, symlink=True))
    document = Document(project)
    before = _files(project)
    with pytest.raises(GeometryError):
        document.import_step("mesh", archive)
    assert _files(outside) == {}
    assert _files(project) == before
    _assert_no_canary(tmp_path)
    home = Workspace(tmp_path / "here")
    with pytest.raises(GeometryError):
        home.open_project(archive)


def test_opening_a_project_zip_file_does_not_unpack_it(tmp_path, isolated_config):
    outside = tmp_path / "outside"
    outside.mkdir()
    archive = tmp_path / "shared.zip"
    archive.write_bytes(_outer_zip(outside, nested=True, symlink=True))
    home = Workspace(tmp_path / "here")
    with pytest.raises(GeometryError):
        open_in_studio(home, archive)
    assert _files(outside) == {}
    _assert_no_canary(tmp_path)
