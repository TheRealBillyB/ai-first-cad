"""A file is opened from its directory, and the opened handle is what gets checked.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, outside the directory
under test. A link to it, a second name, and a file past the cap are refused.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

from aicad.file_access import TooLarge, read_file, read_from_root, read_prefix
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
    (root / "note.txt").write_text("keep\n", encoding="utf-8")
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
