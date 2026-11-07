"""The Strength panel says a solve is running, how long it has run, and why it failed."""

import json
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

# The panel's own code, run under Node with a stand-in for the page and the server.
_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const start = source.indexOf("const feaRun = ");
const end = source.indexOf("async function runChecks");
const nodes = {
  "fea-status": { hidden: true, className: "", textContent: "" },
  "fea-run": { disabled: false },
  "fea-material": { value: "steel" },
  "fea-mesh": { value: "3" },
  "fea-sf": { value: "2" },
};
const clock = { now: 0 };
const timers = [];
const shown = [];
const sent = [];
let pending = null;
const sandbox = {
  $: (id) => nodes[id] || null,
  ui: { part: "motor_mount" },
  feaPlan: { part: "motor_mount", fixes: [{ name: "fix_1" }], loads: [{ name: "load_1" }] },
  performance: { now: () => clock.now },
  setInterval: (fn) => { timers.push(fn); return timers.length; },
  clearInterval: () => { timers.length = 0; },
  command: (name, args) => { sent.push([name, args.name]); return new Promise((resolve, reject) => { pending = { resolve, reject }; }); },
  refresh: async () => { sandbox.refreshed = (sandbox.refreshed || 0) + 1; },
  showError: (error) => shown.push(error.message),
  alert: () => {},
};
vm.createContext(sandbox);
// runFea keeps the setup with the part it was picked on, through planForPart.
const helper = source.indexOf("function planForPart(");
vm.runInContext(source.slice(helper, source.indexOf("\nfunction ", helper + 1)), sandbox);
vm.runInContext(source.slice(start, end), sandbox);
const look = () => ({ text: nodes["fea-status"].textContent, hidden: nodes["fea-status"].hidden, disabled: nodes["fea-run"].disabled, className: nodes["fea-status"].className });
(async () => {
  const report = {};
  const failing = vm.runInContext("runFea()", sandbox);
  report.started = look();
  clock.now = 63400;
  timers.forEach((tick) => tick());
  report.later = look();
  const second = vm.runInContext("runFea()", sandbox);
  await second;
  report.sentWhileRunning = sent.length;
  pending.reject(new Error("CalculiX (ccx) is not on your PATH."));
  await failing;
  report.failed = look();
  report.shown = shown;
  report.refreshedAfterFailure = sandbox.refreshed || 0;
  clock.now = 70000;
  const passing = vm.runInContext("runFea()", sandbox);
  report.retry = look();
  pending.resolve({ status: "pass" });
  await passing;
  report.passed = look();
  report.refreshed = sandbox.refreshed || 0;
  report.sent = sent;
  process.stdout.write(JSON.stringify(report));
})();
"""


def _report(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    answer = subprocess.run([node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(answer.stdout)


def test_a_running_solve_shows_its_time_and_cannot_be_sent_twice(tmp_path):
    report = _report(tmp_path)
    assert report["started"] == {"text": "Solving… 0 s. Meshing and the solve can take a few minutes.", "hidden": False, "disabled": True, "className": "muted"}
    assert report["later"]["text"].startswith("Solving… 63 s.")
    assert report["sentWhileRunning"] == 1


def test_a_failed_solve_says_why_in_the_panel_and_the_notice_bar(tmp_path):
    report = _report(tmp_path)
    assert report["failed"] == {
        "text": "The solve failed. CalculiX (ccx) is not on your PATH.",
        "hidden": False,
        "disabled": False,
        "className": "warn",
    }
    assert report["shown"] == ["CalculiX (ccx) is not on your PATH."]
    assert report["refreshedAfterFailure"] == 0


def test_the_next_solve_clears_the_failure_and_a_result_refreshes_the_panel(tmp_path):
    report = _report(tmp_path)
    assert report["retry"]["text"].startswith("Solving… 0 s.")
    assert report["passed"] == {"text": "", "hidden": True, "disabled": False, "className": ""}
    assert report["refreshed"] == 1
    assert report["sent"] == [["solve_mechanics", "motor_mount"]] * 2


def test_the_panel_draws_the_status_each_time():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    panel = script[script.index('if (ui.tab === "fea")') : script.index("function rememberLoad")]
    assert 'id="fea-status" role="status"' in panel
    assert "renderFeaStatus();" in panel
