"""Test hooks shared by the suite."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

if os.environ.get("AICAD_TEST_APP_WORKER"):
    # A CI step runs the app's security tests this way, because `aicad app` uses the worker.
    import aicad.app as _app

    _one_process_make_server = _app.make_server

    def _make_server_with_worker(workspace, host="127.0.0.1", port=8765, *, worker=True):
        return _one_process_make_server(workspace, host, port, worker=worker)

    _app.make_server = _make_server_with_worker

# These files start a sandboxed script. On Windows every such script uses the
# same container profile, so the runs stay on one worker when the suite is split.
_WINDOWS_SANDBOX_FILES = frozenset(
    {
        "tests/test_app_worker_security.py",
        "tests/test_assembly.py",
        "tests/test_catalog.py",
        "tests/test_document.py",
        "tests/test_fea.py",
        "tests/test_features.py",
        "tests/test_geometry.py",
        "tests/test_honesty.py",
        "tests/test_sandbox_interpreter_links.py",
        "tests/test_launch_paths.py",
        "tests/test_sandbox_probe_failures.py",
        "tests/test_library.py",
        "tests/test_packaging.py",
        "tests/test_plugins.py",
        "tests/test_sandbox_private_folders.py",
        "tests/test_section.py",
        "tests/test_server.py",
        "tests/test_sketch.py",
        "tests/test_studio.py",
        "tests/test_workspace.py",
    }
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard",
        default=None,
        metavar="I/N",
        help="Run part I of N of the suite. The split is by file. See tests/shards.py.",
    )


def _keep_this_shard(config: pytest.Config, items: list[pytest.Item]) -> None:
    text = config.getoption("--shard")
    if not text:
        return
    from shards import assign, measured_seconds, parse, shard_of

    index, count = parse(text)
    placed = assign(measured_seconds(), count)
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        path = item.nodeid.replace("\\", "/").split("::", 1)[0]
        (kept if shard_of(path, count, placed) == index else dropped).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_configure(config: pytest.Config) -> None:
    if config.pluginmanager.hasplugin("xdist"):
        return
    config.addinivalue_line(
        "markers",
        "xdist_group: kept on one worker when the suite is split across processes",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _keep_this_shard(config, items)
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

    Every script shares one container profile, and starting one can replace
    that profile. The script still runs in the real sandbox.
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
def _catalog_cache_in_a_test_folder(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Built catalog solids go to a folder of this run, not to this account's real one.

    It is set in the environment, so an app's geometry process started by a test uses it too.
    """
    previous = os.environ.get("AICAD_CATALOG_CACHE")
    os.environ["AICAD_CATALOG_CACHE"] = str(tmp_path_factory.mktemp("catalog-cache"))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AICAD_CATALOG_CACHE", None)
        else:
            os.environ["AICAD_CATALOG_CACHE"] = previous


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
    if os.environ.get("GITHUB_ACTIONS"):
        # An annotation can be read without signing in to see the log.
        escaped = text[-700:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        sys.stderr.write(f"::error title={report.nodeid}::{escaped}\n")
    sys.stderr.flush()


_NO_SYMLINK_PRIVILEGE = 1314


@pytest.fixture(autouse=True)
def _link_without_the_symlink_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the link tests running on a Windows account that cannot make a symlink.

    A folder link becomes a junction, which the program has to refuse as well.
    A file link has no such stand-in, so that test skips. CI has the privilege,
    and there a failure stays a failure, so a link test cannot quietly stop running.
    """
    if sys.platform != "win32" or os.environ.get("CI"):
        return
    import _winapi

    real = os.symlink

    def symlink(src, dst, target_is_directory=False, *, dir_fd=None):
        try:
            return real(src, dst, target_is_directory, dir_fd=dir_fd)
        except OSError as exc:
            if getattr(exc, "winerror", None) != _NO_SYMLINK_PRIVILEGE:
                raise
        target = Path(dst).parent / os.fspath(src)
        if target_is_directory or target.is_dir():
            _winapi.CreateJunction(str(target.resolve()), str(dst))
            return None
        pytest.skip("this Windows account cannot create a file symlink")

    monkeypatch.setattr(os, "symlink", symlink)
