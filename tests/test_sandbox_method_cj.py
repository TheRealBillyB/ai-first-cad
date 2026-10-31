"""The disk-quota watch stays on the project directory it opened.

The marker is CANARY_HIT. A link is not counted, and a child record cannot
replace the bytes already counted for a live process. The marker stays where
it was written.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from aicad.sandbox import _DiskWatch, _open_real_directory

_MARKER = "CANARY_HIT"


def test_a_child_record_cannot_replace_a_live_process(tmp_path):
    marker = tmp_path / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    disk = _DiskWatch(project, {}, file_cap=10**9, disk_cap=10_000)
    disk._counter = lambda pid: 5000 if pid == 7 else None  # type: ignore[method-assign]
    report = project / "child-writes.json"
    report.write_text(json.dumps({"7": 0, "9": 100}) + "\n", encoding="utf-8")
    disk.child_report = report
    disk.account_io({7})
    assert disk.io_peak == 5100
    disk.account_io({7})
    assert disk.io_peak == 5100
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"


def test_a_link_is_not_a_directory_the_quota_watch_opens(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "SANDBOX_CANARY.txt"
    marker.write_text(_MARKER + "\n", encoding="utf-8")
    (outside / "big.bin").write_bytes(b"x" * 5000)
    project = tmp_path / "project"
    project.mkdir()
    link = project / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")
    assert _open_real_directory(link) is None
    if sys.platform.startswith("linux"):
        opened = _open_real_directory(project)
        assert isinstance(opened, int) and opened >= 0
        os.close(opened)
    disk = _DiskWatch(project, {}, file_cap=10**9, disk_cap=1000)
    disk.scan_new_files()
    disk.note_files()
    assert not disk.hit.is_set()
    assert marker.read_text(encoding="utf-8") == _MARKER + "\n"
