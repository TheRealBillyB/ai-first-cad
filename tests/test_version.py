"""The reported version is 0.9.0 in every place that prints it."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_pyproject():
    from aicad import __version__
    from aicad._version import __version__ as source

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == "0.9.0"
    assert source == "0.9.0"
    assert __version__ == source


def test_cli_version_flag():
    completed = subprocess.run(
        [sys.executable, "-P", "-m", "aicad", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "aicad 0.9.0"


def test_mcp_server_reports_the_package_version(tmp_path):
    from aicad._version import __version__
    from aicad.server import build_server

    server = build_server(tmp_path / "project")
    assert server.name == "aicad"
    assert server.version == __version__ == "0.9.0"
