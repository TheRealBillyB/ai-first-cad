"""Opening a shared project must not run code stored in it.

A later script run stays in the project: a module shipped next to the design
cannot start before the sandbox, and the syscall filter still refuses a
socket when the network is off.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import sys
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.kernel import measure
from aicad.sandbox import design_scripts_enabled, os_probe, run_part_script, sandbox_supported
from aicad.workspace import Workspace

_CONFINED = {1, 13, 101, 10013, 10051}


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


def _confined(label: str) -> None:
    assert label.startswith("denied:"), label
    assert int(label.rsplit(":", 1)[1]) in _CONFINED, label


def _canary_module(canary: Path) -> str:
    return (
        "from pathlib import Path\n"
        f"Path({str(canary)!r}).write_text('CANARY_HIT\\n', encoding='utf-8')\n"
    )


def _plant(project: Path, canary: Path, relative: str) -> None:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canary_module(canary), encoding="utf-8")


def _escape_script(canary: Path) -> str:
    """Try to create the canary. A refusal must not stop the solid from being built."""
    command = f"printf CANARY_HIT > {shlex.quote(str(canary))}"
    return (
        "import aicad.sandbox as sandbox\n"
        "attempts = (\n"
        "    ('link', lambda: (\n"
        f"        sandbox.os.symlink({str(canary)!r}, 'escape_link'),\n"
        "        open('escape_link', 'w', encoding='utf-8').write('CANARY_HIT\\n'),\n"
        "    )),\n"
        "    ('direct', lambda: open(\n"
        f"        {str(canary)!r}, 'w', encoding='utf-8').write('CANARY_HIT\\n')),\n"
        f"    ('child', lambda: sandbox.os.system({command!r})),\n"
        ")\n"
        "for _name, attempt in attempts:\n"
        "    try:\n"
        "        attempt()\n"
        "    except Exception:\n"
        "        pass\n"
        "part = Box(1, 1, 1)\n"
    )


@pytest.mark.sandbox
def test_opening_a_shared_project_does_not_run_its_code(tmp_path, config_home):
    """A folder received from someone else is data until a script is asked to run."""
    shared = tmp_path / "shared"
    outside = tmp_path / "outside"
    shared.mkdir()
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    secret = outside / "outside.step"
    secret.write_text("outside-solid\n", encoding="utf-8")
    _plant(shared, canary, "aicad/__init__.py")
    _plant(shared, canary, "aicad.py")
    _plant(shared, canary, "sitecustomize.py")
    _plant(shared, canary, "payload.py")
    (shared / "design_scripts.json").write_text(
        '{"projects": {"enabled": {"enabled_at": "2000-01-01T00:00:00+00:00"}}}\n',
        encoding="utf-8",
    )
    imports = shared / "imports"
    imports.mkdir()
    try:
        (imports / "linked.step").symlink_to(secret)
        linked = "imports/linked.step"
    except OSError:
        linked = "imports/missing.step"
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "bracket",
                "source": "script",
                "script": _canary_module(canary) + "part = Box(1, 1, 1)\n",
            },
            {
                "name": "plate",
                "source": "ops",
                "ops": [{"op": "add", "shape": "box", "size": [2, 3, 4], "name": "base"}],
            },
            {"name": "climber", "source": "step", "step_file": "../outside.step"},
            {"name": "linked", "source": "step", "step_file": linked},
        ],
    }
    (shared / "session.json").write_text(json.dumps(session), encoding="utf-8")

    document = Document(shared)
    viewer = Workspace(tmp_path / "here")
    opened = viewer.execute("open_project", {"path": str(shared)})

    assert not canary.exists()
    assert secret.read_text(encoding="utf-8") == "outside-solid\n"
    assert document.get_source("bracket")["script_pending"] is True
    bracket = next(part for part in document.list_parts() if part.name == "bracket")
    plate = next(part for part in document.list_parts() if part.name == "plate")
    assert bracket.built is False
    assert plate.built is True
    assert document.measure("plate").volume_mm3 == pytest.approx(24)
    assert any("outside the project" in problem for problem in document.problems)
    assert "bracket" in opened["parts"]
    assert viewer.execute("design_script_status", {})["enabled"] is False
    assert design_scripts_enabled(shared) is False

    _sandbox_or_skip()
    probe = os_probe(shared)
    _confined(probe["write_outside"])
    _confined(probe["tcp"])
    _confined(probe["udp"])
    assert not canary.exists()


@pytest.mark.sandbox
def test_shared_project_package_cannot_run_before_the_sandbox(tmp_path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "shared"
    project.mkdir()
    canary = tmp_path / "SANDBOX_CANARY.txt"
    _plant(project, canary, "aicad/__init__.py")
    shape = run_part_script(_escape_script(canary), project)
    assert not canary.exists()
    assert measure(shape).volume_mm3 == pytest.approx(1)


@pytest.mark.sandbox
def test_shared_project_module_file_cannot_run_before_the_sandbox(tmp_path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "shared"
    project.mkdir()
    canary = tmp_path / "SANDBOX_CANARY.txt"
    _plant(project, canary, "aicad.py")
    shape = run_part_script("part = Box(1, 1, 1)\n", project)
    assert not canary.exists()
    assert measure(shape).volume_mm3 == pytest.approx(1)


@pytest.mark.sandbox
def test_project_on_pythonpath_cannot_run_before_the_sandbox(tmp_path, config_home, monkeypatch):
    _sandbox_or_skip()
    project = tmp_path / "shared"
    project.mkdir()
    canary = tmp_path / "SANDBOX_CANARY.txt"
    _plant(project, canary, "aicad/__init__.py")
    previous = os.environ.get("PYTHONPATH", "")
    entries = [str(project)]
    if previous:
        entries.append(previous)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(entries))
    shape = run_part_script("part = Box(1, 1, 1)\n", project)
    assert not canary.exists()
    assert measure(shape).volume_mm3 == pytest.approx(1)


@pytest.mark.sandbox_linux
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux seccomp filter")
def test_linux_filter_blocks_indirect_sockets_and_cross_process_memory(tmp_path, config_home):
    _sandbox_or_skip()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        vm_nr = 310
        setup_nr = 425
        ia32 = True
    elif machine in ("aarch64", "arm64"):
        vm_nr = 270
        setup_nr = 425
        ia32 = False
    else:
        pytest.skip(machine)
    script = f"""
