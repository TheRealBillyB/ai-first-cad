"""The MCP server and the CLI, exercised the way an assistant would."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
MOTOR_MOUNT = (ROOT / "examples" / "motor_mount.py").read_text(encoding="utf-8")
SHAFT = (ROOT / "examples" / "shaft.py").read_text(encoding="utf-8")


def _text(result) -> str:
    chunks = [block.text for block in result.content if block.type == "text"]
    return "\n".join(chunks)


def test_cli_measures_a_script(tmp_path):
    script = tmp_path / "box.py"
    script.write_text("part = Box(2, 3, 4)\n", encoding="utf-8")
    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicad",
            "run",
            str(script),
            "--name",
            "box",
            "--workspace",
            str(tmp_path / "ws"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["volume_mm3"] == 24


def test_mcp_server_builds_and_inspects_the_example(tmp_path):
    asyncio.run(_mcp_example(tmp_path))


async def _mcp_example(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aicad", "serve", "--workspace", str(tmp_path)],
        cwd=str(tmp_path),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert {
                "create_part_from_script",
                "inspect_part",
                "check_clearance",
                "export_part",
                "check_wall_thickness",
                "section_part",
                "feature_report",
                "search_parts",
                "insert_standard_part",
            } <= names

            created = await session.call_tool(
                "create_part_from_script",
                {"name": "motor_mount", "script": MOTOR_MOUNT},
            )
            assert created.is_error is False, _text(created)
            created_payload = json.loads(_text(created))
            assert created_payload["part"]["watertight"] is True
            assert created_payload["part"]["bounding_box"]["size"]["x"] == 72

            inspected = await session.call_tool(
                "inspect_part",
                {"name": "motor_mount", "min_wall_mm": 1.2},
            )
            assert inspected.is_error is False, _text(inspected)
            kinds = [block.type for block in inspected.content]
            assert "text" in kinds
            assert "image" in kinds
            report = json.loads(_text(inspected))
            assert report["measurement"]["valid"] is True
            assert report["measurement"]["watertight"] is True
            assert report["wall"]["passed"] is True
            assert report["wall"]["estimated_min_mm"] > 1.2
            assert report["wall"]["method"] in {"ray", "section", "ray+section"}
            assert report["features"]["tracked"] is True
            assert report["features"]["redundant_features"] == []

            section = await session.call_tool(
                "section_part",
                {"name": "motor_mount", "axis": "y", "offset": 0},
            )
            assert section.is_error is False, _text(section)
            section_report = json.loads(_text(section))
            assert section_report["area_mm2"] > 100
            assert any(block.type == "image" for block in section.content)

            catalog = await session.call_tool("search_parts", {"query": "608"})
            assert catalog.is_error is False, _text(catalog)
            matches = json.loads(_text(catalog))["matches"]
            assert any(item["size"] == "M8-22-7" for item in matches)
            image_blocks = [block for block in inspected.content if block.type == "image"]
            assert len(image_blocks) >= 8
            assert image_blocks[0].mime_type == "image/png"

            shaft = await session.call_tool(
                "create_part_from_script",
                {"name": "shaft", "script": SHAFT},
            )
            assert shaft.is_error is False, _text(shaft)

            clearance = await session.call_tool(
                "check_clearance",
                {"part_a": "motor_mount", "part_b": "shaft", "min_clearance_mm": 0.1},
            )
            assert clearance.is_error is False, _text(clearance)
            gap = json.loads(_text(clearance))
            assert gap["intersecting"] is False
            assert gap["passed"] is True
            assert abs(gap["min_distance_mm"] - 0.2) < 0.03

            exported = await session.call_tool(
                "export_part",
                {"name": "motor_mount", "format": "step"},
            )
            assert exported.is_error is False, _text(exported)
            path = Path(json.loads(_text(exported))["path"])
            assert path.is_file()
            assert "ISO-10303-21" in path.read_text(encoding="utf-8", errors="replace")

            resources = await session.list_resources()
            uris = {str(resource.uri) for resource in resources.resources}
            assert "aicad://guide" in uris
