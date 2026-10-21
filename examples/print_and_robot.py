"""A shelf that needs a better print orientation, and a two-link arm as a robot.

The shelf is a wall with a plate sticking out. Printed as modeled, the plate's
underside needs support. The check turns it and says which common beds it fits.
The arm is a grounded base and one revolute link, written as URDF and MJCF.

    python examples/print_and_robot.py

Pictures land in examples/renders. The robot files land in examples/robot_arm.
The session is examples/print_workspace.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from aicad.document import Document

ROOT = Path(__file__).resolve().parent
RENDERS = ROOT / "renders"
ROBOT = ROOT / "robot_arm"
WORKSPACE = ROOT / "print_workspace"


def _shelf(document: Document) -> None:
    document.create_part_from_ops(
        "shelf",
        [
            {"op": "add", "shape": "box", "size": [8, 30, 40], "center": [4, 0, 20]},
            {"op": "add", "shape": "box", "size": [36, 30, 4], "center": [22, 0, 38]},
        ],
    )


def _arm(document: Document) -> None:
    document.create_part_from_ops(
        "base",
        [{"op": "add", "shape": "box", "size": [30, 30, 12], "center": [0, 0, 6]}],
    )
    document.create_part_from_ops(
        "link",
        [{"op": "add", "shape": "box", "size": [60, 12, 12], "center": [30, 0, 0]}],
    )
    document.create_assembly(
        {
            "name": "arm",
            "instances": [
                {"name": "base", "part": "base", "material": "pla"},
                {
                    "name": "link",
                    "part": "link",
                    "material": "pla",
                    "payload_kg": 0.2,
                    "payload_at": [60, 0, 0],
                },
            ],
            "joints": [
                {
                    "name": "shoulder",
                    "type": "revolute",
                    "parent": "base",
                    "child": "link",
                    "parent_anchor": [15, 0, 12],
                    "child_anchor": [0, 0, 0],
                    "axis": [0, 1, 0],
                    "min": -45,
                    "max": 90,
                }
            ],
        }
    )


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    document = Document(WORKSPACE)
    _shelf(document)
    _arm(document)
    upright = document.check_print(
        "shelf", {"orientation": "z_up", "printer": "ender_3", "slice": True, "price_per_kg": 20}
    )
    best = document.check_print(
        "shelf", {"orientation": "best", "printer": "prusa_mk4", "slice": True, "price_per_kg": 20}
    )
    robot = document.export_robot("arm", ROBOT)
    RENDERS.mkdir(parents=True, exist_ok=True)
    upright_contact = next(view["path"] for view in upright["views"] if view["name"] == "overhang_contact")
    best_contact = next(view["path"] for view in best["views"] if view["name"] == "overhang_contact")
    shutil.copyfile(upright_contact, RENDERS / "overhang_upright.png")
    shutil.copyfile(best_contact, RENDERS / "overhang_best.png")
    report = {
        "upright": _brief(upright),
        "best": _brief(best),
        "robot": {
            "urdf": "examples/robot_arm/robot.urdf",
            "mjcf": "examples/robot_arm/robot.xml",
            "links": [_round_link(link) for link in robot["links"]],
            "joints": robot["joints"],
            "validation": robot["validation"],
            "note": robot["note"],
        },
    }
    (RENDERS / "print_robot_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def _round_link(link: dict) -> dict:
    return {
        "name": link["name"],
        "part": link["part"],
        "mass_kg": round(link["mass_kg"], 6),
        "center_m": [round(value, 6) for value in link["center_m"]],
    }


def _brief(report: dict) -> dict:
    return {
        "status": report["status"],
        "orientation": report["orientation"],
        "support_area_mm2": report["support_area_mm2"],
        "support_fraction": report["support_fraction"],
        "height_mm": report["height_mm"],
        "size_mm": report["size_mm"],
        "printer": report["printer"],
        "printers": [
            {"id": row["id"], "fits": row["fits"]}
            for row in report["printers"]
        ],
        "slicer": {
            "status": report["slicer"].get("status"),
            "time": report["slicer"].get("time"),
            "grams": report["slicer"].get("grams"),
            "cost": report["slicer"].get("cost"),
            "note": report["slicer"].get("note"),
        },
        "note": report["note"],
    }


if __name__ == "__main__":
    main()
