"""Matplotlib's private config directory stays the directory that was opened.

The marker is SANDBOX_CANARY.txt containing CANARY_HIT. Replacing that
directory's name does not make a later file land in the replacement.
"""

from __future__ import annotations

from pathlib import Path

from aicad.sandbox import (
    _private_matplotlib_dir,
    close_matplotlib_directory,
    open_matplotlib_directory,
)

_MARKER = "SANDBOX_CANARY.txt"
_TEXT = "CANARY_HIT\n"


def test_a_renamed_config_directory_does_not_receive_the_file(tmp_path, monkeypatch):
    marker = tmp_path / _MARKER
    marker.write_text(_TEXT, encoding="utf-8")
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "matplotlibrc").write_text(_TEXT, encoding="utf-8")
    held = open_matplotlib_directory()
    try:
        directory = held.original
        moved = directory.with_name(directory.name + "-moved")
        try:
            directory.rename(moved)
        except OSError:
            (held.public / "probe.txt").write_text("ORIGINAL\n", encoding="utf-8")
            assert not (canary / "probe.txt").exists()
            assert (directory / "probe.txt").read_text(encoding="utf-8") == "ORIGINAL\n"
        else:
            directory.symlink_to(canary, target_is_directory=True)
            (held.public / "probe.txt").write_text("ORIGINAL\n", encoding="utf-8")
            assert (moved / "probe.txt").read_text(encoding="utf-8") == "ORIGINAL\n"
            assert (canary / "matplotlibrc").read_text(encoding="utf-8") == _TEXT
            assert not (canary / "probe.txt").exists()
    finally:
        close_matplotlib_directory(held)
    assert marker.read_text(encoding="utf-8") == _TEXT
    assert (canary / "matplotlibrc").read_text(encoding="utf-8") == _TEXT


def test_the_private_directory_is_the_one_just_opened(monkeypatch):
    import aicad.sandbox as sandbox

    created = {}
    real = open_matplotlib_directory

    def wrapped():
        held = real()
        created["held"] = held
        return held

    monkeypatch.setattr(sandbox, "open_matplotlib_directory", wrapped)
    monkeypatch.setattr(sandbox, "_MATPLOTLIB_CONFIG", None)
    path = _private_matplotlib_dir()
    assert path == created["held"].public
    assert Path(path).is_dir()
