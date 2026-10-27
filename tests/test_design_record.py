"""The design record is written to the file the parent opened."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from aicad.file_access import open_inplace


def _windows_record(path: str) -> tuple[int, int]:
    """A readable file, and a second handle that may be closed.

    Windows can drop a directory entry that was opened with delete sharing.
    Closing the handle passed to ``model_main`` must leave this file open.
    """
    import ctypes
    from ctypes import wintypes

    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    generic_read_write = 0x80000000 | 0x40000000
    share_read_write_delete = 0x1 | 0x2 | 0x4
    open_existing = 3
    normal = 0x80
    duplicate_same_access = 0x2
    invalid = wintypes.HANDLE(-1).value
    handle = kernel32.CreateFileW(
        path,
        generic_read_write,
        share_read_write_delete,
        None,
        open_existing,
        normal,
        None,
    )
    if handle in {invalid, None} or int(handle) in {-1, 2**32 - 1, 2**64 - 1}:
        raise ctypes.WinError(ctypes.get_last_error())
    current = kernel32.GetCurrentProcess()
    copied = wintypes.HANDLE()
    if not kernel32.DuplicateHandle(
        current, handle, current, ctypes.byref(copied), 0, False, duplicate_same_access
    ):
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = msvcrt.open_osfhandle(int(handle), os.O_BINARY)
    return descriptor, int(copied.value)


def test_open_inplace_does_not_follow_a_link(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"kept")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(OSError):
        open_inplace(link)
    assert target.read_bytes() == b"kept"
    record = tmp_path / "record.json"
    record.write_bytes(b"old")
    descriptor = open_inplace(record)
    try:
        os.write(descriptor, b"new")
    finally:
        os.close(descriptor)
    assert record.read_bytes() == b"new"


def test_model_main_writes_the_open_file_not_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_WORKSPACE", str(tmp_path))
    job = tmp_path / "job"
    calls = job / "calls"
    calls.mkdir(parents=True)
    (calls / "STOP").write_text("", encoding="utf-8")
    descriptor, name = tempfile.mkstemp(prefix="aicad-record-", suffix=".json")
    reader: int | None = None
    try:
        if sys.platform == "win32":
            os.close(descriptor)
            descriptor = -1
            reader, token = _windows_record(name)
            os.remove(name)
            Path(name).write_bytes(b"SWAPPED")
        else:
            reader = os.dup(descriptor)
            token = descriptor
            swapped = tmp_path / "swapped"
            swapped.write_bytes(b"SWAPPED")
            os.replace(swapped, name)
        from aicad.design_calls import model_main

        assert model_main(job, Path(name), token) == 0
        os.lseek(reader, 0, os.SEEK_SET)
        body = os.read(reader, 4096)
        assert b'"checks"' in body
        assert Path(name).read_bytes() == b"SWAPPED"
    finally:
        if reader is not None:
            os.close(reader)
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        Path(name).unlink(missing_ok=True)


def test_the_parent_passes_the_open_record(tmp_path, monkeypatch):
    from aicad.sandbox import _DesignRecord

    captured: dict = {}

    class Fake:
        def __init__(self, command, **kwargs):
            captured["command"] = list(command)
            captured["pass_fds"] = kwargs.get("pass_fds")
            captured["startupinfo"] = kwargs.get("startupinfo")

        def poll(self):
            return 0

    if sys.platform != "win32":
        from aicad.paths import _libc

        _libc()
    monkeypatch.setattr("aicad.sandbox.subprocess.Popen", Fake)
    job = tmp_path / "job"
    job.mkdir()
    record = _DesignRecord(tmp_path, job)
    record.start()
    try:
        command = captured["command"]
        assert command[command.index("--model") + 1] == str(job)
        token = int(command[-1])
        if sys.platform == "win32":
            handles = captured["startupinfo"].lpAttributeList["handle_list"]
            assert token in handles
        else:
            assert token in captured["pass_fds"]
    finally:
        record.close()


def test_create_file_creates_a_private_file(tmp_path):
    import stat

    from aicad.file_access import create_file

    descriptor = create_file(tmp_path, "fresh.log")
    try:
        os.write(descriptor, b"ok\n")
        if os.name != "nt":
            assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600
    finally:
        os.close(descriptor)
    assert (tmp_path / "fresh.log").read_text(encoding="utf-8") == "ok\n"
    with pytest.raises(OSError):
        create_file(tmp_path, "fresh.log")
    assert (tmp_path / "fresh.log").read_text(encoding="utf-8") == "ok\n"


def test_create_file_does_not_follow_a_link(tmp_path):
    from aicad.file_access import create_file

    target = tmp_path / "target.log"
    target.write_text("CANARY_HIT\n", encoding="utf-8")
    link = tmp_path / "name.log"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(OSError):
        create_file(tmp_path, link.name)
    assert target.read_text(encoding="utf-8") == "CANARY_HIT\n"
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(OSError):
        create_file(linked, "fresh.log")
    assert not (real / "fresh.log").exists()


def test_the_design_record_log_is_opened_from_its_directory(tmp_path, monkeypatch):
    from aicad import file_access
    from aicad.sandbox import _DesignRecord

    seen: list[tuple[Path, str]] = []
    real = file_access.create_file

    def spy(directory: Path, name: str) -> int:
        seen.append((Path(directory), name))
        return real(directory, name)

    class Fake:
        def __init__(self, command, **kwargs):
            del command, kwargs

        def poll(self):
            return 0

    if sys.platform != "win32":
        from aicad.paths import _libc

        _libc()
    monkeypatch.setattr(file_access, "create_file", spy)
    monkeypatch.setattr("aicad.sandbox.subprocess.Popen", Fake)
    job = tmp_path / "job"
    job.mkdir()
    record = _DesignRecord(tmp_path, job)
    record.start()
    try:
        assert len(seen) == 1
        directory, name = seen[0]
        assert name.endswith(".log")
        assert (directory / name).is_file()
    finally:
        record.close()


def test_model_dispatch_accepts_an_open_file(monkeypatch):
    from aicad import sandbox_run

    seen: dict = {}

    def fake(job, record, record_fd=None):
        del job, record
        seen["fd"] = record_fd
        return 0

    monkeypatch.setattr("aicad.design_calls.model_main", fake)
    assert sandbox_run.main(["--model", "job", "record"]) == 0
    assert seen["fd"] is None
    assert sandbox_run.main(["--model", "job", "record", "7"]) == 0
    assert seen["fd"] == 7
    assert sandbox_run.main(["--model", "job", "record", "nope"]) == 2
