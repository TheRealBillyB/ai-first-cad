"""The solver source-build log check. The compile itself runs in CI."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _checker():
    path = ROOT / ".github" / "scripts" / "build_solver_from_source.py"
    spec = importlib.util.spec_from_file_location("build_solver_from_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_c_file_compiled_with_the_installer_standard_is_accepted():
    module = _checker()
    log = "\n".join(
        (
            "Building wheel for python-solvespace (pyproject.toml) ... done",
            "gcc -std=gnu11 -fPIC -c python_solvespace/extlib/mimalloc/src/alloc.c -o alloc.o",
            "g++ -fPIC -c python_solvespace/src/util.cpp -o util.o -std=c++17",
            "Successfully installed python-solvespace-3.0.8",
        )
    )
    assert module.report_problems(log, "-std=gnu11") == []


def test_a_wheel_the_build_just_created_is_still_a_source_build():
    module = _checker()
    log = "\n".join(
        (
            "Building wheel for python-solvespace (pyproject.toml) ... done",
            "gcc -std=gnu11 -c alloc.c -o alloc.o",
            "Created wheel for python-solvespace: filename=python_solvespace-3.0.8-cp312-cp312-linux_x86_64.whl size=1",
        )
    )
    assert module.report_problems(log, "-std=gnu11") == []


def test_a_wheel_is_not_a_source_build():
    module = _checker()
    log = "\n".join(
        (
            "Downloading python_solvespace-3.0.8-cp312-cp312-manylinux.whl",
            "Installing collected packages: python-solvespace",
        )
    )
    problems = module.report_problems(log, "-std=gnu11")
    assert any("wheel" in problem for problem in problems)
    assert any("source build" in problem for problem in problems)


def test_gnu11_after_the_c_plus_plus_standard_is_rejected():
    module = _checker()
    log = "\n".join(
        (
            "Building wheel for python-solvespace (pyproject.toml) ... done",
            "gcc -std=gnu11 -c alloc.c -o alloc.o",
            "g++ -std=c++17 -c util.cpp -o util.o -std=gnu11",
        )
    )
    problems = module.report_problems(log, "-std=gnu11")
    assert any("C++" in problem for problem in problems)


def test_an_unset_cflags_still_requires_a_compiled_c_file():
    module = _checker()
    log = "\n".join(
        (
            "Building wheel for python-solvespace (pyproject.toml) ... done",
            r'cl.exe /c /nologo /Ox /Tcpython_solvespace\extlib\mimalloc\src\alloc.c /Foalloc.obj',
        )
    )
    assert module.report_problems(log, None) == []
    missing = module.report_problems(
        "Building wheel for python-solvespace (pyproject.toml) ... done\n",
        None,
    )
    assert any("C files were not compiled" in problem for problem in missing)


def test_an_explicit_c17_choice_has_to_appear_on_the_c_file():
    module = _checker()
    log = "\n".join(
        (
            "Building wheel for python-solvespace (pyproject.toml) ... done",
            "gcc -std=c17 -c alloc.c -o alloc.o",
        )
    )
    assert module.chosen_standard("-O2 -std=c17") == "-std=c17"
    assert module.report_problems(log, "-O2 -std=c17") == []
