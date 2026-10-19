"""Command line for the same session the MCP server drives."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aicad.errors import AICADError


def main(argv: list[str] | None = None) -> int:
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

    commands.add_parser("serve", parents=[common], help="Run the MCP server on stdio.")

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

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except AICADError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "serve":
        from aicad.server import serve

        serve(args.workspace)
        return 0

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
    raise AICADError(f"Unknown command {args.command!r}.")


def _create_or_update_script(document, name: str, script: str):
    names = {part.name for part in document.list_parts()}
    if name in names:
        return document.update_part_script(name, script)
    return document.create_part_from_script(name, script)


def _create_or_update_ops(document, name: str, ops: list[dict]):
    names = {part.name for part in document.list_parts()}
    if name in names:
        return document.update_part_ops(name, ops)
    return document.create_part_from_ops(name, ops)


def _emit(payload: dict) -> int:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0
