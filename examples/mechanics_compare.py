"""Same motor load on the gusseted bracket and the buried-rib bracket.

The load is `motor_load.json`: PLA, the four frame holes held, 100 N in +X on
the bore (a shaft side load), and 30 N in −Z on the boss top. The target
safety factor is 2. Both solves use that file, so the only difference is the
solid.

    python examples/mechanics_compare.py

Writes `examples/mechanics_report.json` and the stress and displacement
pictures under `examples/renders/`. Each picture is scaled to its own maximum.
The title carries that maximum.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from aicad import kernel
from aicad.fea import solve_part

ROOT = Path(__file__).resolve().parent
SETUP_PATH = ROOT / "motor_load.json"
REPORT_PATH = ROOT / "mechanics_report.json"
RENDER_DIR = ROOT / "renders"


def _relativize(payload: dict) -> dict:
    for view in payload.get("views", []):
        path = Path(view["path"])
        try:
            view["path"] = str(path.relative_to(ROOT.parent))
        except ValueError:
            view["path"] = path.name
    return payload


def _fmt_point(point) -> str:
    if isinstance(point, dict):
        values = (point["x"], point["y"], point["z"])
    elif isinstance(point, (list, tuple)) and len(point) == 3:
        values = point
    else:
        return "an unreported location"
    return f"({values[0]:.2f}, {values[1]:.2f}, {values[2]:.2f}) mm"


def _near_frame_hole(point: dict | None) -> bool:
    """The four frame holes sit at (±30, ±20). A peak within a few millimeters is that edge."""
    if not point:
        return False
    holes = ((30.0, 20.0), (30.0, -20.0), (-30.0, 20.0), (-30.0, -20.0))
    return min(math.hypot(point["x"] - hx, point["y"] - hy) for hx, hy in holes) < 4.0


def _hotspot_sentence(gussets: dict, ribs: dict) -> str:
    g_at = gussets.get("max_von_mises_at_mm")
    r_at = ribs.get("max_von_mises_at_mm")
    if _near_frame_hole(g_at) and _near_frame_hole(r_at):
        return (
            "On this load the hot spot is a frame-hole edge on both parts, so the ribs and the gussets "
            "are not what sets the safety factor."
        )
    return "The two peaks are in different regions of the part. Read each location before deciding where to add material."


def _reading(setup: dict, gussets: dict, ribs: dict) -> str:
    target = float(setup["target_safety_factor"])
    g_sf = gussets["min_safety_factor"]
    r_sf = ribs["min_safety_factor"]
    g_vm = gussets["max_von_mises_mpa"]
    r_vm = ribs["max_von_mises_mpa"]
    lower = "gusseted" if g_vm <= r_vm else "buried-rib"
    other = "buried-rib" if lower == "gusseted" else "gusseted"
    sentences = [
        (
            "Both brackets are PLA under the same design load: the four Ø3.4 frame holes are fixed, "
            "the bore carries 100 N in +X, and the boss top carries 30 N in −Z. "
            f"The target safety factor is {target:g}."
        ),
        (
            f"Gusseted bracket: peak von Mises {g_vm} MPa at {_fmt_point(gussets['max_von_mises_at_mm'])}, "
            f"minimum safety factor {g_sf}, peak displacement {gussets['max_displacement_mm']} mm, "
            f"design review {gussets['review']['status']}."
        ),
        (
            f"Buried-rib bracket: peak von Mises {r_vm} MPa at {_fmt_point(ribs['max_von_mises_at_mm'])}, "
            f"minimum safety factor {r_sf}, peak displacement {ribs['max_displacement_mm']} mm, "
            f"design review {ribs['review']['status']}."
        ),
        (
            f"The {lower} bracket has the lower peak stress. "
            f"The {other} bracket's peak is {max(g_vm, r_vm) / min(g_vm, r_vm):.2f} times that value. "
            + _hotspot_sentence(gussets, ribs)
        ),
    ]
    for label, report in (("Gusseted", gussets), ("Buried-rib", ribs)):
        review = report["review"]
        if review["overstressed"]:
            center = review["overstressed"][0]["center_mm"]
            sentences.append(
                f"{label}: the hottest region is around {_fmt_point(center)} and is a candidate for added material."
            )
        else:
            sentences.append(
                f"{label}: no region is over the allowable stress at this load and target."
            )
        if review["understressed"]:
            center = review["understressed"][0]["center_mm"]
            sentences.append(
                f"{label}: a quiet region around {_fmt_point(center)} carries almost none of the peak and is a candidate for removed material."
            )
        sentences.append(review["note"])
    sentences.append(
        "Each stress picture and each displacement picture is scaled to its own maximum. "
        "Compare the titles, not the colors."
    )
    sentences.append(
        "The mesh, the material, the supports, and the linear-isotropic caveats are in each report's assumptions. "
        "PLA is a printed material here, so the strength is an in-plane tensile number and the part is weaker across layers."
    )
    return " ".join(sentences)


def main() -> None:
    setup = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
    jobs = (
        ("gussets", ROOT / "motor_mount.py", "fea_gussets"),
        ("buried_ribs", ROOT / "motor_mount_buried_ribs.py", "fea_ribs"),
    )
    solved = {}
    for name, script, stem in jobs:
        shape = kernel.make_from_script(script.read_text(encoding="utf-8"))
        report = solve_part(shape, setup, out_dir=RENDER_DIR, stem=stem, part_name=name)
        solved[name] = _relativize(report.to_dict())
        print(
            f"{name}: {report.status}  von Mises {report.max_von_mises_mpa} MPa  "
            f"safety factor {report.min_safety_factor}  displacement {report.max_displacement_mm} mm  "
            f"review {report.review['status']}"
        )
    payload = {
        "units": {"length": "mm", "force": "N", "stress": "MPa"},
        "load_case": setup,
        "gussets": solved["gussets"],
        "buried_ribs": solved["buried_ribs"],
        "reading": _reading(setup, solved["gussets"], solved["buried_ribs"]),
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(payload["reading"])
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
