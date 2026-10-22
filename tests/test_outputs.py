"""Drawings, the bill of materials, and the exploded view.

These read an assembly. They do not change a solver. The same three names
are a Document method, a workspace command, a CLI subcommand, and an MCP tool.
"""

import asyncio
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import GeometryError
from aicad.server import build_server
from aicad.workspace import Workspace


def _box(document: Document, name: str, size: list[float], center: list[float]) -> None:
    document.create_part_from_ops(
        name,
        [{"op": "add", "shape": "box", "size": size, "center": center}],
    )


def _post(document: Document) -> None:
    _box(document, "base", [40, 20, 10], [0, 0, 5])
    document.create_part_from_ops(
        "post",
        [{"op": "add", "shape": "cylinder", "radius": 5, "height": 30, "center": [0, 0, 25]}],
    )
    document.create_assembly(
        {
            "name": "stand",
            "instances": [
                {"name": "base", "part": "base", "material": "aluminum_6061"},
                {"name": "post", "part": "post", "material": "aluminum_6061"},
            ],
            "joints": [
                {
                    "name": "seat",
                    "type": "fixed",
                    "parent": "base",
                    "child": "post",
                    "parent_anchor": [0, 0, 10],
                    "child_anchor": [0, 0, 10],
                    "axis": [0, 0, 1],
                }
            ],
        }
    )


def test_drawing_states_its_limits_and_writes_pdf_and_dxf(tmp_path):
    document = Document(tmp_path)
    _post(document)
    report = document.export_drawing("stand")
    assert report["result_kind"] == "verified_check"
    assert report["result_label"] == "Verified check"
    assert "not a shop drawing" in report["result_what"].lower()
    assert any("GD&T" in item for item in report["limits"])
    assert any("GD&T" in item for item in report["omits"])
    assert report["projection"] == "third angle"
    assert report["scale"] in {f"{numer}:{denom}" for numer, denom in _SCALES}
    sizes = {item["axis"]: item["mm"] for item in report["dimensions"]}
    assert sizes["X"] == pytest.approx(40, abs=0.05)
    assert sizes["Y"] == pytest.approx(20, abs=0.05)
    assert sizes["Z"] == pytest.approx(40, abs=0.05)
    assert report["curves"]["visible"] > 0

    pdf = Path(report["files"]["pdf"]).read_bytes()
    assert pdf.startswith(b"%PDF")
    assert b"LAYOUT DRAWING" in pdf
    assert b"GD&T" in pdf
    assert b"THIRD ANGLE" in pdf
    assert b"(40)" in pdf
    dxf = Path(report["files"]["dxf"]).read_text(encoding="utf-8")
    assert "\nLINE\n" in dxf or dxf.startswith("0\nSECTION")
    assert "\n40\n" in dxf or "\n1\n40\n" in dxf
    assert "THIRD ANGLE" in dxf
    assert "HIDDEN" in dxf
    png = Path(report["views"][0]["path"])
    assert png.is_file() and png.stat().st_size > 1000

    import ezdxf

    loaded = ezdxf.readfile(report["files"]["dxf"])
    texts = [entity.dxf.text for entity in loaded.modelspace().query("TEXT")]
    assert "40" in texts
    assert "20" in texts
    assert any(text.startswith("SCALE ") for text in texts)


_SCALES = (
    (10, 1),
    (5, 1),
    (2, 1),
    (1, 1),
    (1, 2),
    (1, 5),
    (1, 10),
    (1, 20),
    (1, 50),
    (1, 100),
    (1, 200),
    (1, 500),
    (1, 1000),
)


def test_a_scale_that_does_not_fit_is_replaced_and_said_so(tmp_path):
    document = Document(tmp_path)
    _post(document)
    report = document.export_drawing("stand", scale="10:1")
    assert report["scale"] != "10:1"
    assert "10:1" in report["scale_note"]
    assert "does not fit" in report["scale_note"]


