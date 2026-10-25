"""The macOS and Windows CI CalculiX install is pinned and checksum-checked.

These tests do not download the solver. They check the lock and the decisions
the installer makes before it fetches anything.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _installer():
    path = ROOT / ".github" / "scripts" / "install_calculix.py"
    spec = importlib.util.spec_from_file_location("install_calculix", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["install_calculix"] = module
    spec.loader.exec_module(module)
    return module


def _lock() -> dict:
    return json.loads((ROOT / ".github" / "calculix.lock.json").read_text(encoding="utf-8"))


def test_lock_pins_calculix_and_openblas():
    module = _installer()
    lock = _lock()
    module.validate_lock(lock)
    osx = lock["platforms"]["osx-arm64"]["packages"]
    windows = lock["platforms"]["win-64"]["packages"]
    assert [pkg["build"] for pkg in osx if pkg["name"] == "calculix"] == ["pl5321hd17ca8b_8"]
    assert [pkg["build"] for pkg in windows if pkg["name"] == "calculix"] == ["pl5321h7abefab_8"]
    assert lock["platforms"]["osx-arm64"]["path"] == ["bin"]
    assert lock["platforms"]["win-64"]["path"] == ["Library/bin", "Library/mingw-w64/bin"]
    assert len(osx) == 11
    assert len(windows) == 16


def test_a_lock_that_pulls_mkl_is_refused():
    module = _installer()
    lock = _lock()
    lock["platforms"]["win-64"]["packages"][0]["filename"] = "mkl-2024.1-h123.conda"
    with pytest.raises(ValueError, match="MKL"):
        module.validate_lock(lock)


def test_linux_does_not_download_calculix(capsys):
    module = _installer()
    assert module.install({}, system="linux", machine="x86_64", work=Path("/tmp"), prefix=Path("/tmp")) == 0
    assert "install.py" in capsys.readouterr().out


def test_an_unknown_runner_stops_instead_of_skipping():
    module = _installer()
    with pytest.raises(module.UnsupportedRunner, match="osx-arm64 and win-64"):
        module.select_platform("darwin", "x86_64")
    with pytest.raises(module.UnsupportedRunner):
        module.select_platform("win32", "ARM64")
    assert module.select_platform("darwin", "arm64") == "osx-arm64"
    assert module.select_platform("win32", "AMD64") == "win-64"
    assert module.select_platform("linux", "aarch64") is None


def test_the_ccx_directory_is_searched_first():
    module = _installer()
    directories = [Path("/solver/Library/bin"), Path("/solver/Library/mingw-w64/bin")]
    lines = module.github_path_lines(directories)
    path: list[str] = []
    for line in lines:
        path.insert(0, line)
    assert path[0] == str(directories[0])
    assert path[1] == str(directories[1])


def test_the_link_uses_one_package_cache_that_the_job_creates():
    module = _installer()
    cache = Path("/work/root/pkgs")
    env = module.link_environment(
        {"CONDA_PKGS_DIRS": "/missing/runner/pkgs", "PATH": "/usr/bin"},
        cache,
    )
    assert env["CONDA_PKGS_DIRS"] == str(cache)
    assert env["PATH"] == "/usr/bin"


def test_windows_system_dlls_are_not_copied_onto_path():
    module = _installer()
    assert module.is_windows_system_dll("KERNEL32.dll")
    assert module.is_windows_system_dll("api-ms-win-crt-runtime-l1-1-0.dll")
    assert module.is_windows_system_dll("ucrtbase.dll")
    assert module.is_windows_system_dll("vcruntime140.dll")
    assert not module.is_windows_system_dll("libarpack.dll")
    assert not module.is_windows_system_dll("libgfortran-5.dll")


def _pe_with_imports(names: list[str]) -> bytes:
    """A PE32+ image whose import table names ``names`` and nothing else."""

    def align(value: int, boundary: int) -> int:
        return (value + boundary - 1) & ~(boundary - 1)

    blobs = [name.encode("ascii") + b"\0" for name in names]
    descriptor = len(names) * 20 + 20
    rva_base = 0x1000
    payload = bytearray(descriptor + sum(len(blob) for blob in blobs))
    cursor = descriptor
    for blob in blobs:
        payload[cursor : cursor + len(blob)] = blob
        cursor += len(blob)
    cursor = descriptor
    for index, blob in enumerate(blobs):
        name_rva = rva_base + cursor
        struct.pack_into("<IIIII", payload, index * 20, 0, 0, 0, name_rva, 0)
        cursor += len(blob)
    raw = align(len(payload), 0x200)
    payload.extend(b"\0" * (raw - len(payload)))

    dos = bytearray(64)
    dos[0:2] = b"MZ"
    e_lfanew = 64
    struct.pack_into("<I", dos, 0x3C, e_lfanew)
    pe = bytearray(b"PE\0\0")
    # COFF. Optional header is the PE32+ size with 16 data directories: 112 + 128 = 240.
    size_opt = 240
    pe += struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, size_opt, 0)
    optional = bytearray(size_opt)
    struct.pack_into("<H", optional, 0, 0x20B)
    # Import directory is data directory index 1, at offset 112.
    struct.pack_into("<II", optional, 112 + 8, rva_base, len(payload))
    section = bytearray(40)
    section[0:6] = b".rdata"
    struct.pack_into("<IIII", section, 8, len(payload), rva_base, len(payload), 0x200)
    header = bytes(dos) + bytes(pe) + bytes(optional) + bytes(section)
    header += b"\0" * (0x200 - len(header))
    return header + bytes(payload)


def _pe_forwarding_to(dll: str) -> bytes:
    """A PE32+ whose only export is forwarded to ``dll``."""

    forward = f"{dll}.symbol".encode("ascii") + b"\0"
    export_dir = 40
    functions = 4
    payload = bytearray(export_dir + functions + len(forward))
    string_rva = 0x1000 + export_dir + functions
    struct.pack_into("<I", payload, export_dir, string_rva)
    # NumberOfFunctions at offset 20, AddressOfFunctions at offset 28.
    struct.pack_into("<I", payload, 20, 1)
    struct.pack_into("<I", payload, 24, 1)  # Base
    struct.pack_into("<I", payload, 28, 0x1000 + export_dir)
    payload[export_dir + functions : export_dir + functions + len(forward)] = forward
    raw = (len(payload) + 0x1FF) & ~0x1FF
    payload.extend(b"\0" * (raw - len(payload)))
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 64)
    size_opt = 240
    pe = bytearray(b"PE\0\0")
    pe += struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, size_opt, 0)
    optional = bytearray(size_opt)
    struct.pack_into("<H", optional, 0, 0x20B)
    struct.pack_into("<II", optional, 112, 0x1000, len(payload))
    section = bytearray(40)
    section[0:6] = b".edata"
    struct.pack_into("<IIII", section, 8, len(payload), 0x1000, len(payload), 0x200)
    header = bytes(dos) + bytes(pe) + bytes(optional) + bytes(section)
    header += b"\0" * (0x200 - len(header))
    return header + bytes(payload)


def _pe_with_delay_imports(names: list[str]) -> bytes:
    """A PE32+ image whose delay-load directory names ``names``."""

    blobs = [name.encode("ascii") + b"\0" for name in names]
    descriptors = (len(names) + 1) * 32
    payload = bytearray(descriptors + sum(len(blob) for blob in blobs))
    cursor = descriptors
    rva_base = 0x1000
    for index, blob in enumerate(blobs):
        name_rva = rva_base + cursor
        struct.pack_into("<IIIIIIII", payload, index * 32, 1, name_rva, 0, 0, 0, 0, 0, 0)
        payload[cursor : cursor + len(blob)] = blob
        cursor += len(blob)
    raw = (len(payload) + 0x1FF) & ~0x1FF
    payload.extend(b"\0" * (raw - len(payload)))
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 64)
    size_opt = 240
    pe = bytearray(b"PE\0\0")
    pe += struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, size_opt, 0)
    optional = bytearray(size_opt)
    struct.pack_into("<H", optional, 0, 0x20B)
    # Delay-load directory is data directory index 13, at offset 112 + 104.
    struct.pack_into("<II", optional, 216, rva_base, descriptors)
    section = bytearray(40)
    section[0:6] = b".rdata"
    struct.pack_into("<IIII", section, 8, len(payload), rva_base, len(payload), 0x200)
    header = bytes(dos) + bytes(pe) + bytes(optional) + bytes(section)
    header += b"\0" * (0x200 - len(header))
    return header + bytes(payload)


def test_a_delay_loaded_dll_and_its_imports_are_staged(tmp_path):
    module = _installer()
    library = tmp_path / "Library" / "bin"
    library.mkdir(parents=True)
    exe = library / "ccx.exe"
    exe.write_bytes(_pe_with_delay_imports(["KERNEL32.dll", "libgomp-1.dll"]))
    (library / "libgomp-1.dll").write_bytes(_pe_with_imports(["libgcc_s_seh-1.dll"]))
    (library / "libgcc_s_seh-1.dll").write_bytes(_pe_with_imports(["KERNEL32.dll"]))
    assert module.pe_delay_loaded_dlls(exe) == ["KERNEL32.dll", "libgomp-1.dll"]
    assert module.pe_imported_dlls(exe) == []
    destination = tmp_path / "solver-bin"
    module.stage_solver_bin(exe, [library], destination)
    names = sorted(path.name.lower() for path in destination.iterdir())
    assert names == ["ccx.exe", "libgcc_s_seh-1.dll", "libgomp-1.dll"]


def test_a_forwarded_export_names_the_real_blas_dll(tmp_path):
    module = _installer()
    library = tmp_path / "Library" / "bin"
    library.mkdir(parents=True)
    exe = library / "ccx.exe"
    exe.write_bytes(_pe_with_imports(["libblas.dll"]))
    (library / "libblas.dll").write_bytes(_pe_forwarding_to("openblas"))
    (library / "openblas.dll").write_bytes(_pe_with_imports(["KERNEL32.dll"]))
    assert module.pe_forwarded_dlls(library / "libblas.dll") == ["openblas.dll"]
    destination = tmp_path / "solver-bin"
    module.stage_solver_bin(exe, [library], destination)
    assert (destination / "openblas.dll").is_file()
    assert not (destination / "KERNEL32.dll").exists()


def test_a_solver_bin_keeps_ccx_libraries_and_leaves_system_dlls(tmp_path):
    module = _installer()
    library = tmp_path / "Library" / "bin"
    mingw = tmp_path / "Library" / "mingw-w64" / "bin"
    library.mkdir(parents=True)
    mingw.mkdir(parents=True)
    exe = library / "ccx.exe"
    exe.write_bytes(_pe_with_imports(["KERNEL32.dll", "libarpack.dll", "libblas.dll"]))
    (library / "libblas.dll").write_bytes(_pe_with_imports(["KERNEL32.dll", "openblas.dll"]))
    (library / "openblas.dll").write_bytes(_pe_with_imports(["VCRUNTIME140.dll"]))
    (library / "ucrtbase.dll").write_bytes(b"ucrt")
    (mingw / "libarpack.dll").write_bytes(_pe_with_imports(["libblas.dll", "libgfortran-5.dll"]))
    (library / "libgfortran-5.dll").write_bytes(_pe_with_imports(["libgcc_s_seh-1.dll"]))
    (library / "libgcc_s_seh-1.dll").write_bytes(_pe_with_imports(["KERNEL32.dll"]))
    destination = tmp_path / "solver-bin"
    result = module.stage_solver_bin(exe, [library, mingw], destination)
    assert result == destination
    names = sorted(path.name.lower() for path in destination.iterdir())
    assert names == [
        "ccx.exe",
        "libarpack.dll",
        "libblas.dll",
        "libgcc_s_seh-1.dll",
        "libgfortran-5.dll",
        "openblas.dll",
    ]
    posix = tmp_path / "bin" / "ccx"
    posix.parent.mkdir()
    posix.write_bytes(b"ccx")
    assert module.stage_solver_bin(posix, [library], tmp_path / "unused") == posix.parent


def test_a_lock_path_that_leaves_the_work_directory_is_refused(tmp_path):
    module = _installer()
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    work = tmp_path / "ciwork"
    work.mkdir()

    bad_name = _lock()
    package = bad_name["platforms"]["osx-arm64"]["packages"][0]
    package["filename"] = "../../outside/SANDBOX_CANARY.txt"
    package["url"] = "https://example.invalid/../../outside/SANDBOX_CANARY.txt"
    with pytest.raises(ValueError):
        module.validate_lock(bad_name)

    bad_exec = _lock()
    bad_exec["micromamba"]["osx-arm64"]["executable"] = "../../outside/SANDBOX_CANARY.txt"
    with pytest.raises(ValueError):
        module.validate_lock(bad_exec)

    bad_path = _lock()
    bad_path["platforms"]["win-64"]["path"] = ["../../outside"]
    with pytest.raises(ValueError):
        module.validate_lock(bad_path)

    absolute = _lock()
    absolute["platforms"]["osx-arm64"]["path"] = ["/tmp/evil"]
    with pytest.raises(ValueError):
        module.validate_lock(absolute)

    for root, relative in (
        (work / "pkgs", "../../outside/SANDBOX_CANARY.txt"),
        (work / "micromamba-bin", "../../outside/SANDBOX_CANARY.txt"),
        (work / "prefix", "../../outside"),
        (work / "pkgs", "/tmp/evil"),
    ):
        with pytest.raises(ValueError):
            module.work_path(root, relative)
    assert not canary.exists()
    naive = (work / "pkgs" / "../../outside/SANDBOX_CANARY.txt").resolve()
    assert naive == canary.resolve()
    kept = module.work_path(work / "prefix", "Library/bin")
    assert kept == (work / "prefix").resolve() / "Library" / "bin"
    assert (work / "prefix").resolve() in kept.parents


def test_a_missing_library_is_not_treated_as_a_started_solver():
    module = _installer()
    assert module.solver_started(0, "Usage: CalculiX.exe -i jobname\n", "")
    assert module.solver_started(201, "", "This is Version 2.23\nCalculiX\n")
    assert not module.solver_started(3221225781, "", "The code execution cannot proceed because libarpack.dll was not found.")
    assert not module.solver_started(-9, "CalculiX", "")
