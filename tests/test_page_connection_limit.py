"""Clicks during a long command do not freeze the page.

A browser opens at most six connections to one host. A command, and a mesh the
app has not kept, wait for a running command and hold a connection while they
wait. A few clicks during a long solve took all six, so the state read that
shows the busy note could not be sent and the page froze until the solve ended.
"""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
function make(id) {
  return {
    id, value: "", textContent: "", hidden: false, innerHTML: "", className: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, setAttribute() {}, getAttribute() { return ""; },
    removeAttribute() {}, appendChild() {}, focus() {}, click() {},
  };
}
// Requests that can wait for a running command hang until released, as they do
// while the geometry is busy. Every other request answers at once.
const sent = [];
const held = [];
function fakeFetch(url, options) {
  sent.push(String(url));
  const path = String(url).split("?")[0];
  if (path === "/api/command" || path === "/api/mesh") {
    return new Promise((resolve) => held.push(() => resolve({ status: 200, url })));
  }
  return Promise.resolve({ status: 200, url });
}
const sandbox = {
  console, setTimeout, Headers,
  document: {
    getElementById(id) { return make(id); },
    querySelector() { return { getAttribute() { return "token"; } }; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return make("el"); },
  },
  sessionStorage: { getItem() { return null; }, setItem() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: "test" },
  location: { origin: "http://127.0.0.1:8765", href: "http://127.0.0.1:8765/" },
  fetch: fakeFetch,
  WebSocket: function () {},
  requestAnimationFrame() {}, cancelAnimationFrame() {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
const source = process.argv.slice(2).map((path) => fs.readFileSync(path, "utf8")).join("\n");
vm.runInContext(source, sandbox, { filename: "app.js" });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
(async () => {
  const report = {};
  sent.length = 0;
  const api = sandbox.apiFetch;
  const finished = [];
  // The running solve, then six clicks and two meshes behind it.
  for (let i = 0; i < 7; i++) api("/api/command", { method: "POST" }).then(() => finished.push("command " + i));
  api("/api/mesh?part=a").then(() => finished.push("mesh a"));
  api("/api/mesh?part=b").then(() => finished.push("mesh b"));
  await tick();
  report.waitingSent = sent.length;
  const state = await Promise.race([
    api("/api/state").then(() => "answered"),
    new Promise((resolve) => setTimeout(() => resolve("not sent"), 200)),
  ]);
  report.state = state;
  await api("/api/events");
  report.events = sent.includes("/api/events");
  // The solve ends: each answer lets one more request out, in the order they were made.
  const order = [];
  while (held.length) {
    held.shift()();
    await tick(); await tick();
    order.push(sent.filter((url) => url.startsWith("/api/command") || url.startsWith("/api/mesh")).length);
  }
  report.sentAfterEachAnswer = order;
  report.finished = finished;
  sent.length = 0;
  sandbox.aicadApiFetch("/api/command", { method: "POST" });
  sandbox.aicadApiFetch("/api/command", { method: "POST" });
  sandbox.aicadApiFetch("/api/command", { method: "POST" });
  await tick();
  report.planUsesTheLimit = sent.length === 2;
  process.stdout.write(JSON.stringify(report));
})();
"""


def _run_probe(tmp_path) -> dict:
    node = shutil.which("node")
    assert node
    probe = tmp_path / "limit-probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    static = ROOT / "src" / "aicad" / "app_static"
    result = subprocess.run(
        [node, str(probe), str(static / "app.js"), str(static / "tools.js")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_clicks_during_a_long_command_leave_connections_for_the_state(tmp_path):
    report = _run_probe(tmp_path)
    # Only two requests that may wait are on the wire; the other seven wait in the page.
    assert report["waitingSent"] == 2
    assert report["state"] == "answered"
    assert report["events"] is True
    # Each answer lets the next one out, until all nine were sent and answered in order.
    assert report["sentAfterEachAnswer"] == [3, 4, 5, 6, 7, 8, 9, 9, 9]
    assert report["finished"] == [f"command {i}" for i in range(7)] + ["mesh a", "mesh b"]
    assert report["planUsesTheLimit"] is True


def test_the_plan_page_sends_its_commands_through_the_same_limit():
    script = (ROOT / "src" / "aicad" / "app_static" / "plan.js").read_text(encoding="utf-8")
    assert "globalThis.aicadApiFetch || fetch" in script
    assert 'await fetch("/api/command"' not in script
