"""A viewport click builds a catalog mount, and the spin snaps to 15 degrees."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PICK = ROOT / "src" / "aicad" / "app_static" / "mount_pick.js"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "mount_pick.js" });
const hit = { part: "shaft", ref: "cylinder/r4/z+/0,0,0" };
const mounted = sandbox.mountPickRequest("bearing", hit, { rotation_deg: 22, flip: false, mate: "bore" });
const flipped = sandbox.mountPickRequest("motor", { part: "plate", ref: "plane/z+/0,0,10" }, { rotation_deg: 90, flip: true });
const same = sandbox.mountPickRequest("bearing", { part: "bearing", ref: "plane/z+/0,0,0" }, null);
const empty = sandbox.mountPickRequest("bearing", null, null);
const unselected = sandbox.mountPickRequest("", hit, null);
process.stdout.write(JSON.stringify({
  mounted,
  flipped,
  same,
  empty,
  unselected,
  seven: sandbox.snapMountDegrees(7),
  half: sandbox.snapMountDegrees(7.5),
  negative: sandbox.snapMountDegrees(-10),
  turn: sandbox.snapMountDegrees(180),
  full: sandbox.snapMountDegrees(360),
  bad: sandbox.snapMountDegrees("nope"),
}));
"""


def test_a_click_on_another_part_requests_a_mount(tmp_path):
    page = (ROOT / "src" / "aicad" / "app_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/mount_pick.js") < page.index("/static/app.js")
    assert 'data-mode="mount"' in page
    assert "mountPickRequest" in script
    assert "data-mount-spin" in script
    node = shutil.which("node")
    assert node
    probe = tmp_path / "mount-pick.js"
    probe.write_text(_PROBE, encoding="utf-8")
    result = subprocess.run(
        [node, str(probe), str(PICK)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["mounted"] == {
        "name": "bearing",
        "mount": {
            "part": "shaft",
            "ref": "cylinder/r4/z+/0,0,0",
            "rotation_deg": 15,
            "flip": False,
            "mate": "bore",
        },
    }
    assert report["flipped"]["mount"]["flip"] is True
    assert report["flipped"]["mount"]["rotation_deg"] == 90
    assert report["same"]["error"] == "Pick a face on another part."
    assert report["empty"]["error"] == "Pick a face or a round face to mount on."
    assert report["unselected"]["error"] == "Select the catalog part to mount."
    assert report["seven"] == 0
    assert report["half"] == 15
    assert report["negative"] == -15
    assert report["turn"] == 180
    assert report["full"] == 0
    assert report["bad"] == 0
