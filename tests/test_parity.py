"""MCP has to cover the CLI, the workspace commands, and the document API."""

import argparse
import asyncio
import inspect
import json
from pathlib import Path

from aicad.cli import build_parser
from aicad.document import Document
from aicad.parity import CLI_EXEMPT, CLI_TOOLS, RESOURCE_TEMPLATES, RESOURCE_URIS, SCRIPT_METHODS
from aicad.server import build_server
from aicad.workspace import COMMANDS


def _cli_commands() -> set[str]:
    parser = build_parser()
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    return set(subparsers.choices)


def _document_methods() -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(Document, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def test_mcp_covers_the_cli_the_app_and_the_document(tmp_path: Path):
    server = build_server(tmp_path)
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    descriptions = {tool.name: tool.description or "" for tool in tools}

    cli_commands = _cli_commands()
    assert CLI_EXEMPT <= cli_commands
    assert set(CLI_TOOLS) == cli_commands - CLI_EXEMPT
    missing_cli = sorted(command for command, tool in CLI_TOOLS.items() if tool not in names)
    assert missing_cli == [], f"CLI commands with no MCP tool: {missing_cli}"

    workspace_commands = set(COMMANDS) | {"undo", "redo"}
    missing_commands = sorted(workspace_commands - names)
    assert missing_commands == [], f"Workspace commands with no MCP tool: {missing_commands}"

    public = _document_methods()
    unknown = sorted(SCRIPT_METHODS - public)
    assert unknown == [], f"Design-script methods that are not on Document: {unknown}"
    uncovered = sorted(public - SCRIPT_METHODS - names)
    assert uncovered == [], f"Document methods neither a design-script call nor an MCP tool: {uncovered}"

    resources = asyncio.run(server.list_resources())
    uris = {str(resource.uri) for resource in resources}
    assert RESOURCE_URIS <= uris, sorted(RESOURCE_URIS - uris)

    templates = asyncio.run(server.list_resource_templates())
    patterns = {str(template.uri_template) for template in templates}
    assert RESOURCE_TEMPLATES <= patterns, sorted(RESOURCE_TEMPLATES - patterns)

    assert "allow-scripts" not in names
    assert "allow_design_scripts" not in names
    assert "plugins" not in names
    assert "plugins" in CLI_EXEMPT
    assert "previous assembly" in descriptions["update_assembly"]


def test_mcp_check_reports_carry_the_labels(tmp_path: Path):
    from aicad.workspace import Workspace

    workspace = Workspace(tmp_path)
    workspace.execute(
        "create_part_from_ops",
        {"name": "box", "ops": [{"op": "add", "shape": "box", "size": [2, 3, 4]}]},
    )
    server = build_server(tmp_path)
    measured = asyncio.run(server.call_tool("measure_part", {"name": "box"}))
    body = json.loads(measured.content[0].text)
    assert body["result_kind"] == "verified_check"
    assert body["result_label"] == "Verified check"
    wall = asyncio.run(server.call_tool("check_wall_thickness", {"name": "box", "min_wall_mm": 0.5, "samples": 1}))
    report = json.loads(wall.content[0].text)
    assert report["result_kind"] == "rough_estimate"
    assert report["result_label"] == "Rough estimate"
    assert report["sheet"]["path"]
