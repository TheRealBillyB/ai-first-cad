"""A click along a long edge hits the edge, not only its sample points."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PICK = ROOT / "src" / "aicad" / "app_static" / "edge_pick.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "edge_pick.js" });

const identity = (point) => point;
const longEdge = {
  ref: "line:long",
  part: "rail",
  points: [[0, 0], [400, 0]],
};
const post = {
  ref: "line:post",
  part: "rail",
  points: [[0, 0], [0, 30]],
};
const bend = {
  ref: "curve:bend",
  part: "hook",
  points: [[0, 0], [80, 0], [80, 90]],
};
const middle = sandbox.edgePickHit(200, 4, [longEdge, post], identity, 18);
const off = sandbox.edgePickHit(200, 40, [longEdge], identity, 18);
const end = sandbox.edgePickHit(1, 12, [longEdge, post], identity, 18);
const alongBend = sandbox.edgePickHit(80, 40, [bend, longEdge], identity, 18);
const gap = sandbox.edgePickDistance(50, 0, [[0, 0], null, [100, 0]], (point) => point);
const pointOnly = Math.min(Math.hypot(200 - 0, 4 - 0), Math.hypot(200 - 400, 4 - 0));
process.stdout.write(JSON.stringify({
  middle,
  middleDistance: sandbox.edgePickDistance(200, 4, longEdge.points, identity),
  pointOnly,
  off,
  end,
  alongBend,
  gap,
}));
"""


def test_a_click_along_a_long_edge_hits_the_whole_edge(tmp_path):
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/edge_pick.js") < page.index("/static/app.js")
    assert "edgePickHit" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "edge-pick.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(PICK)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["middle"] == {"ref": "line:long", "part": "rail"}
    assert report["middleDistance"] == 4
    assert report["pointOnly"] > 18
    assert report["off"] is None
    assert report["end"]["ref"] == "line:post"
    assert report["alongBend"] == {"ref": "curve:bend", "part": "hook"}
    assert report["gap"] == 50
