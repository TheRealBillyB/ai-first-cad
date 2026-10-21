"""A drill-rod handling arm, plus a test leg that walks into a block.

The arm is a grounded base, a shoulder, an elbow, a gripper with geared jaws,
and a rod fixed in the gripper. A hydraulic hose and an electrical cable run
through clips on the links. The lift pose raises the rod. The test leg is a
separate assembly: hip and knee keyframes, and a block the foot meets at the
bottom of the stride.

Running this file writes the session under examples/rod_arm_workspace and
copies the pictures and a short report into examples/renders.

    python examples/rod_arm.py

Units are millimeters. The power report is quasi-static.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from aicad.document import Document

ROOT = Path(__file__).resolve().parent
RENDERS = ROOT / "renders"


def _box(size: list[float], center: list[float]) -> list[dict]:
    return [{"op": "add", "shape": "box", "size": size, "center": center}]


def assemble(document: Document) -> None:
    """Add the arm parts, the leg, and both assemblies to a document."""
    document.create_part_from_ops("base_block", _box([100, 80, 50], [0, 0, 25]))
    document.create_part_from_ops("upper_link", _box([220, 28, 28], [110, 0, 0]))
    document.create_part_from_ops("fore_link", _box([180, 22, 22], [90, 0, 0]))
    document.create_part_from_ops("gripper_body", _box([40, 50, 36], [20, 0, 0]))
    document.create_part_from_ops("jaw_plate", _box([8, 12, 28], [8, 0, 0]))
    document.create_part_from_ops("rod_bar", _box([20, 20, 400], [0, 0, -200]))
    document.insert_standard_part("hose_clip", "washer", "M3")

    document.create_part_from_ops("pelvis_block", _box([40, 30, 20], [0, 0, 250]))
    document.create_part_from_ops("thigh_link", _box([24, 24, 120], [0, 0, -60]))
    document.create_part_from_ops("shin_link", _box([18, 18, 110], [0, 0, -55]))
    document.create_part_from_ops("foot_block", _box([70, 28, 16], [20, 0, -8]))
    document.create_part_from_ops("obstacle", _box([50, 40, 24], [25, 0, 12]))

    document.create_assembly(
        {
            "name": "rod_arm",
            "instances": [
                {"name": "base", "part": "base_block", "material": "steel"},
                {"name": "clip", "part": "hose_clip", "material": "steel"},
                {"name": "upper", "part": "upper_link", "material": "aluminum_6061"},
                {"name": "fore", "part": "fore_link", "material": "aluminum_6061"},
                {"name": "gripper", "part": "gripper_body", "material": "steel"},
                {"name": "jaw_l", "part": "jaw_plate", "material": "steel"},
                {"name": "jaw_r", "part": "jaw_plate", "material": "steel"},
                {
                    "name": "rod",
                    "part": "rod_bar",
                    "material": "steel",
                    "payload_kg": 8,
                    "payload_at": [0, 0, -200],
                },
            ],
            "joints": [
                {
                    "name": "clip",
                    "type": "fixed",
                    "parent": "base",
                    "child": "clip",
                    "parent_anchor": [30, 48, 50],
                },
                {
                    "name": "shoulder",
                    "type": "revolute",
                    "parent": "base",
                    "child": "upper",
                    "parent_anchor": [0, 0, 50],
                    "child_anchor": [0, 0, 0],
                    "axis": [0, 1, 0],
                    "value": 30,
                    "min": -50,
                    "max": 45,
                },
                {
                    "name": "elbow",
                    "type": "revolute",
                    "parent": "upper",
                    "child": "fore",
                    "parent_anchor": [220, 0, 0],
                    "child_anchor": [0, 0, 0],
                    "axis": [0, 1, 0],
                    "value": 35,
                    "min": -15,
                    "max": 80,
                },
                {
                    "name": "wrist",
                    "type": "fixed",
                    "parent": "fore",
                    "child": "gripper",
                    "parent_anchor": [180, 0, 0],
                },
                {
                    "name": "jaw",
                    "type": "revolute",
                    "parent": "gripper",
                    "child": "jaw_l",
                    "parent_anchor": [28, -22, 0],
                    "axis": [0, 0, 1],
                    "value": 12,
                    "min": -25,
                    "max": 25,
                },
                {
                    "name": "jaw_r",
                    "type": "revolute",
                    "parent": "gripper",
                    "child": "jaw_r",
                    "parent_anchor": [28, 22, 0],
                    "axis": [0, 0, 1],
                    "value": 0,
                    "min": -25,
                    "max": 25,
                },
                {
                    "name": "grasp",
                    "type": "fixed",
                    "parent": "gripper",
                    "child": "rod",
                    "parent_anchor": [36, 0, -8],
                },
            ],
            "relations": [
                {"name": "mesh", "type": "gear", "driver": "jaw", "driven": "jaw_r", "ratio": -1, "offset": 0}
            ],
            "poses": {
                "reach": {"shoulder": 30, "elbow": 35, "jaw": 12},
                "lift": {"shoulder": -40, "elbow": 5, "jaw": 12},
            },
            "motions": {
                "lift": {
                    "keyframes": [{"pose": "reach"}, {"pose": "lift"}],
                    "samples": 5,
                    "duration_s": 2,
                }
            },
            "cables": [
                {
                    "name": "hose",
                    "kind": "hose",
                    "diameter_mm": 8,
                    "min_bend_radius_mm": 18,
                    "stiffness": "high",
                    "max_strain": 0.15,
                    "waypoints": [
                        {"instance": "base", "at": [20, 70, 62], "clip": True},
                        {"instance": "upper", "at": [30, 70, 0], "clip": True},
                        {"instance": "upper", "at": [190, 70, 0], "clip": True},
                        {"instance": "fore", "at": [24, 70, 0], "clip": True},
                        {"instance": "fore", "at": [150, 70, 0], "clip": True},
                        {"instance": "gripper", "at": [18, 70, 0], "clip": True},
                    ],
                },
                {
                    "name": "cable",
                    "kind": "cable",
                    "diameter_mm": 5,
                    "min_bend_radius_mm": 12,
                    "stiffness": "medium",
                    "max_strain": 0.15,
                    "waypoints": [
                        {"instance": "base", "at": [20, -58, 40], "clip": True},
                        {"instance": "upper", "at": [30, -52, 0], "clip": True},
                        {"instance": "upper", "at": [190, -52, 0], "clip": True},
                        {"instance": "fore", "at": [24, -52, 0], "clip": True},
                        {"instance": "fore", "at": [150, -52, 0], "clip": True},
                        {"instance": "gripper", "at": [18, -52, 0], "clip": True},
                    ],
                },
            ],
            "actuators": [
                {
                    "name": "shoulder_cylinder",
                    "kind": "hydraulic_cylinder",
                    "joint": "shoulder",
                    "bore_mm": 20,
                    "rod_mm": 10,
                    "moment_arm_mm": 50,
                    "efficiency": 0.85,
                },
                {
                    "name": "elbow_cylinder",
                    "kind": "hydraulic_cylinder",
                    "joint": "elbow",
                    "bore_mm": 16,
                    "rod_mm": 8,
                    "moment_arm_mm": 40,
                    "efficiency": 0.85,
                },
            ],
        }
    )
    document.create_assembly(
        {
            "name": "test_leg",
            "instances": [
                {"name": "pelvis", "part": "pelvis_block", "material": "aluminum_6061"},
                {"name": "thigh", "part": "thigh_link", "material": "aluminum_6061"},
                {"name": "shin", "part": "shin_link", "material": "aluminum_6061"},
                {"name": "foot", "part": "foot_block", "material": "aluminum_6061"},
                {"name": "block", "part": "obstacle", "material": "steel"},
            ],
            "joints": [
                {
                    "name": "hip",
                    "type": "revolute",
                    "parent": "pelvis",
                    "child": "thigh",
                    "parent_anchor": [0, 0, 240],
                    "axis": [0, 1, 0],
                    "value": 0,
                    "min": -40,
                    "max": 50,
                },
                {
                    "name": "knee",
                    "type": "revolute",
                    "parent": "thigh",
                    "child": "shin",
                    "parent_anchor": [0, 0, -120],
                    "axis": [0, 1, 0],
                    "value": 0,
                    "min": 0,
                    "max": 90,
                },
                {
                    "name": "ankle",
                    "type": "fixed",
                    "parent": "shin",
                    "child": "foot",
                    "parent_anchor": [0, 0, -110],
                },
            ],
            "motions": {
                "walk": {
                    "keyframes": [
                        {"joints": {"hip": 0, "knee": 0}},
                        {"joints": {"hip": -15, "knee": 55}},
                        {"joints": {"hip": 30, "knee": 40}},
                        {"joints": {"hip": 0, "knee": 0}},
                    ],
                    "samples": 5,
                    "duration_s": 1.2,
                }
            },
        }
    )


def _origin(document: Document, assembly: str, instance: str) -> list[float]:
    solved = document.get_assembly(assembly)["solved"]
    return next(item["origin_mm"] for item in solved["instances"] if item["name"] == instance)


def _copy_pictures(report: dict, stem: str) -> dict:
    pictures = report.get("pictures") or {}
    copied = {}
    RENDERS.mkdir(parents=True, exist_ok=True)
    if pictures.get("strip"):
        destination = RENDERS / f"{stem}_strip.png"
        shutil.copyfile(pictures["strip"], destination)
        copied["strip"] = str(destination)
    if pictures.get("gif"):
        destination = RENDERS / f"{stem}.gif"
        shutil.copyfile(pictures["gif"], destination)
        copied["gif"] = str(destination)
    return copied


def summarize(document: Document) -> dict:
    """Motion, cable, and power numbers for the arm, and the leg's collisions."""
    document.set_pose("rod_arm", "reach")
    reach_z = _origin(document, "rod_arm", "rod")[2]
    document.set_pose("rod_arm", "lift")
    lift_z = _origin(document, "rod_arm", "rod")[2]
    motion = document.run_motion("rod_arm", sequence="lift")
    cables = document.check_cables("rod_arm", sequence="lift")
    power = document.estimate_power("rod_arm", sequence="lift")
    walk = document.run_motion("test_leg", sequence="walk")
    return {
        "rod_z_reach_mm": reach_z,
        "rod_z_lift_mm": lift_z,
        "lift": {
            "status": motion["status"],
            "collision_count": motion["collision_count"],
            "rod_z_mm": [frame["origins_mm"]["rod"][2] for frame in motion["frames"]],
            "pictures": _copy_pictures(motion, "rod_arm"),
        },
        "cables": {
            "status": cables["status"],
            "routes": [
                {
                    "name": cable["name"],
                    "status": cable["status"],
                    "length_mm": cable["length_mm"],
                    "length_change_mm": cable["length_change_mm"],
                    "max_strain": cable["max_strain"],
                    "min_bend_radius_mm": cable["min_bend_radius_mm"],
                    "bend_limit_mm": cable["bend_limit_mm"],
                    "pinch_count": cable["pinch_count"],
                }
                for cable in cables["cables"]
            ],
            "assumptions": cables["assumptions"],
        },
        "power": {
            "duration_s": power["duration_s"],
            "pump": power["pump"],
            "actuators": [
                {
                    "name": item["name"],
                    "peak_torque_nm": item["peak_torque_nm"],
                    "peak_force_n": item["peak_force_n"],
                    "peak_pressure_mpa": item["peak_pressure_mpa"],
                    "peak_power_w": item["peak_power_w"],
                    "displaced_volume_mm3": item["displaced_volume_mm3"],
                }
                for item in power["actuators"]
            ],
            "assumptions": power["assumptions"],
        },
        "walk": {
            "status": walk["status"],
            "collision_count": walk["collision_count"],
            "colliding_frames": walk["colliding_frames"],
            "pictures": _copy_pictures(walk, "rod_leg"),
        },
    }


def main() -> None:
    workspace = ROOT / "rod_arm_workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    document = Document(workspace)
    assemble(document)
    report = summarize(document)
    RENDERS.mkdir(parents=True, exist_ok=True)
    path = RENDERS / "rod_arm_report.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"rod z {report['rod_z_reach_mm']:.1f} mm at reach, {report['rod_z_lift_mm']:.1f} mm at lift")
    print(f"lift {report['lift']['status']}, collisions {report['lift']['collision_count']}")
    for route in report["cables"]["routes"]:
        print(
            f"{route['name']} {route['status']}: strain {route['max_strain']}, "
            f"bend {route['min_bend_radius_mm']} mm, pinches {route['pinch_count']}"
        )
    for actuator in report["power"]["actuators"]:
        print(
            f"{actuator['name']}: {actuator['peak_torque_nm']} N·m, "
            f"{actuator['peak_pressure_mpa']} MPa, {actuator['peak_power_w']} W"
        )
    print(
        f"pump {report['power']['pump']['mean_power_w']} W, "
        f"{report['power']['pump']['displaced_volume_mm3']} mm³"
    )
    print(f"walk {report['walk']['status']} on frames {report['walk']['colliding_frames']}")
    print(path)


if __name__ == "__main__":
    main()
