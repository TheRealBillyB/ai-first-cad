"""GIF animations stay inside the project and match the requested frames."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.workspace import Workspace


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def _stand(document: Document) -> None:
    _box(document, "base", [36, 10, 8], [0, 0, 4])
    document.create_assembly({"name": "stand", "instances": [{"name": "base", "part": "base"}], "joints": []})


def _gif(path: Path) -> Image.Image:
    image = Image.open(path)
    assert image.format == "GIF"
    return image


def test_a_short_orbit_gif_has_the_requested_frames_and_size(tmp_path):
    project = tmp_path / "proj"
    workspace = Workspace(project)
    _stand(workspace.document)
    before = workspace.document.get_assembly("stand")["assembly"]["instances"]
    options = {
        "name": "stand",
        "kind": "orbit",
        "frames": 4,
        "fps": 8,
        "width": 96,
        "height": 72,
        "video": "gif",
        "background": "#ffffff",
        "colors": {"base": "#c04020"},
        "materials": {"base": "aluminum"},
    }
    first = workspace.execute("render_animation", {**options, "path": "renders/orbit.gif"}, source="agent")
    second = workspace.execute("render_animation", {**options, "path": "renders/orbit-again.gif"}, source="agent")
    gif_path = Path(first["gif"])
    assert gif_path.is_file()
    assert gif_path.resolve().is_relative_to(project.resolve())
    assert Path(first["poster"]).resolve().is_relative_to(project.resolve())
    assert first["frames"] == 4
    assert first["width"] == 96
    assert first["height"] == 72
    assert first["frame_ms"] == 130
    assert first["video"] is None
    assert first["videos"] == {}
    assert first["colors"] == ["base"]
    assert first["materials"] == {"base": "aluminum_6061"}
    after = workspace.document.get_assembly("stand")["assembly"]["instances"]
    assert after == before
    with _gif(gif_path) as gif:
        assert gif.n_frames == 4
        assert gif.size == (96, 72)
        assert gif.info.get("duration") == first["frame_ms"]
        gif.seek(0)
        opening = gif.convert("RGB").tobytes()
        gif.seek(1)
        turned = gif.convert("RGB").tobytes()
    assert opening != turned
    assert gif_path.read_bytes() == Path(second["gif"]).read_bytes()


def test_a_motion_study_draws_the_joint_value_and_moves(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [16, 16, 6], [0, 0, 3])
    _box(document, "link", [20, 8, 6], [24, 0, 8])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "link"},
            ],
            "joints": [
                {"name": "hinge", "type": "revolute", "parent": "base", "child": "link", "axis": [0, 0, 1]},
            ],
            "studies": {
                "swing": {
                    "duration_s": 1,
                    "steps": 2,
                    "drivers": [{"joint": "hinge", "expression": "90 * t"}],
                }
            },
        }
    )
    report = document.render_animation(
        "hinge",
        kind="motion",
        sequence="swing",
        frames=2,
        fps=4,
        width=64,
        height=48,
        video="gif",
        overlay="value",
        joint="hinge",
    )
    assert report["kind"] == "motion"
    assert report["overlays"] == ["hinge 0.000 deg", "hinge 90.000 deg"]
    gif_path = Path(report["gif"])
    assert gif_path.resolve().is_relative_to(tmp_path.resolve())
    with _gif(gif_path) as gif:
        assert gif.n_frames == 2
        assert gif.size == (64, 48)
        gif.seek(0)
        start = gif.convert("RGB").tobytes()
        gif.seek(1)
        end = gif.convert("RGB").tobytes()
    assert start != end


def test_a_stored_motion_referenced_by_name_is_drawn(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [16, 16, 6], [0, 0, 3])
    _box(document, "link", [20, 8, 6], [24, 0, 8])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "link"},
            ],
            "joints": [
                {
                    "name": "hinge",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "axis": [0, 0, 1],
                    "min": 0,
                    "max": 90,
                },
            ],
            "motions": {
                "swing": {"sweep": {"joint": "hinge", "start": 0, "end": 90, "frames": 4}, "duration_s": 1}
            },
        }
    )
    report = document.render_animation(
        "hinge",
        kind="motion",
        sequence="swing",
        frames=2,
        fps=4,
        width=64,
        height=48,
        video="gif",
        overlay="value",
    )
    assert report["kind"] == "motion"
    assert report["frames"] == 2
    assert report["overlays"] == ["hinge 0.000 deg", "hinge 90.000 deg"]
    with _gif(Path(report["gif"])) as gif:
        assert gif.n_frames == 2
        gif.seek(0)
        start = gif.convert("RGB").tobytes()
        gif.seek(1)
        end = gif.convert("RGB").tobytes()
    assert start != end


def test_an_exploded_opening_moves_between_the_ends(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [18, 12, 6], [0, 0, 3])
    _box(document, "cap", [10, 10, 4], [0, 0, 10])
    document.create_assembly(
        {
            "name": "stack",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "cap", "part": "cap"},
            ],
            "joints": [
                {"name": "seat", "type": "fixed", "parent": "base", "child": "cap"},
            ],
        }
    )
    report = document.render_animation(
        "stack",
        kind="exploded",
        frames=2,
        fps=4,
        width=64,
        height=48,
        gap_mm=40,
        video="gif",
        path=tmp_path / "renders" / "open.gif",
    )
    assert report["overlays"] == ["1/2", "2/2"]
    assert report["gap_mm"] == pytest.approx(40)
    with _gif(Path(report["gif"])) as gif:
        assert gif.n_frames == 2
        assert gif.size == (64, 48)
        gif.seek(0)
        closed = gif.convert("RGB").tobytes()
        gif.seek(1)
        opened = gif.convert("RGB").tobytes()
    assert closed != opened


def _color_center(image: Image.Image, channel: int) -> tuple[float, float]:
    arr = np.asarray(image.convert("RGB")).astype(int)
    others = [index for index in range(3) if index != channel]
    mask = (arr[:, :, channel] > arr[:, :, others[0]] + 40) & (arr[:, :, channel] > arr[:, :, others[1]] + 40)
    ys, xs = np.nonzero(mask)
    assert len(xs) > 10
    return float(xs.mean()), float(ys.mean())


def test_a_fixed_part_stays_put_while_another_slides(tmp_path):
    document = Document(tmp_path)
    _box(document, "anchor", [8, 8, 8], [0, 0, 4])
    _box(document, "block", [16, 12, 10], [30, 0, 5])
    document.create_assembly(
        {
            "name": "slide",
            "instances": [
                {"name": "anchor", "part": "anchor"},
                {"name": "block", "part": "block"},
            ],
            "joints": [
                {
                    "name": "travel",
                    "type": "prismatic",
                    "parent": "anchor",
                    "child": "block",
                    "axis": [1, 0, 0],
                    "min": 0,
                    "max": 80,
                }
            ],
            "motions": {
                "go": {"sweep": {"joint": "travel", "start": 0, "end": 80, "frames": 2}, "duration_s": 1}
            },
        }
    )
    report = document.render_animation(
        "slide",
        kind="motion",
        sequence="go",
        frames=2,
        fps=4,
        width=200,
        height=140,
        overlay="none",
        video="gif",
        background="white",
        colors={"anchor": "#ff0000", "block": "#0000ff"},
    )
    gif = Image.open(report["gif"])
    centers = []
    for index in range(gif.n_frames):
        gif.seek(index)
        frame = gif.convert("RGB")
        centers.append((_color_center(frame, 0), _color_center(frame, 2)))
    anchor_move = math.hypot(centers[1][0][0] - centers[0][0][0], centers[1][0][1] - centers[0][0][1])
    block_move = math.hypot(centers[1][1][0] - centers[0][1][0], centers[1][1][1] - centers[0][1][1])
    assert anchor_move < 8
    assert block_move > 40


def test_a_time_overlay_follows_the_sampled_clock(tmp_path):
    document = Document(tmp_path)
    _stand(document)
    orbit = document.render_animation(
        "stand",
        kind="orbit",
        frames=4,
        fps=8,
        duration_s=2,
        width=64,
        height=48,
        overlay="time",
        video="gif",
    )
    # The last frame is not a repeat of the first, so it stops one step short of the duration.
    assert orbit["motion_s"] == pytest.approx(2)
    assert orbit["overlays"] == ["0.000 s", "0.500 s", "1.000 s", "1.500 s"]
    _box(document, "cap", [10, 10, 4], [0, 0, 12])
    document.create_assembly(
        {
            "name": "stack",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "cap", "part": "cap"},
            ],
            "joints": [{"name": "seat", "type": "fixed", "parent": "base", "child": "cap"}],
        }
    )
    exploded = document.render_animation(
        "stack",
        kind="exploded",
        frames=3,
        fps=4,
        duration_s=2,
        gap_mm=20,
        width=64,
        height=48,
        overlay="time",
        video="gif",
    )
    assert exploded["overlays"] == ["0.000 s", "1.000 s", "2.000 s"]


def test_a_missing_ffmpeg_still_writes_the_gif(tmp_path, monkeypatch):
    monkeypatch.setattr("aicad.animation.find_program", lambda *_args, **_kwargs: None)
    document = Document(tmp_path)
    _stand(document)
    report = document.render_animation(
        "stand",
        kind="orbit",
        frames=4,
        fps=4,
        width=96,
        height=72,
        video="mp4",
        path="renders/fallback.gif",
    )
    assert Path(report["gif"]).is_file()
    assert report["video"] is None
    assert report["videos"] == {}
    assert report["video_message"]
    assert "ffmpeg" in report["video_message"].lower()
    assert "gif" in report["video_message"].lower()
    with _gif(Path(report["gif"])) as gif:
        assert gif.n_frames == 4
        assert gif.size == (96, 72)


def test_an_animation_path_outside_the_project_is_refused(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    document = Document(project)
    outside = tmp_path / "outside" / "clip.gif"
    with pytest.raises(GeometryError, match="outside the project"):
        document.render_animation("stand", path=outside, frames=2, width=32, height=32)
    with pytest.raises(GeometryError, match="outside the project"):
        document.render_animation("stand", path="../outside/clip.gif", frames=2, width=32, height=32)
    assert not outside.exists()
    assert not (tmp_path / "outside").exists()
    assert list(project.rglob("clip.gif")) == []


def test_a_linked_animation_directory_cannot_leave_the_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a symlink")
    workspace = Workspace(project)
    with pytest.raises(GeometryError, match="outside the project"):
        workspace.execute("render_animation", {"name": "stand", "path": "linked"}, source="agent")
    assert list(outside.iterdir()) == []


def test_the_command_line_writes_a_gif(tmp_path):
    root = tmp_path / "ws"
    document = Document(root)
    _stand(document)
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "aicad",
            "animate",
            "stand",
            "--kind",
            "orbit",
            "--frames",
            "4",
            "--fps",
            "4",
            "--width",
            "96",
            "--height",
            "72",
            "--video",
            "gif",
            "--out",
            "renders/orbit.gif",
            "--workspace",
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    gif_path = Path(payload["gif"])
    assert gif_path.is_file()
    assert gif_path.resolve().is_relative_to(root.resolve())
    with _gif(gif_path) as gif:
        assert gif.n_frames == 4
        assert gif.size == (96, 72)


def test_frame_cleanup_leaves_a_linked_directory(tmp_path, monkeypatch):
    from aicad.animation import _remove_frames

    root = tmp_path / "project"
    frames = root / "renders" / "frames"
    frames.mkdir(parents=True)
    kept = frames / "frame_0000.png"
    kept.write_bytes(b"png")
    monkeypatch.setattr("aicad.animation.is_reparse", lambda path: Path(path) == frames)
    _remove_frames(frames, root)
    assert kept.read_bytes() == b"png"


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows directory link")
def test_frame_cleanup_does_not_follow_a_directory_junction(tmp_path):
    from aicad.animation import _remove_frames
    from aicad.paths import is_reparse

    root = tmp_path / "project"
    target = root / "kept"
    target.mkdir(parents=True)
    marker = target / "model.txt"
    marker.write_text("keep", encoding="utf-8")
    frames = root / "frames"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(frames), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("this machine cannot create a junction")
    assert is_reparse(frames)
    _remove_frames(frames, root)
    assert marker.read_text(encoding="utf-8") == "keep"
    assert frames.exists()
