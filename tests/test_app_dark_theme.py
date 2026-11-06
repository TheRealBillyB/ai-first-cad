"""The app's dark theme stays readable, and the grid sits on real multiples."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
PLAN_CSS = (STATIC / "plan.css").read_text(encoding="utf-8")
SCRIPT = (STATIC / "app.js").read_text(encoding="utf-8")
PAGE = (STATIC / "index.html").read_text(encoding="utf-8")
TOKENS = dict(re.findall(r"--([a-z0-9-]+): (#[0-9a-f]{6});", CSS))

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "viewport_guides.js" });
const scene = vm.runInContext("guideGeometry([13, -27, 40], 80)", sandbox);
const moved = vm.runInContext("guideGeometry([14, -26, 40], 80)", sandbox);
process.stdout.write(JSON.stringify({
  steps: [0.5, 8, 80, 200, 450, 2000, 30000].map((distance) => vm.runInContext(`gridStep(${distance})`, sandbox)),
  step: scene.step,
  center: scene.center,
  movedCenter: moved.center,
  radius: scene.radius,
  positions: Array.from(scene.positions),
  colors: Array.from(scene.colors),
}));
"""


def _channel(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("surface", ["paper", "paper-2", "raised", "selected", "field", "viewport"])
@pytest.mark.parametrize("text", ["ink", "muted", "brass", "pass", "fail", "warn", "free"])
def test_text_is_readable_on_every_surface(text, surface):
    assert _contrast(TOKENS[text], TOKENS[surface]) >= 4.5


@pytest.mark.parametrize(
    ("text", "surface"),
    [("on-accent", "brass"), ("pass", "pass-bg"), ("fail", "fail-bg"), ("warn", "warn-bg"), ("ink", "warn-bg"), ("ink", "fail-bg")],
)
def test_text_is_readable_on_its_own_tint(text, surface):
    assert _contrast(TOKENS[text], TOKENS[surface]) >= 4.5


def test_form_controls_and_scrollbars_are_dark_too():
    assert "color-scheme: dark;" in CSS


@pytest.mark.parametrize("sheet", [CSS, PLAN_CSS])
def test_no_light_surface_is_left_behind(sheet):
    assert not re.search(r"background: (white|#fff)\b", sheet)
    assert not re.search(r"var\(--[a-z0-9-]+, #", sheet)


def test_only_pictures_keep_the_light_sheet():
    users = sorted(line.split("{")[0].strip() for line in CSS.splitlines() if "var(--sheet)" in line)
    assert users == ["#assembly-picture", ".panel img", ".picture-viewer-image"]


def test_the_sketch_uses_the_same_colors_as_the_theme():
    found = dict(re.findall(r"^  ([a-z]+): \"(#[0-9a-f]{6})\",$", SCRIPT, re.M))
    assert found["line"] == TOKENS["ink"]
    assert found["faint"] == TOKENS["muted"]
    assert found["selected"] == TOKENS["brass"]
    assert found["free"] == TOKENS["free"]
    assert _contrast(found["line"], TOKENS["viewport"]) >= 4.5


def test_the_guides_are_drawn_before_the_parts_and_are_not_picked():
    assert PAGE.index("/static/viewport_guides.js") < PAGE.index("/static/app.js")
    draw = SCRIPT[SCRIPT.index("function drawSolid()") :]
    assert draw.index("drawViewportGuides(") < draw.index("gl.useProgram(view.program)")
    pick = SCRIPT[SCRIPT.index("function pickHit(") : SCRIPT.index("function rayDirection(")]
    assert "guide" not in pick.lower()
    source = (STATIC / "viewport_guides.js").read_text(encoding="utf-8")
    assert source.index("gl.disable(gl.DEPTH_TEST)") < source.index("gl.drawArrays") < source.index("gl.enable(gl.DEPTH_TEST)")


def test_the_grid_is_on_whole_multiples_and_names_its_spacing(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "guides.js"
    probe.write_text(_PROBE, encoding="utf-8")
    report = json.loads(
        subprocess.run([node, str(probe), str(STATIC / "viewport_guides.js")], check=True, capture_output=True, text=True).stdout
    )
    assert report["steps"] == [0.02, 0.5, 5, 10, 20, 100, 2000]
    step = report["step"]
    assert step == 5
    assert report["radius"] == 150
    # A small pan does not move the lines.
    assert report["center"] == report["movedCenter"] == [25, -25]
    positions = report["positions"]
    points = [positions[index : index + 3] for index in range(0, len(positions), 3)]
    grid, axes = points[:-6], points[-6:]
    assert all(point[2] == 0 for point in grid)
    assert all(point[0] % step == 0 and point[1] % step == 0 for point in grid)
    assert axes[0] == [0, 0, 0] and axes[1] == [50, 0, 0]
    assert axes[2] == [0, 0, 0] and axes[3] == [0, 50, 0]
    assert axes[4] == [0, 0, 0] and axes[5] == [0, 0, 50]
    assert len(report["colors"]) == len(positions)


def test_text_on_the_light_sheet_stays_readable():
    """A picture that does not load shows its name on the sheet."""
    users = [line for line in CSS.splitlines() if "var(--sheet)" in line]
    assert users and all("color: var(--on-accent)" in line for line in users)
    assert _contrast(TOKENS["on-accent"], TOKENS["sheet"]) >= 4.5
