"""Command line for the same session the MCP server drives."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aicad.errors import AICADError


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "script_network", False):
        os.environ["AICAD_SCRIPT_NETWORK"] = "1"
    try:
        return _dispatch(args)
    except AICADError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aicad",
        description="AI-first CAD for hard-body parts. Units are millimeters.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--workspace",
        "-w",
        default=os.environ.get("AICAD_WORKSPACE", "aicad_workspace"),
        help="Directory that stores the session, renders, and imports "
        "(default: ./aicad_workspace or $AICAD_WORKSPACE).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve_cmd = commands.add_parser("serve", parents=[common], help="Run the MCP server on stdio.")
    serve_cmd.add_argument(
        "--connect",
        default=None,
        help="Forward every tool to a running workspace app, for example http://127.0.0.1:8765.",
    )
    serve_cmd.add_argument(
        "--script-network",
        action="store_true",
        help="Let design scripts use the network for this process. Off unless you pass this.",
    )

    app_cmd = commands.add_parser("app", parents=[common], help="Open the workspace app in a browser.")
    app_cmd.add_argument("--host", default="127.0.0.1")
    app_cmd.add_argument("--port", type=int, default=8765)
    app_cmd.add_argument("--no-browser", action="store_true", help="Serve without opening a browser.")
    app_cmd.add_argument(
        "--script-network",
        action="store_true",
        help="Let design scripts use the network for this process. Off unless you pass this.",
    )

    commands.add_parser(
        "allow-scripts",
        parents=[common],
        help="Allow design scripts in this project. The assistant cannot do this.",
    )

    run = commands.add_parser("run", parents=[common], help="Create or replace a part from a script.")
    run.add_argument("script", type=Path, help="Python file that assigns a solid to `part`.")
    run.add_argument("--name", "-n", required=True)

    ops = commands.add_parser(
        "ops", parents=[common], help="Create or replace a part from a JSON list of operations."
    )
    ops.add_argument("file", type=Path)
    ops.add_argument("--name", "-n", required=True)

    commands.add_parser("list", parents=[common], help="List parts in the document.")

    measure = commands.add_parser("measure", parents=[common], help="Print measurements as JSON.")
    measure.add_argument("name")

    render = commands.add_parser("render", parents=[common], help="Render standard views to PNG.")
    render.add_argument("name")

    export = commands.add_parser("export", parents=[common], help="Write STEP or STL.")
    export.add_argument("name")
    export.add_argument("--format", "-f", required=True, choices=["step", "stl"])
    export.add_argument("--out", "-o", type=Path)

    wall = commands.add_parser("check-wall", parents=[common], help="Estimate minimum wall thickness.")
    wall.add_argument("name")
    wall.add_argument("--min", dest="min_wall", type=float, required=True, help="Threshold in mm.")
    wall.add_argument("--samples", type=int, default=4)

    clearance = commands.add_parser(
        "check-clearance", parents=[common], help="Interference volume or gap between two parts."
    )
    clearance.add_argument("part_a")
    clearance.add_argument("part_b")
    clearance.add_argument(
        "--min",
        dest="min_clearance",
        type=float,
        default=0.0,
        help="Required gap in mm. 0 means the parts must not intersect.",
    )

    inspect = commands.add_parser(
        "inspect", parents=[common], help="Measure, check wall thickness, and render."
    )
    inspect.add_argument("name")
    inspect.add_argument("--min-wall", dest="min_wall", type=float, default=None)
    inspect.add_argument("--samples", type=int, default=4)

    imported = commands.add_parser("import-step", parents=[common], help="Copy a STEP file in as a part.")
    imported.add_argument("name")
    imported.add_argument("file", type=Path)

    features = commands.add_parser(
        "features", parents=[common], help="Report how much each boolean step contributed."
    )
    features.add_argument("name")

    section = commands.add_parser("section", parents=[common], help="Render a planar cross-section.")
    section.add_argument("name")
    section.add_argument("--axis", choices=("x", "y", "z"))
    section.add_argument("--offset", type=float, default=0.0)
    section.add_argument("--origin", help="x,y,z of a point on an arbitrary plane")
    section.add_argument("--normal", help="x,y,z normal of an arbitrary plane")

    catalog = commands.add_parser(
        "catalog", parents=[common], help="Search standard fasteners, nuts, washers, and bearings."
    )
    catalog.add_argument("query", nargs="?", default="")

    insert = commands.add_parser("insert", parents=[common], help="Insert a catalog part and place it.")
    insert.add_argument("name")
    insert.add_argument("--kind", required=True)
    insert.add_argument("--size", required=True)
    insert.add_argument("--length", type=float)
    insert.add_argument("--at", default="0,0,0", help="x,y,z position in mm")
    insert.add_argument("--rot", default="0,0,0", help="rotation degrees about X,Y,Z")

    place = commands.add_parser(
        "place", parents=[common], help="Move a part. Replaces any previous placement."
    )
    place.add_argument("name")
    place.add_argument("--at", required=True, help="x,y,z position in mm")
    place.add_argument("--rot", default="0,0,0", help="rotation degrees about X,Y,Z")

    sketch = commands.add_parser(
        "sketch", parents=[common], help="Solve a sketch, or create one from a JSON spec."
    )
    sketch.add_argument("name")
    sketch.add_argument("--file", type=Path, help="JSON spec. Creates the sketch, or replaces it.")

    dimension = commands.add_parser(
        "dimension", parents=[common], help="Change a named sketch dimension and rebuild parts."
    )
    dimension.add_argument("sketch")
    dimension.add_argument("dimension")
    dimension.add_argument("value", type=float)

    sketch_part = commands.add_parser(
        "sketch-part",
        parents=[common],
        help="Create or replace a solid from a solved sketch and a JSON step list.",
    )
    sketch_part.add_argument("name")
    sketch_part.add_argument("sketch")
    sketch_part.add_argument("--steps", type=Path, required=True)

    commands.add_parser("materials", parents=[common], help="List handbook materials for a linear static solve.")

    faces = commands.add_parser(
        "faces", parents=[common], help="Describe faces so a support or a load can name them."
    )
    faces.add_argument("name")

    assemble = commands.add_parser(
        "assemble", parents=[common], help="Create or replace an assembly from a JSON spec."
    )
    assemble.add_argument("name", help="Assembly name. It overrides the name in the file.")
    assemble.add_argument("--file", type=Path, required=True, help="JSON assembly spec.")

    joint = commands.add_parser("joint", parents=[common], help="Set one joint value.")
    joint.add_argument("name")
    joint.add_argument("joint")
    joint.add_argument("--value", type=float, required=True, help="Degrees or millimeters.")
    joint.add_argument("--slide", type=float, default=None, help="Millimeters along a cylindrical joint.")

    pose = commands.add_parser("pose", parents=[common], help="Apply a named pose.")
    pose.add_argument("name")
    pose.add_argument("pose")

    motion = commands.add_parser(
        "motion", parents=[common], help="Run a named motion, or a motion JSON file."
    )
    motion.add_argument("name")
    motion.add_argument("--sequence", default=None, help="Motion stored on the assembly.")
    motion.add_argument("--file", type=Path, default=None, help="JSON sweep or keyframes.")

    cables = commands.add_parser("cables", parents=[common], help="Check cables and hoses.")
    cables.add_argument("name")
    cables.add_argument("--sequence", default=None)

    power = commands.add_parser(
        "power", parents=[common], help="Estimate joint loads and actuator power."
    )
    power.add_argument("name")
    power.add_argument("--sequence", default=None)

    mechanics = commands.add_parser(
        "fea", parents=[common], help="Run a linear static solve from a JSON setup."
    )
    mechanics.add_argument("name")
    mechanics.add_argument("--file", type=Path, required=True, help="JSON with material, fixes, and loads.")

    modal = commands.add_parser(
        "modal", parents=[common], help="Natural frequencies of one part from a JSON setup."
    )
    modal.add_argument("name")
    modal.add_argument("--file", type=Path, required=True, help="JSON with material, fixes, and excitations.")

    modal_assembly = commands.add_parser(
        "modal-assembly", parents=[common], help="Natural frequencies of a posed assembly."
    )
    modal_assembly.add_argument("name")
    modal_assembly.add_argument("--file", type=Path, default=None, help="Optional JSON setup.")

    bolts = commands.add_parser(
        "bolts", parents=[common], help="Preload, slip, and vibration-loosening check."
    )
    bolts.add_argument("name", nargs="?", default=None, help="Library part whose size to use.")
    bolts.add_argument("--file", type=Path, required=True, help="JSON with grade, loads, and optional size.")

    fatigue = commands.add_parser(
        "fatigue", parents=[common], help="Goodman check of a stated stress amplitude."
    )
    fatigue.add_argument("--file", type=Path, required=True, help="JSON with material and stress_amplitude_mpa.")

    printed = commands.add_parser(
        "print", parents=[common], help="Overhang map, orientation, and printer fit."
    )
    printed.add_argument("name")
    printed.add_argument("--orientation", default="best", help="best, z_up, z_down, x_up, x_down, y_up, or y_down.")
    printed.add_argument("--printer", default=None, help="bambu_a1, prusa_mk4, ender_3, and the other known beds.")
    printed.add_argument("--threshold", type=float, default=45.0, help="Degrees from vertical before a face needs support.")
    printed.add_argument("--slice", action="store_true", help="Ask OrcaSlicer or PrusaSlicer for time and grams.")
    printed.add_argument("--price-per-kg", type=float, default=None, help="Used when the gcode has no filament cost.")

    robot = commands.add_parser(
        "robot", parents=[common], help="Export an assembly as URDF and MJCF."
    )
    robot.add_argument("name")
    robot.add_argument("--out", type=Path, default=None, help="Directory for the robot files.")

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "serve":
        from aicad.server import serve

        serve(args.workspace, connect=args.connect)
        return 0
    if args.command == "app":
        from aicad.app import serve_app

        serve_app(args.workspace, host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "allow-scripts":
        from aicad.sandbox import allow_design_scripts

        return _emit(allow_design_scripts(args.workspace))

    from aicad.document import Document

    document = Document(args.workspace)
    if args.command == "run":
        script = args.script.read_text(encoding="utf-8")
        info = _create_or_update_script(document, args.name, script)
        return _emit(info.to_dict())
    if args.command == "ops":
        ops = json.loads(args.file.read_text(encoding="utf-8"))
        info = _create_or_update_ops(document, args.name, ops)
        return _emit(info.to_dict())
    if args.command == "list":
        return _emit({"units": "mm", "parts": [part.to_dict() for part in document.list_parts()]})
    if args.command == "measure":
        return _emit(document.measure(args.name).to_dict())
    if args.command == "render":
        views = document.render(args.name)
        return _emit({"views": [view.to_dict() for view in views]})
    if args.command == "export":
        destination = args.out
        if destination is None:
            destination = document.workspace / "exports" / f"{args.name}.{args.format}"
        path = document.export(args.name, destination, args.format)
        return _emit({"format": args.format, "path": str(path), "bytes": path.stat().st_size})
    if args.command == "check-wall":
        report = document.check_wall_thickness(args.name, args.min_wall, args.samples)
        return _emit(report.to_dict())
    if args.command == "check-clearance":
        report = document.check_clearance(args.part_a, args.part_b, args.min_clearance)
        return _emit(report.to_dict())
    if args.command == "inspect":
        report = document.inspect(args.name, args.min_wall, args.samples)
        return _emit(report.to_dict())
    if args.command == "import-step":
        info = document.import_step(args.name, args.file)
        return _emit(info.to_dict())
    if args.command == "features":
        return _emit(document.feature_report(args.name).to_dict())
    if args.command == "section":
        report = document.section(args.name, _plane_from_args(args))
        return _emit(report.to_dict())
    if args.command == "catalog":
        return _emit(document.search_parts(args.query))
    if args.command == "insert":
        info = document.insert_standard_part(
            args.name,
            args.kind,
            args.size,
            length=args.length,
            position=_triple(args.at, "--at"),
            rotation_deg=_triple(args.rot, "--rot"),
        )
        return _emit(info.to_dict())
    if args.command == "place":
        info = document.place_part(args.name, _triple(args.at, "--at"), _triple(args.rot, "--rot"))
        return _emit(info.to_dict())
    if args.command == "sketch":
        if args.file is not None:
            spec = json.loads(args.file.read_text(encoding="utf-8"))
            if args.name in document.list_sketches():
                report = document.update_sketch(args.name, spec)
            else:
                report = document.create_sketch(args.name, spec)
        else:
            report = document.get_sketch(args.name)
        return _emit(report.to_dict())
    if args.command == "dimension":
        report = document.set_dimension(args.sketch, args.dimension, args.value)
        payload = report.to_dict()
        payload["rebuilt_parts"] = [
            part.to_dict()
            for part in document.list_parts()
            if document.get_source(part.name).get("sketch") == args.sketch
        ]
        return _emit(payload)
    if args.command == "sketch-part":
        steps = json.loads(args.steps.read_text(encoding="utf-8"))
        info = _create_or_update_sketch_part(document, args.name, args.sketch, steps)
        return _emit(info.to_dict())
    if args.command == "materials":
        return _emit(document.list_materials())
    if args.command == "faces":
        return _emit(document.describe_faces(args.name))
    if args.command == "assemble":
        spec = json.loads(args.file.read_text(encoding="utf-8"))
        spec["name"] = args.name
        names = {item["name"] for item in document.list_assemblies()}
        if args.name in names:
            return _emit(document.update_assembly(spec))
        return _emit(document.create_assembly(spec))
    if args.command == "joint":
        return _emit(document.set_joint(args.name, args.joint, args.value, args.slide))
    if args.command == "pose":
        return _emit(document.set_pose(args.name, args.pose))
    if args.command == "motion":
        inline = json.loads(args.file.read_text(encoding="utf-8")) if args.file else None
        return _emit(document.run_motion(args.name, sequence=args.sequence, motion=inline))
    if args.command == "cables":
        return _emit(document.check_cables(args.name, sequence=args.sequence))
    if args.command == "power":
        return _emit(document.estimate_power(args.name, sequence=args.sequence))
    if args.command == "fea":
        setup = json.loads(args.file.read_text(encoding="utf-8"))
        return _emit(document.solve_mechanics(args.name, setup).to_dict())
    if args.command == "modal":
        setup = json.loads(args.file.read_text(encoding="utf-8"))
        return _emit(document.solve_modal(args.name, setup))
    if args.command == "modal-assembly":
        setup = json.loads(args.file.read_text(encoding="utf-8")) if args.file else {}
        return _emit(document.solve_assembly_modal(args.name, setup))
    if args.command == "bolts":
        spec = json.loads(args.file.read_text(encoding="utf-8"))
        if args.name:
            spec["part"] = args.name
        return _emit(document.check_bolts(spec))
    if args.command == "fatigue":
        return _emit(document.check_fatigue(json.loads(args.file.read_text(encoding="utf-8"))))
    if args.command == "print":
        setup = {
            "orientation": args.orientation,
            "threshold_deg": args.threshold,
            "slice": bool(args.slice),
        }
        if args.printer:
            setup["printer"] = args.printer
        if args.price_per_kg is not None:
            setup["price_per_kg"] = args.price_per_kg
        return _emit(document.check_print(args.name, setup))
    if args.command == "robot":
        return _emit(document.export_robot(args.name, args.out))
    raise AICADError(f"Unknown command {args.command!r}.")


def _create_or_update_script(document, name: str, script: str):
    names = {part.name for part in document.list_parts()}
    if name in names:
        return document.update_part_script(name, script)
    return document.create_part_from_script(name, script)


def _create_or_update_sketch_part(document, name: str, sketch: str, steps: list[dict]):
    names = {part.name for part in document.list_parts()}
    if name in names:
        return document.update_part_from_sketch(name, sketch, steps)
    return document.create_part_from_sketch(name, sketch, steps)


def _create_or_update_ops(document, name: str, ops: list[dict]):
    names = {part.name for part in document.list_parts()}
    if name in names:
        return document.update_part_ops(name, ops)
    return document.create_part_from_ops(name, ops)


def _triple(text: str, label: str) -> list[float]:
    parts = text.split(",")
    if len(parts) != 3:
        raise AICADError(f"{label} must be three comma-separated numbers, such as 0,0,4.")
    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise AICADError(f"{label} must be three comma-separated numbers.") from exc


def _plane_from_args(args) -> dict:
    if args.origin or args.normal:
        if not args.origin or not args.normal:
            raise AICADError("An arbitrary section needs both --origin and --normal.")
        return {"origin": _triple(args.origin, "--origin"), "normal": _triple(args.normal, "--normal")}
    if not args.axis:
        raise AICADError("A section needs --axis (and optional --offset), or --origin and --normal.")
    return {"axis": args.axis, "offset": args.offset}


def _emit(payload: dict) -> int:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0
