"""An action that fails shows its error, so it never looks like a hang."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"
SCRIPT = (STATIC / "app.js").read_text(encoding="utf-8")

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
// A Windows checkout has CRLF line endings, and the slice below looks for a newline, brace, newline.
const source = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const start = source.indexOf("function showUnhandledFailures");
const end = source.indexOf("\n}\n", source.indexOf("function showError"));
if (start < 0 || end < 0) throw new Error(`app.js no longer has the expected functions (start ${start}, end ${end})`);
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
vm.runInContext(source.slice(start, end + 3), sandbox);
vm.runInContext("showUnhandledFailures(window)", sandbox);
let prevented = false;
listeners.unhandledrejection({ reason: new Error("CalculiX (ccx) is not on your PATH."), preventDefault: () => { prevented = true; } });
process.stdout.write(JSON.stringify({ prevented, hidden: notice.hidden, text: text.textContent }));
"""


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_a_failed_action_reaches_the_notice_bar(tmp_path, line_ending):
    """Windows runners check app.js out with CRLF, which once made node exit 1 with no output."""
    node = shutil.which("node")
    assert node
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    script = tmp_path / "app.js"
    script.write_bytes((STATIC / "app.js").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", line_ending))
    done = subprocess.run([node, str(probe), str(script)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, f"node exited {done.returncode}\nstdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    answer = json.loads(done.stdout)
    assert answer == {"prevented": True, "hidden": False, "text": "CalculiX (ccx) is not on your PATH."}


def test_the_page_installs_it_when_it_starts_and_not_when_the_file_loads():
    """Loading app.js has no side effects, so other tests can run it in Node without a window."""
    boot = SCRIPT[SCRIPT.index("async function boot()") :]
    assert boot.index("showUnhandledFailures(window);") < boot.index("initView();")
    assert SCRIPT.count('addEventListener("unhandledrejection"') == 1
    assert SCRIPT.index("function showUnhandledFailures") < SCRIPT.index('addEventListener("unhandledrejection"')
