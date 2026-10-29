"""A file is opened from its directory, and the opened handle is what gets checked.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the directory
under test. A link to it, a second name, and a file past the cap are refused.
"""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path

import pytest

from aicad.file_access import TooLarge, read_file, read_from_root, read_prefix, read_tail, read_text
from aicad.paths import _TooLarge

_CANARY_NAME = "SANDBOX_CANARY.txt"
_CANARY_TEXT = "CANARY_HIT\n"


def _canary(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _CANARY_NAME
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def _link(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if directory and sys.platform == "win32":
            import subprocess

            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(path), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                return
        pytest.fail(f"this system cannot create a link ({exc})")


def test_a_descriptor_path_is_not_reopened_as_a_file():
    from aicad.paths import _reopen_accepts_create

    assert _reopen_accepts_create(Path("/dev/fd/6")) is False
    assert _reopen_accepts_create(Path("\\dev\\fd\\6")) is False


def test_a_windows_read_does_not_ask_to_change_the_directory():
    from aicad import file_access

    change = 0x2 | 0x4 | 0x40 | 0x100 | 0x10000
    assert file_access._DIR_READ & change == 0


def test_the_windows_rename_record_keeps_the_directory_handle_in_place():
    from aicad import file_access

    raw = file_access._rename_information(0x1234, "a.txt", flags=None)
    assert raw[:8] == b"\x00" * 8
    assert int.from_bytes(raw[8:16], "little") == 0x1234
    assert int.from_bytes(raw[16:20], "little") == len("a.txt".encode("utf-16-le"))
    assert raw[20:] == "a.txt".encode("utf-16-le")
    flagged = file_access._rename_information(0x1234, "a.txt", flags=0x2)
    assert int.from_bytes(flagged[:4], "little") == 0x2
    assert flagged[8:] == raw[8:]


def test_a_windows_directory_is_opened_or_created_in_one_request(monkeypatch):
    from aicad import file_access

    calls: list[int] = []

    def fake_nt_open(parent, name, *, access, directory, create, security=None, disposition=None):
        del parent, name, access, directory, create, security
        calls.append(file_access._FILE_OPEN if disposition is None else disposition)
        raise OSError(errno.EIO, "stop")

    monkeypatch.setattr(file_access, "_nt_open", fake_nt_open)
    monkeypatch.setattr(file_access, "_os_handle", lambda descriptor: descriptor)
    with pytest.raises(OSError, match="stop"):
        file_access._windows_open_child(3, "exports", create=True, write=True)
    with pytest.raises(OSError, match="stop"):
        file_access._windows_open_child(3, "exports", create=False, write=False)
    assert calls == [file_access._FILE_OPEN_IF, file_access._FILE_OPEN]


def test_a_windows_directory_write_does_not_ask_to_delete_the_directory():
    from aicad import file_access

    assert file_access._DIR_ACCESS & 0x10000 == 0
    assert file_access._DIR_ACCESS & 0x2
    assert file_access._DIR_ACCESS & 0x4


def test_a_windows_write_can_read_the_file_record():
    from aicad import file_access

    assert file_access._FILE_WRITE_ACCESS & 0x80
    assert file_access._FILE_READ_ACCESS & 0x80


def test_an_installed_module_is_read_with_a_size_cap(tmp_path):
    from aicad.paths import _read_installed_source

    source = tmp_path / "_options.py"
    source.write_bytes(b"options = Options()\r\n")
    assert _read_installed_source(os.fspath(source)) == "options = Options()\n"
    huge = tmp_path / "huge.py"
    huge.write_bytes(b"x" * 1_000_001)
    with pytest.raises(OSError):
        _read_installed_source(os.fspath(huge))


def test_the_windows_file_record_keeps_the_link_count_in_place():
    from aicad import file_access

    info = file_access._FileInfo
    assert ctypes.sizeof(info) == 52
    assert info.dwFileAttributes.offset == 0
    assert info.nFileSizeLow.offset == 36
    assert info.nNumberOfLinks.offset == 40


def test_a_regular_file_is_read_from_its_directory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "note.txt").write_bytes(b"keep\n")
    assert read_from_root(root, Path("note.txt"), 100) == b"keep\n"
    assert read_file(root / "note.txt", maximum=100) == b"keep\n"


def test_a_link_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    root = tmp_path / "project"
    root.mkdir()
    _link(root / "note.txt", canary)
    with pytest.raises(OSError) as caught:
        read_from_root(root, Path("note.txt"), 100)
    assert _CANARY_TEXT.encode() not in str(caught.value).encode()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_directory_link_is_not_opened(tmp_path):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    root = tmp_path / "project"
    nested = root / "documents"
    nested.mkdir(parents=True)
    (nested / "note.txt").write_text("keep\n", encoding="utf-8")
    moved = tmp_path / "hold"
    nested.rename(moved)
    _link(nested, outside, directory=True)
    with pytest.raises(OSError) as caught:
        read_from_root(root, Path("documents") / "note.txt", 100)
    assert _CANARY_TEXT.encode() not in str(caught.value).encode()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert list(outside.iterdir()) == [canary]


def test_a_file_with_another_name_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    canary = _canary(outside)
    root = tmp_path / "project"
    root.mkdir()
    try:
        os.link(canary, root / "alias.txt")
    except OSError as exc:
        pytest.fail(f"this system cannot create a hard link ({exc})")
    with pytest.raises((OSError, _TooLarge)) as caught:
        read_from_root(root, Path("alias.txt"), 100)
    assert b"CANARY_HIT" not in getattr(caught.value, "strerror", "").encode()
    assert _CANARY_TEXT not in str(caught.value)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_an_oversized_file_is_not_read(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    target = root / "big.bin"
    target.write_bytes(b"x" * 32)
    with pytest.raises(_TooLarge):
        read_from_root(root, Path("big.bin"), 8)
    with pytest.raises(TooLarge):
        read_prefix(target, 4, maximum=8)
    assert target.read_bytes() == b"x" * 32


def test_a_swapped_file_is_not_read(tmp_path, monkeypatch):
    from aicad import paths

    outside = tmp_path / "outside"
    canary = _canary(outside)
    root = tmp_path / "project"
    root.mkdir()
    (root / "note.txt").write_text("keep\n", encoding="utf-8")

    def swap(parent, relative):
        if Path(relative).name != "note.txt":
            return
        current = Path(parent) / relative
        if current.is_symlink() or not current.is_file():
            return
        current.unlink()
        _link(current, canary)

    monkeypatch.setattr(paths, "_before_nofollow", swap)
    with pytest.raises(OSError) as caught:
        read_file(root / "note.txt", maximum=100)
    assert _CANARY_TEXT not in str(caught.value)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


@pytest.mark.skipif(sys.platform != "win32", reason="junctions and alternate streams are Windows names")
def test_a_junction_and_an_alternate_stream_are_not_read(tmp_path):
    import subprocess

    outside = tmp_path / "outside"
    canary = _canary(outside)
    root = tmp_path / "project"
    root.mkdir()
    link = root / "joined"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(completed.stderr.strip() or "could not create a junction")
    with pytest.raises(OSError):
        read_from_root(root, Path("joined") / _CANARY_NAME, 100)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    stream = root / "note.txt:hidden"
    with pytest.raises(OSError):
        read_from_root(root, Path("note.txt:hidden"), 100)
    assert not stream.exists()


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text("{}\n", encoding="utf-8")
    return project


def test_a_nested_directory_link_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    nested = outside / "sub"
    nested.mkdir(parents=True)
    canary = nested / "log.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    picture = nested / "sheet.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nOUTSIDE")
    project = _project(tmp_path)
    kept = project / "docs" / "sub"
    kept.mkdir(parents=True)
    (kept / "note.txt").write_bytes(b"keep\n")
    _link(project / "renders", outside, directory=True)
    hidden = project / "renders" / "sub" / "log.txt"
    with pytest.raises(OSError) as caught:
        read_text(hidden, maximum=100)
    assert _CANARY_TEXT not in str(caught.value)
    with pytest.raises(OSError):
        read_tail(hidden, 100)
    with pytest.raises(OSError):
        read_prefix(project / "renders" / "sub" / "sheet.png", 8, maximum=1000)
    from aicad.blueprint import png_size
    from aicad.errors import GeometryError

    with pytest.raises(GeometryError) as picture_error:
        png_size(project / "renders" / "sub" / "sheet.png")
    assert _CANARY_TEXT not in str(picture_error.value)
    assert read_text(kept / "note.txt", maximum=100) == "keep\n"
    assert read_prefix(kept / "note.txt", 4, maximum=100) == b"keep"
    assert read_tail(kept / "note.txt", 100) == b"keep\n"
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert picture.read_bytes().startswith(b"\x89PNG")


def test_a_directory_link_inside_the_project_is_not_followed(tmp_path):
    project = _project(tmp_path)
    kept = project / "kept" / "sub"
    kept.mkdir(parents=True)
    (kept / "note.txt").write_text("keep\n", encoding="utf-8")
    _link(project / "alias", project / "kept", directory=True)
    with pytest.raises(OSError):
        read_text(project / "alias" / "sub" / "note.txt", maximum=100)
    assert (kept / "note.txt").read_text(encoding="utf-8") == "keep\n"


def test_a_linked_session_file_still_anchors_the_project(tmp_path):
    outside = tmp_path / "outside"
    nested = outside / "sub"
    nested.mkdir(parents=True)
    canary = nested / "log.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "marker.json"
    marker.write_text("{}\n", encoding="utf-8")
    _link(project / "session.json", marker)
    _link(project / "renders", outside, directory=True)
    with pytest.raises(OSError):
        read_text(project / "renders" / "sub" / "log.txt", maximum=100)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_remembered_project_does_not_follow_a_nested_directory_link(tmp_path):
    from aicad.paths import remember_project

    outside = tmp_path / "outside"
    nested = outside / "sub"
    nested.mkdir(parents=True)
    canary = nested / "log.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    project = tmp_path / "remembered"
    project.mkdir()
    remember_project(project)
    _link(project / "renders", outside, directory=True)
    with pytest.raises(OSError):
        read_file(project / "renders" / "sub" / "log.txt", maximum=100)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_file_outside_a_project_is_read_from_its_parent(tmp_path):
    real = tmp_path / "real" / "sub"
    real.mkdir(parents=True)
    (real / "note.txt").write_text("keep\n", encoding="utf-8")
    link = tmp_path / "link"
    _link(link, tmp_path / "real", directory=True)
    assert read_text(link / "sub" / "note.txt", maximum=100) == "keep\n"


def test_a_nested_link_under_a_linked_project_is_not_opened_from_the_parent(tmp_path):
    outside = tmp_path / "outside"
    nested = outside / "sub"
    nested.mkdir(parents=True)
    canary = nested / "note.txt"
    canary.write_text(_CANARY_TEXT, encoding="utf-8")
    real = tmp_path / "real"
    real.mkdir()
    (real / "session.json").write_text("{}\n", encoding="utf-8")
    (real / "kept.txt").write_bytes(b"keep\n")
    notes = real / "notes"
    notes.mkdir()
    (notes / "kept.txt").write_text("keep\n", encoding="utf-8")
    _link(real / "docs", outside, directory=True)
    link = tmp_path / "link"
    _link(link, real, directory=True)
    hidden = link / "docs" / "sub" / "note.txt"
    with pytest.raises(OSError):
        read_text(hidden, maximum=100)
    with pytest.raises(OSError):
        read_tail(hidden, 100)
    from aicad.paths import read_nofollow_bytes

    with pytest.raises(OSError):
        read_nofollow_bytes(hidden, maximum=100, label="note", error=OSError)
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert read_text(link / "kept.txt", maximum=100) == "keep\n"
    assert read_text(link / "notes" / "kept.txt", maximum=100) == "keep\n"
    assert read_nofollow_bytes(link / "kept.txt", maximum=100, label="note", error=OSError) == b"keep\n"


def test_a_project_link_under_another_directory_link_is_opened_from_the_project(tmp_path):
    outside = tmp_path / "outside"
    nested = outside / "sub"
    nested.mkdir(parents=True)
    canary = nested / "note.txt"
    canary.write_bytes(b"CANARY_HIT\n")
    real = tmp_path / "real"
    real.mkdir()
    (real / "session.json").write_text("{}\n", encoding="utf-8")
    (real / "kept.txt").write_bytes(b"keep\n")
    _link(real / "docs", outside, directory=True)
    storage = tmp_path / "storage"
    storage.mkdir()
    _link(storage / "project", real, directory=True)
    vault = tmp_path / "vault"
    _link(vault, storage, directory=True)
    hidden = vault / "project" / "docs" / "sub" / "note.txt"
    with pytest.raises(OSError):
        read_text(hidden, maximum=100)
    from aicad.paths import read_nofollow_bytes

    with pytest.raises(OSError):
        read_nofollow_bytes(hidden, maximum=100, label="note", error=OSError)
    assert canary.read_bytes() == b"CANARY_HIT\n"
    assert read_text(vault / "project" / "kept.txt", maximum=100) == "keep\n"


def test_a_parent_segment_is_not_treated_as_a_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_bytes(b"keep\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    from aicad.paths import read_nofollow_bytes

    path = nested / ".." / "outside" / "note.txt"
    assert read_nofollow_bytes(path, maximum=100, label="note", error=OSError) == b"keep\n"
