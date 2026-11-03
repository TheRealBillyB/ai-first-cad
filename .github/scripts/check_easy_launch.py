"""Start the desktop launcher, confirm the app answers, then stop it.

This is the check the easy-install workflow runs after the installer.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:8765/"
READY_SECONDS = 240
STOP_SECONDS = 30


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _launcher(root: Path) -> list[str]:
    if sys.platform == "win32":
        # call keeps the space in the launcher name as one command.
        return ["cmd.exe", "/d", "/c", "call", "Launch AI-First CAD.bat"]
    return [str(root / "launch.sh")]


def _body_ok() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=3) as response:
            status = response.status
            body = response.read(8000)
    except (OSError, urllib.error.URLError, ValueError):
        return False
    return status == 200 and b"aicad workspace" in body.lower()


def _dump(log_path: Path) -> None:
    if not log_path.is_file():
        print("The launcher wrote no log.", file=sys.stderr)
        return
    data = log_path.read_bytes()
    if len(data) > 200_000:
        data = data[-200_000:]
    print("--- launcher log ---", file=sys.stderr)
    print(data.decode("utf-8", errors="replace"), file=sys.stderr)
    print("--- end launcher log ---", file=sys.stderr)


def _read_pid(root: Path) -> int | None:
    path = root / ".venv" / "desktop-server.pid"
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _kill_launcher(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return


def _kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def _wait_until(predicate, seconds: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + seconds
    next_note = time.monotonic()
    while time.monotonic() < deadline:
        if predicate():
            return True
        if proc is not None and proc.poll() is not None and not predicate():
            return False
        if time.monotonic() >= next_note:
            print("waiting for the app...", flush=True)
            next_note = time.monotonic() + 15
        time.sleep(1)
    return False


def main() -> int:
    root = _root()
    log_dir = Path(os.environ.get("RUNNER_TEMP", "/tmp"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "aicad-easy-launch.log"
    command = _launcher(root)
    print(f"starting {' '.join(command)}", flush=True)
    log = log_path.open("w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            command,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log.close()
        print(f"could not start the launcher: {exc}", file=sys.stderr)
        return 1
    server_pid: int | None = None
    try:
        ready = _wait_until(_body_ok, READY_SECONDS, proc)
        server_pid = _read_pid(root)
        if not ready:
            print("the app did not answer at " + URL, file=sys.stderr)
            _dump(log_path)
            return 1
        print("server responded", flush=True)
    finally:
        log.flush()
        _kill_launcher(proc)
        log.close()
    if _wait_until(lambda: not _body_ok(), STOP_SECONDS):
        print("server stopped", flush=True)
        return 0
    if server_pid is not None:
        _kill_pid(server_pid)
        if _wait_until(lambda: not _body_ok(), 10):
            print("server stopped", flush=True)
            return 0
    print("the app kept running after the launcher was stopped", file=sys.stderr)
    _dump(log_path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
