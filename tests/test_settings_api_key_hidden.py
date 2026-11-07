"""The assistant API key is typed into a password field.

The Settings dialog showed the key as plain text. The app records design
steps, writes timelapses, and is screenshotted, so a pasted key stayed on
screen in the clear.
"""

import json
import shutil
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
function fn(name, prefix = "function ") {
  const start = source.indexOf(`${prefix}${name}(`);
  const end = source.indexOf("\n}\n", start);
  if (start < 0 || end < 0) throw new Error(`app.js no longer has ${name}`);
  return source.slice(start, end + 3);
}
const elements = {
  modal: { showModal() {}, close() {} },
  "modal-title": {},
  "modal-body": { innerHTML: "" },
  "modal-form": {},
  "modal-cancel": {},
  "chat-key": {},
};
const asked = [];
const sandbox = {
  $: (id) => elements[id],
  escapeHtml: (text) => String(text),
  escapeAttr: (text) => String(text),
  apiFetch: async () => ({ json: async () => ({ result: { provider: "openai", base_url: "u", model: "m" } }) }),
};
vm.createContext(sandbox);
vm.runInContext(fn("modalInputType") + fn("ask"), sandbox);
const realAsk = sandbox.ask;
sandbox.ask = (title, fields) => { asked.push(fields); realAsk(title, fields); return Promise.resolve(null); };
vm.runInContext(fn("editSettings", "async function "), sandbox);
vm.runInContext("editSettings()", sandbox).then(() => {
  process.stdout.write(JSON.stringify({ fields: asked[0], html: elements["modal-body"].innerHTML }));
});
"""


def _settings_dialog(tmp_path):
    node = shutil.which("node")
    assert node, "Node runs the page scripts in these tests"
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run([node, str(probe), str(STATIC / "app.js")], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_api_key_field_is_a_password_field(tmp_path):
    dialog = _settings_dialog(tmp_path)
    key = next(field for field in dialog["fields"] if field["name"] == "api_key")
    assert key["type"] == "password"
    html = dialog["html"]
    key_input = html[html.index('name="api_key"') :].split("/>", 1)[0]
    assert 'type="password"' in key_input
    assert 'autocomplete="off"' in key_input


def test_the_other_settings_stay_plain_text(tmp_path):
    html = _settings_dialog(tmp_path)["html"]
    for name in ("provider", "base_url", "model"):
        field = html[html.index(f'name="{name}"') :].split("/>", 1)[0]
        assert "type=" not in field, field
