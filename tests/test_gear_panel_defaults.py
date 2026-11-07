"""The Gear tab's default values build every family, the hypoid included.

The tab shares one Teeth field across families. Its hypoid mate defaulted to
16 teeth against 18, so choosing Hypoid and clicking Create gear failed with
"No hypoid pitch point fits". A hypoid ring needs more teeth than its pinion.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad.errors import GeometryError
from aicad.gears import build_gear, gear_data

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
TOOLS = (STATIC / "tools.js").read_text(encoding="utf-8")

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const start = source.indexOf("function hypoidCountsForRole(");
const end = source.indexOf("\n}\n", start);
if (start < 0 || end < 0) throw new Error("tools.js no longer has hypoidCountsForRole");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end + 3), sandbox);
sandbox.cases = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(vm.runInContext("cases.map(([role, teeth, mate]) => hypoidCountsForRole(role, teeth, mate))", sandbox)));
"""


def _defaults() -> dict[str, str]:
    draft = TOOLS[TOOLS.index("const draft = ui.gearDraft || {") : TOOLS.index("};", TOOLS.index("const draft = ui.gearDraft || {"))]
    return dict(re.findall(r'^\s+(\w+): "([^"]*)",', draft, re.M))


def _counts_for_role(tmp_path, cases):
    node = shutil.which("node")
    assert node, "Node runs the page scripts in these tests"
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run([node, str(probe), str(STATIC / "tools.js"), json.dumps(cases)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _hypoid_spec(defaults: dict[str, str], role: str, teeth: str, mate: str) -> dict:
    """What gearSpecFromForm sends for a hypoid."""
    return {
        "type": "hypoid",
        "teeth": float(teeth),
        "face_width_mm": float(defaults["face"]),
        "pressure_angle_deg": float(defaults["pressure"]),
        "role": role,
        "mate_teeth": float(mate),
        "offset_mm": float(defaults["offset"]),
        "ring_diameter_mm": float(defaults["ring"]),
        "spiral_angle_deg": float(defaults["hypoidSpiral"]),
        "shaft_angle_deg": float(defaults["hypoidShaft"]),
    }


def test_the_default_hypoid_pinion_builds():
    defaults = _defaults()
    assert defaults["role"] == "pinion"
    build_gear(_hypoid_spec(defaults, "pinion", defaults["teeth"], defaults["hypoidMate"]))


def test_switching_the_default_to_ring_swaps_the_counts_and_builds(tmp_path):
    defaults = _defaults()
    [counts] = _counts_for_role(tmp_path, [["ring", defaults["teeth"], defaults["hypoidMate"]]])
    assert float(counts["teeth"]) > float(counts["mate"])
    build_gear(_hypoid_spec(defaults, "ring", counts["teeth"], counts["mate"]))


def test_the_other_families_build_from_the_defaults():
    defaults = _defaults()
    common = {"teeth": float(defaults["teeth"]), "face_width_mm": float(defaults["face"]), "pressure_angle_deg": float(defaults["pressure"]), "module_mm": float(defaults["module"])}
    gear_data({"type": "spur", **common})
    gear_data({"type": "helical", **common, "helix_angle_deg": float(defaults["helix"])})
    bevel = {**common, "mate_teeth": float(defaults["mate"]), "shaft_angle_deg": float(defaults["shaft"])}
    gear_data({"type": "straight_bevel", **bevel})
    gear_data({"type": "spiral_bevel", **bevel, "spiral_angle_deg": float(defaults["spiral"])})


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (["ring", "18", "36"], {"teeth": "36", "mate": "18"}),
        (["ring", "36", "18"], {"teeth": "36", "mate": "18"}),
        (["pinion", "36", "18"], {"teeth": "18", "mate": "36"}),
        (["pinion", "8", "16"], {"teeth": "8", "mate": "16"}),
        (["ring", "", "16"], {"teeth": "", "mate": "16"}),
        (["ring", "x", "16"], {"teeth": "x", "mate": "16"}),
    ],
)
def test_the_role_switch_puts_more_teeth_on_the_ring(tmp_path, case, expected):
    assert _counts_for_role(tmp_path, [case]) == [expected]


def test_a_ring_with_fewer_teeth_than_its_pinion_is_named_in_the_error():
    defaults = _defaults()
    with pytest.raises(GeometryError, match="Change one of those four") as refused:
        gear_data(_hypoid_spec(defaults, "pinion", "18", "16"))
    assert "The ring has 16 teeth and the pinion 18" in str(refused.value)
