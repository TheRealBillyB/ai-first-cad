"""Canaries that run on Linux, macOS, and Windows.

A marker file outside the project is SANDBOX_CANARY.txt containing CANARY_HIT.
Gear specs, docs entries, and a confined script must not change it or signal
another process.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from aicad.sandbox import allow_design_scripts, run_design_script, run_part_script, sandbox_supported

CANARY_NAME = "SANDBOX_CANARY.txt"
CANARY_TEXT = "CANARY_HIT\n"

_PART_CANARY = textwrap.dedent(
    """
    import aicad.sandbox as sandbox

    os = sandbox.os
    sys = sandbox.sys
    threading = sandbox.threading
    ctypes = sandbox.ctypes
    helper = __HELPER__
    lines = []

    def note(label, result):
        lines.append("%s:%s" % (label, result))

    def libc():
        return ctypes.CDLL(None, use_errno=True)

    def call_kill(pid):
        library = libc()
        library.kill.argtypes = [ctypes.c_int, ctypes.c_int]
        library.kill.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = library.kill(int(pid), 0)
        if result == 0:
            return "allowed"
        return "denied:%s" % ctypes.get_errno()

    try:
        os.kill(helper, 0)
        note("os-kill", "allowed")
    except OSError as exc:
        note("os-kill", "denied:%s" % exc.errno)

    if sys.platform != "win32":
        note("libc-kill", call_kill(helper))
        boxed = []

        def worker():
            boxed.append(call_kill(helper))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        note("thread-kill", boxed[0])
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            try:
                os.close(read_fd)
                os.write(write_fd, call_kill(helper).encode())
            finally:
                os._exit(0)
        os.close(write_fd)
        os.waitpid(child, 0)
        note("fork-kill", os.read(read_fd, 64).decode())
        os.close(read_fd)

    if sys.platform == "darwin":
        library = libc()
        try:
            fcntl = sys.modules["builtins"].__import__("fcntl")
        except Exception as exc:
            note("setown", "denied:%s" % type(exc).__name__)
        else:
            descriptor = os.open("notice.txt", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if hasattr(fcntl, "F_SETOWN"):
                    try:
                        current = fcntl.fcntl(descriptor, fcntl.F_GETFL)
                        if hasattr(os, "O_ASYNC"):
                            fcntl.fcntl(descriptor, fcntl.F_SETFL, current | os.O_ASYNC)
                        fcntl.fcntl(descriptor, fcntl.F_SETOWN, helper)
                        os.write(descriptor, b"x")
                        note("setown", "allowed")
                    except OSError as exc:
                        note("setown", "denied:%s" % exc.errno)
            finally:
                os.close(descriptor)
        def call_setpriority():
            if not hasattr(library, "setpriority"):
                return "absent"
            library.setpriority.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            library.setpriority.restype = ctypes.c_int
            ctypes.set_errno(0)
            result = library.setpriority(0, helper, 19)
            if result == 0:
                return "allowed"
            return "denied:%s" % ctypes.get_errno()

        def control(flavor, arg):
            ctypes.set_errno(0)
            if hasattr(library, "proc_rlimit_control"):
                library.proc_rlimit_control.argtypes = [
                    ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                ]
                library.proc_rlimit_control.restype = ctypes.c_int
                return library.proc_rlimit_control(helper, flavor, arg)
            library.syscall.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
            ]
            library.syscall.restype = ctypes.c_int
            return library.syscall(446, helper, flavor, arg)

        def call_proc_limit():
            # Ask for a wakeup monitor, then read it back. A kernel that no
            # longer stores one leaves the read buffer untouched.
            class WakeupMonitor(ctypes.Structure):
                _fields_ = [("wm_flags", ctypes.c_uint32), ("wm_rate", ctypes.c_int32)]

            params = WakeupMonitor(1, 1000000)
            control(1, ctypes.byref(params))
            params.wm_flags = 4
            params.wm_rate = 0
            control(1, ctypes.byref(params))
            if params.wm_flags in (1, 17) and params.wm_rate == 1000000:
                return "allowed"
            return "denied:0"

        def call_cpu_monitor():
            class CpuMonitor(ctypes.Structure):
                _fields_ = [
                    ("attr", ctypes.c_uint32),
                    ("percentage", ctypes.c_uint32),
                    ("interval", ctypes.c_uint64),
                    ("deadline", ctypes.c_uint64),
                ]

            attr = CpuMonitor(5, 1, 1, 0)
            library.syscall.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64,
            ]
            library.syscall.restype = ctypes.c_int
            ctypes.set_errno(0)
            result = library.syscall(323, 1, 10, 4, 3, ctypes.byref(attr), helper, 0)
            if result == 0:
                return "allowed"
            return "denied:%s" % ctypes.get_errno()

        note("setpriority", call_setpriority())
        note("proc-limit", call_proc_limit())
        note("cpu-monitor", call_cpu_monitor())
        boxed_limit = []

        def limit_worker():
            boxed_limit.append(call_setpriority())
            boxed_limit.append(call_proc_limit())
            boxed_limit.append(call_cpu_monitor())

        limit_thread = threading.Thread(target=limit_worker)
        limit_thread.start()
        limit_thread.join()
        note("thread-setpriority", boxed_limit[0])
        note("thread-proc-limit", boxed_limit[1])
        note("thread-cpu-monitor", boxed_limit[2])

    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        kernel.SetProcessWorkingSetSize.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
        ]
        kernel.SetProcessWorkingSetSize.restype = ctypes.c_int
        kernel.GenerateConsoleCtrlEvent.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        kernel.GenerateConsoleCtrlEvent.restype = ctypes.c_int

        def open_process(rights):
            handle = kernel.OpenProcess(rights, 0, helper)
            if handle:
                kernel.CloseHandle(handle)
                return "allowed"
            return "denied:%s" % ctypes.get_last_error()

        note("win-open", open_process(0x0001))
        boxed = []

        def worker():
            boxed.append(open_process(0x0001))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        note("win-thread-open", boxed[0])
        quota = kernel.OpenProcess(0x0100, 0, helper)
        if quota:
            kernel.SetProcessWorkingSetSize(quota, 1, 2)
            kernel.CloseHandle(quota)
            note("win-quota", "allowed")
        else:
            note("win-quota", "denied:%s" % ctypes.get_last_error())
        if kernel.GenerateConsoleCtrlEvent(1, helper):
            note("win-console", "allowed")
        else:
            note("win-console", "denied:%s" % ctypes.get_last_error())

    open("platform-canary.txt", "w", encoding="utf-8").write("\\n".join(lines) + "\\n")
    part = Box(1, 1, 1)
    """
)


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _start_helper(tmp_path: Path) -> tuple[subprocess.Popen[bytes], Path]:
    log = tmp_path / "helper-signals.txt"
    ready = tmp_path / "helper-ready"
    script = textwrap.dedent(
        """
        import os
        import signal
        import sys
        import time

        log, ready = sys.argv[1], sys.argv[2]

        def handler(signum, _frame):
            with open(log, "a", encoding="utf-8") as handle:
                handle.write("signal:%s\\n" % signum)

        for name in ("SIGUSR1", "SIGUSR2", "SIGIO", "SIGXCPU", "SIGTERM", "SIGBREAK"):
            value = getattr(signal, name, None)
            if value is not None:
                try:
                    signal.signal(value, handler)
                except OSError:
                    pass
        with open(ready, "w", encoding="utf-8") as handle:
            handle.write("%s\\n" % os.getpid())
        while True:
            time.sleep(0.2)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(log), str(ready)],
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready.is_file() and ready.read_text(encoding="utf-8").strip():
            return proc, log
        if proc.poll() is not None:
            raise RuntimeError("helper exited")
        time.sleep(0.02)
    proc.kill()
    raise RuntimeError("helper did not start")