def test_bill_of_materials_counts_parts_fasteners_and_a_hose(tmp_path):
    document = Document(tmp_path)
    _box(document, "plate", [30, 20, 6], [0, 0, 3])
    _box(document, "link", [20, 8, 8], [10, 0, 4])
    document.insert_standard_part("screw_a", "socket_head_cap_screw", "M5-0.8", length=16)
    document.insert_standard_part("screw_b", "screw", "M5-0.8", length=16)
    document.insert_standard_part("bearing", "bearing", "608")
    document.create_assembly(
        {
            "name": "kit",
            "instances": [
                {"name": "plate", "part": "plate", "material": "steel"},
                {"name": "link_a", "part": "link", "material": "steel"},
                {"name": "link_b", "part": "link", "material": "steel"},
                {"name": "screw_a", "part": "screw_a", "material": "steel"},
                {"name": "screw_b", "part": "screw_b", "material": "steel"},
                {"name": "bearing", "part": "bearing", "material": "steel"},
            ],
            "joints": [
                _fixed("link_a_seat", "plate", "link_a"),
                _fixed("link_b_seat", "plate", "link_b"),
                _fixed("screw_a_seat", "plate", "screw_a", [8, 0, 6]),
                _fixed("screw_b_seat", "plate", "screw_b", [-8, 0, 6]),
                _fixed("bearing_seat", "plate", "bearing"),
            ],
            "cables": [
                {
                    "name": "supply",
                    "kind": "hose",
                    "diameter_mm": 8,
                    "waypoints": [
                        {"instance": "plate", "at": [0, 0, 6]},
                        {"instance": "link_a", "at": [10, 0, 0]},
                    ],
                }
            ],
        }
    )
    report = document.bill_of_materials("kit")
    assert report["result_kind"] == "rough_estimate"
    assert report["result_label"] == "Rough estimate"
    assert "no prices" in report["result_what"].lower()
    assert any("price" in item.lower() for item in report["limits"])
    labeled = {block["result_name"]: block["result_kind"] for block in report["results"]}
    assert labeled["Quantities"] == "verified_check"
    assert labeled["Mass"] == "rough_estimate"
    by_category = {}
    for row in report["rows"]:
        by_category.setdefault(row["category"], []).append(row)
    assert by_category["part"]
    link = next(row for row in by_category["part"] if row["part"] == "link")
    assert link["quantity"] == 2
    screws = by_category["fastener"]
    assert len(screws) == 1
    assert screws[0]["quantity"] == 2
    assert screws[0]["standard"] == "ISO 4762"
    assert "total for this line" in screws[0]["note"]
    assert screws[0]["length_mm"] == 16
    assert "M5" in screws[0]["description"]
    bearing = by_category["standard"][0]
    assert bearing["quantity"] == 1
    assert bearing["category"] == "standard"
    hose = by_category["hose"][0]
    assert hose["name"] == "supply"
    assert hose["mass_estimate_kg"] is None
    plate = next(row for row in by_category["part"] if row["part"] == "plate")
    assert plate["mass_estimate_kg"] == pytest.approx(30 * 20 * 6 * 7.85 / 1_000_000, rel=0.02)

    with Path(report["csv"]).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["item"] == "1"
    assert any(row["category"] == "fastener" and row["quantity"] == "2" for row in rows)
    assert Path(report["views"][0]["path"]).is_file()


def test_exploded_view_pulls_a_slide_along_its_axis_and_can_sit_on_the_drawing(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [20, 20, 10], [0, 0, 5])
    _box(document, "ram", [8, 8, 16], [0, 0, 8])
    document.create_assembly(
        {
            "name": "ram",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "ram", "part": "ram"},
            ],
            "joints": [
                {
                    "name": "stroke",
                    "type": "prismatic",
                    "parent": "base",
                    "child": "ram",
                    "parent_anchor": [0, 0, 10],
                    "child_anchor": [0, 0, 0],
                    "axis": [0, 0, 1],
                    "value": 0,
                }
            ],
        }
    )
    before = document.get_assembly("ram")["solved"]
    before_ram = next(item for item in before["instances"] if item["name"] == "ram")["origin_mm"]
    report = document.exploded_view("ram", gap_mm=25, on_drawing=True)
    assert report["result_kind"] == "verified_check"
    assert report["result_label"] == "Verified check"
    assert "not a disassembly" in report["result_what"].lower()
    ram = next(move for move in report["moves"] if move["instance"] == "ram")
    assert ram["offset_mm"][2] == pytest.approx(25, abs=0.1)
    assert abs(ram["offset_mm"][0]) < 0.1
    assert "axis" in ram["reason"].lower()
    base = next(move for move in report["moves"] if move["instance"] == "base")
    assert base["offset_mm"] == [0.0, 0.0, 0.0]
    assert Path(report["views"][0]["path"]).is_file()
    assert report["drawing"]["result_kind"] == "verified_check"
    assert report["drawing"]["result_label"] == "Verified check"
    drawing_pdf = Path(report["drawing"]["pdf"]).read_bytes()
    assert b"exploded" in drawing_pdf.lower()
    # The stored assembly did not move.
    after = document.get_assembly("ram")["solved"]
    after_ram = next(item for item in after["instances"] if item["name"] == "ram")["origin_mm"]
    assert after_ram == pytest.approx(before_ram, abs=1e-3)


