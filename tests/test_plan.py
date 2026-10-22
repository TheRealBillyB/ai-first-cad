"""Plan & Sketch: data on disk, revisions, and a hard stop before modeling."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from aicad.document import Document
from aicad.errors import PlanError
from aicad.plan import PlanStore
from aicad.workspace import COMMANDS, Workspace

ROOT = Path(__file__).resolve().parents[1]
SHELF = json.loads((ROOT / "examples" / "plan_shelf.json").read_text(encoding="utf-8"))

PLAN_TOOLS = [
    "start_plan",
    "list_plans",
    "get_plan",
    "add_plan_research",
    "add_plan_assumption",
    "add_plan_question",
    "put_plan_sheet",
    "set_plan_dimensions",
    "comment_on_plan",
    "answer_plan_question",
    "mark_plan_item",
    "submit_plan",
    "revise_plan",
    "approve_plan",
    "start_model_from_plan",
    "start_simulation_from_plan",
]

CLI_COMMANDS = [name.replace("_", "-") for name in PLAN_TOOLS]


def _sheet(label="block"):
    return {
        "id": "01_top",
        "title": "Top",
        "panels": [
            {
                "title": "Top",
                "blocks": [{"x": 0, "y": 0, "w": 200, "h": 80, "label": label, "fill": "yellow"}],
                "dimensions": [{"x0": 0, "y0": 0, "x1": 200, "y1": 0, "text": "200 mm", "offset": -30}],
                "notes": ["Sketch only."],
            }
        ],
    }


def test_example_plan_is_data_and_draws_a_blueprint(tmp_path):
    marker = tmp_path / "should-not-run"
    poisoned = json.loads(json.dumps(SHELF))
    poisoned["research"][0]["note"] = "import os; os.system('touch " + str(marker) + "')"
    store = PlanStore(tmp_path)
    plan = store.start("shelf", bundle=poisoned)
    assert not marker.exists()
    assert plan["status"] == "draft"
    assert plan["current_revision"] == 1
    assert plan["revision"]["changed"] == "Started the plan."
    assert plan["views"]
    image_path = Path(plan["views"][0]["path"])
    assert image_path.is_file()
    text = (tmp_path / "plans" / "shelf.json").read_text(encoding="utf-8")
    assert "os.system" in text
    assert "script" not in json.loads(text)
    image = Image.open(image_path).convert("RGB")
    assert image.size[0] > 1000
    sampled = [image.getpixel((x, y)) for y in range(0, image.height, 4) for x in range(0, image.width, 4)]
    assert any(red < 40 and green < 90 and blue > 80 for red, green, blue in sampled)
    assert any(red > 180 and green > 140 and blue < 120 for red, green, blue in sampled)
    session = tmp_path / "session.json"
    Document(tmp_path)
    assert not session.exists()
    assert not marker.exists()


def test_revisions_keep_the_previous_sheet_and_say_what_changed(tmp_path):
    store = PlanStore(tmp_path)
    store.start("shelf", "Shelf")
    store.add_research("shelf", "18 mm sheet is the usual cabinet thickness.", "Woodweb, 32 mm system.")
    store.put_sheet("shelf", _sheet("v1"))
    first = store.get("shelf")
    v1 = Path(first["views"][0]["path"]).read_bytes()
    revised = store.revise("shelf", "Shortened the block after the load answer.", bundle={"sheets": [_sheet("v2")]})
    assert revised["current_revision"] == 2
    assert revised["status"] == "draft"
    assert revised["revision"]["changed"].startswith("Shortened")
    assert revised["revisions"][0]["version"] == 1
    assert revised["revisions"][0]["sheets"][0]["file"].endswith("v1/01_top.png")
    assert revised["revisions"][1]["sheets"][0]["file"].endswith("v2/01_top.png")
    assert Path(revised["revisions"][0]["sheets"][0]["path"]).read_bytes() == v1
    assert Path(revised["views"][0]["path"]).read_bytes() != v1
    with pytest.raises(PlanError, match="source"):
        store.add_research("shelf", "A note with nowhere to look it up.", "")


def test_only_the_person_can_approve_and_modeling_stays_off(tmp_path):
    user = Workspace(tmp_path)
    user.execute("start_plan", {"name": "shelf", "bundle": SHELF}, source="mcp")
    with pytest.raises(PlanError, match="cannot approve"):
        user.execute("approve_plan", {"name": "shelf"}, source="mcp")
    with pytest.raises(PlanError, match="cannot answer"):
        user.execute("answer_plan_question", {"name": "shelf", "item": "q1", "text": "600 mm"}, source="mcp")
    with pytest.raises(PlanError, match="cannot accept"):
        user.execute("mark_plan_item", {"name": "shelf", "item": "a2", "mark": "accepted"}, source="chat")
    with pytest.raises(PlanError) as refused:
        user.execute("start_model_from_plan", {"name": "shelf"}, source="mcp")
    assert "stays off" in str(refused.value)
    assert "Nothing was modeled" in str(refused.value)
    assert "cannot approve" in str(refused.value)
    with pytest.raises(PlanError, match="Nothing was simulated"):
        user.execute("start_simulation_from_plan", {"name": "shelf"}, source="mcp")

    user.execute("comment_on_plan", {"name": "shelf", "item": "q1", "text": "Asking either way."}, source="mcp")
    user.execute("submit_plan", {"name": "shelf"}, source="mcp")
    with pytest.raises(PlanError, match="in review"):
        user.execute("put_plan_sheet", {"name": "shelf", "spec": _sheet()}, source="mcp")
    user.execute("answer_plan_question", {"name": "shelf", "item": "q1", "text": "600 mm apart"}, source="ui")
    user.execute("mark_plan_item", {"name": "shelf", "item": "a2", "mark": "accepted"}, source="ui")
    approved = user.execute("approve_plan", {"name": "shelf"}, source="ui")
    assert approved["status"] == "approved"
    assert approved["approved_revision"] == 1
    released = user.execute("start_model_from_plan", {"name": "shelf"}, source="mcp")
    assert released["ready"] is True
    assert released["released"] == "model"
    names = {item["name"]: item["value_mm"] for item in released["dimensions"]}
    assert names["shelf_depth_mm"] == 280
    assert any(item["id"] == "a2" and item["status"] == "accepted" for item in released["assumptions"])
    simulated = user.execute("start_simulation_from_plan", {"name": "shelf"}, source="api")
    assert simulated["released"] == "simulation"

    again = user.execute(
        "revise_plan",
        {"name": "shelf", "changed": "Studs are 600 mm. Plate length is no longer 480 mm."},
        source="mcp",
    )
    assert again["status"] == "draft"
    assert again["current_revision"] == 2
    assert "stay off" in again["note"]
    with pytest.raises(PlanError, match="Nothing was modeled"):
        user.execute("start_model_from_plan", {"name": "shelf"}, source="mcp")
    assert Path(again["revisions"][0]["sheets"][0]["path"]).is_file()


def test_undo_restores_the_plan_and_the_sheet(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.execute("start_plan", {"name": "shelf", "title": "Shelf"}, source="ui")
    workspace.execute("put_plan_sheet", {"name": "shelf", "spec": _sheet()}, source="ui")
    assert workspace.execute("get_plan", {"name": "shelf"})["revision"]["sheets"]
    workspace.execute("undo", source="ui")
    assert workspace.execute("get_plan", {"name": "shelf"})["revision"]["sheets"] == []
    workspace.execute("redo", source="ui")
    restored = workspace.execute("get_plan", {"name": "shelf"})
    assert restored["revision"]["sheets"][0]["id"] == "01_top"
    assert Path(restored["views"][0]["path"]).is_file()


def test_plan_commands_match_the_document_and_the_cli():
    for name in PLAN_TOOLS:
        assert name in COMMANDS
        assert callable(getattr(Document, name))
    completed = subprocess.run(
        [sys.executable, "-m", "aicad", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    for command in CLI_COMMANDS:
        assert command in completed.stdout


def test_cli_refuses_modeling_then_releases_it(tmp_path):
    workspace = tmp_path / "ws"
    bundle = tmp_path / "shelf.json"
    bundle.write_text(json.dumps(SHELF), encoding="utf-8")

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "aicad", *args, "--workspace", str(workspace)],
            check=False,
            capture_output=True,
            text=True,
        )

    created = run("start-plan", "shelf", "--file", str(bundle))
    assert created.returncode == 0, created.stderr
    blocked = run("start-model-from-plan", "shelf")
    assert blocked.returncode == 1
    assert "Nothing was modeled" in blocked.stderr
    assert "cannot approve" in blocked.stderr
    assert run("submit-plan", "shelf").returncode == 0
    assert run("answer-plan-question", "shelf", "q1", "--text", "600 mm apart").returncode == 0
    assert run("approve-plan", "shelf").returncode == 0
    released = run("start-simulation-from-plan", "shelf")
    assert released.returncode == 0, released.stderr
    payload = json.loads(released.stdout)
    assert payload["ready"] is True
    assert payload["dimensions"]


def test_mcp_plan_tools_match_and_refuse(tmp_path):
    asyncio.run(_mcp_plan(tmp_path))


async def _mcp_plan(tmp_path: Path) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aicad", "serve", "--workspace", str(tmp_path)],
        cwd=str(tmp_path),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert set(PLAN_TOOLS) <= names
            started = await session.call_tool("start_plan", {"name": "shelf", "bundle": SHELF})
            assert started.is_error is False, _mcp_text(started)
            assert any(block.type == "image" for block in started.content)
            blocked = await session.call_tool("start_model_from_plan", {"name": "shelf"})
            assert blocked.is_error is True
            text = _mcp_text(blocked)
            assert "Nothing was modeled" in text
            assert "cannot approve" in text
            denied = await session.call_tool("approve_plan", {"name": "shelf"})
            assert denied.is_error is True
            assert "cannot approve" in _mcp_text(denied)


def _mcp_text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")
