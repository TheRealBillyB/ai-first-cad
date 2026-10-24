"""Opt-in record of design steps, and a timelapse that only reads that record.

Recording stays off until this project is turned on from the app, the command
line, or MCP. The switch is stored outside the project. The record itself
stays inside the project: each step has the command, redacted arguments, a
short summary, a timestamp, a compact model snapshot, and a picture.

A record that arrived with a shared project is data. Replay and export read
it. They do not run the stored commands, open a link, or follow a path that
leaves the project. A symlink, a junction, or a file with another name is
not read.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image, ImageDraw

from aicad.errors import GeometryError
from aicad.kernel import TriangleMesh
from aicad.paths import (
    _export_blocked,
    _path_after_windows_trim,
    is_reparse,
    is_reserved_device_name,
    output_path,
    publish_text,
    session_path_escapes,
    shared_file,
    stays_inside,
)

FORMAT = "session_record.v1"
_RECORD = "records/session.json"
_MAX_STEPS = 300
_MAX_RECORD_BYTES = 32 * 1024 * 1024
_MAX_PICTURE_BYTES = 8 * 1024 * 1024
_MAX_VERTICES = 8000
_MAX_TEXT = 4000
_CAPTURE_SIZE = (128, 96)

_SKIP = frozenset({"session_record"})
_SECRET_NAME = re.compile(
    r"api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization|credential|private[_-]?key",
    re.I,
)
_TOKEN = re.compile(
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"
    r"|\bsk-[A-Za-z0-9]{8,}"
    r"|\bghp_[A-Za-z0-9]{8,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{8,}"
    r"|\bxox[baprs]-[A-Za-z0-9-]{8,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
)
_URL = re.compile(r"(?i)\b(?:https?|file|ftp)://\S+")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,79}")
_PART_COLORS = (
    (196.0, 168.0, 130.0),
    (120.0, 156.0, 176.0),
    (176.0, 132.0, 112.0),
    (150.0, 166.0, 130.0),
    (168.0, 140.0, 160.0),
    (130.0, 148.0, 158.0),
)


class RecordRefused(GeometryError):
    """The session record tried to leave the project or follow a link."""


def recording_enabled(root: str | Path) -> bool:
    """True when this computer has recording on for this project.

    The switch is not read from the project. A shared record cannot turn it on.
    """
    key = str(Path(root).resolve())
    entry = _read_opt().get("projects", {}).get(key)
    return isinstance(entry, dict) and isinstance(entry.get("enabled_at"), str)


def status(root: str | Path) -> dict:
    """Whether recording is on, and how many steps are already stored."""
    folder = Path(root).resolve()
    count = 0
    try:
        loaded = _load(folder)
    except (GeometryError, OSError, json.JSONDecodeError):
        loaded = None
    if loaded is not None:
        count = len(loaded["steps"])
    report = {"enabled": recording_enabled(folder), "steps": count, "path": _RECORD}
    return report


def run_action(document, arguments: dict, revision: int | None = None) -> dict:
    """Start, stop, describe, replay, or export the session record."""
    action = arguments.get("action")
    if not isinstance(action, str):
        raise GeometryError("action must be start, stop, status, replay, or export.")
    key = action.strip().lower()
    root = Path(document.workspace).resolve()
    if key == "start":
        return _start(root)
    if key == "stop":
        return _stop(root)
    if key == "status":
        report = status(root)
        report["summary"] = _status_summary(report)
        return report
    if key == "replay":
        return _replay(root, arguments.get("steps"), arguments.get("step"))
    if key == "export":
        return _export(root, arguments)
    raise GeometryError("action must be start, stop, status, replay, or export.")


def record_command(
    document,
    name: str,
    arguments: dict | None,
    result: dict | None,
    *,
    source: str,
    revision: int | None = None,
) -> None:
    """Append one design step when recording is on. Failures are ignored.

    The design command has already finished. A record that cannot be written
    inside the project is skipped, and the command result is left as it is.
    """
    try:
        _record_command(document, name, arguments or {}, result or {}, source=source, revision=revision)
    except Exception:
        return


def cli_arguments(args) -> dict:
    """Arguments from one command-line invocation, without the project path."""
    raw = {}
    for key, value in vars(args).items():
        if key in {"command", "workspace", "script_network"} or value is None:
            continue
        raw[key] = value
    return raw


def _record_command(document, name: str, arguments: dict, result: dict, *, source: str, revision: int | None) -> None:
    if name in _SKIP or not _should_record(name):
        return
    root = Path(document.workspace).resolve()
    if not recording_enabled(root):
        return
    loaded = _load_or_empty(root)
    if loaded is None or len(loaded["steps"]) >= _MAX_STEPS:
        return
    index = _next_index(loaded["steps"])
    redacted = _redact(_plain(arguments), root, _secret_env_values())
    summary = _redact_text(_summary(name, arguments, result), root, _secret_env_values())
    state = _capture_state(document, revision)
    mesh = _capture_mesh(document)
    frame_rel = f"records/session/frames/{index:04d}.png"
    mesh_rel = f"records/session/meshes/{index:04d}.json"
    picture = _frame_image(mesh, _one_line(summary) or name, *_CAPTURE_SIZE)
    _write_png(root, frame_rel, picture)
    step = {
        "index": index,
        "time": _now(),
        "command": name if _IDENTIFIER.fullmatch(name) else "command",
        "arguments": redacted,
        "summary": _one_line(summary, 160) or name,
        "source": _source_name(source),
        "state": state,
        "frame": frame_rel,
    }
    if mesh is not None and len(mesh.vertices) <= _MAX_VERTICES and _write_mesh(root, mesh_rel, mesh):
        step["mesh"] = mesh_rel
    loaded["steps"].append(step)
    _write_record(root, loaded)


def _should_record(name: str) -> bool:
    from aicad.studio import note_for

    try:
        return bool(note_for(name, {}, {}).get("record"))
    except Exception:
        return False


def _summary(name: str, arguments: dict, result: dict) -> str:
    from aicad.studio import note_for

    try:
        text = note_for(name, arguments, result if isinstance(result, dict) else {})["entry"]["summary"]
    except Exception:
        text = name.replace("_", " ")
    return text if isinstance(text, str) else name.replace("_", " ")


def _start(root: Path) -> dict:
    data = _read_opt()
    projects = data.setdefault("projects", {})
    projects[str(root)] = {"enabled_at": _now()}
    _write_opt(data)
    if _load_or_empty(root) is None:
        raise GeometryError("The session record could not be created inside the project.")
    report = status(root)
    report["summary"] = "Recording is on. Design steps are stored in this project."
    return report


def _stop(root: Path) -> dict:
    data = _read_opt()
    projects = data.setdefault("projects", {})
    projects.pop(str(root), None)
    _write_opt(data)
    report = status(root)
    report["summary"] = "Recording is off. The session record stays in the project."
    return report


def _status_summary(report: dict) -> str:
    count = int(report.get("steps") or 0)
    if count == 1:
        stored = "1 step is stored."
    else:
        stored = f"{count} steps are stored."
    if report.get("enabled"):
        return f"Recording is on. {stored}"
    return f"Recording is off. {stored}"


def _replay(root: Path, steps_value, step_value) -> dict:
    loaded = _require_loaded(root)
    chosen = _select(loaded["steps"], steps_value, step_value)
    public = [_public_step(root, step) for step in chosen]
    count = len(loaded["steps"])
    if len(public) == 1:
        summary = f"Step {public[0]['index']} of {count}."
    else:
        summary = f"{len(public)} steps."
    return {"path": _RECORD, "count": count, "steps": public, "summary": summary}


def _export(root: Path, arguments: dict) -> dict:
    from aicad.animation import write_frames
    from aicad.render import _PAPER, _VIEWS, _camera_basis, frame_scale, render_frame

    loaded = _require_loaded(root)
    chosen = _select(loaded["steps"], arguments.get("steps"), arguments.get("step"))
    if not chosen:
        raise GeometryError("The session record has no steps.")
    camera = _camera_name(arguments.get("camera"))
    width = _size(arguments.get("width"), "width", 320)
    height = _size(arguments.get("height"), "height", 240)
    if len(chosen) * width * height > 50_000_000:
        raise GeometryError("This timelapse is too large. Use fewer steps or a smaller picture.")
    eyes = {name: (eye, up) for name, eye, up, _label in _VIEWS}
    eye, up = eyes[camera]
    basis = _camera_basis(eye, up)
    images = []
    camera_applied = True
    for step in chosen:
        overlay = _overlay(root, step)
        image, applied = _export_image(
            root,
            step,
            basis,
            camera,
            width,
            height,
            overlay,
            frame_scale,
            render_frame,
            _PAPER,
        )
        camera_applied = camera_applied and applied
        images.append(image)
    destination = arguments.get("path") or "records/session/timelapse.gif"
    written = write_frames(
        root,
        images,
        fps=_optional_int(arguments.get("fps")),
        video=arguments.get("video"),
        path=destination,
        stem="session",
    )
    report = {
        "assembly": None,
        "kind": "session",
        "frames": written["frames"],
        "fps": written["fps"],
        "frame_ms": written["frame_ms"],
        "width": written["width"],
        "height": written["height"],
        "camera": camera,
        "camera_applied": camera_applied,
        "included": [int(step["index"]) for step in chosen],
        "path": _RECORD,
        "gif": _public_path(root, written["gif"]),
        "poster": _public_path(root, written["poster"]),
        "summary": f"Wrote a timelapse of {len(chosen)} steps.",
    }
    if written.get("video_message"):
        report["video_message"] = written["video_message"]
    for fmt in ("mp4", "webm"):
        if fmt in written:
            report[fmt] = _public_path(root, written[fmt])
    return report


def _export_image(root, step, basis, camera, width, height, overlay, frame_scale, render_frame, paper):
    mesh_rel = step.get("mesh") if isinstance(step, dict) else None
    if isinstance(mesh_rel, str) and mesh_rel:
        try:
            mesh = _load_mesh(root, mesh_rel)
        except RecordRefused:
            raise
        except (GeometryError, OSError, json.JSONDecodeError, ValueError):
            mesh = None
        if mesh is not None:
            return _render_mesh(mesh, basis, width, height, overlay, frame_scale, render_frame), True
    frame_rel = step.get("frame") if isinstance(step, dict) else None
    if isinstance(frame_rel, str) and frame_rel:
        try:
            picture = _load_png(root, frame_rel)
        except RecordRefused:
            raise
        except (GeometryError, OSError, ValueError):
            picture = None
        if picture is not None:
            return picture.resize((width, height), Image.Resampling.BOX), False
    return _blank(width, height, overlay, paper), False


def _render_mesh(mesh, basis, width, height, overlay, frame_scale, render_frame) -> Image.Image:
    header = max(14, min(28, height // 8)) if overlay else 0
    view_h = height - header
    if view_h < 8:
        raise GeometryError("Height is too small for the step label. Use at least 32.")
    scale = frame_scale([mesh], [basis], width, view_h, 1)
    return render_frame(mesh, basis, scale, width, height, overlay=overlay, ssaa=1)


def _frame_image(mesh: TriangleMesh | None, overlay: str, width: int, height: int) -> Image.Image:
    from aicad.render import _PAPER, _VIEWS, _camera_basis, frame_scale, render_frame

    if mesh is None or len(mesh.vertices) == 0:
        return _blank(width, height, overlay, _PAPER)
    eyes = {name: (eye, up) for name, eye, up, _label in _VIEWS}
    eye, up = eyes["iso"]
    basis = _camera_basis(eye, up)
    return _render_mesh(mesh, basis, width, height, overlay, frame_scale, render_frame)


def _blank(width: int, height: int, overlay: str, paper: tuple[int, int, int]) -> Image.Image:
    from aicad.render import _font, _ink_for

    image = Image.new("RGB", (width, height), paper)
    if overlay:
        ImageDraw.Draw(image).text((6, 6), overlay[:80], fill=_ink_for(paper), font=_font(12))
    return image


def _capture_state(document, revision: int | None) -> dict:
    parts = [info.name for info in document.list_parts() if isinstance(getattr(info, "name", None), str)]
    assemblies = []
    for item in document.list_assemblies():
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            assemblies.append(item["name"])
    sketches = [name for name in document.list_sketches() if isinstance(name, str)]
    state = {"parts": parts, "assemblies": assemblies, "sketches": sketches}
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
        state["revision"] = revision
    return state


def _capture_mesh(document) -> TriangleMesh | None:
    from aicad import kernel

    pieces: list[TriangleMesh] = []
    for index, info in enumerate(document.list_parts()):
        if not getattr(info, "built", False):
            continue
        try:
            part = document._get(info.name)  # noqa: SLF001 — the picture is the current solid
            mesh = kernel.tessellate(part.shape)
        except Exception:
            continue
        color = np.array(_PART_COLORS[index % len(_PART_COLORS)], dtype=np.float64)
        mesh.colors = np.tile(color, (len(mesh.vertices), 1))
        mesh.edges = []
        pieces.append(mesh)
    if not pieces:
        return None
    try:
        return _join(pieces)
    except Exception:
        return None


def _join(pieces: list[TriangleMesh]) -> TriangleMesh:
    vertices = []
    normals = []
    triangles = []
    colors = []
    offset = 0
    for piece in pieces:
        count = len(piece.vertices)
        if count == 0 or piece.triangles.size == 0:
            continue
        vertices.append(np.asarray(piece.vertices, dtype=np.float64))
        normals.append(np.asarray(piece.normals, dtype=np.float64))
        triangles.append(np.asarray(piece.triangles, dtype=np.int32) + offset)
        if piece.colors is None:
            colors.append(np.tile(np.array([[180.0, 186.0, 196.0]]), (count, 1)))
        else:
            colors.append(np.asarray(piece.colors, dtype=np.float64))
        offset += count
    if not vertices:
        raise GeometryError("The model has no triangles to draw.")
    return TriangleMesh(
        vertices=np.vstack(vertices),
        normals=np.vstack(normals),
        triangles=np.vstack(triangles),
        edges=[],
        colors=np.vstack(colors),
    )


def _write_mesh(root: Path, relative: str, mesh: TriangleMesh) -> bool:
    positions = np.round(np.asarray(mesh.vertices, dtype=np.float64), 3)
    normals = np.round(np.asarray(mesh.normals, dtype=np.float64), 4)
    colors = np.clip(np.round(np.asarray(mesh.colors, dtype=np.float64)), 0, 255)
    if not np.isfinite(positions).all() or not np.isfinite(normals).all() or not np.isfinite(colors).all():
        return False
    payload = {
        "positions": positions.reshape(-1).tolist(),
        "normals": normals.reshape(-1).tolist(),
        "triangles": np.asarray(mesh.triangles, dtype=np.int64).reshape(-1).tolist(),
        "colors": colors.reshape(-1).tolist(),
    }
    _write_text(root, relative, json.dumps(payload, separators=(",", ":")))
    return True


def _write_png(root: Path, relative: str, image: Image.Image) -> None:
    from aicad.paths import publish_file

    destination = output_path(root, root / relative, "Session record", GeometryError)

    def write(temporary: Path) -> None:
        image.convert("RGB").save(temporary, format="PNG")

    publish_file(destination, write, root=root)


def _load_mesh(root: Path, relative: str) -> TriangleMesh:
    payload = _read_json_file(root, relative, _MAX_PICTURE_BYTES)
    if not isinstance(payload, dict):
        raise GeometryError("A session mesh is not a picture of the model.")
    positions = _number_list(payload.get("positions"), places=3)
    normals = _number_list(payload.get("normals"), places=3)
    colors = _number_list(payload.get("colors"), places=3)
    triangles = _index_list(payload.get("triangles"))
    count = len(positions) // 3
    if count == 0 or count > _MAX_VERTICES or len(normals) != len(positions) or len(colors) != len(positions):
        raise GeometryError("A session mesh is not a picture of the model.")
    if not triangles or len(triangles) > _MAX_VERTICES * 6:
        raise GeometryError("A session mesh is not a picture of the model.")
    for index in triangles:
        if index < 0 or index >= count:
            raise GeometryError("A session mesh is not a picture of the model.")
    return TriangleMesh(
        vertices=np.asarray(positions, dtype=np.float64).reshape(-1, 3),
        normals=np.asarray(normals, dtype=np.float64).reshape(-1, 3),
        triangles=np.asarray(triangles, dtype=np.int32).reshape(-1, 3),
        edges=[],
        colors=np.asarray(colors, dtype=np.float64).reshape(-1, 3),
    )


def _load_png(root: Path, relative: str) -> Image.Image:
    path = _project_file(root, relative)
    raw = _read_bytes(path, _MAX_PICTURE_BYTES)
    with Image.open(BytesIO(raw)) as picture:
        if picture.format != "PNG":
            raise GeometryError("A session picture has to be a PNG.")
        if picture.width * picture.height > 2_000_000:
            raise GeometryError("A session picture is too large.")
        return picture.convert("RGB")


def _public_step(root: Path, step: dict) -> dict:
    env_values = _secret_env_values()
    arguments = step.get("arguments")
    public = {
        "index": int(step["index"]),
        "time": _time_text(step.get("time")),
        "command": _command_text(step.get("command")),
        "arguments": _redact(arguments if isinstance(arguments, (dict, list)) else {}, root, env_values),
        "summary": _one_line(_redact_text(_text(step.get("summary")), root, env_values), 160),
        "source": _source_name(step.get("source")),
        "state": _public_state(step.get("state")),
    }
    frame = step.get("frame")
    if isinstance(frame, str):
        try:
            _project_file(root, frame)
        except RecordRefused:
            public["frame_omitted"] = True
        except GeometryError:
            public["frame_omitted"] = True
        else:
            public["frame"] = _safe_relative(frame)
    return public


def _public_state(value) -> dict:
    if not isinstance(value, dict):
        return {"parts": [], "assemblies": [], "sketches": []}

    def names(key: str) -> list[str]:
        items = value.get(key)
        if not isinstance(items, list):
            return []
        found = []
        for item in items[:200]:
            text = item.get("name") if isinstance(item, dict) else item
            if isinstance(text, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text):
                found.append(text)
        return found

    state = {"parts": names("parts"), "assemblies": names("assemblies"), "sketches": names("sketches")}
    revision = value.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool) and 0 <= revision <= 1_000_000:
        state["revision"] = revision
    return state


def _select(steps: list[dict], steps_value, step_value) -> list[dict]:
    wanted = _parse_steps(steps_value)
    single = None if step_value is None else _step_number(step_value)
    if single is not None:
        wanted = [single] if wanted is None else [number for number in wanted if number == single]
    by_index = {int(step["index"]): step for step in steps if isinstance(step, dict) and isinstance(step.get("index"), int)}
    if wanted is None:
        return list(steps)
    chosen = []
    for number in wanted:
        step = by_index.get(number)
        if step is None:
            raise GeometryError(f"There is no session step {number}.")
        chosen.append(step)
    return chosen


def _parse_steps(value) -> list[int] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        numbers: list[int] = []
        for part in value.split(","):
            piece = part.strip()
            if not piece:
                continue
            if "-" in piece:
                start_text, end_text = piece.split("-", 1)
                start = _step_number(start_text)
                end = _step_number(end_text)
                if start > end or end - start > _MAX_STEPS:
                    raise GeometryError("steps has a range that is empty or too long.")
                numbers.extend(range(start, end + 1))
            else:
                numbers.append(_step_number(piece))
        if len(numbers) > _MAX_STEPS:
            raise GeometryError("steps lists too many steps.")
        return numbers
    if isinstance(value, list):
        numbers = [_step_number(item) for item in value]
        if len(numbers) > _MAX_STEPS:
            raise GeometryError("steps lists too many steps.")
        return numbers
    return [_step_number(value)]


def _step_number(value) -> int:
    if isinstance(value, bool):
        raise GeometryError("A session step is a whole number.")
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            raise GeometryError("A session step is a whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError("A session step is a whole number.") from exc
    if isinstance(value, float) and not float(value).is_integer():
        raise GeometryError("A session step is a whole number.")
    if number < 1 or number > _MAX_STEPS:
        raise GeometryError(f"A session step is from 1 to {_MAX_STEPS}.")
    return number


def _camera_name(value) -> str:
    from aicad.render import _VIEWS

    names = {name for name, _eye, _up, _label in _VIEWS}
    if value is None or value == "":
        return "iso"
    if not isinstance(value, str) or value.strip().lower() not in names:
        raise GeometryError("camera must be iso, front, back, left, right, top, or bottom.")
    return value.strip().lower()


def _size(value, label: str, default: int) -> int:
    from aicad.animation import _even

    if value is None:
        return default
    return _even(value, label, 16, 1280)


def _optional_int(value):
    if value is None or value == "":
        return None
    return value


def _require_loaded(root: Path) -> dict:
    if not (root / _RECORD).exists() and not (root / _RECORD).is_symlink():
        raise GeometryError("There is no session record yet.")
    try:
        loaded = _load(root)
    except RecordRefused:
        raise
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise GeometryError("The session record could not be read.") from exc
    if loaded is None:
        raise GeometryError("The session record could not be read.")
    return loaded


def _load_or_empty(root: Path) -> dict | None:
    path = root / _RECORD
    if not path.exists() and not path.is_symlink():
        loaded = {"format": FORMAT, "steps": []}
        try:
            _write_record(root, loaded)
        except (GeometryError, OSError):
            return None
        return loaded
    try:
        return _load(root)
    except (GeometryError, OSError, json.JSONDecodeError, UnicodeError):
        return None


def _load(root: Path) -> dict | None:
    payload = _read_json_file(root, _RECORD, _MAX_RECORD_BYTES)
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        return None
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return None
    kept = [step for step in steps[:_MAX_STEPS] if isinstance(step, dict)]
    return {"format": FORMAT, "steps": kept}


def _write_record(root: Path, loaded: dict) -> None:
    payload = {"format": FORMAT, "steps": loaded["steps"]}
    _write_text(root, _RECORD, json.dumps(payload, indent=2) + "\n")


def _write_text(root: Path, relative: str, text: str) -> None:
    destination = output_path(root, root / relative, "Session record", GeometryError)
    publish_text(destination, text, root=root)


def _read_json_file(root: Path, relative: str, limit: int):
    path = _project_file(root, relative)
    raw = _read_bytes(path, limit)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise GeometryError("A session record file is not utf-8 text.") from exc
    return json.loads(text)


def _project_file(root: Path, relative: str) -> Path:
    text = str(relative).strip().replace("\\", "/")
    if (
        not text
        or text.startswith("/")
        or text.startswith("//")
        or re.match(r"^[A-Za-z]:", text)
        or "://" in text
        or "\x00" in text
    ):
        raise RecordRefused("A session record path has to stay inside the project.")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part != Path(part).name or is_reserved_device_name(part):
            raise RecordRefused("A session record path has to stay inside the project.")
        parts.append(part)
    if not parts:
        raise RecordRefused("A session record path has to stay inside the project.")
    current = root.resolve()
    for part in parts:
        if is_reparse(current) or current.is_symlink():
            raise RecordRefused("A session record file is a link and was not followed.")
        current = current / part
        if is_reparse(current) or current.is_symlink():
            raise RecordRefused("A session record file is a link and was not followed.")
        if shared_file(current):
            raise RecordRefused("A session record file has another name and was not read.")
    if not current.is_file():
        raise GeometryError("A session record file is missing.")
    if not stays_inside(root, current):
        raise RecordRefused("A session record path has to stay inside the project.")
    info = current.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RecordRefused("A session record file is not an ordinary file.")
    return current


def _read_bytes(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if info.st_size > limit:
        raise GeometryError("This session record is too large.")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, getattr(errno, "EMLINK", -1)}:
            raise RecordRefused("A session record file is a link and was not followed.") from exc
        raise GeometryError("A session record file could not be read.") from exc
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(limit + 1)


def _public_path(root: Path, path: Path) -> str:
    resolved = Path(path).resolve()
    if not stays_inside(root, resolved):
        raise RecordRefused("A session record path has to stay inside the project.")
    return resolved.relative_to(root.resolve()).as_posix()


def _safe_relative(text: str) -> str:
    return "/".join(part for part in text.replace("\\", "/").split("/") if part not in ("", ".", ".."))


def _next_index(steps: list[dict]) -> int:
    found = [int(step["index"]) for step in steps if isinstance(step, dict) and isinstance(step.get("index"), int)]
    return max(found, default=0) + 1


def _plain(value, depth: int = 0):
    if depth > 8:
        return "<truncated>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        items = [_plain(item, depth + 1) for item in list(value)[:200]]
        if len(value) > 200:
            items.append("<truncated>")
        return items
    if isinstance(value, dict):
        cleaned = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                cleaned["<truncated>"] = True
                break
            if isinstance(key, str):
                cleaned[key[:80]] = _plain(item, depth + 1)
        return cleaned
    return "<omitted>"


def _redact(value, root: Path, env_values: list[str], depth: int = 0):
    if depth > 8:
        return "<truncated>"
    if isinstance(value, dict):
        cleaned = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                cleaned["<truncated>"] = True
                break
            name = str(key)[:80]
            if _SECRET_NAME.search(name):
                cleaned[name] = "<redacted>"
            else:
                cleaned[name] = _redact(item, root, env_values, depth + 1)
        return cleaned
    if isinstance(value, list):
        items = [_redact(item, root, env_values, depth + 1) for item in value[:200]]
        if len(value) > 200:
            items.append("<truncated>")
        return items
    if isinstance(value, str):
        return _redact_text(value, root, env_values)
    return value


def _redact_text(text: str, root: Path, env_values: list[str]) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = text.replace("\x00", "")
    for secret in env_values:
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "<redacted>")
    cleaned = _TOKEN.sub("<redacted>", cleaned)
    cleaned = _URL.sub("<link>", cleaned)
    pieces = []
    for token in re.split(r"(\s+)", cleaned):
        if token == "" or token.isspace():
            pieces.append(token)
        else:
            pieces.append(_rewrite_token(token, root))
    cleaned = "".join(pieces)
    if len(cleaned) > _MAX_TEXT:
        cleaned = cleaned[:_MAX_TEXT] + "<truncated>"
    return cleaned


def _rewrite_token(token: str, root: Path) -> str:
    if token in {"<redacted>", "<link>", "<outside-project>", "<truncated>", "<omitted>"}:
        return token
    core = token.strip("\"'()[]{}<>,;")
    if core == token:
        prefix, suffix = "", ""
    else:
        start = token.find(core)
        prefix, suffix = token[:start], token[start + len(core) :]
    if not _looks_like_path(core):
        return token
    rewritten = _relative_or_outside(core, root)
    return prefix + rewritten + suffix


def _looks_like_path(text: str) -> bool:
    if not text or len(text) > 400 or any(mark in text for mark in "\n\r\t"):
        return False
    if text.startswith(("~", "/", "\\", "./", "../", ".\\", "..\\")):
        return True
    if session_path_escapes(text) or _trimmed_path_escapes(text):
        return True
    normalized = text.replace("\\", "/")
    return ".." in PurePosixPath(normalized).parts


def _trimmed_path_escapes(text: str) -> bool:
    """True when Win32's trimmed form of this text leaves the project.

    ``....\\....\\hidden`` is ``..\\..\\hidden`` after trailing dots are removed.
    A drive-relative path such as ``C:secret.txt`` is already an escape before
    that trim.
    """
    normalized = text.replace("\\", "/")
    trimmed = _path_after_windows_trim(text)
    if trimmed == normalized:
        return False
    return session_path_escapes(trimmed)


def _relative_or_outside(text: str, root: Path) -> str:
    normalized = text.replace("\\", "/")
    if ".." in PurePosixPath(normalized).parts or normalized.startswith("~"):
        return "<outside-project>"
    if _export_blocked(text, root) or _trimmed_path_escapes(text):
        return "<outside-project>"
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path(normalized)
    if not candidate.is_absolute():
        try:
            (root / candidate).resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            return "<outside-project>"
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            return "<outside-project>"
        return relative.as_posix()
    try:
        relative = candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return "<outside-project>"
    if ".." in relative.parts:
        return "<outside-project>"
    return relative.as_posix()


def _secret_env_values() -> list[str]:
    found = []
    for key, value in os.environ.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if len(value) < 8 or not _SECRET_NAME.search(key):
            continue
        found.append(value)
    found.sort(key=len, reverse=True)
    return found


def _number_list(value, places: int) -> list[float]:
    if not isinstance(value, list) or len(value) % places:
        raise GeometryError("A session mesh is not a picture of the model.")
    numbers = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise GeometryError("A session mesh is not a picture of the model.")
        number = float(item)
        if not math.isfinite(number):
            raise GeometryError("A session mesh is not a picture of the model.")
        numbers.append(number)
    return numbers


def _index_list(value) -> list[int]:
    if not isinstance(value, list) or len(value) % 3:
        raise GeometryError("A session mesh is not a picture of the model.")
    numbers = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise GeometryError("A session mesh is not a picture of the model.")
        numbers.append(int(item))
    return numbers


def _command_text(value) -> str:
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value.strip()):
        return value.strip()
    return "<omitted>"


def _time_text(value) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > 40 or any(mark in text for mark in "\n\r\t"):
        return ""
    return text


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _source_name(value) -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-z]{1,16}", value):
        return value
    return "api"


def _one_line(text: str, limit: int = 80) -> str:
    cleaned = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else " " for ch in text)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


def _overlay(root: Path, step: dict) -> str:
    index = step.get("index") if isinstance(step, dict) else ""
    summary = _text(step.get("summary")) if isinstance(step, dict) else ""
    if not summary and isinstance(step, dict):
        summary = _command_text(step.get("command"))
    cleaned = _redact_text(summary, root, _secret_env_values())
    return _one_line(f"{index}  {cleaned}", 80)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _opt_file() -> Path:
    from aicad.sandbox import config_dir

    return config_dir() / "session_record.json"


def _read_opt() -> dict:
    path = _opt_file()
    if not path.is_file() or path.is_symlink():
        return {"projects": {}}
    try:
        if path.stat().st_size > 1_000_000:
            return {"projects": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"projects": {}}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"projects": {}}
    return data


def _write_opt(data: dict) -> None:
    path = _opt_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"projects": data.get("projects", {})}, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)
