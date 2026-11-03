"""Start the installed app and open a browser once it answers.

The window that launched this stays open until the app exits.
Closing that window, or pressing Ctrl+C, stops the app.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
READY_SECONDS = 180


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def app_command(root: Path) -> Path | None:
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "aicad.exe"
    else:
        candidate = root / ".venv" / "bin" / "aicad"
    if candidate.is_file():
        return candidate
    return None


def launch_port() -> int:
    raw = os.environ.get("AICAD_LAUNCH_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PORT
    if 1 <= value <= 65535:
        return value
    return DEFAULT_PORT


def launch_url(port: int) -> str:
    return f"http://{HOST}:{port}/"


def listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            response.read(64)
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def open_browser(url: str) -> None:
    """Open the default browser. A failure here does not stop the app."""

    if os.environ.get("AICAD_NO_BROWSER") == "1":
        print(f"Open this address in your browser: {url}", flush=True)
        return
    try:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["cmd", "/c", "start", "", url],
                check=False,
                timeout=15,
            )
        elif sys.platform == "darwin":
            completed = subprocess.run(["open", url], check=False, timeout=15)
        else:
            opener = _which("xdg-open")
            if opener is None:
                print(f"Open this address in your browser: {url}", flush=True)
                return
            completed = subprocess.run([opener, url], check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        print(f"Open this address in your browser: {url}", flush=True)
        return
    if completed.returncode != 0:
        print(f"Open this address in your browser: {url}", flush=True)


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def start_app(command: list[str], *, cwd: Path) -> subprocess.Popen:
    """Start the app in its own process group so this window can stop it."""

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    kwargs: dict = {"cwd": cwd, "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # A new session so this window can stop the app and the app's children
        # together. Doing that with start_new_session forks, and a fork from a
        # process that already has threads can hang the child before it starts.
        command = [
            sys.executable,
            "-c",
            "import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])",
            *command,
        ]
    process = subprocess.Popen(command, **kwargs)
    _attach_kill_on_close(process)
    return process


def _attach_kill_on_close(process: subprocess.Popen) -> None:
    """On Windows, stop the app when this launcher process exits."""

    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        return
    handle = getattr(process, "_handle", None)
    if not handle or not kernel32.AssignProcessToJobObject(job, handle):
        kernel32.CloseHandle(job)
        return
    kernel32.SetHandleInformation(job, 0x00000001, 0)
    # The handle stays open until this process exits. Closing it stops the app.
    process._aicad_job = job  # type: ignore[attr-defined]


def _terminate_now(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    job = getattr(process, "_aicad_job", None)
    if job and sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject(job, 1)
    if sys.platform != "win32":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            return
        except OSError:
            pass
    try:
        process.terminate()
    except OSError:
        pass


def stop_app(process: subprocess.Popen) -> None:
    _terminate_now(process)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            return


def wait_until_ready(process: subprocess.Popen, url: str, seconds: float) -> bool:
    print("Waiting for the app to start.", flush=True)
    deadline = time.monotonic() + seconds
    next_note = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if http_ok(url):
            return True
        if time.monotonic() >= next_note:
            print("Still waiting for the app to start.", flush=True)
            next_note = time.monotonic() + 5
        time.sleep(0.4)
    return False


def _missing_message() -> str:
    return "\n".join(
        (
            "AI-First CAD is not installed in this folder yet.",
            "Run the installer, wait until it finishes, then start the app again.",
            "  Windows: install.bat, then Launch AI-First CAD.bat",
            "  Mac: Install AI-First CAD.command, then Launch AI-First CAD.command",
            "  Linux: install.sh, then launch.sh",
        )
    )


def main(argv: list[str] | None = None) -> int:
    del argv
    root = project_root()
    command_path = app_command(root)
    if command_path is None:
        print(_missing_message(), flush=True)
        return 1
    port = launch_port()
    url = launch_url(port)
    if listening(HOST, port):
        print(f"Something is already using {url}", flush=True)
        print("If AI-First CAD is already open, use that window.", flush=True)
        print("Close it before starting another copy.", flush=True)
        return 1

    print("Starting AI-First CAD.", flush=True)
    print("This window stays open while the app runs.", flush=True)
    print("To stop it, close this window or press Ctrl+C.", flush=True)
    print(flush=True)

    command = [
        str(command_path),
        "app",
        "--no-browser",
        "--host", HOST,
        "--port", str(port),
        "--workspace", str(root / "aicad_workspace"),
    ]
    process = start_app(command, cwd=root)
    pid_path = root / ".venv" / "desktop-server.pid"
    try:
        pid_path.write_text(str(process.pid), encoding="utf-8")
    except OSError:
        pid_path = None  # type: ignore[assignment]

    state = {"stopping": False}

    def _stop_signal(_signum, _frame) -> None:
        # Do not wait here. The main thread may already be inside process.wait.
        state["stopping"] = True
        _terminate_now(process)

    signal.signal(signal.SIGINT, _stop_signal)
    signal.signal(signal.SIGTERM, _stop_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _stop_signal)

    try:
        if not wait_until_ready(process, url, READY_SECONDS):
            print(flush=True)
            if state["stopping"]:
                print("AI-First CAD has stopped.", flush=True)
                return 0
            if process.poll() is not None:
                print("The app stopped before it was ready.", flush=True)
            else:
                print("The app did not answer in time.", flush=True)
                _terminate_now(process)
            print("The messages above say what went wrong.", flush=True)
            return 1
        print(flush=True)
        print(f"AI-First CAD is ready at {url}", flush=True)
        open_browser(url)
        print("If the browser did not open, paste that address into it.", flush=True)
        print(flush=True)
        print("Leave this window open while you use the app.", flush=True)
        print("Close this window, or press Ctrl+C, to stop the app.", flush=True)
        code = process.wait()
        if state["stopping"]:
            print(flush=True)
            print("AI-First CAD has stopped.", flush=True)
            return 0
        return 0 if code == 0 else int(code)
    finally:
        if process.poll() is None:
            stop_app(process)
        if pid_path is not None:
            pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
