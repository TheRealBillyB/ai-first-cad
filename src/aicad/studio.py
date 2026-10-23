"""Project files, solid sizes, and point-to-point measure for the workspace.

The app, the chat, and MCP call these through the command layer. A project is
the folder on disk. Undo stays the history of the folder that is open. A size
is a number the solid was built from. A script or an imported STEP file has a
placement, not one of those numbers.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

from aicad import kernel, library
from aicad.errors import GeometryError
from aicad.models import round_mm

_RECENT_LIMIT = 8
_ACTIVITY_LIMIT = 12
_FOLDER_LIMIT = 200
# The viewport keeps coordinates in 32-bit floats. Past this, a normal part
# next to the edited one disappears. One kilometer is still a huge part.
_MAX_MM = 1_000_000
_MAX_ANGLE_DEG = 3600
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_FORBIDDEN_NAME = set('<>:"|?*')

# Commands that change the model or answer a check. Reads stay out of the feed.
_LOUD = {
    "undo",
    "redo",
    "open_project",
    "save_project",
    "save_project_as",
    "measure_points",
    "inspect_part",
    "section_part",
    "feature_report",
    "check_wall_thickness",
    "check_clearance",
    "check_packaging",
    "solve_mechanics",
    "solve_modal",
    "solve_assembly_modal",
    "check_bolts",
    "check_print",
    "check_fatigue",
    "check_cables",
    "estimate_power",
    "run_motion",
    "run_motion_study",
    "check_requirements",
}
# Undoable edits that are notes or requirements, not a shape the camera should chase.
_NO_FRAME = {
    "write_document",
    "delete_document",
    "set_requirements",
    "set_selection",
    "check_requirements",
    "save_project",
    "measure_points",
}

_PART_COLORS = (
    (196, 168, 130),
    (120, 156, 176),
    (176, 132, 112),
    (150, 166, 130),
    (168, 140, 160),
    (130, 148, 158),
)


def recent_path() -> Path:
    override = os.environ.get("AICAD_RECENT")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aicad" / "recent.json"


def remember_project(path: Path) -> None:
    """Put this folder at the front of the recent-project list."""
    folder = Path(path).resolve()
    if not folder.is_dir():
        return
    current = _read_recent()
    kept = [item for item in current if item.get("path") != str(folder)]
    kept.insert(0, {"path": str(folder), "name": folder.name})
    _write_recent(kept[:_RECENT_LIMIT])


def list_recent() -> dict:
    projects = []
    for item in _read_recent():
        folder = Path(str(item.get("path") or ""))
        if folder.is_dir():
            projects.append({"path": str(folder), "name": item.get("name") or folder.name})
    if len(projects) != len(_read_recent()):
        _write_recent(projects)
    return {
        "projects": projects,
        "note": "Recent projects are remembered on this computer, outside the project folder.",
    }


def list_folders(path: str | Path) -> dict:
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise GeometryError(f"No folder at {folder}.")
    folders = []
    try:
        children = sorted(folder.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise GeometryError(f"Could not read {folder}. {exc}") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        folders.append(
            {
                "name": child.name,
                "path": str(child.resolve()),
                "project": (child / "session.json").is_file(),
            }
        )
        if len(folders) >= _FOLDER_LIMIT:
            break
    parent = None if folder.parent == folder else str(folder.parent)
    return {
        "path": str(folder),
        "parent": parent,
        "folders": folders,
        "project": (folder / "session.json").is_file(),
        "truncated": len(folders) >= _FOLDER_LIMIT,
    }


def switch_project(workspace, path: Path) -> None:
    """Point the open workspace at another folder. The undo stack starts over."""
    document = workspace.document.__class__(path)
    workspace.document = document
    workspace.root = document.workspace
    workspace.documents = workspace.root / "documents"
    workspace.requirements_path = workspace.root / "requirements.json"
    workspace._undo.clear()
    workspace._redo.clear()
    workspace.selection = {"part": None, "faces": [], "edges": []}
    workspace.panels.clear()
    workspace._mesh_cache.clear()
    workspace._requirement_cache.clear()
    workspace._sketch_cache = []
    workspace._sketch_cache_revision = -1
    workspace._requirements = []
    workspace._activity.clear()
    workspace.geometry_revision += 1
    workspace._ensure_project_files()
    remember_project(workspace.root)


def save_project(workspace) -> dict:
    workspace.document._save()  # noqa: SLF001 — the session file is the project
    remember_project(workspace.root)
    return {"path": str(workspace.root), "name": workspace.root.name, "saved": True}


def save_project_as(workspace, path: str, name: str | None = None) -> dict:
    parent = Path(path).expanduser()
    if name is not None:
        destination = (parent / _folder_name(name)).resolve()
    else:
        destination = parent.resolve()
    source = workspace.root.resolve()
    if _same_folder(destination, source):
        raise GeometryError("Save as needs a different folder.")
    if _nested_folders(destination, source):
        raise GeometryError("Save the copy beside this project, not inside it.")
    if destination.is_file():
        raise GeometryError(f"{destination} is a file, not a folder.")
    if (destination / "session.json").is_file():
        raise GeometryError(f"{destination} already has a project. Pick a new folder.")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        _copy_project(source, destination)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise GeometryError(f"Could not save into {destination}. {reason}.") from exc
    switch_project(workspace, destination)
    return {"path": str(destination), "name": destination.name, "saved": True}


def open_project(workspace, path: str) -> dict:
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise GeometryError(f"No folder at {folder}.")
    # An existing project is only read. A new empty folder gets the note templates.
    new_folder = not (folder / "session.json").is_file()
    opened = workspace.open_project(folder)
    if new_folder:
        workspace._ensure_project_files()
    workspace._activity.clear()
    remember_project(workspace.root)
    names = []
    for part in opened.get("parts") or []:
        names.append(part["name"] if isinstance(part, dict) else part)
    return {
        "path": str(workspace.root),
        "name": workspace.root.name,
        "parts": names,
        "problems": list(opened.get("problems") or []),
    }


def list_parameters(workspace, name: str) -> dict:
    source = workspace.document.get_source(name)
    kind = str(source.get("source") or "")
    parameters: list[dict] = []
    note = "Change a size and the solid rebuilds. The placement is separate."
    if kind == "ops":
        parameters.extend(_op_parameters(source.get("ops") or []))
        if not parameters:
            note = "This solid has no sizes to slide. You can still move it."
    elif kind == "sketch":
        parameters.extend(_dimension_parameters(workspace, source.get("sketch")))
        parameters.extend(_step_parameters(source.get("steps") or []))
        if not parameters:
            note = "This sketch solid has no named size. You can still move it."
    elif kind == "library":
        spec = source.get("library") or {}
        if spec.get("length") is not None:
            parameters.append(
                {
                    "id": "library/length",
                    "label": "length",
                    "value": float(spec["length"]),
                    "unit": "mm",
                    "kind": "length",
                }
            )
        else:
            note = "This catalog part takes its size from the standard. You can move it."
    elif kind == "step":
        note = "This part is an imported STEP file. There is no size to slide. You can move it."
    else:
        note = "This part is a script. There is no size to slide. Move it, or replace the script."
    return {
        "part": name,
        "source": kind,
        "placement": source.get("placement") or {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]},
        "parameters": parameters,
        "note": note,
    }


def set_parameter(workspace, name: str, parameter: str, value: float) -> dict:
    if not isinstance(parameter, str) or not parameter:
        raise GeometryError("Name the parameter to change.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError("A parameter value has to be a number.") from exc
    if not math.isfinite(number):
        raise GeometryError("A parameter value has to be a finite number.")
    listed = list_parameters(workspace, name)
    known = {item["id"]: item for item in listed["parameters"]}
    if parameter not in known:
        choices = ", ".join(known) or "(none)"
        raise GeometryError(f"No parameter {parameter!r} on part {name!r}. Parameters: {choices}.")
    spec = known[parameter]
    if spec["kind"] == "angle":
        if abs(number) > _MAX_ANGLE_DEG:
            raise GeometryError(f"That angle is larger than {_MAX_ANGLE_DEG} degrees.")
    elif abs(number) > _MAX_MM:
        raise GeometryError(f"That size is larger than {_MAX_MM} mm.")
    if spec["kind"] == "length" and number <= 0:
        raise GeometryError(f"{spec['label']} has to be greater than zero.")
    source = workspace.document.get_source(name)
    placement = source.get("placement") or {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]}
    if parameter.startswith("dimension/"):
        dimension = parameter.split("/", 1)[1]
        sketch = source.get("sketch")
        if not isinstance(sketch, str):
            raise GeometryError(f"Part {name!r} has no sketch dimension to change.")
        workspace.document.set_dimension(sketch, dimension, number)
        rebuilt = _parts_from_sketch(workspace, sketch)
    elif parameter.startswith("op/"):
        ops = json.loads(json.dumps(source.get("ops") or []))
        _write_indexed(ops, parameter, "op", number)
        info = workspace.document.update_part_ops(name, ops)
        info = _restore_placement(workspace, name, placement, info)
        rebuilt = [info.to_dict()]
    elif parameter.startswith("step/"):
        steps = json.loads(json.dumps(source.get("steps") or []))
        _write_indexed(steps, parameter, "step", number)
        sketch = source.get("sketch")
        if not isinstance(sketch, str):
            raise GeometryError(f"Part {name!r} has no sketch steps to change.")
        info = workspace.document.update_part_from_sketch(name, sketch, steps)
        info = _restore_placement(workspace, name, placement, info)
        rebuilt = [info.to_dict()]
    elif parameter == "library/length":
        info = _set_library_length(workspace.document, name, number)
        rebuilt = [info.to_dict()]
    else:
        raise GeometryError(f"Parameter {parameter!r} is not editable.")
    return {
        "part": name,
        "parameter": parameter,
        "label": spec["label"],
        "value": number,
        "unit": spec["unit"],
        "rebuilt_parts": rebuilt,
    }


def measure_points(a, b, c=None) -> dict:
    first = _point(a, "a")
    second = _point(b, "b")
    delta = [second[index] - first[index] for index in range(3)]
    distance = math.sqrt(sum(component * component for component in delta))
    report = {
        "units": "mm",
        "a": [round_mm(component) for component in first],
        "b": [round_mm(component) for component in second],
        "delta_mm": [round_mm(component) for component in delta],
        "distance_mm": round_mm(distance),
        "text": f"{_trim(distance)} mm",
        "note": "Distance is the straight line between the two points.",
    }
    if c is not None:
        third = _point(c, "c")
        report["c"] = [round_mm(component) for component in third]
        toward_first = [first[index] - second[index] for index in range(3)]
        toward_third = [third[index] - second[index] for index in range(3)]
        angle = _angle_degrees(toward_first, toward_third)
        span = math.sqrt(sum(component * component for component in toward_third))
        report["distance_bc_mm"] = round_mm(span)
        if angle is None:
            report["note"] = "The second point is on top of another point, so there is no angle."
        else:
            report["angle_deg"] = angle
            report["text"] = f"{_trim(distance)} mm, {angle:g}° at the second point"
            report["note"] = (
                "Distance is from the first point to the second. "
                "The angle is at the second point, between the first and the third."
            )
    return report


def build_scene(workspace) -> dict:
    """One mesh of every part, so a move is visible against the others."""
    parts = list(workspace.document.list_parts())
    positions: list[float] = []
    normals: list[float] = []
    colors: list[float] = []
    triangle_face: list[int] = []
    triangle_part: list[str] = []
    faces: list[dict] = []
    edges: list[dict] = []
    names: list[str] = []
    unbuilt = [info.name for info in parts if not info.built]
    drawable = [info for info in parts if info.built]
    if not drawable:
        scene = _empty_scene()
        scene["unbuilt"] = unbuilt
        return scene
    for index, info in enumerate(drawable):
        mesh = workspace.mesh(info.name)
        color = _PART_COLORS[index % len(_PART_COLORS)]
        face_base = len(faces)
        names.append(info.name)
        positions.extend(mesh["positions"])
        normals.extend(mesh["normals"])
        for _vertex in range(len(mesh["positions"]) // 3):
            colors.extend(color)
        for face in mesh["faces"]:
            copied = dict(face)
            copied["part"] = info.name
            faces.append(copied)
        for local in mesh["triangle_face"]:
            triangle_face.append(face_base + int(local))
            triangle_part.append(info.name)
        for edge in mesh["edges"]:
            copied = dict(edge)
            copied["part"] = info.name
            edges.append(copied)
    mins = [min(positions[offset::3]) for offset in range(3)]
    maxs = [max(positions[offset::3]) for offset in range(3)]
    return {
        "part": None,
        "scene": True,
        "units": "mm",
        "parts": names,
        "positions": positions,
        "normals": normals,
        "colors": colors,
        "triangle_face": triangle_face,
        "triangle_part": triangle_part,
        "faces": faces,
        "edges": edges,
        "bounding_box": {
            "min": [round_mm(value) for value in mins],
            "max": [round_mm(value) for value in maxs],
        },
        "unbuilt": unbuilt,
    }


def note_for(name: str, arguments: dict | None, result: dict | None) -> dict:
    """A one-line description of a command, and whether live mode should frame it."""
    from aicad.workspace import COMMANDS

    arguments = arguments or {}
    result = result if isinstance(result, dict) else {}
    command = COMMANDS.get(name)
    record = bool(command and command.undo) or name in _LOUD
    parts = _affected_parts(name, arguments, result)
    summary = _summary(name, arguments, result, parts)
    frame = ""
    follow = False
    if record and name not in _NO_FRAME:
        if name in ("undo", "redo", "open_project") or not parts:
            if name in ("undo", "redo", "open_project"):
                follow = True
                frame = "scene"
        else:
            follow = True
            frame = "part"
    if name == "save_project_as":
        follow = True
        frame = "scene"
    return {
        "record": record,
        "entry": {"command": name, "summary": summary, "parts": parts},
        "event": {"summary": summary, "parts": parts if record else [], "follow": follow, "frame": frame},
    }


def register(add) -> None:
    from aicad.workspace import Command

    def _obj(properties: dict, required: list[str]) -> dict:
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def _string(description: str) -> dict:
        return {"type": "string", "description": description}

    def _number(description: str) -> dict:
        return {"type": "number", "description": description}

    add(Command(
        "open_project",
        "Open a project folder. The undo stack starts empty in that folder.",
        _obj({"path": _string("Folder that holds the project, or an empty folder for a new one.")}, ["path"]),
        lambda workspace, arguments: open_project(workspace, arguments["path"]),
    ))
    add(Command(
        "save_project",
        "Write the open project to its folder.",
        _obj({}, []),
        lambda workspace, _arguments: save_project(workspace),
    ))
    add(Command(
        "save_project_as",
        "Copy the project to a new folder and switch to that copy.",
        _obj(
            {
                "path": _string("Folder to copy into, or the parent folder when name is set."),
                "name": _string("New folder name inside path."),
            },
            ["path"],
        ),
        lambda workspace, arguments: save_project_as(workspace, arguments["path"], arguments.get("name")),
    ))
    add(Command(
        "list_recent_projects",
        "Folders opened most recently on this computer.",
        _obj({}, []),
        lambda _workspace, _arguments: list_recent(),
    ))
    add(Command(
        "list_folders",
        "List the folders inside a path, so a person can pick a project.",
        _obj({"path": _string("Folder to list. Omit it to list the open project.")}, []),
        lambda workspace, arguments: list_folders(arguments.get("path") or workspace.root),
    ))
    add(Command(
        "list_parameters",
        "Sizes on a solid that can be typed or slid: operation sizes, sketch dimensions, extrude heights, or a catalog length.",
        _obj({"name": _string("Part name.")}, ["name"]),
        lambda workspace, arguments: list_parameters(workspace, arguments["name"]),
    ))
    add(Command(
        "set_parameter",
        "Change one size on a solid and rebuild it. Placement stays where it was.",
        _obj(
            {
                "name": _string("Part name."),
                "parameter": _string("Parameter id from list_parameters, such as op/0/size.x or dimension/width."),
                "value": _number("New size, in millimeters, or degrees for an angle."),
            },
            ["name", "parameter", "value"],
        ),
        lambda workspace, arguments: set_parameter(
            workspace, arguments["name"], arguments["parameter"], arguments["value"]
        ),
        undo=True,
    ))
    add(Command(
        "measure_points",
        "Distance between two points, in millimeters. A third point adds the angle at the second point.",
        _obj(
            {
                "a": {"type": "array", "description": "First point [x, y, z] in millimeters."},
                "b": {"type": "array", "description": "Second point [x, y, z] in millimeters."},
                "c": {"type": "array", "description": "Optional third point. The angle is at b."},
            },
            ["a", "b"],
        ),
        lambda _workspace, arguments: measure_points(arguments["a"], arguments["b"], arguments.get("c")),
    ))


def register_mcp(mcp, run, guard) -> None:
    """Thin MCP wrappers. The work happens in the command layer."""

    @mcp.tool()
    def open_project(path: str) -> dict:
        """Open a project folder. The running app switches to that folder.

        The undo stack starts empty. The folder can be an existing project or an
        empty folder for a new one.
        """
        return guard(lambda: run("open_project", path=path))

    @mcp.tool()
    def save_project() -> dict:
        """Write the open project to its folder."""
        return guard(lambda: run("save_project"))

    @mcp.tool()
    def save_project_as(path: str, name: str | None = None) -> dict:
        """Copy the project to a new folder and switch to the copy.

        Pass path as the new folder, or path as the parent and name as the new
        folder name. A folder that already has a project is refused.
        """
        return guard(lambda: run("save_project_as", path=path, name=name))

    @mcp.tool()
    def list_folders(path: str | None = None) -> dict:
        """List the folders inside a path so a project folder can be chosen.

        Omit path to list the folder that is open. Names starting with a dot are skipped.
        """
        return guard(lambda: run("list_folders", path=path))

    @mcp.tool()
    def list_recent_projects() -> dict:
        """Folders opened most recently on this computer."""
        return guard(lambda: run("list_recent_projects"))

    @mcp.tool()
    def list_parameters(name: str) -> dict:
        """Sizes on a solid that set_parameter can change.

        An operation box exposes size and center. A cylinder exposes radius and
        height. A sketch solid exposes named dimensions and the extrude height
        or revolve angle. A screw exposes length. A script or a STEP file has
        no size here; use place_part to move it.
        """
        return guard(lambda: run("list_parameters", name=name))

    @mcp.tool()
    def set_parameter(name: str, parameter: str, value: float) -> dict:
        """Change one size and rebuild the solid. The part stays where it was placed.

        parameter is an id from list_parameters. value is millimeters, or degrees
        for an angle. A bad value leaves the previous solid in place.
        """
        return guard(lambda: run("set_parameter", name=name, parameter=parameter, value=value))

    @mcp.tool()
    def measure_points(a: list[float], b: list[float], c: list[float] | None = None) -> dict:
        """Distance between two points, in millimeters.

        a and b are [x, y, z]. Pass c to also get the angle at b, in degrees,
        between a and c. The points are coordinates, so this works without a click.
        """
        return guard(lambda: run("measure_points", a=a, b=b, c=c))


def _read_recent() -> list[dict]:
    path = recent_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    projects = payload.get("projects") if isinstance(payload, dict) else None
    if not isinstance(projects, list):
        return []
    return [item for item in projects if isinstance(item, dict) and item.get("path")]


def _write_recent(projects: list[dict]) -> None:
    path = recent_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"projects": projects}, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def _copy_project(source: Path, destination: Path) -> None:
    for name in ("session.json", "requirements.json"):
        file = source / name
        if file.is_file():
            shutil.copy2(file, destination / name)
    for folder in ("documents", "imports"):
        origin = source / folder
        if origin.is_dir():
            shutil.copytree(origin, destination / folder, dirs_exist_ok=True)
    # A script part is stored as text plus a STEP solid. Opening the copy
    # must not run the script, so the solid has to travel with the folder.
    geometry = source / ".aicad" / "geometry"
    if geometry.is_dir():
        shutil.copytree(geometry, destination / ".aicad" / "geometry", dirs_exist_ok=True)
    log = source / ".aicad" / "script_log.jsonl"
    if log.is_file():
        target = destination / ".aicad" / "script_log.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log, target)


def reject_placement(position, rotation_deg=None) -> None:
    """Refuse a move the viewport cannot draw next to a normal part."""
    _bounded_vector(position, "That position", _MAX_MM, "mm")
    if rotation_deg is not None:
        _bounded_vector(rotation_deg, "That rotation", _MAX_ANGLE_DEG, "degrees")


def _bounded_vector(value, label: str, limit: float, unit: str) -> None:
    if not isinstance(value, (list, tuple)):
        return
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return
        if not math.isfinite(number):
            raise GeometryError(f"{label} has to be a finite number.")
        if abs(number) > limit:
            shown = int(limit) if limit == int(limit) else limit
            raise GeometryError(f"{label} is larger than {shown} {unit}.")


def _same_folder(left: Path, right: Path) -> bool:
    """True when two paths are one folder, including a Windows case difference."""
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _nested_folders(left: Path, right: Path) -> bool:
    """True when one folder is inside the other. Case matches Windows."""
    left_key = os.path.normcase(str(left))
    right_key = os.path.normcase(str(right))
    if left_key == right_key:
        return False
    return left_key.startswith(right_key + os.sep) or right_key.startswith(left_key + os.sep)


def _folder_name(name: str) -> str:
    """A new folder name that Windows can create as well as Linux and macOS."""
    if not isinstance(name, str) or not name.strip() or name.strip() in {".", ".."}:
        raise GeometryError("Type a folder name.")
    if name != name.strip() or name.endswith("."):
        raise GeometryError("The folder name cannot start or end with a space or a dot.")
    if any(ord(character) < 32 or character in _FORBIDDEN_NAME or character in "/\\" for character in name):
        raise GeometryError("The folder name cannot contain a slash or any of : * ? \" < > |.")
    stem = name.split(".", 1)[0].upper()
    reserved = stem in _WINDOWS_RESERVED or (
        len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789"
    )
    if reserved:
        raise GeometryError(f"{name} is reserved on Windows. Pick another folder name.")
    return name


def _op_parameters(ops: list) -> list[dict]:
    parameters = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        label = str(op.get("name") or op.get("shape") or f"step {index + 1}")
        shape = op.get("shape")
        if shape == "box":
            parameters.extend(_vector_parameters(f"op/{index}", label, "size", op.get("size"), "length"))
        elif shape == "cylinder":
            parameters.append(_scalar(f"op/{index}/radius", f"{label} radius", op.get("radius"), "length"))
            parameters.append(_scalar(f"op/{index}/height", f"{label} height", op.get("height"), "length"))
        parameters.extend(_vector_parameters(f"op/{index}", label, "center", op.get("center") or [0, 0, 0], "offset"))
    return [item for item in parameters if item is not None]


def _step_parameters(steps: list) -> list[dict]:
    parameters = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        label = str(step.get("name") or step.get("op") or f"step {index + 1}")
        op = step.get("op")
        prefix = f"step/{index}"
        if op in ("extrude", "cut"):
            parameters.append(_scalar(f"{prefix}/height", f"{label} height", step.get("height"), "length"))
            parameters.append(_scalar(f"{prefix}/z", f"{label} z", step.get("z", 0), "offset"))
        elif op == "revolve":
            parameters.append(_scalar(f"{prefix}/angle", f"{label} angle", step.get("angle", 360), "angle"))
        elif op == "cut_box":
            parameters.extend(_vector_parameters(prefix, label, "size", step.get("size"), "length"))
            parameters.extend(_vector_parameters(prefix, label, "center", step.get("center") or [0, 0, 0], "offset"))
        elif op == "gusset":
            for field, kind in (
                ("angle", "angle"),
                ("outer", "length"),
                ("plate_thickness", "length"),
                ("boss_height", "length"),
            ):
                if field in step:
                    parameters.append(_scalar(f"{prefix}/{field}", f"{label} {field.replace('_', ' ')}", step.get(field), kind))
    return [item for item in parameters if item is not None]


def _dimension_parameters(workspace, sketch_name) -> list[dict]:
    if not isinstance(sketch_name, str):
        return []
    spec = workspace.document._get_sketch(sketch_name).spec  # noqa: SLF001
    parameters = []
    for constraint in spec.get("constraints") or []:
        if not isinstance(constraint, dict) or not constraint.get("name"):
            continue
        if "value" in constraint:
            kind = "angle" if constraint.get("type") == "angle" else "length"
            value = constraint["value"]
        elif "diameter" in constraint:
            kind = "length"
            value = constraint["diameter"]
        else:
            continue
        unit = "deg" if kind == "angle" else "mm"
        parameters.append(
            {
                "id": f"dimension/{constraint['name']}",
                "label": str(constraint["name"]),
                "value": float(value),
                "unit": unit,
                "kind": kind,
            }
        )
    return parameters


def _scalar(identifier: str, label: str, value, kind: str) -> dict | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return {
        "id": identifier,
        "label": label,
        "value": number,
        "unit": "deg" if kind == "angle" else "mm",
        "kind": kind,
    }


def _vector_parameters(prefix: str, label: str, field: str, value, kind: str) -> list[dict]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return []
    parameters = []
    for axis, component in zip(("x", "y", "z"), value, strict=True):
        item = _scalar(f"{prefix}/{field}.{axis}", f"{label} {field} {axis}", component, kind)
        if item is not None:
            parameters.append(item)
    return parameters


def _write_indexed(records: list, parameter: str, prefix: str, value: float) -> None:
    parts = parameter.split("/")
    if len(parts) != 3 or parts[0] != prefix:
        raise GeometryError(f"Parameter {parameter!r} is not a {prefix} size.")
    try:
        index = int(parts[1])
    except ValueError as exc:
        raise GeometryError(f"Parameter {parameter!r} does not name a step.") from exc
    if index < 0 or index >= len(records) or not isinstance(records[index], dict):
        raise GeometryError(f"Parameter {parameter!r} is outside this part.")
    field = parts[2]
    record = records[index]
    if "." in field:
        key, axis = field.split(".", 1)
        if axis not in ("x", "y", "z"):
            raise GeometryError(f"Parameter {parameter!r} is not a size.")
        current = list(record.get(key) or [0, 0, 0])
        if len(current) != 3:
            raise GeometryError(f"{key} on this step is not three numbers.")
        current["xyz".index(axis)] = value
        record[key] = current
        return
    record[field] = value


def _restore_placement(workspace, name: str, placement: dict, info):
    position = placement.get("position") or [0, 0, 0]
    rotation = placement.get("rotation_deg") or [0, 0, 0]
    if any(abs(float(component)) > 1e-12 for component in list(position) + list(rotation)):
        return workspace.document.place_part(name, position, rotation)
    return info


def _set_library_length(document, name: str, length: float):
    with document._lock:
        part = document._get(name)
        spec = dict(part.library_spec or {})
        spec["length"] = float(length)
        base = kernel.adopt(library.build(spec["kind"], spec["size"], spec["length"], spec.get("fastener_type")))
        part.library_spec = spec
        part.base = base
        part.shape = kernel.place(
            base,
            tuple(part.placement["position"]),
            tuple(part.placement["rotation_deg"]),
        )
        document._save()
        return document._info(part)


def _parts_from_sketch(workspace, sketch: str) -> list[dict]:
    rebuilt = []
    for part in workspace.document.list_parts():
        source = workspace.document.get_source(part.name)
        if source.get("sketch") == sketch:
            rebuilt.append(part.to_dict())
    return rebuilt


def _point(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be a list of three numbers.")
    try:
        point = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a list of three numbers.") from exc
    if not all(math.isfinite(component) for component in point):
        raise GeometryError(f"{label} must be a list of three numbers.")
    return point


def _angle_degrees(first: list[float], second: list[float]) -> float | None:
    first_length = math.sqrt(sum(component * component for component in first))
    second_length = math.sqrt(sum(component * component for component in second))
    if first_length < 1e-9 or second_length < 1e-9:
        return None
    cosine = sum(a * b for a, b in zip(first, second, strict=True)) / (first_length * second_length)
    cosine = max(-1.0, min(1.0, cosine))
    return round(math.degrees(math.acos(cosine)), 2)


def _trim(value: float) -> str:
    text = f"{round_mm(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _affected_parts(name: str, arguments: dict, result: dict) -> list[str]:
    parts: list[str] = []
    if isinstance(arguments.get("name"), str) and name not in ("read_document", "write_document", "delete_document"):
        parts.append(arguments["name"])
    part = result.get("part")
    if isinstance(part, dict) and isinstance(part.get("name"), str):
        parts.append(part["name"])
    elif isinstance(part, str):
        parts.append(part)
    for item in result.get("rebuilt_parts") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            parts.append(item["name"])
    if isinstance(arguments.get("sketch"), str) and name == "set_dimension":
        parts.append(arguments["sketch"])
    ordered = []
    for item in parts:
        if item not in ordered:
            ordered.append(item)
    return ordered


def _summary(name: str, arguments: dict, result: dict, parts: list[str]) -> str:
    part = parts[0] if parts else "the part"
    if name == "place_part":
        position = arguments.get("position")
        if isinstance(position, (list, tuple)) and len(position) == 3:
            try:
                text = ", ".join(f"{float(component):g}" for component in position)
            except (TypeError, ValueError):
                text = ""
            if text:
                return f"Moved {part} to {text}"
        return f"Moved {part}"
    if name == "set_parameter":
        label = result.get("label") or arguments.get("parameter") or "a size"
        value = result.get("value", arguments.get("value"))
        unit = result.get("unit") or ""
        try:
            shown = f"{float(value):g}"
        except (TypeError, ValueError):
            shown = str(value)
        return f"Set {label} on {part} to {shown} {unit}".rstrip()
    if name == "set_dimension":
        return f"Set {arguments.get('dimension')} to {arguments.get('value')}"
    if name == "measure_points":
        return f"Measured {result.get('text') or result.get('distance_mm')}"
    if name == "open_project":
        return f"Opened {result.get('name') or arguments.get('path')}"
    if name == "save_project":
        return f"Saved {result.get('name') or 'the project'}"
    if name == "save_project_as":
        return f"Saved a copy as {result.get('name') or 'a new folder'}"
    if name == "undo":
        return "Undid the last change"
    if name == "redo":
        return "Redid the last change"
    verbs = {
        "create_part_from_script": f"Created {part} from a script",
        "create_part_from_ops": f"Created {part} from shapes",
        "create_part_from_sketch": f"Built {part} from a sketch",
        "update_part_script": f"Rebuilt {part} from a script",
        "update_part_ops": f"Rebuilt {part} from shapes",
        "update_part_from_sketch": f"Rebuilt {part} from a sketch",
        "delete_part": f"Deleted {part}",
        "insert_standard_part": f"Inserted {part}",
        "import_step": f"Imported {part}",
        "inspect_part": f"Inspected {part}",
        "section_part": f"Sectioned {part}",
        "create_sketch": f"Created sketch {arguments.get('name')}",
        "update_sketch": f"Updated sketch {arguments.get('name')}",
        "set_joint": f"Set joint {arguments.get('joint')} to {arguments.get('value')}",
        "set_pose": f"Applied pose {arguments.get('pose')}",
        "run_motion": f"Ran motion on {part}",
        "write_document": f"Wrote {arguments.get('name')}",
        "delete_document": f"Deleted note {arguments.get('name')}",
        "check_clearance": "Checked clearance",
        "check_packaging": "Checked packaging",
        "solve_mechanics": f"Solved the strength of {part}",
        "check_print": f"Checked the print of {part}",
    }
    if name in verbs:
        return verbs[name]
    text = name.replace("_", " ")
    if parts and name not in ("write_document", "delete_document", "read_document"):
        return f"{text[:1].upper()}{text[1:]}: {part}"
    return text[:1].upper() + text[1:]


def _empty_scene() -> dict:
    return {
        "part": None,
        "scene": True,
        "units": "mm",
        "parts": [],
        "positions": [],
        "normals": [],
        "colors": [],
        "triangle_face": [],
        "triangle_part": [],
        "faces": [],
        "edges": [],
        "bounding_box": {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]},
        "unbuilt": [],
    }