import aicad.sandbox as sandbox
libc = sandbox.ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = sandbox.ctypes.c_long
libc.syscall.argtypes = [sandbox.ctypes.c_long] * 7
lines = []

def call(*args):
    rc = libc.syscall(*args)
    return int(rc), sandbox.ctypes.get_errno()

params = sandbox.ctypes.create_string_buffer(256)
rc, err = call({setup_nr}, 1, sandbox.ctypes.addressof(params), 0, 0, 0, 0)
lines.append("io_uring:%s:%s" % (rc, err))
local = sandbox.ctypes.create_string_buffer(8)
remote = sandbox.ctypes.create_string_buffer(b"abcdefgh")

class Iovec(sandbox.ctypes.Structure):
    _fields_ = [("base", sandbox.ctypes.c_uint64), ("len", sandbox.ctypes.c_uint64)]

local_iov = Iovec(sandbox.ctypes.addressof(local), 8)
remote_iov = Iovec(sandbox.ctypes.addressof(remote), 8)
rc, err = call({vm_nr}, sandbox.os.getpid(), sandbox.ctypes.addressof(local_iov), 1, sandbox.ctypes.addressof(remote_iov), 1, 0)
lines.append("vm:%s:%s" % (rc, err))
"""
    if ia32:
        script += """
code = bytes([
    0xB8, 0x67, 0x01, 0x00, 0x00,
    0xBB, 0x02, 0x00, 0x00, 0x00,
    0xB9, 0x02, 0x00, 0x00, 0x00,
    0x31, 0xD2,
    0xCD, 0x80,
    0xC3,
])
mem, err = call(9, 0, 4096, 7, 0x22, -1, 0)
if mem < 0:
    lines.append("ia32:mmap:%s" % err)
else:
    sandbox.ctypes.memmove(mem, code, len(code))
    fn = sandbox.ctypes.CFUNCTYPE(sandbox.ctypes.c_long)(mem)
    fd = int(fn())
    lines.append("ia32:socket" if fd >= 0 else "ia32:denied:%s" % fd)
"""
    script += "open('isolation.txt', 'w', encoding='utf-8').write('\\n'.join(lines) + '\\n')\npart = Box(1, 1, 1)\n"
    run_part_script(script, tmp_path)
    text = (tmp_path / "isolation.txt").read_text(encoding="utf-8")
    assert "io_uring:-1:1" in text
    assert "vm:-1:1" in text
    if ia32:
        assert "ia32:denied:" in text
        assert "ia32:socket" not in text
