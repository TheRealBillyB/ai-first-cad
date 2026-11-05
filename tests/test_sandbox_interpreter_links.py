"""A container can start Python through a link such as uv's version junction.

uv names its Python ``cpython-3.11-windows-x86_64-none``, a junction to the
exact release, and a venv records that name. The release folder is granted
resolved. Without leave to pass the junction itself, the container got
"Access is denied" on it, so the network-on container could not start.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from aicad import sandbox_windows as win


def _link(target: Path, link: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_every_link_on_the_way_is_found_once(tmp_path):
    real = tmp_path / "cpython-3.11.15"
    (real / "Lib").mkdir(parents=True)
    link = tmp_path / "cpython-3.11"
    _link(real, link)
    found = win.links_on_the_way([link / "Lib", link, real / "Lib", str(link / "python.exe")])
    assert found == [link]


def test_a_plain_path_has_no_links(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert win.links_on_the_way([tmp_path / "a" / "b"]) == []


def test_the_link_itself_is_granted_once_per_container(tmp_path, monkeypatch):
    real = tmp_path / "release"
    real.mkdir()
    link = tmp_path / "minor"
    _link(real, link)
    commands = []
    walked = []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(win, "_run_icacls", lambda command, path, recurse: commands.append(command) or Done())
    monkeypatch.setattr(win, "_grant_traverse", lambda path, sid, package_sid=None: walked.append(path))
    monkeypatch.setattr(win, "_granted_links", set())
    for _attempt in (1, 2):
        win._grant_links_on_the_way([link / "Lib"], "S-1-15-2-1-2", "S-1-15-2-1")
    win._grant_links_on_the_way([link / "Lib"], "S-1-15-2-9-9", "S-1-15-2-1")
    assert commands == [
        ["icacls", str(link), "/grant", "*S-1-15-2-1-2:(RX)", "/L", "/C", "/Q"],
        ["icacls", str(link), "/grant", "*S-1-15-2-9-9:(RX)", "/L", "/C", "/Q"],
    ]
    assert walked == [tmp_path, tmp_path]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_a_container_can_look_at_a_junction_to_its_python(tmp_path, monkeypatch):
    """The real container looks at a junction to a folder it was granted resolved, as Python does with uv's home."""
    link = Path(tempfile.mkdtemp(prefix="aicad-pylink-")) / "python-minor"
    _link(Path(sys.base_prefix), link)
    try:
        win._bind()
        # The stricter container does not start Python on CI's runners. The run logs that.
        monkeypatch.setattr(win, "_mode", "appcontainer")
        # As a venv over uv's Python names its home.
        monkeypatch.setattr(win, "_interpreter_paths", lambda: [str(link)])
        root = tmp_path / "work"
        root.mkdir()
        code, out, err, timed_out, _memory, _mechanism = win.spawn(
            [
                str(Path(sys.base_prefix).resolve() / "python.exe"), "-I", "-S", "-B", "-c",
                # Python's own start checks its home this way, which is what failed.
                f"import os; os.stat({str(link)!r}); os.listdir({str(link)!r}); print('through the junction')",
            ],
            cwd=str(root),
            env=win._probe_env(root),
            read_paths=[Path(sys.base_prefix).resolve()],
            workspace=root,
            cpu_seconds=30,
            memory_bytes=1 << 31,
            network=False,
            timeout_seconds=60,
            stdout_path=root / "out.txt",
            stderr_path=root / "err.txt",
            child_checks_network=True,
        )
        assert (code, timed_out) == (0, False), err
        assert "through the junction" in out
    finally:
        os.rmdir(link)
