"""A long command says how far it has got.

A motion study ran for minutes with no word until it ended. The app's note
said only "Still working on run_motion_study", and a script or an agent
driving the commands heard nothing at all.
"""

from __future__ import annotations

import io
import math
import threading
import time
from pathlib import Path

from aicad import progress
from aicad.app_worker import GeometryWorker
from aicad.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def test_nobody_listening_costs_nothing_and_a_listener_hears_at_most_one_a_second(monkeypatch):
    progress.report("nobody hears this")
    heard: list[dict] = []
    clock = iter([10.0, 10.2, 11.5, 11.6])
    monkeypatch.setattr(progress.time, "monotonic", lambda: next(clock))
    with progress.listening(heard.append):
        for step in range(4):
            progress.report(f"step {step + 1} of 4", step, 4)
    assert heard == [{"text": "step 1 of 4", "done": 0, "total": 4}, {"text": "step 3 of 4", "done": 2, "total": 4}]
    progress.report("after the block")
    assert len(heard) == 2


def test_a_failing_listener_does_not_stop_the_command():
    def broken(_update):
        raise RuntimeError("listener broke")

    with progress.listening(broken):
        progress.report("still fine")


def test_a_workspace_command_reports_to_its_listeners_and_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_EVERY_SECONDS", 0.0)
    workspace = Workspace(tmp_path / "project")
    workspace.execute("create_part_from_ops", {"name": "box", "ops": [{"op": "add", "shape": "box", "size": [10, 10, 10]}]})
    heard: list[dict] = []
    workspace.listen_progress(heard.append)
    stream = io.StringIO()
    workspace.progress_stream = stream
    workspace.execute("check_wall_thickness", {"name": "box", "min_wall_mm": 1.0}, source="agent")
    assert [entry["text"] for entry in heard] == ["rays from each face", "planar sections"]
    assert all(entry["command"] == "check_wall_thickness" for entry in heard)
    assert "aicad: check_wall_thickness: planar sections" in stream.getvalue()
    assert workspace.progress is None


def _drilled_disc() -> list[dict]:
    ops = [
        {"op": "add", "shape": "cylinder", "radius": 62, "height": 6},
        {"op": "cut", "shape": "cylinder", "radius": 30, "height": 20},
    ]
    for ring, (radius, count) in enumerate([(40, 24), (48, 30), (56, 36)]):
        for index in range(count):
            angle = 2 * math.pi * (index + 0.5 * ring) / count
            ops.append({"op": "cut", "shape": "cylinder", "radius": 2.0, "height": 20, "center": [radius * math.cos(angle), radius * math.sin(angle), 0]})
    return ops


def test_the_app_busy_note_carries_the_progress(tmp_path):
    worker = GeometryWorker(tmp_path / "project")
    try:
        worker.execute("create_part_from_ops", {"name": "rotor", "ops": _drilled_disc()}, "ui")
        done = threading.Event()

        def check() -> None:
            try:
                worker.execute("check_wall_thickness", {"name": "rotor", "min_wall_mm": 1.0}, "ui")
            finally:
                done.set()

        threading.Thread(target=check, daemon=True).start()
        seen = set()
        while not done.wait(0.05):
            busy = worker.state().get("busy") or {}
            if busy.get("progress"):
                seen.add(busy["progress"]["text"])
        assert "rays from each face" in seen or "planar sections" in seen
        assert "busy" not in worker.state()
    finally:
        worker.close()


def test_the_page_shows_the_progress_on_the_busy_note():
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "busy.progress.text" in script


def test_a_new_stage_within_a_second_is_not_dropped(monkeypatch):
    """Meshing took under a second, so "solving" was dropped and "meshing" stayed up for the whole solve."""
    heard: list[dict] = []
    clock = iter([10.0, 10.3, 10.5, 10.6, 10.7])
    monkeypatch.setattr(progress.time, "monotonic", lambda: next(clock))
    with progress.listening(heard.append):
        progress.report("meshing at 0.7 mm")
        progress.report("solving 579,474 equations with the iterative solver")
        progress.report("frame 1 of 48", 0, 48)
        progress.report("frame 2 of 48", 1, 48)
        progress.report("reading results")
    assert [entry["text"] for entry in heard] == [
        "meshing at 0.7 mm",
        "solving 579,474 equations with the iterative solver",
        "frame 1 of 48",
        "reading results",
    ]
