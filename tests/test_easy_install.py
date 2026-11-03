"""Double-click installers and the desktop launcher.

The shell scripts are run here against a stand-in for install.py. The
workflow in .github/workflows/easy-install.yml runs them against the real
installer, then checks that the app answers and stops.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL_LAUNCHERS = (
    "install.sh",
    "launch.sh",
    "Install AI-First CAD.command",
    "Launch AI-First CAD.command",
)


def _bash() -> str:
    found = shutil.which("bash")
    if found:
        return found
    windows = Path(r"C:\Program Files\Git\bin\bash.exe")
    if windows.is_file():
        return str(windows)
    pytest.fail("bash is required to check the install and launch scripts")


def _load_launch():
    import importlib.util

    path = ROOT / "scripts" / "desktop_launch.py"
    spec = importlib.util.spec_from_file_location("aicad_desktop_launch_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _copy_script(name: str, directory: Path) -> Path:
    target = directory / name
    target.write_bytes((ROOT / name).read_bytes())
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _install_tree(tmp_path: Path) -> Path:
    _copy_script("install.sh", tmp_path)
    return tmp_path / "install.sh"


def _run_install(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(script)],
        cwd=script.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CI"] = "true"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("AICAD_PYTHON", None)
    env.pop("AICAD_EASY_INSTALL_PATH_ONLY", None)
    return env


def test_readme_explains_double_click_install_before_the_terminal_command():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    easy = text.index("### Easy install (no command line)")
    command = text.index("python install.py")
    assert easy < command
    for name in (
        "install.bat",
        "Launch AI-First CAD.bat",
        "install.sh",
        "launch.sh",
        "Install AI-First CAD.command",
        "Launch AI-First CAD.command",
    ):
        assert name in text


def test_shell_scripts_are_lf_and_parse():
    bash = _bash()
    for name in SHELL_LAUNCHERS:
        data = (ROOT / name).read_bytes()
        assert b"\r" not in data, name
        completed = subprocess.run(
            [bash, "-n", str(ROOT / name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_windows_scripts_are_crlf():
    for name in ("install.bat", "Launch AI-First CAD.bat"):
        data = (ROOT / name).read_bytes()
        assert b"\r\n" in data, name
        assert b"\n" not in data.replace(b"\r\n", b""), name
        text = data.decode("utf-8")
        assert "desktop_launch.py" in text or "install.py" in text
        assert "Ctrl+C" in text
        assert "https://www.python.org/downloads/" in text or "desktop_launch.py" in text


def test_windows_ci_runs_the_installer_in_cmd():
    """Git Bash would swallow /d and /c, so the workflow uses the cmd shell."""

    text = (ROOT / ".github/workflows/easy-install-suite.yml").read_text(encoding="utf-8")
    commands = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
    assert all("cmd.exe /d /c" not in line for line in commands)
    assert "shell: cmd" in text
    assert text.count("run: install.bat") == 2


def test_install_bat_offers_winget_and_points_at_python():
    text = (ROOT / "install.bat").read_text(encoding="utf-8")
    assert "winget" in text
    assert "Python.Python.3.11" in text
    assert "https://www.python.org/downloads/" in text
    assert "install.py" in text
    assert "3.11" in text


def test_mac_command_files_run_the_shell_scripts():
    install = (ROOT / "Install AI-First CAD.command").read_text(encoding="utf-8")
    launch = (ROOT / "Launch AI-First CAD.command").read_text(encoding="utf-8")
    assert "install.sh" in install
    assert "launch.sh" in launch
    assert 'cd "$(dirname "$0")"' in install
    assert 'cd "$(dirname "$0")"' in launch


def test_shell_launchers_are_executable():
    if sys.platform == "win32":
        listed = subprocess.check_output(
            ["git", "ls-files", "-s", "--", *SHELL_LAUNCHERS],
            cwd=ROOT,
            text=True,
        )
        lines = [line for line in listed.splitlines() if line.strip()]
        assert len(lines) == len(SHELL_LAUNCHERS)
        for line in lines:
            assert line.split()[0] == "100755", line
        return
    for name in SHELL_LAUNCHERS:
        assert os.access(ROOT / name, os.X_OK), name


def test_install_sh_runs_install_py_twice(tmp_path: Path):
    script = _install_tree(tmp_path)
    (tmp_path / "install.py").write_text(
        "from pathlib import Path\n"
        "path = Path(__file__).resolve().parent / 'runs.txt'\n"
        "count = int(path.read_text()) if path.exists() else 0\n"
        "path.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    env = _base_env()
    env["AICAD_PYTHON"] = sys.executable
    output = ""
    for _ in range(2):
        completed = _run_install(script, env)
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output
    assert (tmp_path / "runs.txt").read_text(encoding="utf-8") == "2"
    assert "Install finished." in output
    assert "Running this installer again is safe." in output


def test_install_sh_reports_a_failed_install(tmp_path: Path):
    script = _install_tree(tmp_path)
    (tmp_path / "install.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    env = _base_env()
    env["AICAD_PYTHON"] = sys.executable
    completed = _run_install(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "Installation did not finish." in output


def test_install_sh_rejects_an_old_requested_python(tmp_path: Path):
    script = _install_tree(tmp_path)
    (tmp_path / "install.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    fake = tmp_path / "oldpython"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    env = _base_env()
    env["AICAD_PYTHON"] = str(fake)
    completed = _run_install(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "AICAD_PYTHON" in output
    assert "3.11" in output
    assert "Install finished." not in output


def _posix_executable() -> str:
    """Path Git Bash can execute. A Windows path in a shell script is not one."""

    path = Path(sys.executable)
    if os.name != "nt":
        return str(path)
    drive = path.drive.rstrip(":").lower()
    rest = path.as_posix().split(":", 1)[-1]
    if drive and rest.startswith("/"):
        return f"/{drive}{rest}"
    return str(path)


def test_install_sh_prefers_a_python_that_can_create_a_venv(tmp_path: Path):
    script = _install_tree(tmp_path)
    (tmp_path / "install.py").write_text(
        "from pathlib import Path\n"
        "Path('chosen.txt').write_text('yes')\n",
        encoding="utf-8",
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    old = bindir / "python3.11"
    old.write_text(
        "#!/bin/sh\n"
        "case \"$2\" in\n"
        "  *version_info*) exit 0 ;;\n"
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    old.chmod(0o755)
    newer = bindir / "python3.12"
    newer.write_text(
        "#!/bin/sh\nexec " + shlex.quote(_posix_executable()) + ' "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    newer.chmod(0o755)
    env = _base_env()
    env["AICAD_EASY_INSTALL_PATH_ONLY"] = "1"
    env["PATH"] = os.pathsep.join((str(bindir), "/usr/bin", "/bin"))
    completed = _run_install(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert (tmp_path / "chosen.txt").is_file()
    assert "python3.12" in output


def test_install_sh_tells_you_where_to_get_python_when_it_is_missing(tmp_path: Path):
    script = _install_tree(tmp_path)
    (tmp_path / "install.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("python3.11", "python3.12", "python3.13", "python3.14", "python3", "python"):
        program = bindir / name
        program.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        program.chmod(0o755)
    env = _base_env()
    env["AICAD_EASY_INSTALL_PATH_ONLY"] = "1"
    env["PATH"] = os.pathsep.join((str(bindir), "/usr/bin", "/bin"))
    completed = _run_install(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "https://www.python.org/downloads/" in output
    assert "3.11" in output
    assert "Install finished." not in output


def test_launcher_waits_until_the_server_answers_then_stops_it(tmp_path: Path):
    # The suite keeps a timeout thread. A fork from that process can hang the
    # child, which is what macOS did. The launcher has to start anyway.
    threading.Thread(target=time.sleep, args=(60,), daemon=True).start()
    module = _load_launch()
    port = _free_port()
    server = tmp_path / "server.py"
    server.write_text(
        textwrap.dedent(
            """
            import sys
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = b"aicad workspace"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, fmt, *args):
                    return

            from pathlib import Path
            Path("ready.txt").write_text("ready\\n", encoding="utf-8")
            ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
            """
        ),
        encoding="utf-8",
    )
    trace = tmp_path / "trace.txt"
    os.environ["AICAD_LAUNCH_TRACE"] = str(trace)
    process = module.start_app([sys.executable, str(server), str(port)], cwd=tmp_path)
    url = f"http://127.0.0.1:{port}/"
    try:
        ready = module.wait_until_ready(process, url, 20)
        detail = trace.read_text(encoding="utf-8") if trace.is_file() else "no trace"
        ready_note = (tmp_path / "ready.txt").read_text(encoding="utf-8") if (tmp_path / "ready.txt").is_file() else "no ready"
        assert ready, f"poll={process.poll()} trace={detail!r} ready={ready_note!r}"
        assert module.http_ok(url)
    finally:
        os.environ.pop("AICAD_LAUNCH_TRACE", None)
        module.stop_app(process)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and module.listening("127.0.0.1", port):
        time.sleep(0.1)
    assert process.poll() is not None
    assert not module.listening("127.0.0.1", port)


def test_wait_until_ready_fails_when_the_process_exits(tmp_path: Path):
    module = _load_launch()
    process = module.start_app([sys.executable, "-c", "import sys; sys.exit(1)"], cwd=tmp_path)
    try:
        assert module.wait_until_ready(process, "http://127.0.0.1:9/", 10) is False
    finally:
        module.stop_app(process)


def test_open_browser_can_be_skipped(monkeypatch, capsys):
    module = _load_launch()
    monkeypatch.setenv("AICAD_NO_BROWSER", "1")
    module.open_browser("http://127.0.0.1:9/")
    assert "http://127.0.0.1:9/" in capsys.readouterr().out


def test_main_reports_a_missing_install(tmp_path: Path, monkeypatch, capsys):
    module = _load_launch()
    monkeypatch.setattr(module, "project_root", lambda: tmp_path)
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "not installed" in output
    assert "install.bat" in output
    assert "install.sh" in output


def test_main_stops_when_the_port_is_taken(tmp_path: Path, monkeypatch, capsys):
    module = _load_launch()
    if sys.platform == "win32":
        command = tmp_path / ".venv" / "Scripts" / "aicad.exe"
    else:
        command = tmp_path / ".venv" / "bin" / "aicad"
    command.parent.mkdir(parents=True)
    command.write_text("", encoding="utf-8")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    monkeypatch.setenv("AICAD_LAUNCH_PORT", str(port))
    monkeypatch.setattr(module, "project_root", lambda: tmp_path)
    try:
        assert module.main() == 1
    finally:
        sock.close()
    output = capsys.readouterr().out
    assert "already" in output.lower()
    assert str(port) in output


@pytest.mark.skipif(sys.platform == "win32", reason="launch.sh is the macOS and Linux launcher")
def test_launch_sh_opens_a_server_and_stopping_it_closes_the_port(tmp_path: Path):
    module = _load_launch()
    _copy_script("launch.sh", tmp_path)
    helper = tmp_path / "scripts"
    helper.mkdir()
    shutil.copy(ROOT / "scripts" / "desktop_launch.py", helper / "desktop_launch.py")
    bindir = tmp_path / ".venv" / "bin"
    bindir.mkdir(parents=True)
    os.symlink(sys.executable, bindir / "python")
    port = _free_port()
    aicad = bindir / "aicad"
    aicad.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import sys
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            port = 8765
            if "--port" in sys.argv:
                port = int(sys.argv[sys.argv.index("--port") + 1])

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = b"aicad workspace"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, fmt, *args):
                    return

            from pathlib import Path
            Path("ready.txt").write_text("ready\\n", encoding="utf-8")
            ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
            """
        ),
        encoding="utf-8",
    )
    aicad.chmod(0o755)
    log_path = tmp_path / "launch.log"
    env = _base_env()
    env["AICAD_NO_BROWSER"] = "1"
    env["AICAD_LAUNCH_PORT"] = str(port)
    env["AICAD_LAUNCH_TRACE"] = str(tmp_path / "trace.txt")
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(tmp_path / "launch.sh")],
        cwd=tmp_path,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if module.http_ok(url):
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        log.flush()
        text = log_path.read_text(encoding="utf-8")
        trace_path = tmp_path / "trace.txt"
        detail = trace_path.read_text(encoding="utf-8") if trace_path.is_file() else "no trace"
        ready_path = tmp_path / "ready.txt"
        ready_note = ready_path.read_text(encoding="utf-8") if ready_path.is_file() else "no ready"
        assert module.http_ok(url), f"{text}\ntrace={detail!r} ready={ready_note!r} poll={proc.poll()}"
        assert "Ctrl+C" in text
        os.killpg(proc.pid, signal.SIGTERM)
        closed = time.monotonic() + 10
        while time.monotonic() < closed and module.listening("127.0.0.1", port):
            time.sleep(0.1)
        assert not module.listening("127.0.0.1", port), text
    finally:
        log.close()
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait(timeout=10)
        pid_file = tmp_path / ".venv" / "desktop-server.pid"
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
            except (OSError, ValueError):
                pass


