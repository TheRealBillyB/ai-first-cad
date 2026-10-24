#!/usr/bin/env python3
"""Run the hypoid mesh check on macOS and keep the native crash report.

The first runs leave OpenCascade on its stock worker threads
(``AICAD_MACOS_OCCT_WORKERS=default``) and disable the suite timeout, so a
signal is not confused with the timeout helper. Later runs use the calling
thread, including a hypoid build started off the main thread, and the suite
timeout. This file is not part of the suite.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "macos-diagnostic-reports"
PYTEST = ROOT / ".venv" / "bin" / "pytest"
PYTHON = ROOT / ".venv" / "bin" / "python"
STOCK_TESTS = [
    "tests/test_gear_mesh.py::test_a_misclocked_or_misplaced_hypoid_assembly_is_flagged",
]
FIXED_TESTS = [
    *STOCK_TESTS,
    "tests/test_gear_mesh.py::test_a_hypoid_build_runs_on_a_worker_thread",
    "tests/test_geometry.py::test_macos_python_threads_get_a_geometry_stack",
]
STAMP = time.time()


def _signal_name(code: int) -> str:
    if code >= 0:
        return f"exit {code}"
    number = -code
    try:
        return f"signal {signal.Signals(number).name} ({number})"
    except ValueError:
        return f"signal {number}"


def _probe() -> None:
    probe = r"""
import ctypes, os, sys, threading
print("cpu", os.cpu_count())
print("platform", sys.platform)
libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
libc.pthread_self.restype = ctypes.c_void_p
libc.pthread_get_stacksize_np.restype = ctypes.c_size_t
libc.pthread_get_stacksize_np.argtypes = [ctypes.c_void_p]

def show(label):
    size = libc.pthread_get_stacksize_np(libc.pthread_self())
    print(f"stack {label} {size}", flush=True)

show("main")
thread = threading.Thread(target=lambda: show("python-thread"))
thread.start()
thread.join()

CALLBACK = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)

def raw(_arg):
    show("raw-pthread")
    return None

callback = CALLBACK(raw)
libc.pthread_create.restype = ctypes.c_int
libc.pthread_create.argtypes = [
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, CALLBACK, ctypes.c_void_p,
]
libc.pthread_join.restype = ctypes.c_int
libc.pthread_join.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ident = ctypes.c_void_p()
print("pthread_create", libc.pthread_create(ctypes.byref(ident), None, callback, None))
libc.pthread_join(ident, None)
"""
    subprocess.run([str(PYTHON), "-c", probe], check=False)


def _collect(label: str) -> None:
    time.sleep(12)
    dest = OUT / label
    dest.mkdir(parents=True, exist_ok=True)
    folders = [
        Path.home() / "Library/Logs/DiagnosticReports",
        Path("/Library/Logs/DiagnosticReports"),
    ]
    copied = 0
    for folder in folders:
        if not folder.is_dir():
            print(f"no report folder {folder}")
            continue
        listing = dest / f"{folder.name}.listing"
        lines = []
        for path in sorted(folder.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            lines.append(f"{path.stat().st_mtime:.0f} {path.name}")
            if path.stat().st_mtime + 1 >= STAMP:
                shutil.copy2(path, dest / path.name)
                copied += 1
                print(f"report {path}")
        listing.write_text("\n".join(lines[:40]) + "\n", encoding="utf-8")
    print(f"copied {copied} reports for {label}")


def _run(label: str, env: dict[str, str], timeout: int, extra: list[str], tests: list[str]) -> str:
    log = OUT / f"{label}.log"
    command = [
        str(PYTEST),
        "-p",
        "faulthandler",
        "-ra",
        "--tb=short",
        "-vv",
        "--durations=10",
        *extra,
        *tests,
    ]
    print(f"=== {label} ===", flush=True)
    print(" ".join(command), flush=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        code: int | None = None
        assert process.stdout is not None
        stream = process.stdout
        os.set_blocking(stream.fileno(), False)
        watcher = selectors.DefaultSelector()
        watcher.register(stream, selectors.EVENT_READ)
        pending = ""

        def _emit(line: str) -> None:
            handle.write(line)
            handle.flush()
            sys.stdout.write(line)
            sys.stdout.flush()

        def _take() -> bytes | None:
            try:
                chunk = os.read(stream.fileno(), 1 << 16)
            except BlockingIOError:
                return b""
            return chunk

        try:
            while True:
                readable = bool(watcher.select(timeout=5))
                if readable:
                    chunk = _take()
                    if chunk:
                        pending += chunk.decode("utf-8", errors="replace")
                        lines = pending.split("\n")
                        pending = lines[-1]
                        for line in lines[:-1]:
                            _emit(line + "\n")
                        continue
                if process.poll() is not None:
                    while True:
                        chunk = _take()
                        if not chunk:
                            break
                        pending += chunk.decode("utf-8", errors="replace")
                    if pending:
                        _emit(pending if pending.endswith("\n") else pending + "\n")
                    code = process.returncode
                    break
                if time.monotonic() - started > timeout:
                    process.kill()
                    process.wait(timeout=30)
                    code = None
                    print(f"EXIT {label} watchdog after {timeout}s", flush=True)
                    break
        except Exception:
            process.kill()
            raise
        finally:
            watcher.close()
    if code is None:
        _collect(label)
        return "watchdog"
    print(f"EXIT {label} {_signal_name(code)} after {time.monotonic() - started:.0f}s", flush=True)
    if code < 0:
        _collect(label)
        return _signal_name(code)
    return f"exit {code}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not PYTEST.is_file():
        print(f"missing {PYTEST}", file=sys.stderr)
        return 2
    _probe()
    base = os.environ.copy()
    base["PYTHONUNBUFFERED"] = "1"
    base["PYTHONFAULTHANDLER"] = "1"
    stock = dict(base)
    stock["AICAD_MACOS_OCCT_WORKERS"] = "default"
    results: list[str] = []
    crashed = False
    for index in range(1, 4):
        outcome = _run(f"stock-{index}", stock, timeout=600, extra=["--timeout=0"], tests=STOCK_TESTS)
        results.append(f"stock-{index} {outcome}")
        if outcome.startswith("signal"):
            crashed = True
            break
    proof_failures = 0
    for index in range(1, 5):
        outcome = _run(f"fixed-{index}", base, timeout=720, extra=[], tests=FIXED_TESTS)
        results.append(f"fixed-{index} {outcome}")
        if outcome != "exit 0":
            proof_failures += 1
            _collect(f"fixed-{index}")
    summary = "\n".join(results) + f"\ncrashed={crashed} proof_failures={proof_failures}\n"
    (OUT / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    if proof_failures:
        return 1
    if not crashed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
