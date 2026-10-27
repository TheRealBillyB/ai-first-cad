"""Command line for the same session the MCP server drives."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aicad.errors import AICADError, GeometryError


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
    except (OSError, ValueError) as exc:
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
    wall.add_argument(
        "--min",
        "--min-wall",
        dest="min_wall",
        type=float,
        required=True,
        help="Threshold in mm. --min-wall is the same flag inspect uses.",
    )
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

    packaging = commands.add_parser(
        "check-packaging",
        parents=[common],
        help="Check that parts fit inside an envelope, with wall clearance and overlap.",
    )
    packaging.add_argument(
        "--envelope",
        required=True,
        help="Part name, or a JSON primitive or outline.",
    )
    packaging.add_argument(
        "--parts",
        default=None,
        help="Comma-separated part or instance names. With an assembly, the default is every instance except the envelope.",
    )
    packaging.add_argument("--assembly", default=None, help="Check the posed instances of this assembly.")
    packaging.add_argument("--pose", default=None, help="Named pose. The stored assembly is left unchanged.")
    packaging.add_argument(
        "--joints",
        default=None,
        help="JSON object of joint values for this check only. Degrees, millimeters, or {value, slide} for a cylindrical joint.",
    )
    packaging.add_argument(
        "--min",
        dest="min_clearance",
        type=float,
        default=0.0,
        help="Required gap to the envelope wall, in mm. 0 allows a part to touch the wall.",
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
        "catalog",
        parents=[common],
        help="Search bearings, seals, fasteners, motors, and gearboxes.",
    )
    catalog.add_argument("query", nargs="?", default="")

    insert = commands.add_parser(
        "insert", parents=[common], help="Insert a catalog part by designation and place it."
    )
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

    motion_study = commands.add_parser(
        "motion-study",
        parents=[common],
        help="Drive joints over time and report collisions and joint-limit violations.",
    )
    motion_study.add_argument("name")
    motion_study.add_argument("--sequence", default=None, help="Study stored on the assembly.")
    motion_study.add_argument("--file", type=Path, default=None, help="JSON study of drivers and couplings.")
    motion_study.add_argument("--steps", type=int, default=None, help="Poses to solve, including both ends.")
    motion_study.add_argument(
        "--clearance",
        type=float,
        default=None,
        help="Minimum allowed gap in millimeters. A smaller gap is interference.",
    )

    kinematics = commands.add_parser(
        "kinematics",
        parents=[common],
        help="Solve forward or inverse kinematics, or step a tool path.",
    )
    kinematics.add_argument("name")
    kinematics.add_argument(
        "--file",
        type=Path,
        required=True,
        help="JSON request: tool, optional at, joints, target, or path.",
    )

    error_budget = commands.add_parser(
        "error-budget",
        parents=[common],
        help="Tip position error of a serial chain against a tolerance.",
    )
    error_budget.add_argument("name")
    error_budget.add_argument(
        "--file",
        type=Path,
        required=True,
        help="JSON request: tool, tolerance_mm, sources, links, poses, or path.",
    )

    cables = commands.add_parser("cables", parents=[common], help="Check cables, hoses, and tendon routes.")
    cables.add_argument("name")
    cables.add_argument(
        "--sequence",
        default=None,
        help="Motion, or a motion study when no motion has this name.",
    )
    cables.add_argument("--study", default=None, help="Motion study stored on the assembly.")
    cables.add_argument("--file", type=Path, default=None, help="JSON motion study.")
    cables.add_argument("--steps", type=int, default=None, help="Poses to solve, including both ends.")

    power = commands.add_parser(
        "power", parents=[common], help="Estimate joint loads and actuator power."
    )
    power.add_argument("name")
    power.add_argument(
        "--sequence",
        default=None,
        help="Motion, or a motion study when no motion has this name.",
    )

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
    printed.add_argument(
        "--bridge",
        type=float,
        default=None,
        help="Span in millimeters that still counts as a bridge. 0 counts every overhang.",
    )
    printed.add_argument("--slice", action="store_true", help="Ask OrcaSlicer or PrusaSlicer for time and grams.")
    printed.add_argument("--price-per-kg", type=float, default=None, help="Used when the gcode has no filament cost.")

    robot = commands.add_parser(
        "robot", parents=[common], help="Export an assembly as URDF and MJCF."
    )
    robot.add_argument("name")
    robot.add_argument("--out", type=Path, default=None, help="Directory for the robot files.")

    drawing = commands.add_parser(
        "export-drawing",
        parents=[common],
        help="Write a dimensioned third-angle sheet of an assembly as PDF and DXF.",
    )
    drawing.add_argument("name")
    drawing.add_argument("--scale", default=None, help="Sheet scale, such as 1:2 or 2:1. Omit to fit the sheet.")
    drawing.add_argument(
        "--exploded",
        action="store_true",
        help="Draw the isometric view with the parts pulled apart.",
    )
    drawing.add_argument("--gap", type=float, default=None, help="Millimeters each joint opens in an exploded view.")
    drawing.add_argument("--out", type=Path, default=None, help="Directory for the PDF and DXF.")

    bom = commands.add_parser(
        "bill-of-materials",
        parents=[common],
        help="Count parts, catalog fasteners, and hoses, and write a CSV.",
    )
    bom.add_argument("name")
    bom.add_argument("--out", type=Path, default=None, help="CSV path. The default is exports/bom/<name>.csv.")

    exploded = commands.add_parser(
        "exploded-view",
        parents=[common],
        help="Render the assembly with parts pulled apart along the joints.",
    )
    exploded.add_argument("name")
    exploded.add_argument("--gap", type=float, default=None, help="Millimeters each joint opens. Omit to size it from the assembly.")
    exploded.add_argument(
        "--on-drawing",
        action="store_true",
        help="Also put that exploded isometric on the dimensioned sheet.",
    )
    exploded.add_argument("--out", type=Path, default=None, help="Directory for the sheet, used with --on-drawing.")

    animate = commands.add_parser(
        "animate",
        parents=[common],
        help="Write a GIF of a motion study, a camera orbit, or an exploded opening.",
    )
    animate.add_argument("name")
    animate.add_argument("--kind", choices=["motion", "orbit", "exploded"], default=None)
    animate.add_argument("--sequence", default=None, help="Study stored on the assembly.")
    animate.add_argument("--file", type=Path, default=None, help="JSON motion study.")
    animate.add_argument("--frames", type=int, default=None, help="Pictures, including both ends.")
    animate.add_argument("--fps", type=int, default=None, help="Playback frames per second.")
    animate.add_argument("--duration", dest="duration_s", type=float, default=None, help="Playback length in seconds when --frames is omitted.")
    animate.add_argument("--width", type=int, default=None, help="Picture width in pixels. Even, from 16 to 1280.")
    animate.add_argument("--height", type=int, default=None, help="Picture height in pixels. Even, from 16 to 1280.")
    animate.add_argument(
        "--camera",
        default=None,
        help="iso, front, back, left, right, top, bottom, or orbit.",
    )
    animate.add_argument(
        "--camera-file",
        type=Path,
        default=None,
        help="JSON list of {azimuth_deg, elevation_deg} points.",
    )
    animate.add_argument("--turns", type=float, default=None, help="How many times an orbit goes around.")
    animate.add_argument("--elevation", dest="elevation_deg", type=float, default=None, help="Orbit elevation in degrees.")
    animate.add_argument("--background", default=None, help="paper, white, black, or #rrggbb.")
    animate.add_argument("--colors", type=Path, default=None, help="JSON map of instance name to a color.")
    animate.add_argument("--materials", type=Path, default=None, help="JSON map of instance name to a material.")
    animate.add_argument("--overlay", choices=["none", "step", "time", "value"], default=None)
    animate.add_argument("--joint", default=None, help="Joint drawn when the overlay is value.")
    animate.add_argument("--gap", type=float, default=None, help="Millimeters each joint opens in an exploded animation.")
    animate.add_argument(
        "--video",
        choices=["gif", "mp4", "webm", "both"],
        default=None,
        help="gif writes only the GIF. mp4 and webm need ffmpeg.",
    )
    animate.add_argument("--out", type=Path, default=None, help="A .gif, .mp4, or .webm file, or a directory, inside the project.")

    start_plan = commands.add_parser(
        "start-plan", parents=[common], help="Start a plan. Optional JSON bundle of research, sheets, and questions."
    )
    start_plan.add_argument("name")
    start_plan.add_argument("--title", default=None)
    start_plan.add_argument("--summary", default="")
    start_plan.add_argument("--file", type=Path, default=None, help="JSON bundle. Data only. It is not executed.")

    commands.add_parser("list-plans", parents=[common], help="List plans, status, and revision.")

    get_plan = commands.add_parser("get-plan", parents=[common], help="Print one plan, including revisions and sheet paths.")
    get_plan.add_argument("name")

    research = commands.add_parser(
        "add-plan-research", parents=[common], help="Add a research note. A source is required."
    )
    research.add_argument("name")
    research.add_argument("--note", required=True)
    research.add_argument("--source", required=True)
    research.add_argument("--title", default="")

    assumption = commands.add_parser("add-plan-assumption", parents=[common], help="Add an assumption for the person to mark.")
    assumption.add_argument("name")
    assumption.add_argument("--text", required=True)

    question = commands.add_parser("add-plan-question", parents=[common], help="Add an open question for the person to answer.")
    question.add_argument("name")
    question.add_argument("--text", required=True)

    sheet = commands.add_parser(
        "put-plan-sheet", parents=[common], help="Draw or replace one blueprint sheet from a JSON spec."
    )
    sheet.add_argument("name")
    sheet.add_argument("--file", type=Path, required=True)

    dimensions = commands.add_parser(
        "set-plan-dimensions", parents=[common], help="Set the named millimeters modeling will use after approval."
    )
    dimensions.add_argument("name")
    dimensions.add_argument("--file", type=Path, required=True, help="JSON list, or an object with a dimensions list.")

    comment = commands.add_parser("comment-on-plan", parents=[common], help="Comment on the plan or on one item.")
    comment.add_argument("name")
    comment.add_argument("--item", default="plan")
    comment.add_argument("--text", required=True)

    answer = commands.add_parser(
        "answer-plan-question", parents=[common], help="Answer a question. This is the person's command, not the agent's."
    )
    answer.add_argument("name")
    answer.add_argument("item")
    answer.add_argument("--text", required=True)

    mark = commands.add_parser(
        "mark-plan-item",
        parents=[common],
        help="Accept or reject an assumption, or waive a question. The person's command.",
    )
    mark.add_argument("name")
    mark.add_argument("item")
    mark.add_argument("--mark", required=True, choices=["open", "accepted", "rejected", "waived"])

    submit = commands.add_parser("submit-plan", parents=[common], help="Move a draft to in review. This does not approve it.")
    submit.add_argument("name")

    revise = commands.add_parser(
        "revise-plan", parents=[common], help="Open the next revision and record what changed."
    )
    revise.add_argument("name")
    revise.add_argument("--changed", required=True)
    revise.add_argument("--file", type=Path, default=None, help="Optional JSON bundle applied to the new revision.")

    approve = commands.add_parser(
        "approve-plan", parents=[common], help="Approve the current revision. Only you can do this."
    )
    approve.add_argument("name")

    model_from = commands.add_parser(
        "start-model-from-plan",
        parents=[common],
        help="Release modeling from an approved plan. Refuses until you have approved it.",
    )
    model_from.add_argument("name")

    simulate_from = commands.add_parser(
        "start-simulation-from-plan",
        parents=[common],
        help="Release simulation from an approved plan. Refuses until you have approved it.",
    )
    simulate_from.add_argument("name")

    gear = commands.add_parser(
        "gear",
        parents=[common],
        help="Create or replace a spur, helical, bevel, or hypoid gear.",
    )
    gear.add_argument("name")
    gear.add_argument("--file", type=Path, default=None, help="JSON gear spec. Flags override it.")
    gear.add_argument("--type", default=None, help="spur, helical, straight_bevel, spiral_bevel, or hypoid.")
    gear.add_argument("--module", type=float, default=None, help="Normal module, or outer transverse module for a bevel.")
    gear.add_argument("--teeth", type=int, default=None)
    gear.add_argument("--pressure-angle", type=float, default=None)
    gear.add_argument("--helix-angle", type=float, default=None, help="Degrees. Positive is right-hand.")
    gear.add_argument("--spiral-angle", type=float, default=None, help="Mean spiral angle, degrees. Positive is right-hand.")
    gear.add_argument("--face-width", type=float, default=None)
    gear.add_argument("--profile-shift", type=float, default=None)
    gear.add_argument("--shaft-angle", type=float, default=None)
    gear.add_argument("--mate-teeth", type=int, default=None)
    gear.add_argument("--bore", type=float, default=None)
    gear.add_argument("--rack", default=None, help="ISO 53 basic rack A, B, C, or D.")
    gear.add_argument("--hand", default=None, choices=["right", "left"])
    gear.add_argument("--offset", type=float, default=None, help="Hypoid pinion offset in mm. Positive puts the pinion axis on +Y.")
    gear.add_argument("--ratio", type=float, default=None, help="Ring teeth divided by pinion teeth.")
    gear.add_argument("--ring-diameter", type=float, default=None, help="Hypoid ring mean pitch diameter in mm.")
    gear.add_argument("--role", default=None, choices=["pinion", "ring"], help="Which member of a hypoid pair this gear is.")
    gear.add_argument("--at", default=None, help="Position x,y,z in mm.")
    gear.add_argument("--rot", default=None, help="Rotation about X, Y, Z in degrees.")

    mesh = commands.add_parser(
        "check-gears",
        parents=[common],
        help="Centre distance, backlash, contact ratio, and interference for a gear pair.",
    )
    mesh.add_argument("pinion", nargs="?", default=None, help="Gear spec name, or omit when --file or --assembly is set.")
    mesh.add_argument("gear", nargs="?", default=None, help="Mate gear.")
    mesh.add_argument("--file", type=Path, default=None, help="JSON spec. Flags override it.")
    mesh.add_argument("--assembly", default=None, help="Check every gear pair in this assembly.")
    mesh.add_argument("--centre", type=float, default=None, help="Actual centre distance, or a signed bevel axis offset.")
    mesh.add_argument("--shaft-angle", type=float, default=None, help="Actual shaft angle in degrees.")
    mesh.add_argument("--offset", type=float, default=None, help="Axis offset in millimetres. For a hypoid pair this is the pinion offset.")
    mesh.add_argument("--backlash-min", type=float, default=None, help="Minimum circumferential and normal backlash, mm.")
    mesh.add_argument("--backlash-max", type=float, default=None, help="Maximum circumferential and normal backlash, mm.")
    mesh.add_argument("--steps", type=int, default=None, help="Samples over one turn of the smaller gear.")

    train = commands.add_parser(
        "gear-train",
        parents=[common],
        help="Speed and torque of every shaft in an assembly gear train.",
    )
    train.add_argument("name")
    train.add_argument("--file", type=Path, default=None, help="JSON drive: inputs and any extra speeds.")
    train.add_argument("--shaft", default=None, help="Input shaft, used when --file is omitted.")
    train.add_argument("--speed", type=float, default=None, help="Input speed in revolutions per minute.")
    train.add_argument("--torque", type=float, default=None, help="Input torque in newton-metres.")

    recorded = commands.add_parser(
        "record",
        parents=[common],
        help="Record design steps, replay that record, or write a timelapse.",
    )
    recorded.add_argument("action", choices=["start", "stop", "status", "replay", "export"])
    recorded.add_argument("--fps", type=int, default=None, help="Playback frames per second, from 1 to 60.")
    recorded.add_argument(
        "--camera",
        default=None,
        help="iso, front, back, left, right, top, or bottom.",
    )
    recorded.add_argument("--steps", default=None, help="Step numbers to include, such as 1,3-5. Omit for every step.")
    recorded.add_argument("--step", type=int, default=None, help="One step to show when the action is replay.")
    recorded.add_argument(
        "--video",
        choices=["gif", "mp4", "webm", "both"],
        default=None,
        help="gif writes only the GIF. mp4 and webm need ffmpeg.",
    )
    recorded.add_argument("--width", type=int, default=None, help="Picture width in pixels. Even, from 16 to 1280.")
    recorded.add_argument("--height", type=int, default=None, help="Picture height in pixels. Even, from 16 to 1280.")
    recorded.add_argument("--out", type=Path, default=None, help="A .gif, .mp4, or .webm file, or a directory, inside the project.")

    from aicad.plugins import add_cli

    add_cli(commands, common)
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
    # Listing or allowing a plugin must not open the design. Opening a file is not consent.
    if args.command == "plugins":
        from aicad.plugins import dispatch_cli

        return dispatch_cli(args)

    from aicad.document import Document

    document = Document(args.workspace)

    def emit(payload: dict) -> int:
        from aicad.honesty import attach_sheet

        stamped = payload
        if isinstance(payload, dict):
            stamped = attach_sheet(
                payload,
                document.workspace / "renders" / "sheets",
                args.command,
                root=document.workspace,
            )
        if isinstance(stamped, dict):
            _remember_cli_step(document, args, stamped)
        return _emit(stamped)

    if args.command == "record":
        from aicad.session_record import run_action

        return emit(run_action(document, _record_arguments(args)))

    if args.command == "run":
        script = _read_named_file(args.script)
        info = _create_or_update_script(document, args.name, script)
        return emit(info.to_dict())
    if args.command == "ops":
        ops = json.loads(_read_named_file(args.file))
        info = _create_or_update_ops(document, args.name, ops)
        return emit(info.to_dict())
    if args.command == "list":
        return emit({"units": "mm", "parts": [part.to_dict() for part in document.list_parts()]})
    if args.command == "measure":
        return emit(document.measure(args.name).to_dict())
    if args.command == "render":
        views = document.render(args.name)
        return emit({"views": [view.to_dict() for view in views]})
    if args.command == "export":
        from aicad.paths import require_export_path

        destination = args.out
        if destination is None:
            destination = document.workspace / "exports" / f"{args.name}.{args.format}"
        destination = require_export_path(document.workspace, destination, "Export path", AICADError)
        path = document.export(args.name, destination, args.format)
        return emit({"format": args.format, "path": str(path), "bytes": path.stat().st_size})
    if args.command == "check-wall":
        report = document.check_wall_thickness(args.name, args.min_wall, args.samples)
        return emit(report.to_dict())
    if args.command == "check-clearance":
        report = document.check_clearance(args.part_a, args.part_b, args.min_clearance)
        return emit(report.to_dict())
    if args.command == "check-packaging":
        report = document.check_packaging(
            _envelope_argument(args.envelope),
            parts=_name_list(args.parts, "--parts"),
            assembly=args.assembly,
            pose=args.pose,
            joints=_joints_argument(args.joints),
            min_clearance_mm=args.min_clearance,
        )
        return emit(report.to_dict())
    if args.command == "inspect":
        report = document.inspect(args.name, args.min_wall, args.samples)
        return emit(report.to_dict())
    if args.command == "import-step":
        info = document.import_step(args.name, args.file)
        return emit(info.to_dict())
    if args.command == "features":
        return emit(document.feature_report(args.name).to_dict())
    if args.command == "section":
        report = document.section(args.name, _plane_from_args(args))
        return emit(report.to_dict())
    if args.command == "catalog":
        return emit(document.search_parts(args.query))
    if args.command == "insert":
        info = document.insert_standard_part(
            args.name,
            args.kind,
            args.size,
            length=args.length,
            position=_triple(args.at, "--at"),
            rotation_deg=_triple(args.rot, "--rot"),
        )
        return emit(info.to_dict())
    if args.command == "gear":
        spec = _json_object(args.file) if args.file is not None else {}
        overrides = {
            "type": args.type,
            "module_mm": args.module,
            "teeth": args.teeth,
            "pressure_angle_deg": args.pressure_angle,
            "helix_angle_deg": args.helix_angle,
            "spiral_angle_deg": args.spiral_angle,
            "face_width_mm": args.face_width,
            "profile_shift": args.profile_shift,
            "shaft_angle_deg": args.shaft_angle,
            "mate_teeth": args.mate_teeth,
            "bore_mm": args.bore,
            "rack": args.rack,
            "hand": args.hand,
            "offset_mm": args.offset,
            "ratio": args.ratio,
            "ring_diameter_mm": args.ring_diameter,
            "role": args.role,
        }
        for key, value in overrides.items():
            if value is not None:
                spec[key] = value
        position = _triple(args.at, "--at") if args.at else None
        rotation = _triple(args.rot, "--rot") if args.rot else None
        names = {part.name for part in document.list_parts()}
        if args.name in names:
            report = document.update_gear(args.name, spec, position, rotation)
        else:
            report = document.create_gear(args.name, spec, position, rotation)
        return emit(report)
    if args.command == "check-gears":
        spec = _json_object(args.file) if args.file is not None else {}
        if args.pinion is not None:
            spec["pinion"] = args.pinion
        if args.gear is not None:
            spec["gear"] = args.gear
        if args.assembly is not None:
            spec["assembly"] = args.assembly
        if args.centre is not None:
            spec["centre_distance_mm"] = args.centre
        if args.shaft_angle is not None:
            spec["shaft_angle_deg"] = args.shaft_angle
        if args.offset is not None:
            spec["offset_mm"] = args.offset
        if args.steps is not None:
            spec["steps"] = args.steps
        if args.backlash_min is not None or args.backlash_max is not None:
            if args.backlash_min is None or args.backlash_max is None:
                raise AICADError("Pass both --backlash-min and --backlash-max.")
            spec["backlash_mm"] = {"min": args.backlash_min, "max": args.backlash_max}
        return emit(document.check_gears(spec))
    if args.command == "gear-train":
        if args.file is not None:
            drive = json.loads(_read_named_file(args.file))
        else:
            if args.shaft is None or args.speed is None or args.torque is None:
                raise AICADError("Pass --file, or --shaft, --speed, and --torque.")
            drive = {"inputs": [{"shaft": args.shaft, "speed_rpm": args.speed, "torque_nm": args.torque}]}
        return emit(document.solve_gear_train(args.name, drive))
    if args.command == "place":
        info = document.place_part(args.name, _triple(args.at, "--at"), _triple(args.rot, "--rot"))
        return emit(info.to_dict())
    if args.command == "sketch":
        if args.file is not None:
            spec = json.loads(_read_named_file(args.file))
            if args.name in document.list_sketches():
                report = document.update_sketch(args.name, spec)
            else:
                report = document.create_sketch(args.name, spec)
        else:
            report = document.get_sketch(args.name)
        return emit(report.to_dict())
    if args.command == "dimension":
        report = document.set_dimension(args.sketch, args.dimension, args.value)
        payload = report.to_dict()
        payload["rebuilt_parts"] = [
            part.to_dict()
            for part in document.list_parts()
            if document.get_source(part.name).get("sketch") == args.sketch
        ]
        return emit(payload)
    if args.command == "sketch-part":
        steps = json.loads(_read_named_file(args.steps))
        info = _create_or_update_sketch_part(document, args.name, args.sketch, steps)
        return emit(info.to_dict())
    if args.command == "materials":
        return emit(document.list_materials())
    if args.command == "faces":
        return emit(document.describe_faces(args.name))
    if args.command == "assemble":
        spec = json.loads(_read_named_file(args.file))
        spec["name"] = args.name
        names = {item["name"] for item in document.list_assemblies()}
        if args.name in names:
            return emit(document.update_assembly(spec))
        return emit(document.create_assembly(spec))
    if args.command == "joint":
        return emit(document.set_joint(args.name, args.joint, args.value, args.slide))
    if args.command == "pose":
        return emit(document.set_pose(args.name, args.pose))
    if args.command == "motion":
        inline = json.loads(_read_named_file(args.file)) if args.file else None
        return emit(document.run_motion(args.name, sequence=args.sequence, motion=inline))
    if args.command == "motion-study":
        inline = json.loads(_read_named_file(args.file)) if args.file else None
        return emit(
            document.run_motion_study(
                args.name,
                sequence=args.sequence,
                study=inline,
                steps=args.steps,
                clearance_mm=args.clearance,
            )
        )
    if args.command == "kinematics":
        request = json.loads(_read_named_file(args.file))
        if not isinstance(request, dict):
            raise GeometryError("Kinematics request must be an object.")
        unknown = set(request) - {"tool", "at", "rotation_deg", "joints", "slides", "target", "path"}
        if unknown:
            raise GeometryError(f"Kinematics request has unknown fields: {', '.join(sorted(unknown))}.")
        return emit(
            document.solve_kinematics(
                args.name,
                request.get("tool"),
                at=request.get("at"),
                rotation_deg=request.get("rotation_deg"),
                joints=request.get("joints"),
                slides=request.get("slides"),
                target=request.get("target"),
                path=request.get("path"),
            )
        )
    if args.command == "error-budget":
        return emit(document.check_position_error(args.name, _json_object(args.file)))
    if args.command == "cables":
        if args.file and args.study:
            raise GeometryError("Pass --study or --file, not both.")
        inline = json.loads(_read_named_file(args.file)) if args.file else None
        return emit(
            document.check_cables(
                args.name,
                sequence=args.sequence,
                study=inline if inline is not None else args.study,
                steps=args.steps,
            )
        )
    if args.command == "power":
        return emit(document.estimate_power(args.name, sequence=args.sequence))
    if args.command == "fea":
        setup = json.loads(_read_named_file(args.file))
        return emit(document.solve_mechanics(args.name, setup).to_dict())
    if args.command == "modal":
        setup = json.loads(_read_named_file(args.file))
        return emit(document.solve_modal(args.name, setup))
    if args.command == "modal-assembly":
        setup = json.loads(_read_named_file(args.file)) if args.file else {}
        return emit(document.solve_assembly_modal(args.name, setup))
    if args.command == "bolts":
        spec = json.loads(_read_named_file(args.file))
        if args.name:
            spec["part"] = args.name
        return emit(document.check_bolts(spec))
    if args.command == "fatigue":
        return emit(document.check_fatigue(json.loads(_read_named_file(args.file))))
    if args.command == "print":
        setup = {
            "orientation": args.orientation,
            "threshold_deg": args.threshold,
            "slice": bool(args.slice),
        }
        if args.bridge is not None:
            setup["bridge_mm"] = args.bridge
        if args.printer:
            setup["printer"] = args.printer
        if args.price_per_kg is not None:
            setup["price_per_kg"] = args.price_per_kg
        return emit(document.check_print(args.name, setup))
    if args.command == "robot":
        return emit(document.export_robot(args.name, args.out))
    if args.command == "export-drawing":
        return emit(
            document.export_drawing(
                args.name,
                scale=args.scale,
                exploded=bool(args.exploded),
                gap_mm=args.gap,
                out_dir=args.out,
            )
        )
    if args.command == "bill-of-materials":
        return emit(document.bill_of_materials(args.name, args.out))
    if args.command == "exploded-view":
        return emit(
            document.exploded_view(
                args.name,
                gap_mm=args.gap,
                on_drawing=bool(args.on_drawing),
                out_dir=args.out,
            )
        )
    if args.command == "animate":
        if args.camera and args.camera_file:
            raise AICADError("Pass --camera or --camera-file, not both.")
        return emit(
            document.render_animation(
                args.name,
                kind=args.kind,
                sequence=args.sequence,
                study=_json_object(args.file) if args.file else None,
                frames=args.frames,
                fps=args.fps,
                duration_s=args.duration_s,
                width=args.width,
                height=args.height,
                camera=args.camera,
                camera_path=_camera_path_file(args.camera_file) if args.camera_file else None,
                turns=args.turns,
                elevation_deg=args.elevation_deg,
                background=args.background,
                colors=_json_object(args.colors) if args.colors else None,
                materials=_json_object(args.materials) if args.materials else None,
                overlay=args.overlay,
                joint=args.joint,
                gap_mm=args.gap,
                video=args.video,
                path=args.out,
            )
        )
    if args.command == "start-plan":
        bundle = _json_object(args.file) if args.file else None
        return emit(document.start_plan(args.name, args.title, summary=args.summary, bundle=bundle))
    if args.command == "list-plans":
        return emit(document.list_plans())
    if args.command == "get-plan":
        return emit(document.get_plan(args.name))
    if args.command == "add-plan-research":
        return emit(document.add_plan_research(args.name, args.note, args.source, title=args.title))
    if args.command == "add-plan-assumption":
        return emit(document.add_plan_assumption(args.name, args.text))
    if args.command == "add-plan-question":
        return emit(document.add_plan_question(args.name, args.text))
    if args.command == "put-plan-sheet":
        return emit(document.put_plan_sheet(args.name, _json_object(args.file)))
    if args.command == "set-plan-dimensions":
        payload = json.loads(_read_named_file(args.file))
        rows = payload.get("dimensions") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise AICADError("The dimensions file must be a list, or an object with a dimensions list.")
        return emit(document.set_plan_dimensions(args.name, rows))
    if args.command == "comment-on-plan":
        return emit(document.comment_on_plan(args.name, args.item, args.text))
    if args.command == "answer-plan-question":
        return emit(document.answer_plan_question(args.name, args.item, args.text))
    if args.command == "mark-plan-item":
        return emit(document.mark_plan_item(args.name, args.item, args.mark))
    if args.command == "submit-plan":
        return emit(document.submit_plan(args.name))
    if args.command == "revise-plan":
        bundle = _json_object(args.file) if args.file else None
        return emit(document.revise_plan(args.name, args.changed, bundle=bundle))
    if args.command == "approve-plan":
        return emit(document.approve_plan(args.name))
    if args.command == "start-model-from-plan":
        return emit(document.start_model_from_plan(args.name))
    if args.command == "start-simulation-from-plan":
        return emit(document.start_simulation_from_plan(args.name))
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


def _envelope_argument(text: str):
    stripped = text.strip()
    if not stripped.startswith("{"):
        return text
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AICADError(f"--envelope is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AICADError("--envelope JSON must be an object.")
    return payload


def _name_list(text: str | None, label: str) -> list[str] | None:
    if text is None:
        return None
    names = [part.strip() for part in text.split(",")]
    if not names or any(not part for part in names):
        raise AICADError(f"{label} is a comma-separated list of names.")
    return names


def _joints_argument(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AICADError(f"--joints is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AICADError("--joints must be a JSON object of joint names and values.")
    return payload


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


def _read_named_file(path: Path) -> str:
    """Read a file named on the command line. A link at that name is not followed."""

    from aicad.paths import read_nofollow_bytes

    try:
        data = read_nofollow_bytes(
            path,
            maximum=32 * 1024 * 1024,
            label="Command file",
            error=AICADError,
        )
    except OSError as exc:
        raise AICADError(f"Could not read {path}: {exc}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise AICADError(f"Could not read {path}.") from exc
    # Match Path.read_text: a Windows line ending arrives as a line feed.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _camera_path_file(path: Path) -> list:
    try:
        payload = json.loads(_read_named_file(path))
    except json.JSONDecodeError as exc:
        raise AICADError(f"Could not read {path}: {exc}") from exc
    except OSError as exc:
        raise AICADError(f"Could not read {path}: {exc}") from exc
    if isinstance(payload, dict):
        if set(payload) - {"path"}:
            raise AICADError(f'{path} must be a list of points, or {{"path": [...]}}.')
        payload = payload.get("path")
    if not isinstance(payload, list):
        raise AICADError(f"{path} must be a list of camera points.")
    return payload


def _json_object(path: Path) -> dict:
    try:
        payload = json.loads(_read_named_file(path))
    except json.JSONDecodeError as exc:
        raise AICADError(f"Could not read {path}: {exc}") from exc
    except OSError as exc:
        raise AICADError(f"Could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AICADError(f"{path} must be a JSON object.")
    return payload


def _record_arguments(args: argparse.Namespace) -> dict:
    payload: dict = {"action": args.action}
    if args.fps is not None:
        payload["fps"] = args.fps
    if args.camera is not None:
        payload["camera"] = args.camera
    if args.steps is not None:
        payload["steps"] = args.steps
    if args.step is not None:
        payload["step"] = args.step
    if args.video is not None:
        payload["video"] = args.video
    if args.width is not None:
        payload["width"] = args.width
    if args.height is not None:
        payload["height"] = args.height
    if args.out is not None:
        payload["path"] = args.out
    return payload


def _remember_cli_step(document, args: argparse.Namespace, result: dict) -> None:
    from aicad.parity import CLI_TOOLS
    from aicad.session_record import cli_arguments, record_command

    name = CLI_TOOLS.get(args.command)
    if not name:
        return
    record_command(document, name, cli_arguments(args), result, source="cli")


def _emit(payload: dict) -> int:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
