"""Test hooks shared by the suite."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

# These files start a sandboxed script. On Windows every such script uses the
# same firewall rules, so the runs stay on one worker when the suite is split.
_WINDOWS_SANDBOX_FILES = frozenset(
    {
        "tests/test_assembly.py",
        "tests/test_catalog.py",
        "tests/test_document.py",
        "tests/test_fea.py",
        "tests/test_features.py",
        "tests/test_geometry.py",
        "tests/test_honesty.py",
        "tests/test_launch_paths.py",
        "tests/test_library.py",
        "tests/test_packaging.py",
        "tests/test_plugins.py",
        "tests/test_section.py",
        "tests/test_server.py",
        "tests/test_sketch.py",
        "tests/test_studio.py",
        "tests/test_workspace.py",
    }
)


def pytest_configure(config: pytest.Config) -> None:
    if config.pluginmanager.hasplugin("xdist"):
        return
    config.addinivalue_line(
        "markers",
        "xdist_group: kept on one worker when the suite is split across processes",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32":
        return
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        path = nodeid.split("::", 1)[0]
        grouped = (
            path.startswith("tests/test_sandbox")
            or path.startswith("tests/bughunt/")
            or path in _WINDOWS_SANDBOX_FILES
        )
        if grouped:
            item.add_marker(pytest.mark.xdist_group("windows-sandbox"))


@contextmanager
def _windows_sandbox_lock() -> Iterator[None]:
    """Hold the machine lock for one sandboxed script.

    The firewall rules are one pair for the whole machine. A second process
    installing or removing them can open the network for a script that must
    stay blocked. The script still runs in the real sandbox.
    """

    import msvcrt
    import os
    import tempfile
    import time
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / "aicad-pytest-windows-sandbox.lock"
    handle = path.open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) < 1:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                # 13 and 36 are the Windows errors for a region another process holds.
                if exc.errno not in (13, 36):
                    raise
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


@pytest.fixture(scope="session", autouse=True)
def _one_windows_sandbox_at_a_time() -> Iterator[None]:
    if sys.platform != "win32":
        yield
        return
    import aicad.sandbox as sandbox

    original = sandbox._spawn
    original_helper = sandbox.run_confined_helper
    if getattr(original, "_aicad_pytest_locked", False):
        yield
        return

    def locked_spawn(*args, **kwargs):
        with _windows_sandbox_lock():
            return original(*args, **kwargs)

    def locked_helper(*args, **kwargs):
        with _windows_sandbox_lock():
            return original_helper(*args, **kwargs)

    locked_spawn._aicad_pytest_locked = True  # type: ignore[attr-defined]
    sandbox._spawn = locked_spawn
    sandbox.run_confined_helper = locked_helper
    try:
        yield
    finally:
        sandbox._spawn = original
        sandbox.run_confined_helper = original_helper


def pytest_runtest_logreport(report) -> None:
    """Print a failure as soon as it happens.

    GitHub Actions stops the Windows job on the clock. pytest's end-of-run
    summary is still in the buffer then, so the log never names the test.
    """
    if not report.failed or report.when not in {"setup", "call"}:
        return
    text = getattr(report, "longreprtext", None) or str(report.longrepr)
    sys.stderr.write(f"\n=== FAIL {report.nodeid} ({report.when}) ===\n{text}\n")
    sys.stderr.flush()
