"""A project cannot set the tool's package index or process environment.

The marker is CANARY_ENV_MARKER_VALUE. A module and sitecustomize in the
project would write SANDBOX_CANARY.txt if they ran.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from aicad.kernel import make_from_script
from aicad.workspace import Workspace

_MARKER = "CANARY_ENV_MARKER_VALUE"
_CANARY_TEXT = "CANARY_HIT\n"


def _listener() -> socket.socket:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(0.2)
    return server


def test_project_environment_files_are_not_applied(tmp_path, monkeypatch):
    monkeypatch.delenv("AICAD_CANARY_MARKER", raising=False)
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    monkeypatch.delenv("DYLD_INSERT_LIBRARIES", raising=False)
    monkeypatch.delenv("PYTHONSTARTUP", raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    project = tmp_path / "project"
    project.mkdir()
    listener = _listener()
    port = listener.getsockname()[1]
    index = f"http://127.0.0.1:{port}/simple"
    (project / ".env").write_text(
        f"AICAD_CANARY_MARKER={_MARKER}\n"
        f"LD_PRELOAD={outside / 'libcanary.so'}\n"
        f"DYLD_INSERT_LIBRARIES={outside / 'libcanary.so'}\n"
        f"PYTHONPATH={project}\n"
        f"PYTHONSTARTUP={project / 'startup.py'}\n"
        f"PIP_INDEX_URL={index}\n"
        f"PATH={outside}\n",
        encoding="utf-8",
    )
    (project / "pip.conf").write_text(f"[global]\nindex-url = {index}\n", encoding="utf-8")
    (project / "uv.toml").write_text(f'index-url = "{index}"\n', encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[tool.uv]\n"
        f'index-url = "{index}"\n',
        encoding="utf-8",
    )
    (project / "requirements.txt").write_text(f"--index-url {index}\ncanary==0\n", encoding="utf-8")
    (project / "startup.py").write_text(
        f"open({str(canary)!r}, 'w', encoding='utf-8').write({_CANARY_TEXT!r})\n",
        encoding="utf-8",
    )
    (project / "sitecustomize.py").write_text(
        f"open({str(canary)!r}, 'w', encoding='utf-8').write({_CANARY_TEXT!r})\n",
        encoding="utf-8",
    )
    (project / "json.py").write_text(
        f"open({str(canary)!r}, 'w', encoding='utf-8').write({_CANARY_TEXT!r})\n",
        encoding="utf-8",
    )
    before = os.environ.get("AICAD_CANARY_MARKER")
    try:
        Workspace(project)
        make_from_script("import json\npart = Box(1, 1, 1)\n", project)
    finally:
        try:
            listener.accept()
            accepted = True
        except TimeoutError:
            accepted = False
        listener.close()
    assert before is None
    assert os.environ.get("AICAD_CANARY_MARKER") is None
    assert os.environ.get("PIP_INDEX_URL") is None
    assert os.environ.get("LD_PRELOAD") is None
    assert os.environ.get("PYTHONSTARTUP") is None
    assert accepted is False
    assert not canary.exists()
