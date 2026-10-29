"""Geometry measurements, scripts, operations, and checks."""

import math
import sys
from pathlib import Path

import pytest

from aicad import Document
from aicad.errors import GeometryError, ScriptError

BOX = "part = Box(10, 20, 30)\n"


def test_box_measurements(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    measured = document.measure("box")

    assert measured.units == "mm"
    assert measured.volume_mm3 == pytest.approx(6000)
    assert measured.area_mm2 == pytest.approx(2200)
    assert measured.valid is True
    assert measured.watertight is True
    assert measured.solid_count == 1
    assert (measured.face_count, measured.edge_count, measured.vertex_count) == (6, 12, 8)
    assert measured.bounding_box.size.x == pytest.approx(10)
    assert measured.bounding_box.size.y == pytest.approx(20)
    assert measured.bounding_box.size.z == pytest.approx(30)
    assert measured.bounding_box.min.z == pytest.approx(-15)
    assert measured.center_of_mass_mm.x == pytest.approx(0, abs=1e-6)
    assert measured.center_of_mass_mm.y == pytest.approx(0, abs=1e-6)
    assert measured.center_of_mass_mm.z == pytest.approx(0, abs=1e-6)


def test_hole_reduces_volume_and_stays_closed(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script(
        "plate",
        "part = Box(10, 10, 4) - Cylinder(1.5, 8)\n",
    )
    measured = document.measure("plate")
    removed = math.pi * 1.5**2 * 4
    assert measured.volume_mm3 == pytest.approx(400 - removed, rel=1e-6)
    assert measured.watertight is True
    assert measured.valid is True
    assert measured.face_count > 6


def test_script_must_assign_a_solid(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(ScriptError, match="assign"):
        document.create_part_from_script("empty", "x = 1\n")
    with pytest.raises(ScriptError, match="no solid volume"):
        document.create_part_from_script("sketch", "part = Circle(5)\n")


def test_script_error_names_the_line(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(ScriptError, match=r"line 2"):
        document.create_part_from_script("bad", "part = Box(1, 1, 1)\npart = missing\n")


def test_failed_update_keeps_the_previous_solid(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("box", BOX)
    with pytest.raises(ScriptError):
        document.update_part_script("box", "part = no_such_name\n")
    assert document.measure("box").volume_mm3 == pytest.approx(6000)


def test_ops_box_with_hole_and_horizontal_cylinder(tmp_path):
    document = Document(tmp_path)
    info = document.create_part_from_ops(
        "plate",
        [
            {"op": "add", "shape": "box", "size": [10, 20, 4], "center": [0, 0, 2]},
            {"op": "cut", "shape": "cylinder", "radius": 1.5, "height": 10, "center": [0, 0, 2]},
        ],
    )
    removed = math.pi * 1.5**2 * 4
    assert info.volume_mm3 == pytest.approx(800 - removed, rel=1e-6)

    document.create_part_from_ops(
        "pin",
        [{"op": "add", "shape": "cylinder", "radius": 1, "height": 10, "axis": [1, 0, 0]}],
    )
    size = document.measure("pin").bounding_box.size
    assert size.x == pytest.approx(10)
    assert size.y == pytest.approx(2)
    assert size.z == pytest.approx(2)


def test_ops_reject_a_cut_of_nothing(tmp_path):
    document = Document(tmp_path)
    with pytest.raises(GeometryError, match="no solid yet"):
        document.create_part_from_ops(
            "bad",
            [{"op": "cut", "shape": "box", "size": [1, 1, 1]}],
        )


def test_plate_and_tube_wall_thickness(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("plate", "part = Box(10, 10, 2)\n")
    plate = document.check_wall_thickness("plate", min_wall_mm=1.2)
    assert plate.estimated_min_mm == pytest.approx(2.0, abs=0.05)
    assert plate.passed is True
    assert plate.sample_count > 0
    assert plate.thinnest_point_mm is not None

    too_thick = document.check_wall_thickness("plate", min_wall_mm=5)
    assert too_thick.passed is False

    document.create_part_from_script(
        "tube",
        "part = Cylinder(10, 20) - Cylinder(8, 24)\n",
    )
    tube = document.check_wall_thickness("tube", min_wall_mm=1.5)
    assert tube.estimated_min_mm == pytest.approx(2.0, abs=0.05)
    assert tube.passed is True


def test_a_wall_the_same_size_as_the_first_probe_is_measured(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("shell", "part = Box(10, 10, 10) - Box(9.98, 9.98, 9.98)\n")
    wall = document.check_wall_thickness("shell", min_wall_mm=1.0, samples=4)
    assert wall.estimated_min_mm == pytest.approx(0.01, abs=0.002)
    assert wall.passed is False
    assert wall.method == "ray+section"


def test_round_and_rotated_solids_report_normal_thickness(tmp_path):
    """An off-center slice is a chord. It is not a thinner wall."""
    document = Document(tmp_path)
    document.create_part_from_script("rod", "part = Cylinder(10, 40)\n")
    rod = document.check_wall_thickness("rod", min_wall_mm=18, samples=4)
    assert rod.ray_estimate_mm == pytest.approx(20, abs=0.2)
    assert rod.estimated_min_mm == pytest.approx(20, abs=0.2)
    assert rod.section_estimate_mm < 19
    assert rod.passed is True

    document.create_part_from_script("turned", "part = Rot(0, 0, 45) * Box(10, 10, 30)\n")
    turned = document.check_wall_thickness("turned", min_wall_mm=8, samples=4)
    assert turned.ray_estimate_mm == pytest.approx(10, abs=0.2)
    assert turned.estimated_min_mm == pytest.approx(10, abs=0.2)
    assert turned.passed is True

    document.create_part_from_script("ball", "part = Sphere(10)\n")
    ball = document.check_wall_thickness("ball", min_wall_mm=18, samples=4)
    assert ball.estimated_min_mm == pytest.approx(20, abs=0.3)
    assert ball.passed is True


def test_a_pointed_cone_does_not_pass_a_one_millimetre_wall(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("cone", "part = Cone(10, 0, 20)\n")
    wall = document.check_wall_thickness("cone", min_wall_mm=1.0, samples=4)
    assert wall.estimated_min_mm is not None
    assert wall.estimated_min_mm < 1.0
    assert wall.passed is False


def test_interference_and_clearance(tmp_path):
    document = Document(tmp_path)
    document.create_part_from_script("left", "part = Box(10, 10, 10)\n")
    document.create_part_from_script("overlap", "part = Pos(5, 0, 0) * Box(10, 10, 10)\n")
    document.create_part_from_script("apart", "part = Pos(30, 0, 0) * Box(10, 10, 10)\n")

    hit = document.check_clearance("left", "overlap", min_clearance_mm=0.2)
    assert hit.intersecting is True
    assert hit.interference_volume_mm3 == pytest.approx(500, rel=1e-4)
    assert hit.min_distance_mm == 0
    assert hit.passed is False

    gap = document.check_clearance("left", "apart", min_clearance_mm=0)
    assert gap.intersecting is False
    assert gap.min_distance_mm == pytest.approx(20)
    assert gap.passed is True
    assert gap.closest_point_a_mm is not None

    # 10 mm plate with a 5 mm radius hole, and a 4.8 mm radius pin centered in it.
    document.create_part_from_script(
        "block",
        "part = Box(20, 20, 10) - Cylinder(5, 14)\n",
    )
    document.create_part_from_script("pin", "part = Cylinder(4.8, 8)\n")
    fit = document.check_clearance("block", "pin", min_clearance_mm=0.15)
    assert fit.intersecting is False
    assert fit.min_distance_mm == pytest.approx(0.2, abs=0.02)
    assert fit.passed is True
    tight = document.check_clearance("block", "pin", min_clearance_mm=0.5)
    assert tight.passed is False


def test_macos_keeps_opencascade_on_the_calling_thread():
    import aicad.kernel  # noqa: F401
    from OCP.OSD import OSD_Parallel, OSD_ThreadPool

    pool = OSD_ThreadPool.DefaultPool_s()
    if sys.platform == "darwin":
        assert pool.NbThreads() == 1
        assert not pool.HasThreads()
        assert pool.NbDefaultThreadsToLaunch() == 1
        assert OSD_Parallel.ToUseOcctThreads_s()
        return
    assert pool.NbThreads() == OSD_Parallel.NbLogicalProcessors_s()


def test_macos_python_threads_get_a_geometry_stack():
    import threading

    from aicad.thread_stack import (
        GEOMETRY_THREAD_STACK,
        current_thread_can_run_geometry,
        current_thread_stack_bytes,
        prepare_geometry_thread_stack,
    )

    assert current_thread_can_run_geometry() is True
    if sys.platform != "darwin":
        assert current_thread_stack_bytes() is None
        assert prepare_geometry_thread_stack() == 0
        if sys.platform != "win32":
            # prepare must not clear a size already chosen by the process.
            # Reading the size stores the default, so put the explicit size back.
            threading.stack_size(2 * 1024 * 1024)
            try:
                assert prepare_geometry_thread_stack() == 0
                current = threading.stack_size(2 * 1024 * 1024)
                assert current == 2 * 1024 * 1024
            finally:
                threading.stack_size(0)
        return
    # Clear an explicit size, then let the product choose again.
    # A call with no size stores the default, so the check below reads the
    # thread itself.
    threading.stack_size(0)
    assert prepare_geometry_thread_stack() >= GEOMETRY_THREAD_STACK
    main_stack = current_thread_stack_bytes()
    assert main_stack is not None
    assert main_stack + 64 * 1024 >= GEOMETRY_THREAD_STACK
    seen: dict = {}

    def read() -> None:
        seen["main"] = threading.current_thread() is threading.main_thread()
        seen["bytes"] = current_thread_stack_bytes()

    worker = threading.Thread(target=read, name="aicad-geometry-stack")
    worker.start()
    worker.join()
    assert seen["main"] is False
    assert seen["bytes"] is not None
    assert seen["bytes"] >= GEOMETRY_THREAD_STACK


def test_a_small_stack_runs_clearance_on_a_larger_thread(monkeypatch):
    """A deep boolean does not stay on a thread that is too small for it.

    On macOS the pytest worker's main thread is the process stack. A rod
    handler mesh and the booleans that seat its parts overflow that stack,
    and the worker exits with no traceback. The same call from a short
    stack runs on the geometry thread instead.
    """
    import threading

    from aicad import thread_stack
    from aicad.kernel import clearance_between, make_from_ops

    def needs_a_larger_stack() -> bool:
        return threading.current_thread() is threading.main_thread()

    monkeypatch.setattr(thread_stack, "geometry_call_needs_a_larger_stack", needs_a_larger_stack)
    started: list[str] = []
    real = threading.Thread

    def spy(*args, **kwargs):
        started.append(kwargs.get("name", ""))
        return real(*args, **kwargs)

    monkeypatch.setattr(thread_stack.threading, "Thread", spy)
    left = make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10]}])
    right = make_from_ops([{"op": "add", "shape": "box", "size": [4, 4, 4]}])
    overlap, distance, _first, _second = clearance_between(left, right)
    assert started == ["aicad-geometry"]
    assert overlap == pytest.approx(64, abs=0.1)
    assert distance == pytest.approx(0, abs=0.01)


def test_child_processes_inherit_a_geometry_stack():
    from aicad.thread_stack import _limit_covers_geometry, child_inherits_geometry_stack

    try:
        import resource
    except ImportError:
        with child_inherits_geometry_stack():
            return
        return
    before = resource.getrlimit(resource.RLIMIT_STACK)
    soft0, hard0 = before
    # A limit already within 64 KiB of 8 MiB is the macOS maximum and stays.
    already = _limit_covers_geometry(soft0) or not _limit_covers_geometry(hard0)
    with child_inherits_geometry_stack():
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        if sys.platform == "darwin" and not already:
            assert _limit_covers_geometry(soft)
            assert soft >= soft0
            assert hard == hard0
        else:
            assert (soft, hard) == before
    assert resource.getrlimit(resource.RLIMIT_STACK) == before
    # macOS rejects setrlimit on this resource, including a smaller limit.
    # The raise itself is checked where the kernel accepts a new limit.
    if sys.platform in ("darwin", "win32") or soft0 <= 1024 * 1024:
        return
    from aicad.thread_stack import GEOMETRY_THREAD_STACK, _raise_stack_limit

    resource.setrlimit(resource.RLIMIT_STACK, (1024 * 1024, hard0))
    try:
        assert _raise_stack_limit(1024 * 1024, hard0, GEOMETRY_THREAD_STACK)
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        assert _limit_covers_geometry(soft)
        assert hard == hard0
    finally:
        resource.setrlimit(resource.RLIMIT_STACK, before)


def test_the_sandbox_child_sets_its_stack_before_geometry(tmp_path):
    import subprocess

    script = tmp_path / "load_stack.py"
    script.write_text(
        "import importlib.util\n"
        "import sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('sandbox_run_early', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "helper = module._early_thread_stack()\n"
        "assert helper is not None\n"
        "helper.prepare_geometry_thread_stack()\n"
        "assert helper.current_thread_can_run_geometry() is True\n"
        "assert 'aicad.kernel' not in sys.modules\n"
        "assert 'build123d' not in sys.modules\n"
        "assert 'OCP' not in sys.modules\n",
        encoding="utf-8",
    )
    runner = Path(__file__).resolve().parents[1] / "src" / "aicad" / "sandbox_run.py"
    completed = subprocess.run(
        [sys.executable, str(script), str(runner)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
