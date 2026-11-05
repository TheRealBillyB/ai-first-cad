"""A picture named in a report loads in the page."""

import json
import shutil
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

import pytest

from aicad.app import make_server

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"

_PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: "project_path.js" });
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
sandbox.cases = cases;
process.stdout.write(JSON.stringify(vm.runInContext("cases.map(([path, root]) => projectRelativePath(path, root))", sandbox)));
"""


def _relative(tmp_path, cases):
    node = shutil.which("node")
    assert node
    probe = tmp_path / "probe.js"
    probe.write_text(_PROBE, encoding="utf-8")
    listed = tmp_path / "cases.json"
    listed.write_text(json.dumps(cases), encoding="utf-8")
    answer = subprocess.run(
        [node, str(probe), str(STATIC / "project_path.js"), str(listed)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(answer.stdout)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    running = make_server(tmp_path / "project", "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _exchange(server, method, path, body=None):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=120)
    headers = {"Origin": f"http://127.0.0.1:{port}", "X-Aicad-Token": server.page_token}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body)
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    answer = response.read()
    connection.close()
    return response.status, answer


def _command(server, name, arguments):
    status, answer = _exchange(server, "POST", "/api/command", {"name": name, "arguments": arguments})
    assert status == 200, answer
    return json.loads(answer)["result"]


def test_every_picture_in_a_report_loads_through_the_page_path(server, tmp_path):
    box = [{"op": "add", "shape": "box", "size": [20, 20, 20]}]
    _command(server, "create_part_from_ops", {"name": "block", "ops": box})
    report = _command(server, "inspect_part", {"name": "block"})
    assert any(view["name"] == "contact" for view in report["views"])
    root = str(server.workspace.root)
    paths = [view["path"] for view in report["views"]]
    relative = _relative(tmp_path, [[path, root] for path in paths])
    for path in relative:
        assert not Path(path).is_absolute()
        status, picture = _exchange(server, "GET", "/api/file?path=" + quote(path))
        assert status == 200, path
        assert picture.startswith(b"\x89PNG")


def test_the_file_route_still_refuses_a_full_path(server):
    box = [{"op": "add", "shape": "box", "size": [20, 20, 20]}]
    _command(server, "create_part_from_ops", {"name": "block", "ops": box})
    path = _command(server, "inspect_part", {"name": "block"})["views"][0]["path"]
    status, _answer = _exchange(server, "GET", "/api/file?path=" + quote(path))
    assert status == 400


@pytest.mark.parametrize(
    ("path", "root", "expected"),
    [
        ("C:\\work\\box\\renders\\a.png", "C:\\work\\box", "renders/a.png"),
        ("c:\\WORK\\box\\renders\\a.png", "C:\\work\\box\\", "renders/a.png"),
        ("/home/me/box/renders/a.png", "/home/me/box", "renders/a.png"),
        ("renders/sheets/a.png", "/home/me/box", "renders/sheets/a.png"),
        # Outside the project stays as it was, and the route refuses it.
        ("C:\\work\\box2\\a.png", "C:\\work\\box", "C:\\work\\box2\\a.png"),
        ("/home/me/box2/a.png", "/home/me/box", "/home/me/box2/a.png"),
        ("/home/ME/box/a.png", "/home/me/box", "/home/ME/box/a.png"),
        ("/home/me/box", "/home/me/box", "/home/me/box"),
    ],
)
def test_a_path_is_made_relative_only_inside_the_project(tmp_path, path, root, expected):
    assert _relative(tmp_path, [[path, root]]) == [expected]


def test_a_page_with_no_project_yet_leaves_the_path_alone(tmp_path):
    assert _relative(tmp_path, [["/home/me/a.png", None]]) == ["/home/me/a.png"]


def test_the_page_uses_the_helper_for_every_picture():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert page.index("/static/project_path.js") < page.index("/static/app.js")
    assert script.count('"/api/file?path="') == 1
    body = script[script.index("function fileUrl(path)") : script.index("let refreshChain")]
    assert "projectRelativePath(path" in body
