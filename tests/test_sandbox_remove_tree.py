"""Removing a project folder unlinks a directory link.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT, outside the folder
being removed. A symlink or a Windows junction is removed, and the marker stays.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_CANARY_TEXT = "CANARY_HIT\n"


def _link(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(path), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                return
        pytest.fail(f"this system cannot create a link ({exc})")


def _canary(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SANDBOX_CANARY.txt"
    path.write_text(_CANARY_TEXT, encoding="utf-8")
    return path


def test_a_directory_link_is_unlinked(tmp_path):
    from aicad.paths import remove_tree

    outside = tmp_path / "outside"
    canary = _canary(outside)
    link = tmp_path / "renders"
    _link(link, outside)
    remove_tree(link)
    assert not link.exists()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_a_child_directory_link_is_unlinked(tmp_path):
    from aicad.paths import remove_tree

    outside = tmp_path / "outside"
    canary = _canary(outside)
    (outside / "note.txt").write_text("keep\n", encoding="utf-8")
    folder = tmp_path / "renders"
    nested = folder / "real"
    nested.mkdir(parents=True)
    (nested / "frame.png").write_bytes(b"png")
    _link(folder / "joined", outside)
    remove_tree(folder)
    assert not folder.exists()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
    assert (outside / "note.txt").read_text(encoding="utf-8") == "keep\n"


def test_frame_cleanup_unlinks_a_child_directory_link(tmp_path):
    from aicad.animation import _remove_frames

    root = tmp_path / "project"
    outside = tmp_path / "outside"
    canary = _canary(outside)
    frames = root / "renders" / "frames"
    frames.mkdir(parents=True)
    (frames / "frame_0000.png").write_bytes(b"png")
    _link(frames / "joined", outside)
    _remove_frames(frames, root)
    assert not frames.exists()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT


def test_plan_restore_unlinks_a_directory_link(tmp_path):
    from aicad.plan import restore_plan_files

    root = tmp_path / "project"
    outside = tmp_path / "outside"
    canary = _canary(outside)
    plans = root / "plans"
    plans.mkdir(parents=True)
    (plans / "shelf.json").write_text("{}\n", encoding="utf-8")
    _link(plans / "shelf", outside)
    restore_plan_files(root, {})
    assert not (plans / "shelf").exists()
    assert not (plans / "shelf.json").exists()
    assert canary.read_text(encoding="utf-8") == _CANARY_TEXT
