"""An action that fails shows its error, so it never looks like a hang."""

import json
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
SCRIPT = (STATIC / "app.js").read_text(encoding="utf-8")

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const start = source.indexOf("function showUnhandledFailures");
const end = source.indexOf("function appendBubble");
const listeners = {};
const shown = [];
const notice = { hidden: true };
const text = { textContent: "" };
const sandbox = {
  window: { addEventListener: (name, listener) => { listeners[name] = listener; } },
  $: (id) => (id === "app-notice" ? notice : id === "app-notice-text" ? text : null),
  appendBubble: (_kind, message) => shown.push(message),
};
vm.createContext(sandbox);
vm.runInContext(source.slice(start, source.indexOf("\n}\n", source.indexOf("function showError")) + 3), sandbox);
vm.runInContext("showUnhandledFailures(window)", sandbox);
let prevented = false;
listeners.unhandledrejection({ reason: new Error("CalculiX (ccx) is not on your PATH."), preventDefault: () => { prevented = true; } });
process.stdout.write(JSON.stringify({ prevented, hidden: notice.hidden, text: text.textContent }));
"""


def test_a_failed_action_reaches_the_notice_bar(tmp_path):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    answer = json.loads(subprocess.run([node, str(probe), str(STATIC / "app.js")], check=True, capture_output=True, text=True).stdout)
    assert answer == {"prevented": True, "hidden": False, "text": "CalculiX (ccx) is not on your PATH."}


def test_the_page_installs_it_when_it_starts_and_not_when_the_file_loads():
    """Loading app.js has no side effects, so other tests can run it in Node without a window."""
    boot = SCRIPT[SCRIPT.index("async function boot()") :]
    assert boot.index("showUnhandledFailures(window);") < boot.index("initView();")
    assert SCRIPT.count('addEventListener("unhandledrejection"') == 1
    assert SCRIPT.index("function showUnhandledFailures") < SCRIPT.index('addEventListener("unhandledrejection"')