def _rows(project: Path) -> dict[str, str]:
    text = (project / "platform-canary.txt").read_text(encoding="utf-8")
    return dict(line.split(":", 1) for line in text.splitlines() if ":" in line)


def test_a_confined_script_cannot_signal_or_limit_another_process(tmp_path, config_home):
    _sandbox_or_skip()
    project = tmp_path / "project"
    project.mkdir()
    helper, log = _start_helper(tmp_path)
    try:
        script = _PART_CANARY.replace("__HELPER__", str(helper.pid))
        run_part_script(script, project)
        text = (project / "platform-canary.txt").read_text(encoding="utf-8")
        rows = _rows(project)
        time.sleep(0.4)
        heard = log.read_text(encoding="utf-8") if log.is_file() else ""
        assert heard == "", text
        assert helper.poll() is None, text
        required = ["os-kill"]
        if sys.platform == "win32":
            required.extend(["win-open", "win-thread-open", "win-quota", "win-console"])
        else:
            required.extend(["libc-kill", "thread-kill", "fork-kill"])
        if sys.platform == "darwin":
            required.extend(
                [
                    "setpriority",
                    "proc-limit",
                    "cpu-monitor",
                    "thread-setpriority",
                    "thread-proc-limit",
                    "thread-cpu-monitor",
                ]
            )
        for label in required:
            assert rows[label].startswith("denied"), (label, text)
    finally:
        if helper.poll() is None:
            helper.kill()
        helper.wait(timeout=5)


