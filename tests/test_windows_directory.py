"""The geometry import must not leave the project directory.

FreeImage's attach routine asks to enter the directory of the process image.
On Windows that image is the base interpreter. The guard refuses that one
directory and, while the request is in progress, a relative plugin. These
checks execute the guard on Linux through a System V adapter. The Windows
process installs the same bytes.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from aicad import paths
from aicad.windows_directory import (
    _assemble_directory_hook,
    _assemble_library_hook,
    _steal,
    _x64_instruction,
    interpreter_directory,
    pin_interpreter_directory,
)

_FOLDER = r"C:\hostedtoolcache\windows\Python\3.11.9\x64"


def test_interpreter_directory_stops_at_the_last_separator():
    assert interpreter_directory(_FOLDER + r"\python.exe") == _FOLDER
    assert interpreter_directory("/usr/bin/python3") == "/usr/bin"
    assert interpreter_directory("python.exe") == ""
    assert interpreter_directory(r"C:\python.exe") == "C:"


def test_a_common_prologue_is_five_bytes_without_a_rewrite():
    prologue = bytes.fromhex("48 89 5C 24 08 48 89 6C 24 10")
    assert _x64_instruction(prologue, 0) == (5, None)
    assert _steal(prologue, 5) == (5, [])
    rip = bytes.fromhex("48 8B 05 00 00 00 00")
    assert _x64_instruction(rip, 0) == (7, 3)


def test_directory_guard_fits_beside_its_trampoline():
    base = 0x100000000
    hook = _assemble_directory_hook(base, base + 0x180, base + 0x200, base + 0x2000)
    library = _assemble_library_hook(base + 0x1000, base + 0x1180, base + 0x2000)
    assert len(hook) <= 0x180
    assert len(library) <= 0x180


def test_prepare_geometry_import_pins_the_interpreter_directory(monkeypatch):
    called = []
    monkeypatch.setattr(paths, "pin_interpreter_directory", lambda: called.append(1))
    paths.prepare_geometry_import()
    assert called == [1]


def test_pin_is_a_no_op_off_windows():
    if sys.platform == "win32":
        return
    pin_interpreter_directory()


def _execute_guard():
    import mmap

    page = mmap.mmap(-1, 0x3000, prot=mmap.PROT_READ | mmap.PROT_WRITE)
    base = ctypes.addressof(ctypes.c_char.from_buffer(page))
    folder = _FOLDER.encode("utf-16-le") + b"\x00\x00"
    hook = _assemble_directory_hook(base, base + 0x180, base + 0x200, base + 0x2000)
    library = _assemble_library_hook(base + 0x1000, base + 0x1180, base + 0x2000)
    raw = (ctypes.c_char * 0x3000).from_buffer(page)
    raw[: len(hook)] = hook
    raw[0x180:0x186] = b"\xb8\x02\x00\x00\x00\xc3"
    raw[0x200 : 0x200 + len(folder)] = folder
    raw[0x1000 : 0x1000 + len(library)] = library
    raw[0x1180:0x1186] = b"\xb8\x02\x00\x00\x00\xc3"
    directory_at = base + 0x1400
    library_at = base + 0x1480
    raw[0x1400:0x1408] = _adapter(directory_at, base)
    raw[0x1480:0x1488] = _adapter(library_at, base + 0x1000)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    libc.mprotect.restype = ctypes.c_int
    if libc.mprotect(base, 0x2000, mmap.PROT_READ | mmap.PROT_EXEC) != 0:
        raise OSError("the guard page could not be executed")
    call = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
    directory = call(directory_at)
    plugin = call(library_at)
    flag = (ctypes.c_char * 1).from_address(base + 0x2000)
    return page, directory, plugin, flag


def _adapter(at: int, target: int) -> bytes:
    relative = target - (at + 8)
    return b"\x48\x89\xf9\xe9" + int(relative).to_bytes(4, "little", signed=True)


def _wide(text: str):
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    return ctypes.create_string_buffer(encoded)


@pytest.mark.skipif(sys.platform != "linux", reason="the guard is executed with a System V adapter")
def test_only_the_interpreter_directory_is_ignored_and_a_project_plugin_is_refused():
    page, directory, plugin, flag = _execute_guard()
    try:
        assert flag[0] == b"\x00"
        cases = [
            _FOLDER,
            _FOLDER.lower(),
            _FOLDER.upper(),
            _FOLDER + "\\",
            _FOLDER + "/",
        ]
        for text in cases:
            buf = _wide(text)
            assert directory(ctypes.addressof(buf)) == 1
            assert flag[0] == b"\x01"
            # The paired request is the restore, including a repeat of the same directory.
            assert directory(ctypes.addressof(buf)) == 1
            assert flag[0] == b"\x00"
        for text in (
            _FOLDER + r"\python.exe",
            r"D:\a\project",
            _FOLDER + "extra",
            "",
            _FOLDER[:-2],
        ):
            buf = _wide(text)
            assert directory(ctypes.addressof(buf)) == 2, text
            assert flag[0] == b"\x00"
        assert directory(None) == 2

        project = ctypes.create_string_buffer(b"plugins\\evil.fip\x00")
        other = ctypes.create_string_buffer(b"kernel32.dll\x00")
        absolute = ctypes.create_string_buffer(b"C:\\Python\\evil.fip\x00")
        rooted = ctypes.create_string_buffer(b"\\server\\evil.fip\x00")
        assert plugin(ctypes.addressof(project)) == 2
        buf = _wide(_FOLDER)
        assert directory(ctypes.addressof(buf)) == 1
        assert flag[0] == b"\x01"
        assert plugin(ctypes.addressof(project)) == 0
        assert plugin(ctypes.addressof(ctypes.create_string_buffer(b"evil.FIP\x00"))) == 0
        assert plugin(ctypes.addressof(other)) == 2
        assert plugin(ctypes.addressof(absolute)) == 2
        assert plugin(ctypes.addressof(rooted)) == 2
        assert plugin(None) == 2
        assert directory(ctypes.addressof(_wide(r"D:\a\project"))) == 2
        assert flag[0] == b"\x00"
        assert plugin(ctypes.addressof(project)) == 2
    finally:
        page.close()
