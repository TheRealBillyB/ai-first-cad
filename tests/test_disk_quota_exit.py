"""A script that exits before the next disk sample is still counted.

The canary is a marker that must not be written outside the project.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from aicad.sandbox import _DiskWatch, _exited_unreaped, _wait_script
from aicad.sandbox_run import _wait_for_quota_hold


def test_the_quota_hold_waits_until_the_parent_releases(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setenv("AICAD_QUOTA_HOLD", str(read_fd))
    finished = threading.Event()

    def run() -> None:
        _wait_for_quota_hold()
        finished.set()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        time.sleep(0.05)
        assert not finished.is_set()
        os.write(write_fd, b"\n")
        thread.join(2)
        assert finished.is_set()
    finally:
        os.close(write_fd)
        if thread.is_alive():
            os.close(read_fd)
            thread.join(2)


def test_a_closed_quota_hold_does_not_block(monkeypatch):
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    monkeypatch.setenv("AICAD_QUOTA_HOLD", str(read_fd))
    _wait_for_quota_hold()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux keeps the write counter in /proc")
def test_the_exit_reading_counts_bytes_written_before_any_sample(tmp_path):
    read_fd, write_fd = os.pipe()
    code = (
        "import os\n"
        "from pathlib import Path\n"
        "os.read(int(os.environ['HOLD']), 1)\n"
        "Path('gone.bin').write_bytes(b'x' * 300000)\n"
        "Path('gone.bin').unlink()\n"
    )
    env = os.environ.copy()
    env["HOLD"] = str(read_fd)
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        pass_fds=(read_fd,),
    )
    os.close(read_fd)
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    try:
        disk.hold_counter(proc.pid)
        disk.baseline(proc.pid)
        assert proc.pid in disk._io_fds
        os.write(write_fd, b"\n")
        os.close(write_fd)
        write_fd = -1
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            exited = _exited_unreaped(proc.pid)
            if exited:
                break
            time.sleep(0.01)
        else:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail("the child did not exit")
        disk.note_io({proc.pid})
        proc.wait(timeout=5)
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        disk.close_counters()
    assert disk.io_peak >= 300000
    assert disk.hit.is_set()
    message = disk.settle()
    assert message is not None
    assert "wrote more than 1000 bytes" in message
    assert not (tmp_path / "gone.bin").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux shows a zombie in /proc")
def test_the_exit_reading_happens_before_the_process_is_collected():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
    seen: dict[str, bool] = {}

    def before_reap() -> None:
        seen["still_listed"] = Path(f"/proc/{proc.pid}").exists()

    try:
        _wait_script(proc, 30, None, before_reap=before_reap)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert seen["still_listed"] is True
    assert not Path(f"/proc/{proc.pid}").exists()
