"""A shared project must not run code by being opened, or by shadowing the sandbox.

The canary is one file, SANDBOX_CANARY.txt, containing CANARY_HIT. Opening the
project must not create it. A module shipped in the project must not run in
place of the sandbox. A script that does run stays inside the project, off the
network, and cannot use a child process to leave. On Linux the syscall filter
also refuses another instruction set, io_uring, and cross-process memory.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.kernel import measure
from aicad.sandbox import _child_env, _pythonpath_for_child, run_part_script, sandbox_supported
from aicad.workspace import Workspace

_PART_CANARY = (
    "from pathlib import Path\n"
    "Path('SANDBOX_CANARY.txt').write_text('CANARY_HIT\\n', encoding='utf-8')\n"
    "part = Box(1, 1, 1)\n"
)
_BOX = "part = Box(1, 1, 1)\n"
_CONFINED_ERRNOS = {1, 13, 101, 10013, 10051}


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _session(script: str) -> str:
    payload = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [{"name": "canary", "source": "script", "script": script}],
    }
    return json.dumps(payload, indent=2) + "\n"


def _shadow_text(outside: Path) -> str:
    return (
        "from pathlib import Path\n"
        "Path('SANDBOX_CANARY.txt').write_text('CANARY_HIT\\n', encoding='utf-8')\n"
        f"Path({str(outside)!r}).write_text('CANARY_HIT\\n', encoding='utf-8')\n"
    )


def _assert_canary_absent(project: Path, outside: Path) -> None:
    assert not (project / "SANDBOX_CANARY.txt").exists()
    assert not outside.exists()
    assert not (project / "SHADOW_RUNNER.txt").exists()


def test_opening_a_shared_project_does_not_run_its_part_script(tmp_path, config_home):
    project = tmp_path / "canary-project"
    project.mkdir()
    (project / "session.json").write_text(_session(_PART_CANARY), encoding="utf-8")
    document = Document(project)
    assert document.get_source("canary")["script_pending"] is True
    assert document.list_parts()[0].built is False
    assert not (project / "SANDBOX_CANARY.txt").exists()

    workspace = Workspace(tmp_path / "here")
    opened = workspace.execute("open_project", {"path": str(project)})
    assert opened["parts"] == ["canary"]
    assert workspace.document.list_parts()[0].built is False
    assert workspace.document.get_source("canary")["script_pending"] is True
    assert workspace.execute("design_script_status", {})["enabled"] is False
    assert not (project / "SANDBOX_CANARY.txt").exists()


def test_child_env_does_not_put_the_project_on_the_import_path(tmp_path, monkeypatch):
    other = tmp_path / "libs"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["", ".", str(tmp_path), str(other)]))
    env = _child_env(tmp_path, tmp_path / "job", False)
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["PYTHONPATH"] == str(other)
    named = tmp_path / "aicad"
    named.mkdir()
    parent_path = _pythonpath_for_child(named, str(tmp_path))
    assert parent_path is None


def test_a_project_package_cannot_replace_the_sandbox_runner(tmp_path, config_home):
    project = tmp_path / "shared"
    outside = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    package = project / "aicad"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(_shadow_text(outside), encoding="utf-8")
    (package / "sandbox_run.py").write_text(
        "from pathlib import Path\n"
        "Path('SHADOW_RUNNER.txt').write_text('CANARY_HIT\\n', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    shape = run_part_script(_BOX, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(project, outside)


def test_a_project_module_cannot_replace_the_sandbox_runner(tmp_path, config_home):
    project = tmp_path / "shared"
    project.mkdir()
    outside = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    (project / "aicad.py").write_text(_shadow_text(outside), encoding="utf-8")
    shape = run_part_script(_BOX, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(project, outside)


def test_pythonpath_cannot_point_the_runner_at_the_project(tmp_path, config_home, monkeypatch):
    project = tmp_path / "aicad"
    outside = tmp_path / "outside" / "SANDBOX_CANARY.txt"
    project.mkdir()
    (project / "__init__.py").write_text(_shadow_text(outside), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    shape = run_part_script(_BOX, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(project, outside)


def test_part_script_canary_cannot_leave_the_project(tmp_path, config_home):
    project = tmp_path / "shared"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret\n", encoding="utf-8")
    (project / "link-out").symlink_to(secret)
    outside_canary = outside / "SANDBOX_CANARY.txt"
    sub_canary = outside / "subprocess-canary.txt"
    script = """\
from pathlib import Path
notes = ["CANARY_HIT"]

def attempt(label, action):
    try:
        action()
        notes.append(label + ":hit")
    except Exception as exc:
        errno = getattr(exc, "errno", None)
        notes.append(label + ":blocked:" + type(exc).__name__ + ":" + str(errno))

attempt("read", lambda: open("/etc/passwd", encoding="utf-8").read(1))
attempt("write", lambda: Path(OUTSIDE).write_text("CANARY_HIT\\n", encoding="utf-8"))
attempt("symlink", lambda: open("link-out", encoding="utf-8").read(1))

def network():
    import aicad.llm as llm
    try:
        llm.urllib.request.urlopen("http://1.1.1.1:443", timeout=2)
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        errno = getattr(reason, "errno", None)
        raise OSError(0 if errno is None else errno, type(exc).__name__) from exc

attempt("network", network)

def spawned():
    import aicad.sandbox as sandbox
    code = "open(SUB, 'w', encoding='utf-8').write('CANARY_HIT\\\\n')"
    done = sandbox.subprocess.run(
        [sandbox.sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        detail = (done.stderr or "failed").strip().splitlines()
        raise OSError(done.returncode, detail[-1] if detail else "failed")

attempt("subprocess", spawned)
Path("SANDBOX_CANARY.txt").write_text("\\n".join(notes) + "\\n", encoding="utf-8")
part = Box(1, 1, 1)
""".replace("OUTSIDE", repr(str(outside_canary))).replace("SUB", repr(str(sub_canary)))
    shape = run_part_script(script, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    text = (project / "SANDBOX_CANARY.txt").read_text(encoding="utf-8")
    assert text.startswith("CANARY_HIT\n")
    assert ":hit" not in text
    assert not outside_canary.exists()
    assert not sub_canary.exists()
    assert secret.read_text(encoding="utf-8") == "secret\n"
    network = next(line for line in text.splitlines() if line.startswith("network:"))
    errno = int(network.rsplit(":", 1)[1])
    assert errno in _CONFINED_ERRNOS
    if sys.platform.startswith("linux"):
        assert "read:blocked:" in text
        assert "write:blocked:" in text
        assert "symlink:blocked:" in text
        assert "subprocess:blocked:" in text


@pytest.mark.sandbox_linux
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux seccomp filter")
def test_linux_filter_blocks_indirect_sockets_and_cross_process_memory(tmp_path, config_home):
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
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
