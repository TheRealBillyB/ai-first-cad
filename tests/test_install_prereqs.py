"""install.py names a missing compiler, Python headers, or libGLU before pip runs."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_install():
    spec = importlib.util.spec_from_file_location("aicad_install_prereqs_under_test", ROOT / "install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("platform", "version", "needed"),
    [
        ("linux", (3, 11), True),
        ("linux", (3, 12), True),
        ("win32", (3, 11), False),
        ("win32", (3, 12), True),
        ("darwin", (3, 11), False),
        ("darwin", (3, 13), True),
    ],
)
def test_the_solver_compiles_from_source_on_linux_on_every_python(platform, version, needed):
    module = _load_install()
    assert module._solver_needs_compiler(platform, version) is needed


def _linux_prereqs(module, monkeypatch, *, glu=True, cxx=True, headers=True):
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module, "_glu_found", lambda: glu)
    monkeypatch.setattr(module, "_find_cxx_compiler", lambda: "/usr/bin/g++" if cxx else None)
    monkeypatch.setattr(module, "_python_headers_found", lambda: headers)


def test_a_complete_linux_machine_has_no_prerequisite_notes(monkeypatch):
    module = _load_install()
    _linux_prereqs(module, monkeypatch)
    assert module._missing_prerequisites() == []


def test_missing_libglu_is_reported_with_the_package_names(monkeypatch):
    module = _load_install()
    _linux_prereqs(module, monkeypatch, glu=False)
    (note,) = module._missing_prerequisites()
    assert "libGLU" in note and "Gmsh" in note
    assert "libglu1-mesa" in note and "mesa-libGLU" in note


def test_a_missing_compiler_is_reported_on_linux_python_311(monkeypatch):
    module = _load_install()
    _linux_prereqs(module, monkeypatch, cxx=False)
    monkeypatch.setattr(module, "_solver_needs_compiler", lambda: True)
    (note,) = module._missing_prerequisites()
    assert "C++ compiler" in note
    assert "every Python version" in note
    assert "g++" in note


def test_missing_python_headers_are_reported(monkeypatch):
    module = _load_install()
    _linux_prereqs(module, monkeypatch, headers=False)
    (note,) = module._missing_prerequisites()
    assert "Python.h" in note and "python3-dev" in note


def test_the_check_only_reports_runtime_libraries(monkeypatch):
    """--check runs after pip. A compiler is no longer needed then; libGLU still is."""

    module = _load_install()
    _linux_prereqs(module, monkeypatch, glu=False, cxx=False, headers=False)
    notes = module._missing_prerequisites(build=False)
    assert len(notes) == 1 and "libGLU" in notes[0]


def test_windows_311_needs_no_compiler_note(monkeypatch):
    module = _load_install()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "_solver_needs_compiler", lambda: False)
    assert module._missing_prerequisites() == []


def test_windows_312_names_the_build_tools(monkeypatch):
    module = _load_install()
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "_solver_needs_compiler", lambda: True)
    (note,) = module._missing_prerequisites()
    assert "Microsoft C++ Build Tools" in note


def test_cxx_from_the_environment_is_preferred(monkeypatch):
    module = _load_install()
    monkeypatch.setenv("CXX", "my-cxx -O2")
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/opt/bin/{name}" if name == "my-cxx" else None)
    assert module._find_cxx_compiler() == "/opt/bin/my-cxx"


def test_the_real_prerequisite_probe_runs_on_this_machine():
    module = _load_install()
    notes = module._missing_prerequisites()
    assert all(isinstance(note, str) and note for note in notes)