def _watch_opens(canary: Path):
    seen: list[str] = []
    real_open = os.open

    def watched(path, flags, mode=0o777, *, dir_fd=None):
        if not isinstance(path, int) and CANARY_NAME in os.fsdecode(path):
            seen.append(os.fsdecode(path))
        return real_open(path, flags, mode, dir_fd=dir_fd)

    return seen, real_open, watched


def test_gear_and_assembly_specs_do_not_open_a_marker(tmp_path):
    canary = tmp_path / "outside" / CANARY_NAME
    canary.parent.mkdir()
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    before = canary.read_bytes()
    seen, real_open, watched = _watch_opens(canary)
    os.open = watched
    try:
        from aicad.document import Document
        from aicad.gears.hypoid import normalize_hypoid

        spec = {
            "type": "hypoid",
            "role": "pinion",
            "teeth": 12,
            "mate_teeth": 41,
            "offset_mm": str(canary),
            "shaft_angle_deg": 90,
            "ring_diameter_mm": 180,
            "face_width_mm": 30,
            "spiral_angle_deg": 35,
            "cutter": {"process": str(canary), "path": str(canary)},
            "note_path": str(canary),
        }
        with pytest.raises(Exception):
            normalize_hypoid(spec)
        for label, value in (("huge", 1e12), ("nan", float("nan")), ("inf", float("inf"))):
            trial = {
                "type": "hypoid",
                "role": "pinion",
                "teeth": 12,
                "mate_teeth": 41,
                "offset_mm": 40 if label != "huge" else value,
                "shaft_angle_deg": 90,
                "ring_diameter_mm": 180,
                "face_width_mm": value if label == "inf" else 30,
                "spiral_angle_deg": 35,
            }
            if label == "nan":
                trial["offset_mm"] = value
            with pytest.raises(Exception):
                normalize_hypoid(trial)
        project = tmp_path / "project"
        document = Document(project)
        document.create_gear(
            "pinion",
            {
                "type": "spur",
                "teeth": 18,
                "module_mm": 2,
                "face_width_mm": 10,
                "bore_mm": 8,
                "path": str(canary),
                "file": str(canary),
            },
        )
        with pytest.raises(Exception):
            document.place_part("pinion", position=[str(canary), 0, 0], rotation_deg=[0, 0, 1e18])
        with pytest.raises(Exception):
            document.check_gears(
                {
                    "pinion": "pinion",
                    "gear": {
                        "type": "spur",
                        "teeth": 36,
                        "module_mm": 2,
                        "face_width_mm": 10,
                        "path": str(canary),
                    },
                    "centre_distance_mm": str(canary),
                    "pinion_phase_deg": 1e18,
                }
            )
    finally:
        os.open = real_open
    assert seen == []
    assert canary.read_bytes() == before
    session = (tmp_path / "project" / "session.json").read_text(encoding="utf-8")
    assert CANARY_NAME not in session
    assert json.loads(session)["parts"]


