"""Modal frequencies, resonance flags, and bolted-joint loosening."""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from aicad import kernel
from aicad.bolts import check_joint
from aicad.document import Document
from aicad.fea import fea_available
from aicad.kinematics import normalize
from aicad.modal import (
    _parse_dat,
    classify_resonance,
    fatigue_check,
    harmonic_response,
    random_response,
    solve_modal,
    solve_modal_assembly,
)
from aicad.workspace import Workspace

_AVAILABLE, _REASON = fea_available()
needs_solver = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)

# 200 x 8 x 8 mm steel cantilever. tonne-mm-s, so 1 N = 1 tonne·mm/s².
_EULER_HZ = (1.8751040684**2) / (2 * math.pi * 200.0**2) * math.sqrt(
    210000.0 * (8.0**4 / 12.0) / (7.85e-9 * 64.0)
)


def test_bolt_slip_is_high_while_the_bolt_stays_below_yield():
    report = check_joint(
        {
            "size": "M8",
            "grade": "8.8",
            "condition": "dry",
            "preload_fraction": 0.70,
            "friction": 0.14,
            "grip_mm": 20,
            "axial_n": 1000,
            "shear_n": 2500,
            "vibration": "intense",
        }
    )
    assert report["status"] == "fail"
    assert report["loosening"]["rating"] == "high"
    assert report["slip_status"] == "slips"
    assert report["slip_margin"] < 1
    assert report["yield_margin"] > 1
    assert report["stripping"]["status"] == "holds"
    expected_torque = report["nut_factor"] * report["preload_n"] * 8 / 1000
    assert report["tightening_torque_nm"] == pytest.approx(expected_torque, rel=0.01)
    names = [item["name"] for item in report["loosening"]["countermeasures"]]
    assert names == [
        "higher preload",
        "longer grip length",
        "nyloc nut",
        "wedge-locking washers",
        "threadlocker",
        "safety wire",
    ]
    assert any(item["recommended"] and item["name"] == "wedge-locking washers" for item in report["loosening"]["countermeasures"])
    assert len(report["assumptions"]) >= 8


def test_larger_bolt_with_reserve_stays_low_and_the_worst_joint_leads():
    report = check_joint(
        {
            "vibration": "intense",
            "friction": 0.14,
            "grip_mm": 20,
            "axial_n": 1000,
            "bolts": [
                {"size": "M8", "grade": "8.8", "shear_n": 2500},
                {"size": "M12", "grade": "10.9", "shear_n": 800},
            ],
        }
    )
    assert report["bolts"][0]["loosening"]["rating"] == "high"
    assert report["bolts"][1]["loosening"]["rating"] == "low"
    assert report["bolts"][1]["slip_margin"] > 3
    assert report["worst"] == "high"
    assert report["assumptions"]


def test_resonance_flags_a_pump_line_and_ignores_a_slow_drill():
    modes = [167.3, 167.3, 1041.0]
    clear = classify_resonance(modes, [{"name": "drill rotary", "kind": "drill", "rpm": 120}])
    assert clear["status"] == "clear"
    assert clear["excitations"][0]["fundamental_hz"] == pytest.approx(2.0)
    assert clear["excitations"][0]["harmonics"] == [1, 2, 3]
    near = classify_resonance(
        modes,
        [
            {"name": "drill rotary", "kind": "drill", "rpm": 120},
            {"name": "hydraulic pump", "kind": "pump", "hz": 167.3 * 0.97},
        ],
    )
    assert near["status"] == "resonance"
    assert near["flags"][0]["source"] == "hydraulic pump"
    assert near["flags"][0]["harmonic"] == 1
    assert any("Stiffen" in line or "stiffen" in line.lower() or "Adding mass" in line or "adding mass" in line.lower() for line in near["suggestions"])
    second = classify_resonance([100.0], [{"name": "engine", "kind": "engine", "rpm": 3000}])
    # 3000 rpm is 50 Hz. Harmonic 2 is 100 Hz.
    assert second["status"] == "resonance"
    assert second["flags"][0]["harmonic"] == 2


def test_goodman_fatigue_for_mild_steel_is_below_a_factor_of_two():
    report = fatigue_check(
        {
            "material": "steel",
            "stress_amplitude_mpa": 90,
            "mean_stress_mpa": 40,
            "surface": "machined",
            "section_mm": 12,
        }
    )
    assert report["endurance_mpa"] == pytest.approx(142.8, abs=0.1)
    assert report["safety_factor"] == pytest.approx(1.378, abs=0.02)
    assert report["status"] == "fail"
    assert report["assumptions"]


def test_harmonic_and_miles_state_their_models():
    harmonic = harmonic_response(
        {"frequency_hz": 100, "force_n": [0, 0, -1], "zeta": 0.02},
        natural_hz=100,
        modal_force=1.0,
        phi_norm=1.0,
    )
    assert harmonic["amplification"] == pytest.approx(25.0, abs=0.05)
    miles = random_response({"psd_g2_per_hz": 0.04, "zeta": 0.02, "stress_per_g": 10}, 100)
    expected = math.sqrt(math.pi / 2 * 100 * 25 * 0.04)
    assert miles["sigma_g"] == pytest.approx(expected, rel=1e-3)
    assert miles["stress_sigma_mpa"] == pytest.approx(10 * expected, rel=1e-3)
    assert miles["assumptions"]