def _run_bat(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    return subprocess.run(
        [comspec, "/d", "/c", "call", str(script)],
        cwd=script.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="install.bat runs on Windows")
def test_install_bat_runs_install_py_twice(tmp_path: Path):
    script = _copy_script("install.bat", tmp_path)
    (tmp_path / "install.py").write_text(
        "from pathlib import Path\n"
        "path = Path(__file__).resolve().parent / 'runs.txt'\n"
        "count = int(path.read_text()) if path.exists() else 0\n"
        "path.write_text(str(count + 1))\n",
        encoding="utf-8",
    )
    env = _base_env()
    env["AICAD_PYTHON"] = sys.executable
    output = ""
    for _ in range(2):
        completed = _run_bat(script, env)
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output
    assert (tmp_path / "runs.txt").read_text(encoding="utf-8") == "2"
    assert "Install finished." in output


@pytest.mark.skipif(sys.platform != "win32", reason="install.bat runs on Windows")
def test_install_bat_reports_a_failed_install(tmp_path: Path):
    script = _copy_script("install.bat", tmp_path)
    (tmp_path / "install.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    env = _base_env()
    env["AICAD_PYTHON"] = sys.executable
    completed = _run_bat(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "Installation did not finish." in output


@pytest.mark.skipif(sys.platform != "win32", reason="install.bat runs on Windows")
def test_install_bat_rejects_an_old_requested_python(tmp_path: Path):
    script = _copy_script("install.bat", tmp_path)
    (tmp_path / "install.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    fake = tmp_path / "oldpython.cmd"
    fake.write_text("@echo off\r\nexit /b 1\r\n", encoding="utf-8")
    env = _base_env()
    env["AICAD_PYTHON"] = str(fake)
    completed = _run_bat(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "AICAD_PYTHON" in output
    assert "3.11" in output


@pytest.mark.skipif(sys.platform != "win32", reason="install.bat runs on Windows")
def test_install_bat_tells_you_where_to_get_python_when_it_is_missing(tmp_path: Path):
    script = _copy_script("install.bat", tmp_path)
    (tmp_path / "install.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    env = _base_env()
    env["AICAD_EASY_INSTALL_PATH_ONLY"] = "1"
    system = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = str(Path(system) / "System32")
    completed = _run_bat(script, env)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "https://www.python.org/downloads/" in output
    assert "Install finished." not in output
