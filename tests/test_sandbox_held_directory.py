"""A helper directory is opened by its handle.

Replacing that directory's name does not change the file that is read.
The marker file outside the project stays as it was written. Nothing is
sent to another machine.
"""

from __future__ import annotations

import os
from pathlib import Path

from aicad.file_access import read_text
from aicad.paths import held_tool_directory, publish_text, run_in_held_directory


def _real_directory(folder: Path) -> Path:
    text = os.fspath(folder).replace("\\", "/")
    if text.startswith("/proc/self/fd/"):
        return Path(os.readlink(text))
    return Path(folder)


def test_a_replaced_helper_directory_is_not_the_one_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text("untouched\n", encoding="utf-8")
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "keep.txt").write_text("CANARY_HIT\n", encoding="utf-8")
    moved = None
    real = None
    try:
        with held_tool_directory(prefix="aicad-held-") as folder:
            publish_text(folder / "keep.txt", "ORIGINAL\n", root=folder)
            real = _real_directory(folder)
            moved = real.with_name(real.name + "-moved")
            try:
                os.rename(real, moved)
            except OSError:
                moved = None
            else:
                try:
                    os.symlink(canary, real, target_is_directory=True)
                except OSError:
                    real.mkdir()
                    (real / "keep.txt").write_text("CANARY_HIT\n", encoding="utf-8")
            text = read_text(folder / "keep.txt", maximum=1000)
            assert text == "ORIGINAL\n"
            assert "CANARY_HIT" not in text
            publish_text(folder / "after.txt", "STILL\n", root=folder)
            if moved is not None:
                assert (moved / "after.txt").read_text(encoding="utf-8") == "STILL\n"
                assert not (canary / "after.txt").exists()
            if os.name != "nt":
                completed = run_in_held_directory(
                    folder,
                    ["/bin/cat", "keep.txt"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert completed.returncode == 0
                assert completed.stdout == "ORIGINAL\n"
        assert marker.read_text(encoding="utf-8") == "untouched\n"
        assert (canary / "keep.txt").read_text(encoding="utf-8") == "CANARY_HIT\n"
        if moved is not None:
            assert not moved.exists()
    finally:
        if real is not None and real.is_symlink():
            real.unlink()
