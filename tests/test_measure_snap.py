"""The measure tool snaps to a point along an edge, not only its ends."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "src" / "aicad" / "app_static" / "measure_snap.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "measure_snap.js" });

const identity = (point) => [point[0], point[1]];
const rail = { part: "rail", points: [[0, 0, 0], [400, 0, 0]] };
const post = { part: "rail", points: [[0, 0, 0], [0, 30, 0]] };
const other = { part: "hook", points: [[200, 0, 0], [200, 80, 0]] };
const bend = { part: "rail", points: [[0, 0, 0], [80, 0, 0], [80, 40, 0]] };
const gap = { part: "rail", points: [[0, 0, 0], null, [100, 0, 0]] };
const depth = { part: "rail", points: [[0, 0, 0], [0, 0, 100]] };
const projectDepth = (point) => [point[0], point[2]];
const options = { limit: 14 };
const middle = sandbox.measureSnapPoint(200, 4, [rail, post], identity, options);
const quarter = sandbox.measureSnapPoint(100, 4, [rail], identity, options);
const nearEnd = sandbox.measureSnapPoint(3, 2, [rail], identity, options);
const magnet = sandbox.measureSnapPoint(190, 4, [rail], identity, options);
const off = sandbox.measureSnapPoint(200, 40, [rail], identity, options);
const filtered = sandbox.measureSnapPoint(200, 4, [other], identity, { ...options, part: "rail" });
const alongBend = sandbox.measureSnapPoint(80, 20, [bend, rail], identity, options);
const acrossGap = sandbox.measureSnapPoint(50, 0, [gap], identity, options);
const midDepth = sandbox.measureSnapPoint(3, 50, [depth], projectDepth, options);
const endsOnly = Math.min(Math.hypot(200, 4), Math.hypot(200 - 400, 4));
process.stdout.write(JSON.stringify({
  middle, quarter, nearEnd, magnet, off, filtered, alongBend, acrossGap, midDepth, endsOnly,
}));
"""


def test_measure_snaps_along_an_edge_and_to_its_midpoint(tmp_path):
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/measure_snap.js") < page.index("/static/app.js")
    assert "measureSnapPoint" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "measure-snap.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(SNAP)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["endsOnly"] > 14
    assert report["middle"] == [200, 0, 0]
    assert report["quarter"] == [100, 0, 0]
    assert report["nearEnd"] == [0, 0, 0]
    assert report["magnet"] == [200, 0, 0]
    assert report["off"] is None
    assert report["filtered"] is None
    assert report["alongBend"] == [80, 20, 0]
    assert report["acrossGap"] is None
    assert report["midDepth"] == [0, 0, 50]
