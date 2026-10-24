"""Sandbox findings from the 2026-10-21 bug hunt, as tests that must pass.

These are ordinary tests: a confined script must not clear the plugin gate,
signal its parent, leave a process behind, or use up a network grant it did
not successfully run with.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from aicad.errors import ScriptError
from aicad.sandbox import (
    _block_sockets,
    allow_design_scripts,
    run_design_script,
    run_part_script,
    sandbox_supported,
)
from aicad.workspace import Workspace


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_a_design_script_cannot_record_a_check_it_did_not_run(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    script = (
        "import json\n"
        "import pathlib\n"
        "real_write = pathlib.Path.write_text\n"
        "def write_result(self, data, *args, **kwargs):\n"
        "    if getattr(self, 'name', '') == 'result.json':\n"
        "        try:\n"
        "            body = json.loads(data)\n"
        "        except Exception:\n"
        "            body = None\n"
        "        if isinstance(body, dict):\n"
        "            body['checks'] = [{'method': 'check_gears', 'result': {'ok': True}}]\n"
        "            body['changed_parts'] = [{'name': 'forged'}]\n"
        "            data = json.dumps(body) + '\\n'\n"
        "    return real_write(self, data, *args, **kwargs)\n"
        "pathlib.Path.write_text = write_result\n"
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
        "try:\n"
        "    object.__getattribute__(document, '_state')\n"
        "    print('STATE')\n"
        "except Exception as exc:\n"
        "    print('STATE %s' % type(exc).__name__)\n"
        "try:\n"
        "    document.approve_plan()\n"
        "    print('APPROVED')\n"
        "except Exception as exc:\n"
        "    print('PLAN %s' % type(exc).__name__)\n"
        "try:\n"
        "    document.delete_part()\n"
        "except Exception as exc:\n"
        "    tb = exc.__traceback__\n"
        "    leaked = False\n"
        "    while tb is not None:\n"
        "        if type(tb.tb_frame.f_locals.get('self')).__name__ == 'Document':\n"
        "            leaked = True\n"
        "        tb = tb.tb_next\n"
        "    print('LEAK' if leaked else 'CLEAN')\n"
        "else:\n"
        "    print('DELETED')\n"
        "print(len(document.list_parts()))\n"
        "print('FOUND' if found else 'NONE')\n"
        "try:\n"
        "    import aicad.sandbox as sandbox\n"
        "    gc = sandbox.sys.modules['builtins'].__import__('gc')\n"
        "    for obj in gc.get_objects():\n"
        "        if type(obj) is not list or not obj or len(obj) > 20:\n"
        "            continue\n"
        "        if all(type(item) is dict and 'method' in item for item in obj):\n"
        "            obj.append({'method': 'check_gears', 'result': {'ok': True}})\n"
        "except Exception:\n"
        "    pass\n"
    )
    payload = run_design_script(tmp_path, script)
    assert payload.get("checks") == []
    assert payload.get("changed_parts") == []
    assert "forged" not in json.dumps(payload)
    stdout = str(payload.get("stdout"))
    assert "NONE" in stdout
    assert "STATE AttributeError" in stdout
    assert "PLAN AttributeError" in stdout
    assert "CLEAN" in stdout
    assert "APPROVED" not in stdout
    assert "DELETED" not in stdout


def test_an_older_landlock_abi_still_runs_a_script(monkeypatch):
    class Libc:
        def __init__(self, abi: int):
            self.abi = abi

        def syscall(self, *_args):
            return self.abi

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("aicad.sandbox._arch", lambda: "x86_64")
    for abi, supported in ((3, False), (4, True), (5, True), (6, True), (7, True)):
        monkeypatch.setattr("aicad.sandbox._libc", lambda abi=abi: Libc(abi))
        ok, reason = sandbox_supported()
        assert ok is supported
        if supported:
            assert reason == ""
        else:
            assert "signal" not in reason


def test_the_process_filter_refuses_a_signal_to_another_process(tmp_path):
    if not sys.platform.startswith("linux"):
        pytest.skip("the signal filter is the Linux process limit")
    report = tmp_path / "signals.txt"
    script = textwrap.dedent(
        """
        import ctypes
        import os

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise SystemExit("could not lock the process")
        from aicad.sandbox import _GETTID, _ISOLATION_SYSCALLS, _SIGNAL_SYSCALLS, _arch, _block_sockets

        _block_sockets(network=False)
        arch = _arch()
        libc.syscall.restype = ctypes.c_long
        pid = os.getpid()
        tid = int(libc.syscall(_GETTID[arch]))
        parent = os.getppid()

        def once(label, number, *args):
            ctypes.set_errno(0)
            result = libc.syscall(number, *args)
            if result == 0:
                return f"{label}:allowed"
            return f"{label}:denied:{ctypes.get_errno()}"

        signals = _SIGNAL_SYSCALLS[arch]
        pidfd = next(number for number in _ISOLATION_SYSCALLS[arch] if number == 424)
        lines = [
            once("self-kill", signals["kill"], pid, 0),
            once("parent-kill", signals["kill"], parent, 0),
            once("init-kill", signals["kill"], 1, 0),
            once("group-kill", signals["kill"], 0, 0),
            once("minus-one", signals["kill"], -1, 0),
            once("negative-self", signals["kill"], -pid, 0),
            once("self-tkill", signals["tkill"], tid, 0),
            once("parent-tkill", signals["tkill"], parent, 0),
            once("self-tgkill", signals["tgkill"], pid, tid, 0),
            once("parent-tgkill", signals["tgkill"], parent, parent, 0),
            once("self-rt", signals["rt_sigqueueinfo"], pid, 0, 0),
            once("parent-rt", signals["rt_sigqueueinfo"], parent, 0, 0),
            once("self-rt-tg", signals["rt_tgsigqueueinfo"], pid, tid, 0, 0),
            once("parent-rt-tg", signals["rt_tgsigqueueinfo"], parent, parent, 0, 0),
            once("pidfd", pidfd, 0, 0, 0, 0),
        ]
        open(os.environ["AICAD_SIGNAL_REPORT"], "w", encoding="utf-8").write("\\n".join(lines) + "\\n")
        """
    )
    env = os.environ.copy()
    env["AICAD_SIGNAL_REPORT"] = str(report)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    text = report.read_text(encoding="utf-8")
    rows = dict(line.split(":", 1) for line in text.splitlines())
    assert rows["self-kill"] == "allowed"
    assert rows["self-tkill"] == "allowed"
    assert rows["self-tgkill"] == "allowed"
    for label in ("self-rt", "self-rt-tg"):
        assert rows[label] != "denied:1"
    refused = (
        "parent-kill",
        "init-kill",
        "group-kill",
        "minus-one",
        "negative-self",
        "parent-tkill",
        "parent-tgkill",
        "parent-rt",
        "parent-rt-tg",
        "pidfd",
    )
    for label in refused:
        assert rows[label] == "denied:1"


def _plant_landlock_cap(project: Path, monkeypatch, config_dir: Path) -> None:
    """Write the cap into every file and variable a person can open. None of them are read."""
    project.mkdir(parents=True, exist_ok=True)
    session = {
        "units": "mm",
        "parts": [],
        "sketches": [],
        "assemblies": [],
        "landlock_abi_cap": 5,
        "_test_landlock_abi": 5,
    }
    (project / "session.json").write_text(json.dumps(session) + "\n", encoding="utf-8")
    (project / "aicad.plugin.json").write_text(
        json.dumps(
            {
                "api": 1,
                "name": "volume-note",
                "publisher": "Example",
                "summary": "A note about volume.",
                "permissions": ["checks"],
                "provides": ["analysis"],
                "landlock_abi_cap": 5,
                "_test_landlock_abi": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key": "",
                "landlock_abi_cap": 5,
                "_test_landlock_abi": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (config_dir / "plugins.json").write_text(
        json.dumps({"version": 1, "allowed": {}, "landlock_abi_cap": 5, "_test_landlock_abi": 5}) + "\n",
        encoding="utf-8",
    )
    (config_dir / "design_scripts.json").write_text(
        json.dumps(
            {
                "landlock_abi_cap": 5,
                "_test_landlock_abi": 5,
                "projects": {str(project.resolve()): {"enabled_at": "2026-10-24T00:00:00+00:00", "landlock_abi_cap": 5}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for key in (
        "AICAD_LANDLOCK_ABI",
        "AICAD_LANDLOCK_ABI_CAP",
        "LANDLOCK_ABI",
        "_TEST_LANDLOCK_ABI",
        "AICAD_TEST_LANDLOCK_ABI",
    ):
        monkeypatch.setenv(key, "5")
    monkeypatch.setenv("AICAD_SETTINGS", str(config_dir / "settings.json"))
    monkeypatch.setenv("AICAD_PLUGIN_CONSENT", str(config_dir / "plugins.json"))


def _reported_landlock_abi() -> int:
    from aicad.sandbox import _LANDLOCK, _arch, _libc

    arch = _arch()
    assert arch is not None
    return int(_libc().syscall(_LANDLOCK[arch][0], None, 0, 1))


def test_the_installed_package_cannot_lower_the_landlock_abi():
    import ast
    import inspect

    import aicad
    import aicad.plugins as plugins
    import aicad.sandbox as sandbox
    from aicad.workspace import COMMANDS

    package = Path(aicad.__file__).resolve().parent
    assert package.name == "aicad"
    assert "tests" not in package.parts
    banned = (
        "abi_cap",
        "_test_landlock_abi",
        "landlock_abi_cap",
        "_harness_landlock_abi",
        "_LANDLOCK_PROBE_LAUNCH",
    )
    sources = list(package.rglob("*.py"))
    assert sources
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.relative_to(package)} contains {word}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [arg.arg for arg in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs)]
            if node.args.vararg is not None:
                names.append(node.args.vararg.arg)
            if node.args.kwarg is not None:
                names.append(node.args.kwarg.arg)
            for name in names:
                assert name not in banned
                lowered = name.lower()
                assert not ("landlock" in lowered and "abi" in lowered)
    for fn in (
        sandbox.apply_sandbox,
        sandbox._landlock,
        sandbox._spawn,
        run_part_script,
        run_design_script,
    ):
        parameters = inspect.signature(fn).parameters
        assert "abi_cap" not in parameters
        assert "_test_landlock_abi" not in parameters
        assert "landlock_abi_cap" not in parameters
    with pytest.raises(TypeError):
        sandbox.apply_sandbox(Path("."), False, _test_landlock_abi=5)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        sandbox._landlock(Path("."), False, [], abi_cap=5)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        run_part_script("part = Box(1, 1, 1)\n", Path("."), _test_landlock_abi=5)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        run_design_script(Path("."), "print('no')\n", _test_landlock_abi=5)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        sandbox._spawn(Path("."), "part", "part = Box(1, 1, 1)\n", False, landlock_abi_cap=5)  # type: ignore[call-arg]
    problem, fields = plugins._validate_manifest(
        {
            "api": 1,
            "name": "volume-note",
            "publisher": "Example",
            "summary": "A note about volume.",
            "permissions": ["checks"],
            "provides": ["analysis"],
            "landlock_abi_cap": 5,
            "_test_landlock_abi": 5,
        },
        "volume-note",
    )
    assert problem is None
    assert fields is not None
    assert "landlock_abi_cap" not in fields
    assert "_test_landlock_abi" not in fields
    for name in ("run_design_script", "create_part_from_script", "update_part_script"):
        properties = COMMANDS[name].parameters["properties"]
        assert "landlock_abi_cap" not in properties
        assert "_test_landlock_abi" not in properties


def test_the_script_is_not_run_when_the_process_filter_cannot_install(monkeypatch):
    monkeypatch.setattr("aicad.sandbox._arch", lambda: "x86_64")

    class Libc:
        def syscall(self, number, *_args):
            if number == 186:
                return 1234
            return -1

    monkeypatch.setattr("aicad.sandbox._libc", lambda: Libc())
    with pytest.raises(ScriptError, match="process limits"):
        _block_sockets(network=False)


def _report_landlock_abi(abi: int) -> None:
    """Monkeypatch this process so its Landlock query reports ``abi``.

    The installed package has no argument for this. Only this test calls it,
    and only in the process it starts for the signal probe.
    """
    import aicad.sandbox as sandbox

    factory = sandbox._libc
    arch = sandbox._arch()
    if arch is None:
        return
    create = sandbox._LANDLOCK[arch][0]

    def libc():
        lib = factory()
        real = lib.syscall

        def syscall(number, *args):
            version_query = (
                number == create
                and len(args) >= 3
                and args[0] is None
                and args[1] == 0
                and args[2] == 1
            )
            if version_query:
                return abi
            return real(number, *args)

        lib.syscall = syscall
        return lib

    sandbox._libc = libc


def _run_capped_signal_probe(workspace: str) -> None:
    """One confined process whose Landlock query reports ABI 5.

    This test replaces the query in this process. The installed package does not
    take an ABI argument.
    """
    import ctypes

    import aicad.sandbox as sandbox

    _report_landlock_abi(5)
    root = Path(workspace).resolve()
    sandbox.apply_sandbox(root, network=False)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    arch = sandbox._arch()
    signals = sandbox._SIGNAL_SYSCALLS[arch]
    pid = os.getpid()
    tid = int(libc.syscall(sandbox._GETTID[arch]))
    outside = os.getppid()
    lines = [
        "abi:%s" % sandbox._landlock_abi_used,
        "scope:%s" % sandbox._landlock_signal_scope,
    ]

    def once(number, *args):
        ctypes.set_errno(0)
        result = libc.syscall(number, *args)
        if result == 0:
            return "allowed"
        return "denied:%s" % ctypes.get_errno()

    def record(label, number, *args):
        lines.append("%s:%s" % (label, once(number, *args)))

    def oskill(label, target):
        try:
            os.kill(target, 0)
        except OSError as exc:
            lines.append("%s:denied:%s" % (label, exc.errno))
        else:
            lines.append("%s:allowed" % label)

    record("self-kill", signals["kill"], pid, 0)
    record("parent-kill", signals["kill"], outside, 0)
    record("init-kill", signals["kill"], 1, 0)
    record("group-kill", signals["kill"], 0, 0)
    record("minus-one", signals["kill"], -1, 0)
    record("negative-self", signals["kill"], -pid, 0)
    record("pidfd", 424, 0, 0, 0, 0)
    oskill("os-self", pid)
    oskill("os-parent", outside)
    oskill("os-init", 1)
    oskill("os-group", 0)
    oskill("os-minus-one", -1)
    oskill("os-negative-self", -pid)
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        child_lines = []

        def crecord(label, number, *args):
            child_lines.append("%s:%s" % (label, once(number, *args)))

        crecord("child-self", signals["kill"], os.getpid(), 0)
        crecord("child-script", signals["kill"], pid, 0)
        crecord("child-outside", signals["kill"], outside, 0)
        crecord("child-init", signals["kill"], 1, 0)
        crecord("child-group", signals["kill"], 0, 0)
        crecord("child-minus-one", signals["kill"], -1, 0)
        crecord("child-negative", signals["kill"], -pid, 0)
        crecord("child-pidfd", 424, 0, 0, 0, 0)
        os.write(write_fd, ("\n".join(child_lines) + "\n").encode())
        os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    os.waitpid(child, 0)
    child_text = os.read(read_fd, 65536).decode()
    os.close(read_fd)
    (root / "signals.txt").write_text("\n".join(lines) + "\n" + child_text, encoding="utf-8")


# This text lives in the test file. The installed package does not start it.
_SIGNAL_PROBE_LAUNCH = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from tests.test_sandbox_findings import _run_capped_signal_probe\n"
    "_run_capped_signal_probe(sys.argv[1])\n"
)


def test_signals_are_refused_when_landlock_cannot_scope_them(tmp_path):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux applies the signal filter in the kernel")
    _sandbox_or_skip()
    checkout = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", _SIGNAL_PROBE_LAUNCH, str(tmp_path), str(checkout)],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    text = (tmp_path / "signals.txt").read_text(encoding="utf-8")
    rows = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    assert rows["abi"] == "5"
    assert rows["scope"] == "False"
    assert rows["self-kill"] == "allowed"
    assert rows["os-self"] == "allowed"
    # A forked child inherits the filter baked with the script pid, so it can
    # signal that script and no other pid. The script process is inside the sandbox.
    assert rows["child-script"] == "allowed"
    refused = (
        "parent-kill",
        "init-kill",
        "group-kill",
        "minus-one",
        "negative-self",
        "pidfd",
        "os-parent",
        "os-init",
        "os-group",
        "os-minus-one",
        "os-negative-self",
        "child-self",
        "child-outside",
        "child-init",
        "child-group",
        "child-minus-one",
        "child-negative",
        "child-pidfd",
    )
    for label in refused:
        assert rows[label] == "denied:1", label


def _run_owner_signal_probe(workspace: str) -> None:
    """Confined process. A resource limit or a file notice must not reach another pid.

    ``AICAD_OWNER_ABI`` is read only here. The package has no such switch.
    """
    import ctypes
    import threading

    import aicad.sandbox as sandbox

    abi = os.environ.get("AICAD_OWNER_ABI")
    if abi:
        _report_landlock_abi(int(abi))
    root = Path(workspace).resolve()
    sandbox.apply_sandbox(root, network=False)
    libc = ctypes.CDLL(None, use_errno=True)
    arch = sandbox._arch()
    helper = int(os.environ["AICAD_HELPER_PID"])
    lines = [
        "abi:%s" % sandbox._landlock_abi_used,
        "scope:%s" % sandbox._landlock_signal_scope,
    ]

    class Limit(ctypes.Structure):
        _fields_ = [("cur", ctypes.c_ulong), ("max", ctypes.c_ulong)]

    def status(result: int, err: int) -> str:
        if result >= 0:
            return "allowed"
        return "denied:%s" % err

    def prlimit(pid: int, new: Limit | None) -> str:
        libc.syscall.argtypes = [
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        libc.syscall.restype = ctypes.c_long
        old = Limit()
        ctypes.set_errno(0)
        new_ptr = None if new is None else ctypes.byref(new)
        result = libc.syscall(sandbox._PRLIMIT64[arch], pid, 0, new_ptr, ctypes.byref(old))
        return status(int(result), ctypes.get_errno())

    def fcntl_cmd(fd: int, cmd: int, arg: int) -> str:
        libc.syscall.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_long, ctypes.c_long]
        libc.syscall.restype = ctypes.c_long
        ctypes.set_errno(0)
        result = libc.syscall(sandbox._FCNTL[arch], fd, cmd, arg)
        return status(int(result), ctypes.get_errno())

    def ioctl_pgrp(fd: int, pid: int) -> str:
        libc.syscall.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_ulong, ctypes.c_void_p]
        libc.syscall.restype = ctypes.c_long
        target = ctypes.c_int(pid)
        ctypes.set_errno(0)
        result = libc.syscall(sandbox._IOCTL[arch], fd, 0x5410, ctypes.byref(target))
        return status(int(result), ctypes.get_errno())

    def attempt(prefix: str) -> list[str]:
        read_fd, write_fd = os.pipe()
        try:
            return [
                "%s-prlimit:%s" % (prefix, prlimit(helper, Limit(0, 10**12))),
                "%s-setown:%s" % (prefix, fcntl_cmd(read_fd, 8, helper)),
                "%s-setsig:%s" % (prefix, fcntl_cmd(read_fd, 10, 10)),
                "%s-ioctl:%s" % (prefix, ioctl_pgrp(read_fd, helper)),
            ]
        finally:
            os.close(read_fd)
            os.close(write_fd)

    lines.append("self-prlimit:%s" % prlimit(os.getpid(), None))
    lines.append("zero-prlimit:%s" % prlimit(0, None))
    lines.append("negative-prlimit:%s" % prlimit(-1, None))
    read_fd, write_fd = os.pipe()
    try:
        lines.append("fcntl-getfl:%s" % fcntl_cmd(read_fd, 3, 0))
    finally:
        os.close(read_fd)
        os.close(write_fd)
    lines.extend(attempt("main"))
    boxed: list[str] = []

    def worker() -> None:
        boxed.extend(attempt("thread"))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    lines.extend(boxed)
    report_read, report_write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(report_read)
        code = 0
        try:
            text = "\n".join(attempt("child")) + "\n"
            os.write(report_write, text.encode())
        except Exception as exc:
            os.write(report_write, ("child-error:%s\n" % exc).encode())
            code = 1
        finally:
            os.close(report_write)
            os._exit(code)
    os.close(report_write)
    _pid, status = os.waitpid(child, 0)
    if status != 0:
        raise RuntimeError("confined child status %s" % status)
    lines.append(os.read(report_read, 65536).decode())
    os.close(report_read)
    time.sleep(0.4)
    (root / "owner-signals.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


_OWNER_PROBE_LAUNCH = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from tests.test_sandbox_findings import _run_owner_signal_probe\n"
    "_run_owner_signal_probe(sys.argv[1])\n"
)


def _start_signal_helper(tmp_path: Path) -> tuple[subprocess.Popen[bytes], Path]:
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

        for name in ("SIGUSR1", "SIGUSR2", "SIGIO", "SIGXCPU", "SIGWINCH", "SIGTERM"):
            value = getattr(signal, name, None)
            if value is not None:
                signal.signal(value, handler)
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
            raise RuntimeError("signal helper exited")
        time.sleep(0.02)
    proc.kill()
    raise RuntimeError("signal helper did not start")


def test_a_resource_limit_or_file_notice_cannot_signal_another_process(tmp_path):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux applies the signal filter in the kernel")
    _sandbox_or_skip()
    helper, log = _start_signal_helper(tmp_path)
    try:
        checkout = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["AICAD_HELPER_PID"] = str(helper.pid)
        refused = (
            "main-prlimit",
            "main-setown",
            "main-setsig",
            "main-ioctl",
            "thread-prlimit",
            "thread-setown",
            "thread-ioctl",
            "child-prlimit",
            "child-setown",
            "child-ioctl",
            "negative-prlimit",
        )
        allowed = ("self-prlimit", "zero-prlimit", "fcntl-getfl")
        for abi in ("", "5"):
            work = tmp_path / ("abi-real" if abi == "" else "abi-5")
            work.mkdir()
            run_env = env.copy()
            if abi:
                run_env["AICAD_OWNER_ABI"] = abi
            else:
                run_env.pop("AICAD_OWNER_ABI", None)
            completed = subprocess.run(
                [sys.executable, "-c", _OWNER_PROBE_LAUNCH, str(work), str(checkout)],
                cwd=checkout,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert completed.returncode == 0, completed.stderr
            text = (work / "owner-signals.txt").read_text(encoding="utf-8")
            rows = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
            if abi:
                assert rows["abi"] == "5"
                assert rows["scope"] == "False"
            else:
                assert rows["scope"] in {"True", "False"}
            for label in allowed:
                assert rows[label] == "allowed", label
            for label in refused:
                assert rows[label] == "denied:1", (label, rows.get(label))
        time.sleep(0.4)
        heard = log.read_text(encoding="utf-8") if log.is_file() else ""
        assert heard == ""
        assert helper.poll() is None
    finally:
        helper.kill()
        helper.wait(timeout=5)


def _assert_job_has_no_abi_cap(project: Path) -> None:
    jobs = project / ".aicad" / "jobs"
    files = list(jobs.glob("*/job.json")) if jobs.is_dir() else []
    assert files, "the script job was not written"
    for path in files:
        spec = json.loads(path.read_text(encoding="utf-8"))
        assert "landlock_abi_cap" not in spec
        assert "_test_landlock_abi" not in spec


def test_a_project_or_config_cannot_lower_the_landlock_abi(tmp_path, config_home, monkeypatch):
    project = tmp_path / "project"
    _plant_landlock_cap(project, monkeypatch, config_home / "config")
    allow_design_scripts(project)
    opt = config_home / "config" / "design_scripts.json"
    stored = json.loads(opt.read_text(encoding="utf-8"))
    stored["landlock_abi_cap"] = 5
    stored["_test_landlock_abi"] = 5
    stored["projects"][str(project.resolve())]["landlock_abi_cap"] = 5
    opt.write_text(json.dumps(stored) + "\n", encoding="utf-8")

    def stop(_job: Path) -> list[str]:
        raise ScriptError("stopped-before-launch")

    import aicad.sandbox as sandbox_module

    real_command = sandbox_module._child_command
    monkeypatch.setattr("aicad.sandbox._child_command", stop)
    supported, _reason = sandbox_supported()
    if supported:
        with pytest.raises(ScriptError, match="stopped-before-launch"):
            run_part_script("part = Box(1, 1, 1)\n", project)
        workspace = Workspace(project)
        with pytest.raises(ScriptError, match="stopped-before-launch"):
            workspace.execute(
                "run_design_script",
                {
                    "script": "print('no')\n",
                    "landlock_abi_cap": 5,
                    "_test_landlock_abi": 5,
                },
                source="agent",
            )
        _assert_job_has_no_abi_cap(project)
    monkeypatch.setattr("aicad.sandbox._child_command", real_command)

    if not sys.platform.startswith("linux"):
        return
    _sandbox_or_skip()
    _plant_landlock_cap(project, monkeypatch, config_home / "config")
    allow_design_scripts(project)
    stored = json.loads(opt.read_text(encoding="utf-8"))
    stored["landlock_abi_cap"] = 5
    stored["_test_landlock_abi"] = 5
    stored["projects"][str(project.resolve())]["landlock_abi_cap"] = 5
    opt.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    workspace = Workspace(project)
    script = (
        "import aicad.sandbox as sandbox\n"
        "open('abi.txt', 'w', encoding='utf-8').write('%s %s\\n' % ("
        "sandbox._landlock_abi_used, sandbox._landlock_signal_scope))\n"
    )
    payload = workspace.execute(
        "run_design_script",
        {"script": script, "landlock_abi_cap": 5, "_test_landlock_abi": 5},
        source="agent",
    )
    assert payload.get("ok") is True, payload.get("error")
    abi_text, scope = (project / "abi.txt").read_text(encoding="utf-8").split()
    real = _reported_landlock_abi()
    assert int(abi_text) == real
    assert scope == ("True" if real >= 6 else "False")


def test_a_job_file_cannot_lower_the_landlock_abi(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux applies the Landlock version in the kernel")
    _sandbox_or_skip()
    project = tmp_path / "project"
    project.mkdir()
    for key in (
        "AICAD_LANDLOCK_ABI",
        "AICAD_LANDLOCK_ABI_CAP",
        "LANDLOCK_ABI",
        "_TEST_LANDLOCK_ABI",
        "AICAD_TEST_LANDLOCK_ABI",
    ):
        monkeypatch.setenv(key, "5")
    job = project / ".aicad" / "jobs" / "planted"
    job.mkdir(parents=True)
    (job / "job.json").write_text(
        json.dumps(
            {
                "kind": "part",
                "network": False,
                "landlock_abi_cap": 5,
                "_test_landlock_abi": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (job / "script.py").write_text(
        "import aicad.sandbox as sandbox\n"
        "open('abi.txt', 'w', encoding='utf-8').write('%s %s\\n' % ("
        "sandbox._landlock_abi_used, sandbox._landlock_signal_scope))\n"
        "part = Box(1, 1, 1)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AICAD_WORKSPACE"] = str(project)
    completed = subprocess.run(
        [sys.executable, "-P", "-m", "aicad.sandbox_run", str(job)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    abi_text, scope = (project / "abi.txt").read_text(encoding="utf-8").split()
    real = _reported_landlock_abi()
    assert int(abi_text) == real
    assert scope == ("True" if real >= 6 else "False")
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] is True, result.get("error")


def test_bh_s01_design_script_cannot_clear_the_plugin_gate(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    script = (
        "import aicad.sandbox as sandbox\n"
        "sandbox.os.environ['AICAD_SANDBOX_CHILD'] = '0'\n"
        "import aicad.plugins as plugins\n"
        "try:\n"
        "    plugins.allow('no-such-plugin', accept=True)\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
    )
    payload = run_design_script(tmp_path, script)
    assert "PluginConsentError" in str(payload.get("stdout"))


def test_bh_s02_script_cannot_signal_its_parent(tmp_path):
    _sandbox_or_skip()
    script = (
        "import aicad.sandbox as sandbox\n"
        "try:\n"
        "    sandbox.os.kill(sandbox.os.getppid(), 0)\n"
        "    open('signal.txt', 'w').write('delivered')\n"
        "except OSError as exc:\n"
        "    open('signal.txt', 'w').write('denied:%s' % exc.errno)\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(script, tmp_path)
    assert (tmp_path / "signal.txt").read_text(encoding="utf-8").startswith("denied")


def test_bh_s03_script_cannot_leave_a_detached_child(tmp_path):
    _sandbox_or_skip()
    if sys.platform == "win32":
        # Windows has no fork. A second Python process is the same kind of
        # leftover: it sleeps, then writes the file, unless the job kills it.
        script = (
            "import aicad.sandbox as sandbox\n"
            "code = (\n"
            "    'import time\\n'\n"
            "    'from pathlib import Path\\n'\n"
            "    'time.sleep(8)\\n'\n"
            "    'Path(\"phase2.txt\").write_text(\"survived\\\\n\", encoding=\"utf-8\")\\n'\n"
            ")\n"
            "sandbox.subprocess.Popen([sandbox.sys.executable, '-c', code])\n"
            "part = Box(1, 1, 1)\n"
        )
    else:
        script = (
            "import aicad.sandbox as sandbox\n"
            "pid = sandbox.os.fork()\n"
            "if pid == 0:\n"
            "    sandbox.os.setsid()\n"
            "    \n"
            "    for fd in (0, 1, 2):\n"
            "        try:\n"
            "            sandbox.os.close(fd)\n"
            "        except OSError:\n"
            "            pass\n"
            "    sandbox.os.system('sleep 2')\n"
            "    open('phase2.txt', 'w').write('survived\\n')\n"
            "    sandbox.os._exit(0)\n"
            "part = Box(1, 1, 1)\n"
        )
    run_part_script(script, tmp_path)
    time.sleep(4)
    assert not (tmp_path / "phase2.txt").exists()


def test_a_reparented_child_stays_in_the_set_and_a_failed_listing_is_not_clean():
    """A child that has left its parent is still tied to the script by parent id.

    The parent unique id does not follow the process group. A listing that
    failed is not reported as an empty, clean tree.
    """
    from aicad.sandbox import _refuse_unaccounted, _related_pids

    identities = {
        5: (200, 100),
        6: (300, 200),
        9: (400, 1),
    }
    found = _related_pids({100}, identities, set())
    assert found == {5, 6}
    assert _related_pids({100}, identities, {7}) == {5, 6, 7}
    assert 9 not in found

    _refuse_unaccounted(True, set())
    with pytest.raises(ScriptError, match="could not account"):
        _refuse_unaccounted(False, set())
    with pytest.raises(ScriptError, match="could not account"):
        _refuse_unaccounted(True, {6})


def test_bh_f06_a_failed_script_keeps_the_network_grant(tmp_path, config_home):
    _sandbox_or_skip()
    allow_design_scripts(tmp_path)
    workspace = Workspace(tmp_path)
    workspace.allow_script_network_once()
    assert workspace.script_status()["network"] == "next run"
    workspace.execute("run_design_script", {"script": "raise RuntimeError('boom')"})
    assert workspace.script_status()["network"] == "next run"