def _docs_builder():
    path = Path(__file__).resolve().parents[1] / "tools" / "build_docs.py"
    spec = importlib.util.spec_from_file_location("platform_canary_docs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["platform_canary_docs"] = module
    spec.loader.exec_module(module)
    return module


def test_docs_entries_do_not_change_a_marker_or_the_master(tmp_path):
    canary = tmp_path / "outside" / CANARY_NAME
    canary.parent.mkdir()
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    before = canary.read_bytes()
    root = tmp_path / "repo"
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / "docs" / "tools").mkdir()
    (root / "README.md").write_text(
        "# Guide\n\n<!-- generated:tools -->\n"
        "| Tool | What it does |\n| --- | --- |\n"
        "| `inspect_part` | Measure it. |\n"
        "<!-- /generated:tools -->\n",
        encoding="utf-8",
    )
    (root / "docs" / "tools" / "inspect-part.md").write_text(
        "# `inspect_part`\n\nMeasure it.\n",
        encoding="utf-8",
    )
    build = _docs_builder()
    cases = {
        "shell.md": "---\ndate: 2026-10-24\nrun: " + str(canary) + "\n---\n\n# Alpha\n\nBecause.\n",
        "break.md": "---\ndate: 2026-10-24\n---\n\n# Alpha\u2028Next\n\nBecause.\n",
        "comment.md": "---\ndate: 2026-10-24\n---\n\n# Alpha <!-- generated:tools -->\n\nBecause.\n",
    }
    for name, text in cases.items():
        for child in (root / "docs" / "decisions").glob("*.md"):
            child.unlink()
        (root / "docs" / "decisions" / f"2026-10-24-{name}").write_text(text, encoding="utf-8")
        errors = build.build(root)
        assert errors, name
        master = root / "docs" / "DECISIONS.md"
        master_text = master.read_text(encoding="utf-8") if master.is_file() else ""
        assert CANARY_NAME not in master_text
        assert "\u2028" not in master_text
        assert canary.read_bytes() == before
    for child in (root / "docs" / "decisions").glob("*.md"):
        child.unlink()
    outside_entry = tmp_path / "outside" / "2026-10-24-link.md"
    outside_entry.write_text(
        "---\ndate: 2026-10-24\n---\n\n# Outside title\n\nOutside rationale.\n",
        encoding="utf-8",
    )
    link = root / "docs" / "decisions" / "2026-10-24-link.md"
    linked = False
    try:
        link.symlink_to(outside_entry)
    except OSError:
        linked = False
    else:
        linked = True
        errors = build.build(root)
        assert errors
        master = root / "docs" / "DECISIONS.md"
        master_text = master.read_text(encoding="utf-8") if master.is_file() else ""
        assert "Outside title" not in master_text
        link.unlink()
    hard = root / "docs" / "decisions" / "2026-10-24-hard.md"
    try:
        os.link(canary, hard)
    except OSError as exc:
        if not linked:
            pytest.skip(f"links are not available: {exc}")
    else:
        errors = build.build(root)
        assert errors
        assert canary.read_bytes() == before
        hard.unlink()


def test_a_design_script_check_record_is_the_call_that_ran(tmp_path, config_home):
    """A check record is what the document call returned, on every platform."""
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    script = (
        "fn = document.list_parts\n"
        "found = False\n"
        "closure = getattr(fn, '__closure__', None)\n"
        "if closure:\n"
        "    for cell in closure:\n"
        "        value = cell.cell_contents\n"
        "        if isinstance(value, list):\n"
        "            value.append({'method': 'check_gears', 'result': {'ok': True}})\n"
        "            found = True\n"
        "for value in list(getattr(fn, '__globals__', {}).values()):\n"
        "    if isinstance(value, list):\n"
        "        value.append({'method': 'check_gears', 'result': {'ok': True}})\n"
        "        found = True\n"
        "print('FOUND' if found else 'NONE')\n"
        "print(len(document.list_parts()))\n"
    )
    payload = run_design_script(tmp_path, script)
    assert payload.get("checks") == []
    assert "NONE" in str(payload.get("stdout"))
