"""The short preview examples run headless and write a picture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("enclosure.py", "sketch_pad.py", "mesh_block.py")


@pytest.mark.parametrize("script", SCRIPTS)
def test_example_script_runs(tmp_path, script):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "examples" / script), "--out", str(tmp_path / script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["volume_mm3"] > 0
    pictures = list((tmp_path / script).glob("*.png"))
    assert pictures
    assert all(picture.stat().st_size > 1000 for picture in pictures)
