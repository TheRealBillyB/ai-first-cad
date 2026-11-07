"""Choosing a library size fills in a part name that Insert accepts.

Bearing designations such as 608 start with a digit, and part names must start
with a letter or underscore. The suggested name was the bare designation, so
search, choose, Insert failed for every ball and tapered bearing.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad import library
from aicad.document import _NAME
from aicad.paths import is_reserved_device_name

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const start = source.indexOf("function suggestedLibraryName(");
const end = source.indexOf("\n}\n", start);
if (start < 0 || end < 0) throw new Error("tools.js no longer has suggestedLibraryName");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end + 3), sandbox);
sandbox.matches = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(vm.runInContext("matches.map(suggestedLibraryName)", sandbox)));
"""


def _suggest(tmp_path, matches):
    node = shutil.which("node")
    assert node, "Node runs the page scripts in these tests"
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    listed = tmp_path / "matches.json"
    listed.write_text(json.dumps(matches), encoding="utf-8")
    done = subprocess.run([node, str(probe), str(STATIC / "tools.js"), str(listed)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _every_catalog_size():
    kinds = [match["kind"] for match in library.search("")["matches"]]
    return [{"kind": kind, "size": size} for kind in kinds for size in library._sizes(kind)]


def test_every_catalog_size_gets_a_name_insert_accepts(tmp_path):
    matches = _every_catalog_size()
    assert any(re.match(r"\d", match["size"]) for match in matches), "the catalog has sizes that start with a digit"
    names = _suggest(tmp_path, matches)
    rejected = [
        (match, name) for match, name in zip(matches, names) if not _NAME.match(name) or is_reserved_device_name(name)
    ]
    assert rejected == []


@pytest.mark.parametrize(
    ("match", "expected"),
    [
        ({"kind": "ball_bearing", "size": "608"}, "ball_bearing_608"),
        ({"kind": "tapered_roller_bearing", "size": "30204"}, "tapered_roller_bearing_30204"),
        ({"kind": "socket_head_cap_screw", "size": "M5-0.8"}, "M5-0_8"),
        ({"kind": "stepper_motor", "size": "NEMA17"}, "NEMA17"),
        ({"kind": "plain_washer", "size": "CON"}, "CON_part"),
        ({"kind": "gearbox", "size": ""}, "part"),
    ],
)
def test_the_suggested_name_keeps_the_designation(tmp_path, match, expected):
    assert _suggest(tmp_path, [match]) == [expected]


def test_inserting_under_the_suggested_name_works(tmp_path):
    from aicad.workspace import Workspace

    [name] = _suggest(tmp_path, [{"kind": "ball_bearing", "size": "608"}])
    workspace = Workspace(tmp_path / "project")
    workspace.execute("insert_standard_part", {"name": name, "kind": "ball_bearing", "size": "608"}, "ui")
    assert name in [part["name"] for part in workspace.execute("list_parts", {}, "ui")["parts"]]
