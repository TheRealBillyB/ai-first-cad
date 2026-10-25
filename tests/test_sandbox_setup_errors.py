"""A sandbox that fails to start does not return a traceback."""

import json
import subprocess
import sys
from pathlib import Path

from aicad.sandbox import _WINDOWS_CHILD_BOOT


def _assert_no_traceback(error: str, stderr: str) -> None:
    assert "Traceback" not in error
    assert 'File "' not in error
    assert "Traceback" not in stderr


def test_a_windows_child_boot_does_not_record_a_traceback(tmp_path: Path):
    job = tmp_path / "job"
    job.mkdir()
    boot = tmp_path / "boot.py"
    boot.write_text(_WINDOWS_CHILD_BOOT, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(boot), str(job), str(tmp_path / "missing-runner.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "Error" in result["error"]
    _assert_no_traceback(result["error"], completed.stderr)


def test_sandbox_setup_does_not_record_a_traceback(tmp_path: Path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"kind": "part", "network": False}) + "\n", encoding="utf-8")
    code = (
        "import os\n"
        "from aicad import sandbox_run\n"
        f"os.environ['AICAD_WORKSPACE'] = {str(tmp_path)!r}\n"
        "def apply(*args, **kwargs):\n"
        "    raise RuntimeError('sandbox setup failed')\n"
        "sandbox_run._load_sandbox = lambda: (False, 'thread', apply, lambda *a, **k: None)\n"
        f"raise SystemExit(sandbox_run.main([{str(job)!r}]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert "sandbox setup failed" in result["error"]
    _assert_no_traceback(result["error"], completed.stderr)


def test_a_sandbox_import_failure_does_not_record_a_traceback(tmp_path: Path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"kind": "part", "network": False}) + "\n", encoding="utf-8")
    code = (
        "import os\n"
        "from aicad import sandbox_run\n"
        f"os.environ['AICAD_WORKSPACE'] = {str(tmp_path)!r}\n"
        "def broken():\n"
        "    raise RuntimeError('sandbox import failed')\n"
        "sandbox_run._load_sandbox = broken\n"
        f"raise SystemExit(sandbox_run.main([{str(job)!r}]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert "sandbox import failed" in result["error"]
    _assert_no_traceback(result["error"], completed.stderr)
