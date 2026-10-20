"""Modes and bolted joints for the drill-rod arm.

The upper link is held at the shoulder end. A drill rotary at 120 rpm is
declared, and a hydraulic pump is placed at 97% of the first flexible
frequency so the report shows a near-resonance. Four M8 bolts clamp the base
flange. The shear is enough to slip the joint while the bolts stay below yield.

    python examples/vibration_arm.py

Pictures and the report land in examples/renders. The session is
examples/vibration_workspace.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from aicad.bolts import check_joint
from aicad.document import Document
from aicad.modal import classify_resonance, fatigue_check, harmonic_response, random_response
from rod_arm import assemble

ROOT = Path(__file__).resolve().parent
RENDERS = ROOT / "renders"
WORKSPACE = ROOT / "vibration_workspace"


def _flexible(report: dict) -> float:
    for mode in report["modes"]:
        if not mode["rigid_body"]:
            return float(mode["frequency_hz"])
    raise RuntimeError("no flexible mode")


def _with_pump(report: dict, fn: float) -> dict:
    """Restate the excitation so a pump line sits just under the computed mode."""
    excitations = [
        {"name": "drill rotary", "kind": "drill", "rpm": 120},
        {
            "name": "hydraulic pump",
            "kind": "pump",
            "hz": round(fn * 0.97, 3),
            "note": "Set at 97% of the first flexible frequency so the margin flags a near-resonance.",
        },
    ]
    updated = dict(report)
    updated["resonance"] = classify_resonance(report["modes"], excitations)
    updated["stated_excitations"] = excitations
    return updated


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    document = Document(WORKSPACE)
    assemble(document)

    link = document.solve_modal(
        "upper_link",
        {
            "material": "aluminum_6061",
            "mesh_size_mm": 6,
            "modes": 4,
            "render_modes": 1,
            "fixes": [{"name": "shoulder", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
            "excitations": [{"name": "drill rotary", "kind": "drill", "rpm": 120}],
        },
    )
    fn = _flexible(link)
    link = _with_pump(link, fn)
    link["harmonic"] = harmonic_response(
        {"frequency_hz": round(fn * 0.97, 3), "force_n": [0, 0, -20], "zeta": 0.02},
        natural_hz=fn,
        modal_force=1.0,
        phi_norm=1.0,
    )
    link["harmonic"]["note"] = (
        "Displacement is for a unit modal force at ζ = 0.02, not a force integrated over the mesh. "
        "The amplification is the number that matters next to the pump line."
    )
    link["random"] = random_response({"psd_g2_per_hz": 0.02, "zeta": 0.02, "stress_per_g": 15}, fn)
    link["random"]["note"] = "stress_per_g is a stated 15 MPa per g. It was not computed from this mesh."

    assembly = document.solve_assembly_modal(
        "rod_arm",
        {
            "mesh_size_mm": 12,
            "modes": 4,
            "render_modes": 1,
            "excitations": [{"name": "drill rotary", "kind": "drill", "rpm": 120}],
        },
    )
    assembly = _with_pump(assembly, _flexible(assembly))

    bolts = check_joint(
        {
            "vibration": "intense",
            "condition": "dry",
            "friction": 0.14,
            "grip_mm": 20,
            "axial_n": 1000,
            "bolts": [
                {"name": "base flange M8", "size": "M8", "grade": "8.8", "shear_n": 2500},
                {"name": "contrast M12", "size": "M12", "grade": "10.9", "shear_n": 800},
            ],
        }
    )
    bolts["load_note"] = (
        "Each fastener is given 1000 N of separating shock and a transverse share of the rig. "
        "This is not the 8 kg rod's static weight. The grip is a 20 mm flange, not the full 50 mm base block."
    )
    fatigue = fatigue_check(
        {
            "material": "steel",
            "stress_amplitude_mpa": 90,
            "mean_stress_mpa": 40,
            "surface": "machined",
            "section_mm": 12,
            "target_safety_factor": 2,
        }
    )

    RENDERS.mkdir(parents=True, exist_ok=True)
    picture = next((view for view in link.get("views") or [] if str(view["name"]).endswith("_contact")), None)
    if picture:
        shutil.copyfile(picture["path"], RENDERS / "vibration_mode.png")
    assembly_picture = next(
        (view for view in assembly.get("views") or [] if str(view["name"]).endswith("_contact")),
        None,
    )
    if assembly_picture:
        shutil.copyfile(assembly_picture["path"], RENDERS / "vibration_assembly_mode.png")

    payload = {
        "source": "drill rotary at 120 rpm, and a hydraulic pump at 97% of each first flexible frequency",
        "upper_link_hz": fn,
        "upper_link": _compact(link),
        "assembly_hz": _flexible(assembly),
        "assembly": _compact(assembly),
        "bolts": bolts,
        "fatigue": fatigue,
    }
    (RENDERS / "vibration_report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "upper_link_hz": fn,
                "link_resonance": link["resonance"]["status"],
                "assembly_hz": payload["assembly_hz"],
                "assembly_resonance": assembly["resonance"]["status"],
                "bolt_worst": bolts["worst"],
                "m8_slip": bolts["bolts"][0]["slip_margin"],
                "m8_yield": bolts["bolts"][0]["yield_margin"],
                "fatigue_sf": fatigue["safety_factor"],
            },
            indent=2,
        )
    )


def _compact(report: dict) -> dict:
    kept = dict(report)
    for mode in kept.get("modes") or []:
        mode.pop("views", None)
    kept.pop("views", None)
    kept.pop("log_tail", None)
    return kept


if __name__ == "__main__":
    main()
