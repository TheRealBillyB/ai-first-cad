"""A child that exits before its write counter is opened is still counted.

The canary is a marker that must not be written outside the project.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from aicad.sandbox import _DiskWatch


def test_collecting_an_exited_child_adds_its_writes_on_linux(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux adds a collected child's logical writes to its parent")
    code = textwrap.dedent(
        """
        import subprocess
        import sys
        import time
        from pathlib import Path

        from aicad.sandbox_run import _collect_descendant_writes

        def wchar() -> int:
            for line in Path(f"/proc/{__import__('os').getpid()}/io").read_text(encoding="utf-8").splitlines():
                if line.startswith("wchar:"):
                    return int(line.split(":", 1)[1].strip())
            raise SystemExit("no wchar")

        before = wchar()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; p = Path('child.bin'); p.write_bytes(b'x' * 400000); p.unlink()",
            ]
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = Path(f"/proc/{proc.pid}/stat")
            if not status.exists():
                raise SystemExit("child disappeared before it was collected")
            fields = status.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                break
            time.sleep(0.01)
        else:
            raise SystemExit("child did not exit")
        _collect_descendant_writes()
        after = wchar()
        if after - before < 400000:
            raise SystemExit(f"delta {after - before}")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_a_reported_child_counts_once(tmp_path: Path):
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    disk.note_reported_children({42: 5000})
    assert disk.io_peak == 5000
    assert disk.hit.is_set()
    disk.note_reported_children({42: 5000})
    disk.note_io({42})
    assert disk.io_peak == 5000


def test_a_child_report_is_added_to_the_script_total(tmp_path: Path):
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    disk._counter = lambda pid: 600 if pid == 1 else None  # type: ignore[method-assign]
    report = tmp_path / "child-writes.json"
    report.write_text(json.dumps({"2": 500}) + "\n", encoding="utf-8")
    disk.child_report = report
    disk.account_io({1})
    assert disk.io_peak == 1100
    assert disk.hit.is_set()


def test_a_child_report_is_read_from_the_job(tmp_path: Path):
    report = tmp_path / "child-writes.json"
    report.write_text(json.dumps({"7": 8000}) + "\n", encoding="utf-8")
    disk = _DiskWatch(tmp_path, {}, file_cap=10**9, disk_cap=1000)
    disk.child_report = report
    disk.absorb_child_report()
    assert disk.io_peak == 8000
    assert disk.hit.is_set()


def test_linux_process_counter_sees_the_collected_child(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux reports logical writes in /proc")
    code = textwrap.dedent(
        """
        import subprocess
        import sys
        from pathlib import Path

        before = None
        for line in Path(f"/proc/{__import__('os').getpid()}/io").read_text(encoding="utf-8").splitlines():
            if line.startswith("wchar:"):
                before = int(line.split(":", 1)[1].strip())
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; p = Path('child.bin'); p.write_bytes(b'y' * 250000); p.unlink()",
            ],
            check=True,
        )
        after = None
        for line in Path(f"/proc/{__import__('os').getpid()}/io").read_text(encoding="utf-8").splitlines():
            if line.startswith("wchar:"):
                after = int(line.split(":", 1)[1].strip())
        if before is None or after is None or after - before < 250000:
            raise SystemExit(f"delta {before} {after}")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
