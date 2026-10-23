"""What the MCP server must cover, written down in one place.

The parity test reads these lists. A new command or a new document method
fails that test until it is added here and exposed to the assistant.
"""

from __future__ import annotations

# Consent switches. They are not design tools, and the assistant must not be
# able to flip them. `serve` and `app` start a process. `allow-scripts` is the
# person agreeing that design scripts may run in one project. `plugins` is the
# plugin door: list, show, allow, and run. It is not a modeling command.
# Parity does not need an MCP tool for these consent switches.
CLI_EXEMPT = frozenset({"serve", "app", "allow-scripts", "plugins"})

# CLI subcommand -> MCP tool that does the same work.
CLI_TOOLS = {
    "run": "create_part_from_script",
    "ops": "create_part_from_ops",
    "list": "list_parts",
    "measure": "measure_part",
    "render": "render_part",
    "export": "export_part",
    "check-wall": "check_wall_thickness",
    "check-clearance": "check_clearance",
    "check-packaging": "check_packaging",
    "inspect": "inspect_part",
    "import-step": "import_step",
    "features": "feature_report",
    "section": "section_part",
    "catalog": "search_parts",
    "insert": "insert_standard_part",
    "place": "place_part",
    "sketch": "create_sketch",
    "dimension": "set_dimension",
    "sketch-part": "create_part_from_sketch",
    "materials": "list_materials",
    "faces": "describe_faces",
    "assemble": "create_assembly",
    "joint": "set_joint",
    "pose": "set_pose",
    "motion": "run_motion",
    "motion-study": "run_motion_study",
    "cables": "check_cables",
    "power": "estimate_power",
    "fea": "solve_mechanics",
    "modal": "solve_modal",
    "modal-assembly": "solve_assembly_modal",
    "bolts": "check_bolts",
    "fatigue": "check_fatigue",
    "print": "check_print",
    "robot": "export_robot",
    "export-drawing": "export_drawing",
    "gear": "create_gear",
    "bill-of-materials": "bill_of_materials",
    "exploded-view": "exploded_view",
    "start-plan": "start_plan",
    "list-plans": "list_plans",
    "get-plan": "get_plan",
    "add-plan-research": "add_plan_research",
    "add-plan-assumption": "add_plan_assumption",
    "add-plan-question": "add_plan_question",
    "put-plan-sheet": "put_plan_sheet",
    "set-plan-dimensions": "set_plan_dimensions",
    "comment-on-plan": "comment_on_plan",
    "answer-plan-question": "answer_plan_question",
    "mark-plan-item": "mark_plan_item",
    "submit-plan": "submit_plan",
    "revise-plan": "revise_plan",
    "approve-plan": "approve_plan",
    "start-model-from-plan": "start_model_from_plan",
    "start-simulation-from-plan": "start_simulation_from_plan",
}

# Public Document methods a design script may call. This is an explicit list
# on purpose: a method added to Document is not callable from a script until
# it is written here, and the parity test fails until that happens.
SCRIPT_METHODS = frozenset(
    {
        "assembly_mesh",
        "check_bolts",
        "check_cables",
        "check_clearance",
        "check_fatigue",
        "check_packaging",
        "check_print",
        "check_wall_thickness",
        "create_assembly",
        "create_gear",
        "create_part_from_ops",
        "create_part_from_script",
        "create_part_from_sketch",
        "create_sketch",
        "delete_part",
        "describe_faces",
        "bill_of_materials",
        "estimate_power",
        "exploded_view",
        "export",
        "export_drawing",
        "export_robot",
        "feature_report",
        "get_assembly",
        "get_sketch",
        "get_source",
        "import_step",
        "insert_standard_part",
        "inspect",
        "list_assemblies",
        "list_materials",
        "list_parts",
        "list_printers",
        "list_sketches",
        "measure",
        "place_part",
        "render",
        "render_assembly",
        "replace_session",
        "run_motion",
        "run_motion_study",
        "search_parts",
        "section",
        "session_payload",
        "set_dimension",
        "set_joint",
        "set_pose",
        "solve_assembly_modal",
        "solve_mechanics",
        "solve_modal",
        "update_assembly",
        "update_gear",
        "update_part_from_sketch",
        "update_part_ops",
        "update_part_script",
        "update_sketch",
    }
)

# Top-level modules a design script may import. Anything else is refused
# before the code runs. The operating-system sandbox is what actually stops
# a script that finds a way around this list.
ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "aicad",
        "build123d",
        "cmath",
        "collections",
        "copy",
        "dataclasses",
        "decimal",
        "fractions",
        "functools",
        "itertools",
        "json",
        "math",
        "numbers",
        "numpy",
        "pathlib",
        "re",
        "typing",
    }
)

# Resources an agent can open by URI, besides the guide.
RESOURCE_URIS = frozenset(
    {
        "aicad://guide",
        "aicad://parts",
        "aicad://assemblies",
        "aicad://sketches",
        "aicad://results",
        "aicad://session",
    }
)

RESOURCE_TEMPLATES = frozenset(
    {
        "aicad://parts/{name}",
        "aicad://assemblies/{name}",
    }
)
