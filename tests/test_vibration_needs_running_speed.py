"""With no running speed there is nothing to compare, so the check is not a clear.

The Vibration panel sent an empty rpm field as 0 rpm. A 0 Hz line never lands
near a mode, so the verdict read "clear".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad.modal import _headline, classify_resonance

APP = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static" / "app.js"


@pytest.mark.parametrize("excitations", [[], [{"name": "drill", "kind": "drill", "rpm": 0}]])
def test_no_running_speed_is_a_skipped_check_not_a_clear(excitations):
    resonance = classify_resonance([0.0, 833.0, 2400.0], excitations)
    assert resonance["status"] == "skipped"
    assert resonance["flags"] == []
    assert "nothing was compared" in resonance["suggestions"][-1]
    modes = [{"frequency_hz": 833.0, "rigid_body": False}]
    assert _headline(modes, resonance) == (
        "First flexible frequency 833.00 Hz. No running speed was given, so resonance was not checked."
    )


def test_a_running_speed_still_gives_clear_or_resonance():
    assert classify_resonance([833.0], [{"name": "drill", "kind": "drill", "rpm": 120}])["status"] == "clear"
    assert classify_resonance([833.0], [{"name": "drill", "kind": "drill", "rpm": 50000}])["status"] == "resonance"


_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
const start = code.indexOf("async function runModal(");
const end = code.indexOf("\nasync function ", start + 1);
const out = [];
(async () => {
  for (const rpm of JSON.parse(process.argv[3])) {
    const alerts = [];
    const sent = [];
    const nodes = {
      "modal-kind": { value: "drill" },
      "modal-material": { value: "steel" },
      "modal-mesh": { value: "4" },
      "modal-count": { value: "6" },
      "modal-rpm": { value: rpm },
    };
    const context = {
      $: (id) => nodes[id] || null,
      ui: { part: "beam" },
      modalPlan: { part: "beam", fixes: [{ name: "fix_1", select: { ref: "plane/x-/-50.000,0.000,0.000" } }] },
      planForPart: (plan) => plan,
      alert: (text) => alerts.push(text),
      command: async (name, args) => { sent.push(args.setup.excitations); },
      refresh: async () => {},
    };
    vm.createContext(context);
    vm.runInContext(code.slice(start, end), context);
    await vm.runInContext("runModal()", context);
    out.push({ alerts, sent });
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def test_the_panel_asks_for_the_running_speed_before_the_solve(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    cases = ["", "  ", "0", "-120", "fast", "3000"]
    done = subprocess.run(
        [node, str(probe), str(APP), json.dumps(cases)], capture_output=True, text=True, check=True, timeout=60
    )
    *refused, typed = json.loads(done.stdout)
    for item in refused:
        assert item == {"alerts": ["Enter the running speed in rpm, above 0."], "sent": []}
    assert typed == {"alerts": [], "sent": [[{"name": "drill", "kind": "drill", "rpm": 3000}]]}