def test_dat_parser_keeps_hertz_and_stops_before_participation():
    text = """
 EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.1105014E+07   0.1051196E+04   0.1673031E+03   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S

      1   0.3403915E-08   0.2480455E-02   0.7441614E-02   0.1000000E+00
"""
    modes = _parse_dat(text, 4)
    assert len(modes) == 1
    assert modes[0]["hz"] == pytest.approx(167.3031, rel=1e-6)


def test_workspace_bolt_check_lists_assumptions(tmp_path):
    workspace = Workspace(tmp_path)
    report = workspace.execute(
        "check_bolts",
        {
            "spec": {
                "size": "M8",
                "grade": "8.8",
                "shear_n": 2500,
                "axial_n": 1000,
                "friction": 0.14,
                "grip_mm": 20,
                "vibration": "intense",
            }
        },
        source="api",
    )
    assert report["loosening"]["rating"] == "high"
    assert report["assumptions"]
    state = workspace.execute("get_state")
    assert state["panels"]["bolts"]["loosening"]["rating"] == "high"


def test_cli_bolts(tmp_path):
    spec = tmp_path / "joint.json"
    spec.write_text(
        json.dumps(
            {
                "size": "M12",
                "grade": "10.9",
                "shear_n": 800,
                "axial_n": 1000,
                "friction": 0.14,
                "grip_mm": 20,
                "vibration": "intense",
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "aicad",
            "bolts",
            "--file",
            str(spec),
            "--workspace",
            str(tmp_path / "session"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["loosening"]["rating"] == "low"
    assert payload["assumptions"]


@needs_solver
def test_cantilever_frequency_matches_euler_bernoulli(tmp_path):
    shape = kernel.make_from_ops(
        [{"op": "add", "shape": "box", "size": [200, 8, 8], "center": [100, 0, 0]}]
    )
    report = solve_modal(
        shape,
        {
            "material": "steel",
            "mesh_size_mm": 4,
            "modes": 4,
            "render_modes": 1,
            "fixes": [{"select": {"kind": "plane", "normal": [-1, 0, 0]}}],
            "excitations": [{"name": "drill rotary", "kind": "drill", "rpm": 120}],
        },
        tmp_path,
        part_name="beam",
    )
    first = report["modes"][0]["frequency_hz"]
    assert first == pytest.approx(_EULER_HZ, rel=0.15)
    assert report["resonance"]["status"] == "clear"
    assert report["assumptions"]
    picture = next(view for view in report["views"] if view["name"].endswith("_contact"))
    assert Path(picture["path"]).is_file()
    assert Path(picture["path"]).stat().st_size > 1000


@needs_solver
def test_tied_assembly_is_near_the_solid_beam(tmp_path):
    box = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [40, 12, 12], "center": [20, 0, 0]}])
    solid = kernel.make_from_ops([{"op": "add", "shape": "box", "size": [80, 12, 12], "center": [40, 0, 0]}])
    spec = normalize(
        {
            "name": "pair",
            "instances": [
                {"name": "a", "part": "box", "material": "steel"},
                {"name": "b", "part": "box", "material": "steel"},
            ],
            "joints": [
                {
                    "name": "mid",
                    "type": "fixed",
                    "parent": "a",
                    "child": "b",
                    "parent_anchor": [40, 0, 0],
                    "child_anchor": [0, 0, 0],
                }
            ],
        }
    )
    tied = solve_modal_assembly(
        spec,
        {"box": box},
        {
            "mesh_size_mm": 4,
            "modes": 2,
            "render_modes": 0,
            "fixes": [{"instance": "a", "select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        },
        tmp_path / "tied",
    )
    one = solve_modal(
        solid,
        {
            "material": "steel",
            "mesh_size_mm": 4,
            "modes": 2,
            "render_modes": 0,
            "fixes": [{"select": {"kind": "plane", "normal": [-1, 0, 0]}}],
        },
        tmp_path / "solid",
        part_name="solid",
    )
    assert tied["support"]["automatic"] is False
    ratio = tied["modes"][0]["frequency_hz"] / one["modes"][0]["frequency_hz"]
    assert 0.8 <= ratio <= 1.2
    assert any("locked" in line.lower() or "does not flex" in line.lower() for line in tied["assumptions"])


def test_document_modal_command_uses_a_library_screw_size(tmp_path):
    document = Document(tmp_path)
    document.insert_standard_part("cap", "socket_head_cap_screw", "M8-1.25", length=20)
    report = document.check_bolts(
        {
            "part": "cap",
            "grade": "8.8",
            "shear_n": 2500,
            "axial_n": 1000,
            "friction": 0.14,
            "vibration": "intense",
        }
    )
    assert report["part"] == "cap"
    assert report["size"] == "M8"
    assert report["grip_mm"] == 20
    assert report["loosening"]["rating"] == "high"