def test_a_catalog_screw_lifts_along_its_axis(tmp_path):
    document = Document(tmp_path)
    _box(document, "plate", [40, 20, 8], [0, 0, 4])
    document.insert_standard_part("screw", "screw", "M5-0.8", length=16)
    document.create_assembly(
        {
            "name": "fastened",
            "instances": [
                {"name": "plate", "part": "plate"},
                {"name": "screw", "part": "screw"},
            ],
            "joints": [
                _fixed("screw_seat", "plate", "screw", [0, 0, 8]),
            ],
        }
    )
    report = document.exploded_view("fastened", gap_mm=20)
    screw = next(move for move in report["moves"] if move["instance"] == "screw")
    assert screw["offset_mm"][2] == pytest.approx(20, abs=0.1)
    assert abs(screw["offset_mm"][0]) < 0.1
    assert abs(screw["offset_mm"][1]) < 0.1
    assert "fastener" in screw["reason"].lower()


def test_a_hinge_comes_off_its_pin(tmp_path):
    document = Document(tmp_path)
    _box(document, "base", [10, 10, 10], [0, 0, 5])
    _box(document, "link", [40, 8, 8], [20, 0, 4])
    document.create_assembly(
        {
            "name": "hinge",
            "instances": [
                {"name": "base", "part": "base"},
                {"name": "link", "part": "link"},
            ],
            "joints": [
                {
                    "name": "pin",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "parent_anchor": [5, 0, 5],
                    "child_anchor": [0, 0, 0],
                    "axis": [0, 0, 1],
                    "value": 0,
                }
            ],
        }
    )
    report = document.exploded_view("hinge", gap_mm=30)
    link = next(move for move in report["moves"] if move["instance"] == "link")
    assert link["offset_mm"][0] > 20
    assert "pin" in link["reason"].lower()


def test_workspace_commands_remember_the_reports(tmp_path):
    workspace = Workspace(tmp_path)
    _box(workspace.document, "base", [10, 10, 4], [0, 0, 2])
    workspace.document.create_assembly(
        {"name": "only", "instances": [{"name": "base", "part": "base"}], "joints": []}
    )
    drawing = workspace.execute("export_drawing", {"name": "only"})
    bom = workspace.execute("bill_of_materials", {"name": "only"})
    exploded = workspace.execute("exploded_view", {"name": "only", "gap_mm": 15})
    assert drawing["result_kind"] == "verified_check"
    assert drawing["result_label"] == "Verified check"
    assert drawing["sheet"]["path"]
    assert bom["result_kind"] == "rough_estimate"
    assert exploded["result_kind"] == "verified_check"
    assert "not a disassembly" in exploded["result_what"].lower()
    panels = workspace.state()["panels"]
    assert panels["drawing"]["result_kind"] == "verified_check"
    assert panels["bom"]["line_count"] == 1
    assert panels["exploded"]["moves"][0]["offset_mm"] == [0.0, 0.0, 0.0]
    names = {command["name"] for command in workspace.commands()}
    assert {"export_drawing", "bill_of_materials", "exploded_view"} <= names


def test_cli_writes_the_bill_of_materials(tmp_path):
    document = Document(tmp_path / "ws")
    _box(document, "base", [10, 8, 4], [0, 0, 2])
    document.create_assembly({"name": "only", "instances": [{"name": "base", "part": "base"}], "joints": []})
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aicad",
            "bill-of-materials",
            "only",
            "--workspace",
            str(tmp_path / "ws"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["result_kind"] == "rough_estimate"
    assert payload["result_label"] == "Rough estimate"
    assert payload["sheet"]["path"]
    assert Path(payload["csv"]).is_file()


def test_output_commands_match_on_the_cli_and_the_mcp_server(tmp_path):
    import argparse

    from aicad.cli import main

    parser_names = _cli_commands()
    assert {"export-drawing", "bill-of-materials", "exploded-view"} <= parser_names
    methods = {"export_drawing", "bill_of_materials", "exploded_view"}
    for command in ("export-drawing", "bill-of-materials", "exploded-view"):
        assert command.replace("-", "_") in methods
    for method in methods:
        assert callable(getattr(Document, method))
    tools = asyncio.run(_tool_names(tmp_path))
    assert methods <= tools
    # main is imported so a missing dispatch still fails the suite's import of cli.
    assert callable(main)
    del argparse


def test_unknown_scale_is_an_error(tmp_path):
    document = Document(tmp_path)
    _post(document)
    with pytest.raises(GeometryError):
        document.export_drawing("stand", scale="3:1")


def _fixed(name, parent, child, anchor=None):
    return {
        "name": name,
        "type": "fixed",
        "parent": parent,
        "child": child,
        "parent_anchor": anchor or [0, 0, 0],
        "child_anchor": [0, 0, 0],
        "axis": [0, 0, 1],
    }


def _cli_commands() -> set[str]:
    import re

    import aicad.cli as cli

    source = Path(cli.__file__).read_text(encoding="utf-8")
    return set(re.findall(r"add_parser\(\s*[\"']([^\"']+)[\"']", source))


async def _tool_names(tmp_path: Path) -> set[str]:
    server = build_server(tmp_path)
    listed = await server.list_tools()
    return {tool.name for tool in listed}
