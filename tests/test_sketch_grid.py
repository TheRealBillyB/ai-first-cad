"""The sketch grid draws both directions and follows the view."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOM = ROOT / "src" / "aicad" / "app_static" / "sketch_geom.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "sketch_geom.js" });

function summarize(lines) {
  const vertical = lines.filter((line) => line.axis === "x");
  const horizontal = lines.filter((line) => line.axis === "y");
  return {
    vertical: vertical.map((line) => line.at),
    horizontal: horizontal.map((line) => line.at),
    sample: lines.find((line) => line.axis === "x" && line.at === 0) || null,
    next: lines.find((line) => line.axis === "x" && line.at === 10) || null,
    row: lines.find((line) => line.axis === "y" && line.at === 0) || null,
  };
}

const base = { width: 200, height: 100, scale: 2, spacing: 10, rotation: 0 };
const centered = summarize(sandbox.sketchGridLines({ ...base, centerX: 0, centerY: 0 }));
const panned = summarize(sandbox.sketchGridLines({ ...base, centerX: 15, centerY: -7, rotation: 0 }));
const zoomed = summarize(sandbox.sketchGridLines({ ...base, centerX: 0, centerY: 0, scale: 4 }));
const turned = summarize(sandbox.sketchGridLines({
  ...base, centerX: 0, centerY: 0, rotation: Math.PI / 2,
}));
const configured = sandbox.sketchGridSpacing({ spec: { grid_mm: 5 } });
const fallback = sandbox.sketchGridSpacing({ spec: {} });
process.stdout.write(JSON.stringify({ centered, panned, zoomed, turned, configured, fallback }));
"""


def _gap(values):
    ordered = sorted(values)
    return [round(ordered[index + 1] - ordered[index], 6) for index in range(len(ordered) - 1)]


def test_the_sketch_grid_draws_both_axes_as_the_view_moves(tmp_path):
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert "sketchGridLines" in script
    assert "sketchGridSpacing" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "sketch-grid.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(GEOM)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    centered = report["centered"]
    assert centered["vertical"]
    assert centered["horizontal"]
    assert 0 in centered["vertical"]
    assert 0 in centered["horizontal"]
    assert set(_gap(centered["vertical"])) == {10}
    assert set(_gap(centered["horizontal"])) == {10}
    sample = centered["sample"]["screen"]
    assert sample[0] == sample[2]
    assert sample[1] != sample[3]
    row = centered["row"]["screen"]
    assert row[1] == row[3]
    assert row[0] != row[2]

    panned = report["panned"]
    assert 0 in panned["vertical"]
    assert 0 in panned["horizontal"]
    assert all(value % 10 == 0 for value in panned["vertical"])
    assert all(value % 10 == 0 for value in panned["horizontal"])
    assert panned["sample"]["at"] == 0
    assert panned["sample"]["screen"][0] != centered["sample"]["screen"][0]

    def pitch(summary):
        return abs(summary["next"]["screen"][0] - summary["sample"]["screen"][0])

    assert pitch(centered) == 20
    assert pitch(report["zoomed"]) == 40

    turned = report["turned"]["sample"]["screen"]
    assert abs(turned[1] - turned[3]) < 1e-6
    assert abs(turned[0] - turned[2]) > 1
    assert report["configured"] == 5
    assert report["fallback"] == 10
