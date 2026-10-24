"""Headless animations of an assembly.

A motion study, a camera orbit, or an exploded opening is drawn with the same
software rasterizer as the still views. Nothing here opens a window or uses a
GPU. The GIF is written with Pillow, which is already a dependency. MP4 and
WebM are written by ``ffmpeg`` when that program is on PATH. It is not
downloaded. When it is missing, or when it cannot encode the frames, the GIF
is still written and the report says so.

``frames`` counts both ends. Playback shows each frame for the nearest
whole centisecond to ``1000 / fps`` milliseconds, because a GIF stores its
delay in hundredths of a second. A motion study is sampled across its own
duration at those frames. The study's step count is not the frame count.
Colors and materials recolor the pictures only. The assembly is not changed.
Output paths stay inside the project.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from aicad.assembly import _concatenate, _instance_color, _transform_mesh
from aicad.errors import GeometryError
from aicad.fea import lookup_material
from aicad.kinematics import solve as solve_joints
from aicad.motion_study import posed_at, study_by_name
from aicad.paths import output_dir, output_path, publish_file, refuse_reserved_device, require_export_path
from aicad.render import _PAPER, _VIEWS, _camera_basis, frame_scale, render_frame

_FILE_SUFFIXES = {".gif", ".mp4", ".webm"}
_KINDS = {
    "motion": "motion",
    "motion-study": "motion",
    "motion_study": "motion",
    "orbit": "orbit",
    "exploded": "exploded",
    "explode": "exploded",
}
_NAMED_CAMERAS = {name: (eye, up) for name, eye, up, _label in _VIEWS}
_OVERLAYS = {"none", "step", "time", "value"}
_VIDEOS = {"gif", "none", "mp4", "webm", "both"}
_MATERIAL_COLOR = {
    "pla": (245.0, 236.0, 220.0),
    "petg": (196.0, 214.0, 220.0),
    "abs": (70.0, 74.0, 78.0),
    "nylon": (232.0, 228.0, 216.0),
    "aluminum_6061": (196.0, 202.0, 208.0),
    "steel": (110.0, 116.0, 124.0),
}
_FFMPEG_MISSING = (
    "ffmpeg was not found on PATH, so no MP4 or WebM was written. "
    "The GIF is the animation. Install ffmpeg and run this again to write a video."
)
_MAX_FRAMES = 300
_MAX_PIXELS = 50_000_000
_ISO_EYE = (1.0, -1.0, 0.85)


def refuse_output(root: Path, path: str | Path) -> Path:
    """Resolve an animation path and refuse one that leaves the project.

    Nothing is created. The caller still writes through :func:`output_path`.
    """
    from aicad.sandbox import require_in_project

    require_in_project(path, root, writing=True)
    refuse_reserved_device(path, root, "Export path", GeometryError)
    return require_export_path(root, path, "Export path", GeometryError)


def render_clip(
    *,
    root: Path,
    spec: dict,
    shapes: dict,
    libraries: dict,
    kind: str | None = None,
    sequence: str | None = None,
    study: dict | None = None,
    frames: int | None = None,
    fps: float | None = None,
    duration_s: float | None = None,
    width: int | None = None,
    height: int | None = None,
    camera: str | dict | None = None,
    camera_path: list | None = None,
    turns: float | None = None,
    elevation_deg: float | None = None,
    background: str | None = None,
    colors: dict | None = None,
    materials: dict | None = None,
    overlay: str | None = None,
    joint: str | None = None,
    gap_mm: float | None = None,
    video: str | None = None,
    path: str | Path | None = None,
) -> dict:
    """Draw the animation and return the file paths."""
    chosen_kind = _kind(kind, sequence, study)
    if gap_mm is not None and chosen_kind != "exploded":
        raise GeometryError("gap_mm is the opening of an exploded animation.")
    if joint is not None and chosen_kind != "motion":
        raise GeometryError("joint names the measured value on a motion study.")
    rate = 12 if fps is None else _whole(fps, "fps", 1, 60)
    picture_w = 480 if width is None else _even(width, "width", 16, 1280)
    picture_h = 360 if height is None else _even(height, "height", 16, 1280)
    paper = _background(background)
    color_of, material_of, color_note = _appearances(spec, colors, materials)
    formats = _video_formats(video)
    clock = _timeline(
        spec,
        chosen_kind,
        sequence,
        study,
        frames,
        rate,
        duration_s,
    )
    clock["fps_for_text"] = rate
    _apply_overlay(clock, overlay)
    count = clock["frames"]
    if picture_h < 32 and clock["overlay"] != "none":
        raise GeometryError("Height must be at least 32 when an overlay is drawn, or use overlay none.")
    if count * picture_w * picture_h > _MAX_PIXELS:
        raise GeometryError(
            "This animation is too large. Use fewer frames or a smaller picture. "
            f"The limit is {_MAX_PIXELS} pixels in total."
        )
    camera_spec = _camera_spec(chosen_kind, camera, camera_path, turns, elevation_deg)
    joint_name = _measured_joint(spec, clock.get("drivers") or [], joint, clock["overlay"], chosen_kind)
    samples = _samples(spec, shapes, libraries, chosen_kind, clock, gap_mm)
    header = 0 if clock["overlay"] == "none" else max(14, min(28, picture_h // 8))
    view_h = picture_h - header
    if view_h < 16:
        raise GeometryError("Height is too small for the overlay. Use at least 32, or overlay none.")
    meshes, bases, texts = _frames_of(
        samples,
        shapes,
        color_of,
        material_of,
        camera_spec,
        clock,
        joint_name,
    )
    ssaa = 2 if max(picture_w, view_h) <= 400 else 1
    scale = frame_scale(meshes, bases, picture_w, view_h, ssaa)
    images = [
        render_frame(
            mesh,
            basis,
            scale,
            picture_w,
            picture_h,
            background=paper,
            overlay=_drawn(text) if clock["overlay"] != "none" else "",
            ssaa=ssaa,
        )
        for mesh, basis, text in zip(meshes, bases, texts, strict=True)
    ]
    stem = _clip_stem(spec["name"], chosen_kind, clock.get("study_name"))
    targets = _targets(root, path, stem, formats)
    # GIF delay is a whole number of centiseconds. Half-up, and never zero.
    frame_ms = max(10, int(math.floor(1000 / rate / 10 + 0.5)) * 10)
    _write_png(targets["poster"], images[0])
    _write_gif(targets["gif"], images, frame_ms)
    videos, video_message = _write_videos(root, targets, images, rate, formats)
    primary = videos.get("mp4") or videos.get("webm")
    note = (
        "The GIF is drawn by the software renderer, with no window and no GPU. "
        "Colors and materials on this call change the pictures only. "
        "The assembly is unchanged."
    )
    if color_note:
        note = f"{note} {color_note}"
    if video_message:
        note = f"{note} {video_message}"
    gif_path = str(targets["gif"].resolve())
    poster_path = str(targets["poster"].resolve())
    report = {
        "assembly": spec["name"],
        "kind": chosen_kind,
        "frames": count,
        "fps": rate,
        "frame_ms": frame_ms,
        "duration_s": count * frame_ms / 1000.0,
        "motion_s": clock.get("motion_s"),
        "width": picture_w,
        "height": picture_h,
        "camera": _camera_report(camera_spec),
        "background": [int(channel) for channel in paper],
        "overlay": clock["overlay"],
        "overlays": texts if clock["overlay"] != "none" else [],
        "colors": sorted(color_of),
        "materials": {name: material_of[name] for name in sorted(material_of)},
        "gif": gif_path,
        "poster": poster_path,
        "video": primary,
        "videos": videos,
        "video_message": video_message,
        "views": [
            {"name": "poster", "path": poster_path, "width": picture_w, "height": picture_h},
        ],
        "note": note,
    }
    if chosen_kind == "exploded":
        report["gap_mm"] = samples["gap_mm"]
    return report


def _kind(kind: str | None, sequence: str | None, study: dict | None) -> str:
    if kind is None:
        return "motion" if (sequence or study) else "orbit"
    if not isinstance(kind, str):
        raise GeometryError("kind must be motion, orbit, or exploded.")
    chosen = _KINDS.get(kind.strip().lower())
    if chosen is None:
        raise GeometryError("kind must be motion, orbit, or exploded.")
    return chosen


def _timeline(spec, kind, sequence, study, frames, fps, duration_s) -> dict:
    motion_s = None
    drivers: list[str] = []
    study_name = None
    chosen_study = None
    if kind == "motion":
        chosen_study = study_by_name(spec, sequence, study)
        if chosen_study is None:
            known = ", ".join(spec.get("studies") or {}) or "(none)"
            raise GeometryError(
                f"Name a motion study on {spec['name']!r}, or pass one. Studies: {known}."
            )
        from aicad.motion_study import normalize_study

        chosen_study = normalize_study(
            chosen_study, spec["joints"], spec["relations"], name=chosen_study.get("name")
        )
        motion_s = float(chosen_study["duration_s"])
        drivers = [driver["joint"] for driver in chosen_study["drivers"]]
        study_name = chosen_study["name"]
        span = motion_s if duration_s is None else _duration(duration_s)
    else:
        if duration_s is None:
            span = 2.0
        else:
            span = _duration(duration_s)
    if frames is None:
        count = int(math.floor(span * fps + 0.5))
        if count < 2:
            raise GeometryError(
                "duration_s and fps make fewer than 2 frames. Pass frames, or use a longer duration."
            )
    else:
        count = _whole(frames, "frames", 2, _MAX_FRAMES)
    if count > _MAX_FRAMES:
        raise GeometryError(f"frames must be from 2 to {_MAX_FRAMES}.")
    overlay = "none" if kind == "orbit" else "step"
    return {
        "frames": count,
        "motion_s": motion_s if kind == "motion" else span,
        "study": chosen_study,
        "study_name": study_name,
        "drivers": drivers,
        "overlay_default": overlay,
        "overlay": overlay,
        "span": span,
    }


def _apply_overlay(clock: dict, overlay: str | None) -> None:
    if overlay is None:
        return
    if not isinstance(overlay, str) or overlay.strip().lower() not in _OVERLAYS:
        raise GeometryError("overlay must be none, step, time, or value.")
    clock["overlay"] = overlay.strip().lower()


def _duration(value) -> float:
    number = _finite(value, "duration_s")
    if number <= 0 or number > 120:
        raise GeometryError("duration_s must be greater than 0 and at most 120 seconds.")
    return number


def _samples(spec, shapes, libraries, kind, clock, gap_mm) -> dict:
    count = clock["frames"]
    if kind == "motion":
        rows = posed_at(spec, clock["study"], count)
        return {"kind": kind, "rows": rows, "gap_mm": None, "offsets": None, "solved": None}
    solved = solve_joints(spec)
    if kind == "orbit":
        return {
            "kind": kind,
            "rows": [{"index": index, "time_s": round(clock["span"] * index / count, 6), "solved": solved} for index in range(count)],
            "gap_mm": None,
            "offsets": None,
            "solved": solved,
        }
    from aicad import explode

    planned = explode.plan(spec, solved, shapes, libraries, gap_mm)
    return {
        "kind": kind,
        "rows": [{"index": index, "time_s": round(clock["span"] * index / max(count - 1, 1), 6), "solved": solved} for index in range(count)],
        "gap_mm": planned["gap_mm"],
        "offsets": planned["offsets"],
        "solved": solved,
    }


def _frames_of(samples, shapes, color_of, material_of, camera_spec, clock, joint_name):
    count = len(samples["rows"])
    cache: dict = {}
    meshes = []
    bases = []
    texts = []
    for row in samples["rows"]:
        index = row["index"]
        factor = 0.0 if count == 1 else index / (count - 1)
        if samples["kind"] == "orbit":
            factor = 0.0
        meshes.append(
            _mesh(cache, shapes, row["solved"], color_of, material_of, samples.get("offsets"), factor)
        )
        bases.append(_basis_at(index, count, camera_spec))
        texts.append(_overlay_text(clock, samples, row, index, count, joint_name, camera_spec))
    return meshes, bases, texts


def _mesh(cache, shapes, solved, color_of, material_of, offsets, factor):
    pieces = []
    for index, instance in enumerate(solved["instances"]):
        part = instance["part"]
        base = cache.get(part)
        if base is None:
            from aicad import kernel

            base = kernel.tessellate(shapes[part])
            cache[part] = base
        piece = _transform_mesh(base, instance["matrix"])
        delta = None if not offsets else offsets.get(instance["name"])
        if delta and factor:
            shift = np.array([float(component) * factor for component in delta], dtype=np.float64)
            piece.vertices = piece.vertices + shift
            piece.edges = [edge + shift for edge in piece.edges]
        color = _paint(instance, index, color_of, material_of)
        piece.colors = np.tile(np.array(color, dtype=np.float64), (len(piece.vertices), 1))
        pieces.append(piece)
    if not pieces:
        raise GeometryError("The assembly has no parts to render.")
    return _concatenate(pieces)


def _paint(instance, index, color_of, material_of) -> tuple[float, float, float]:
    name = instance["name"]
    if name in color_of:
        return color_of[name]
    if name in material_of:
        return _MATERIAL_COLOR.get(material_of[name], (176.0, 186.0, 196.0))
    return _instance_color(instance, index)


def _overlay_text(clock, samples, row, index, count, joint_name, camera_spec) -> str:
    mode = clock["overlay"]
    if mode == "none":
        return ""
    if mode == "step":
        return f"{index + 1}/{count}"
    if mode == "time":
        if samples["kind"] == "motion":
            shown = float(row["time_s"])
        else:
            shown = index / max(clock.get("fps_for_text", 1), 1)
        return f"{shown:.3f} s"
    if samples["kind"] == "exploded":
        opening = float(samples["gap_mm"]) * (0.0 if count == 1 else index / (count - 1))
        return f"{opening:.3f} mm"
    if samples["kind"] == "orbit":
        traveled = _traveled_degrees(index, count, camera_spec)
        return f"{traveled:.1f} deg"
    return _joint_text(row["solved"], clock["drivers"], joint_name)


def _joint_text(solved, drivers, joint_name) -> str:
    rows = {item["name"]: item for item in solved["joints"]}
    names = [joint_name] if joint_name else list(drivers)
    if not names:
        names = [item["name"] for item in solved["joints"][:1]]
    parts = []
    for name in names:
        row = rows.get(name)
        if row is None:
            continue
        unit = row.get("unit") or "deg"
        parts.append(f"{name} {float(row['value']):.3f} {unit}")
    return ", ".join(parts) if parts else ""


def _drawn(text: str) -> str:
    if len(text) <= 80:
        return text
    return text[:77] + "..."


def _measured_joint(spec, drivers, joint, overlay, kind) -> str | None:
    if joint is None:
        if overlay == "value" and kind == "motion" and not drivers and not spec.get("joints"):
            raise GeometryError("This assembly has no joint to measure.")
        return None
    if not isinstance(joint, str) or not joint.strip():
        raise GeometryError("joint must name a joint.")
    name = joint.strip()
    if overlay != "value":
        raise GeometryError("joint is the measured value. Set overlay to value.")
    known = [item["name"] for item in spec.get("joints") or []]
    if name not in known:
        listed = ", ".join(known) or "(none)"
        raise GeometryError(f"No joint {name!r}. Joints: {listed}.")
    return name


def _traveled_degrees(index: int, count: int, camera_spec: dict) -> float:
    if camera_spec["mode"] == "waypoints":
        azimuth, _elevation = _blend_waypoints(camera_spec["waypoints"], index, count)
        start, _elevation0 = camera_spec["waypoints"][0]
        return azimuth - start
    if camera_spec["mode"] == "orbit":
        return 360.0 * camera_spec["turns"] * index / count
    return 0.0


def _camera_spec(kind, camera, camera_path, turns, elevation_deg) -> dict:
    raw: dict = {}
    if isinstance(camera, dict):
        if camera_path is not None or turns is not None or elevation_deg is not None:
            raise GeometryError(
                "Put turns, elevation_deg, and path on the camera object, or pass them beside a camera name."
            )
        unknown = set(camera) - {"path", "turns", "elevation_deg"}
        if unknown:
            raise GeometryError(f"Camera has unknown fields: {', '.join(sorted(unknown))}.")
        raw = dict(camera)
    else:
        if camera is not None and camera_path is not None:
            raise GeometryError("Pass a camera name or a camera path, not both.")
        if camera is not None:
            if not isinstance(camera, str):
                raise GeometryError("camera must be a view name or an object.")
            raw["path"] = camera
        if camera_path is not None:
            raw["path"] = camera_path
        if turns is not None:
            raw["turns"] = turns
        if elevation_deg is not None:
            raw["elevation_deg"] = elevation_deg
    path = raw.get("path")
    if path is None:
        path = "orbit" if kind == "orbit" else "iso"
    if isinstance(path, list):
        if "turns" in raw or "elevation_deg" in raw:
            raise GeometryError("A camera path lists azimuth and elevation on each point.")
        points = _waypoints(path)
        return {"mode": "waypoints", "waypoints": points, "label": "waypoints"}
    if not isinstance(path, str):
        raise GeometryError("camera path must be a view name or a list of points.")
    name = path.strip().lower()
    if name == "orbit":
        spin = 1.0 if "turns" not in raw else _finite(raw["turns"], "turns")
        if spin <= 0 or spin > 8:
            raise GeometryError("turns must be greater than 0 and at most 8.")
        elevation = _iso_elevation_deg() if "elevation_deg" not in raw else _finite(raw["elevation_deg"], "elevation_deg")
        if elevation < -80 or elevation > 80:
            raise GeometryError("elevation_deg must be from -80 to 80.")
        return {
            "mode": "orbit",
            "turns": spin,
            "elevation_deg": elevation,
            "azimuth0": _iso_azimuth_rad(),
            "label": "orbit",
        }
    if "turns" in raw or "elevation_deg" in raw:
        raise GeometryError("turns and elevation_deg are for an orbit.")
    if name not in _NAMED_CAMERAS:
        known = ", ".join(("orbit", *tuple(_NAMED_CAMERAS)))
        raise GeometryError(f"No camera {path!r}. Cameras: {known}.")
    return {"mode": "named", "name": name, "label": name}


def _waypoints(path: list) -> list[tuple[float, float]]:
    if len(path) < 2:
        raise GeometryError("A camera path needs at least two points.")
    points = []
    for index, point in enumerate(path):
        if not isinstance(point, dict):
            raise GeometryError(f"Camera path point {index} must be an object.")
        unknown = set(point) - {"azimuth_deg", "elevation_deg"}
        if unknown:
            raise GeometryError(
                f"Camera path point {index} has unknown fields: {', '.join(sorted(unknown))}."
            )
        if "azimuth_deg" not in point or "elevation_deg" not in point:
            raise GeometryError(f"Camera path point {index} needs azimuth_deg and elevation_deg.")
        azimuth = _finite(point["azimuth_deg"], f"Camera path point {index} azimuth_deg")
        elevation = _finite(point["elevation_deg"], f"Camera path point {index} elevation_deg")
        if elevation < -80 or elevation > 80:
            raise GeometryError(f"Camera path point {index} elevation_deg must be from -80 to 80.")
        points.append((azimuth, elevation))
    return points


def _basis_at(index: int, count: int, camera_spec: dict):
    if camera_spec["mode"] == "waypoints":
        azimuth, elevation = _blend_waypoints(camera_spec["waypoints"], index, count)
        return _camera_basis(_eye_from_angles(azimuth, elevation), (0.0, 0.0, 1.0))
    if camera_spec["mode"] == "orbit":
        spin = 360.0 * camera_spec["turns"] * index / count
        azimuth = math.degrees(camera_spec["azimuth0"]) + spin
        return _camera_basis(_eye_from_angles(azimuth, camera_spec["elevation_deg"]), (0.0, 0.0, 1.0))
    eye, up = _NAMED_CAMERAS[camera_spec["name"]]
    return _camera_basis(eye, up)


def _blend_waypoints(points: list[tuple[float, float]], index: int, count: int) -> tuple[float, float]:
    if count <= 1:
        return points[0]
    scaled = (index / (count - 1)) * (len(points) - 1)
    slot = min(int(math.floor(scaled)), len(points) - 2)
    local = scaled - slot
    start = points[slot]
    end = points[slot + 1]
    return (
        start[0] + (end[0] - start[0]) * local,
        start[1] + (end[1] - start[1]) * local,
    )


def _eye_from_angles(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
    azimuth = math.radians(azimuth_deg)
    elevation = math.radians(elevation_deg)
    return (
        math.cos(elevation) * math.cos(azimuth),
        math.cos(elevation) * math.sin(azimuth),
        math.sin(elevation),
    )


def _iso_azimuth_rad() -> float:
    return math.atan2(_ISO_EYE[1], _ISO_EYE[0])


def _iso_elevation_deg() -> float:
    eye = np.array(_ISO_EYE, dtype=np.float64)
    eye /= np.linalg.norm(eye)
    return math.degrees(math.asin(float(eye[2])))


def _camera_report(camera_spec: dict) -> dict:
    if camera_spec["mode"] == "orbit":
        return {
            "path": "orbit",
            "turns": camera_spec["turns"],
            "elevation_deg": round(float(camera_spec["elevation_deg"]), 3),
        }
    if camera_spec["mode"] == "waypoints":
        return {
            "path": "waypoints",
            "points": [
                {"azimuth_deg": azimuth, "elevation_deg": elevation}
                for azimuth, elevation in camera_spec["waypoints"]
            ],
        }
    return {"path": camera_spec["label"]}


def _appearances(spec, colors, materials):
    names = [instance["name"] for instance in spec["instances"]]
    known = set(names)
    color_of = _color_map(colors, known, names)
    material_of = _material_map(materials, known, names)
    both = sorted(set(color_of) & set(material_of))
    note = ""
    if both:
        note = "A color replaces the material color on " + ", ".join(both) + "."
    return color_of, material_of, note


def _color_map(colors, known: set[str], names: list[str]) -> dict[str, tuple[float, float, float]]:
    if colors is None:
        return {}
    if not isinstance(colors, dict):
        raise GeometryError("colors must map an instance name to a color.")
    painted = {}
    for name, value in colors.items():
        ident = str(name).strip()
        if ident not in known:
            listed = ", ".join(names) or "(none)"
            raise GeometryError(f"No instance {ident!r}. Instances: {listed}.")
        painted[ident] = _parse_color(value, f"Color for {ident}")
    return painted


def _material_map(materials, known: set[str], names: list[str]) -> dict[str, str]:
    if materials is None:
        return {}
    if not isinstance(materials, dict):
        raise GeometryError("materials must map an instance name to a material.")
    chosen = {}
    for name, value in materials.items():
        ident = str(name).strip()
        if ident not in known:
            listed = ", ".join(names) or "(none)"
            raise GeometryError(f"No instance {ident!r}. Instances: {listed}.")
        if not isinstance(value, str):
            raise GeometryError(f"Material for {ident} must be a name.")
        found = lookup_material(value)
        chosen[ident] = str(found["id"])
    return chosen


def _parse_color(value, label: str) -> tuple[float, float, float]:
    if isinstance(value, str):
        return _parse_color_text(value, label)
    if isinstance(value, (list, tuple)) and len(value) == 3:
        channels = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise GeometryError(f"{label} must be #rrggbb or three numbers from 0 to 255.")
            number = float(item)
            if not math.isfinite(number) or number < 0 or number > 255:
                raise GeometryError(f"{label} channels must be from 0 to 255.")
            channels.append(number)
        return (channels[0], channels[1], channels[2])
    raise GeometryError(f"{label} must be #rrggbb or three numbers from 0 to 255.")


def _background(value: str | None) -> tuple[int, int, int]:
    if value is None:
        return _PAPER
    if not isinstance(value, str):
        raise GeometryError("background must be paper, white, black, or #rrggbb.")
    text = value.strip().lower()
    named = {
        "paper": _PAPER,
        "white": (255, 255, 255),
        "black": (0, 0, 0),
    }
    if text in named:
        return named[text]
    red, green, blue = _parse_color_text(value, "background")
    return (int(red), int(green), int(blue))


def _parse_color_text(value: str, label: str) -> tuple[float, float, float]:
    text = value.strip()
    if text.lower() in {"paper", "white", "black"} and label != "background":
        named = {"paper": _PAPER, "white": (255, 255, 255), "black": (0, 0, 0)}
        channels = named[text.lower()]
        return (float(channels[0]), float(channels[1]), float(channels[2]))
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise GeometryError(f"{label} must be #rrggbb or three numbers from 0 to 255.")
    return tuple(float(int(text[index : index + 2], 16)) for index in (0, 2, 4))


def _video_formats(video: str | None) -> list[str]:
    if video is None:
        video = "mp4"
    if not isinstance(video, str) or video.strip().lower() not in _VIDEOS:
        raise GeometryError("video must be gif, mp4, webm, or both.")
    key = video.strip().lower()
    if key in {"gif", "none"}:
        return []
    if key == "both":
        return ["mp4", "webm"]
    return [key]


def _clip_stem(assembly: str, kind: str, study_name: str | None) -> str:
    base = _file_stem(assembly)
    if kind == "motion":
        return _file_stem(f"{base}_{_file_stem(study_name or 'motion')}")
    if kind == "exploded":
        return _file_stem(f"{base}_exploded")
    return _file_stem(f"{base}_orbit")


def _file_stem(name: str) -> str:
    from aicad.paths import is_reserved_device_name

    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name).strip())
    cleaned = cleaned.strip("._") or "animation"
    cleaned = cleaned[:64]
    if is_reserved_device_name(cleaned):
        raise GeometryError(f"{cleaned} is reserved on Windows. Pick another name.")
    return cleaned


def _targets(root: Path, path: str | Path | None, stem: str, formats: list[str]) -> dict:
    if path is not None:
        refuse_output(root, path)
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(root).resolve() / candidate
        refuse_reserved_device(candidate, root, "Export path", GeometryError)
        if _directory_target(candidate):
            folder = output_dir(root, candidate, "Render", GeometryError)
            gif = folder / f"{stem}.gif"
        else:
            suffix = candidate.suffix.lower()
            if suffix not in _FILE_SUFFIXES:
                raise GeometryError("An animation file must end in .gif, .mp4, or .webm.")
            gif = candidate.with_suffix(".gif")
    else:
        folder = output_dir(root, Path(root) / "renders" / "assemblies" / stem, "Render", GeometryError)
        gif = folder / f"{stem}.gif"
    gif_path = output_path(root, gif, "Render", GeometryError)
    poster = output_path(root, gif_path.with_name(f"{gif_path.stem}_poster.png"), "Render", GeometryError)
    videos = {}
    for fmt in formats:
        videos[fmt] = output_path(root, gif_path.with_suffix(f".{fmt}"), "Render", GeometryError)
    frame_dir = gif_path.parent / f".{gif_path.stem}-frames"
    return {"gif": gif_path, "poster": poster, "videos": videos, "frames": frame_dir}


def _directory_target(path: Path) -> bool:
    if path.exists() and path.is_dir():
        return True
    return path.suffix.lower() not in _FILE_SUFFIXES


def _write_png(path: Path, image: Image.Image) -> None:
    def write(temporary: Path) -> None:
        image.convert("RGB").save(temporary, format="PNG")

    publish_file(path, write)


def _write_gif(path: Path, images: list[Image.Image], frame_ms: int) -> None:
    frames = [image.convert("RGB") for image in images]
    durations = [frame_ms] * len(frames)

    def write(temporary: Path) -> None:
        frames[0].save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
            optimize=False,
        )

    publish_file(path, write)


def _write_videos(root: Path, targets: dict, images: list[Image.Image], fps: int, formats: list[str]):
    if not formats:
        return {}, None
    executable = shutil.which("ffmpeg")
    if not executable:
        return {}, _FFMPEG_MISSING
    frame_dir = output_dir(root, targets["frames"], "Render", GeometryError)
    videos = {}
    failures = []
    try:
        for index, image in enumerate(images):
            _write_png(frame_dir / f"frame_{index:04d}.png", image)
        for fmt in formats:
            try:
                _encode(executable, frame_dir, len(images), fps, fmt, targets["videos"][fmt])
                videos[fmt] = str(targets["videos"][fmt].resolve())
            except (OSError, RuntimeError) as exc:
                reason = str(exc).strip() or "the encoder failed"
                failures.append(
                    f"ffmpeg could not write the {fmt.upper()} ({reason}). The GIF is the animation."
                )
    finally:
        _remove_frames(frame_dir, root)
    message = " ".join(failures) if failures else None
    if failures and not videos and len(failures) == 1:
        message = failures[0]
    return videos, message


def _encode(executable: str, frame_dir: Path, count: int, fps: int, fmt: str, destination: Path) -> None:
    if fmt == "mp4":
        codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23"]
    else:
        codec = ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "1M"]

    def write(temporary: Path) -> None:
        command = [
            executable,
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(fps),
            "-start_number",
            "0",
            "-i",
            "frame_%04d.png",
            "-frames:v",
            str(count),
            "-an",
            *codec,
            str(temporary),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=frame_dir,
                capture_output=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg did not finish") from exc
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            reason = (completed.stderr or b"").decode("utf-8", "replace").strip().replace("\n", " ")
            if len(reason) > 300:
                reason = reason[-300:]
            raise RuntimeError(reason or f"exit {completed.returncode}")

    publish_file(destination, write)


def _remove_frames(folder: Path, root: Path) -> None:
    try:
        if folder.is_symlink():
            return
        resolved = folder.resolve()
        root_resolved = root.resolve()
    except OSError:
        return
    if resolved == root_resolved or root_resolved not in resolved.parents:
        return
    if resolved.is_symlink():
        return
    shutil.rmtree(resolved, ignore_errors=True)


def write_frames(
    root: Path,
    images: list[Image.Image],
    *,
    fps: int | None = None,
    video: str | None = None,
    path: str | Path | None = None,
    stem: str = "timelapse",
) -> dict:
    """Write frames with the same GIF and ffmpeg path as an assembly animation.

    The GIF is always written. MP4 and WebM are written when ``video`` asks
    for them and ``ffmpeg`` is on PATH. A missing encoder leaves the GIF.
    """
    if not images:
        raise GeometryError("There are no frames to write.")
    rate = 12 if fps is None else _whole(fps, "fps", 1, 60)
    formats = _video_formats(video)
    targets = _targets(root, path, stem, formats)
    frame_ms = max(10, int(math.floor(1000 / rate / 10 + 0.5)) * 10)
    _write_png(targets["poster"], images[0])
    _write_gif(targets["gif"], images, frame_ms)
    videos, video_message = _write_videos(root, targets, images, rate, formats)
    report = {
        "frames": len(images),
        "fps": rate,
        "frame_ms": frame_ms,
        "width": images[0].width,
        "height": images[0].height,
        "gif": targets["gif"],
        "poster": targets["poster"],
        "video_message": video_message,
    }
    for fmt, destination in targets["videos"].items():
        if fmt in videos:
            report[fmt] = destination
    return report


def _whole(value, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{label} must be a whole number.")
    if isinstance(value, float) and not math.isfinite(value):
        raise GeometryError(f"{label} must be a whole number.")
    if isinstance(value, float) and not value.is_integer():
        raise GeometryError(f"{label} must be a whole number.")
    number = int(value)
    if number < low or number > high:
        raise GeometryError(f"{label} must be from {low} to {high}.")
    return number


def _even(value, label: str, low: int, high: int) -> int:
    number = _whole(value, label, low, high)
    if number % 2:
        raise GeometryError(f"{label} must be an even number so a video frame can be encoded.")
    return number


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{label} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"{label} must be a finite number.")
    return number
