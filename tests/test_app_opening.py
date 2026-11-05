"""`aicad app` answers at once and says the project is still opening.

The first worker opens the project in the background. Until it is ready, the
page gets an opening state, and every other request, including one from an
assistant over MCP, gets a clear "still opening" answer that changed nothing.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad.app import HttpCommandClient, make_server
from aicad.app_worker import GeometryWorker
from aicad.errors import GeometryError

STATIC = Path(__file__).resolve().parents[1] / "src" / "aicad" / "app_static"


@pytest.fixture
def held_open(monkeypatch):
    """The worker starts only when the test lets it."""
    gate = threading.Event()
    real_start = GeometryWorker._start

    def held_start(self):
        assert gate.wait(120)
        real_start(self)

    monkeypatch.setattr(GeometryWorker, "_start", held_start)
    yield gate
    gate.set()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def _serve(project):
    server = make_server(project, "127.0.0.1", 0, worker=True, open_in_background=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _exchange(server, method, path, body=None, token=None):
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=60)
    headers = {"Origin": f"http://127.0.0.1:{port}", "X-Aicad-Token": token or server.page_token}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body)
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    answer = response.read()
    connection.close()
    return response.status, answer


def _state(server):
    status, answer = _exchange(server, "GET", "/api/state")
    assert status == 200
    return json.loads(answer)["result"]


def _wait_until_open(server, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = _state(server)
        if "opening" not in state:
            return state
        time.sleep(0.2)
    raise AssertionError("the project did not open")


def _box(name):
    return {"name": "create_part_from_ops", "arguments": {"name": name, "ops": [{"op": "add", "shape": "box", "size": [10, 10, 10]}]}}


def test_the_page_answers_while_the_project_opens(environment, held_open):
    started = time.monotonic()
    server = _serve(environment / "project")
    try:
        assert time.monotonic() - started < 10
        status, page = _exchange(server, "GET", "/")
        assert status == 200 and b"aicad" in page
        state = _state(server)
        assert state["opening"]["error"] is None
        assert state["opening"]["seconds"] >= 0
        assert state["project"]["name"] == "project"
        status, answer = _exchange(server, "POST", "/api/command", _box("early"))
        assert status == 400
        assert "still opening" in json.loads(answer)["error"]
        status, _answer = _exchange(server, "GET", "/api/mesh?scene=1")
        assert status == 400

        events = []
        server.workspace.listen(events.append)
        held_open.set()
        state = _wait_until_open(server)
        assert state["parts"] == []
        assert any(event.get("command") == "open" for event in events)
        status, answer = _exchange(server, "POST", "/api/command", _box("late"))
        assert status == 200, answer
        # The early request changed nothing.
        assert [part["name"] for part in _state(server)["parts"]] == ["late"]
    finally:
        server.shutdown()
        server.server_close()


def test_an_assistant_hears_still_opening_and_can_retry(environment, held_open):
    server = _serve(environment / "project")
    try:
        client = HttpCommandClient(f"http://127.0.0.1:{server.server_address[1]}")
        with pytest.raises(GeometryError, match="still opening the project .* Nothing was changed"):
            client.execute("list_parts", {})
        held_open.set()
        _wait_until_open(server)
        assert client.execute("list_parts", {}) is not None
    finally:
        server.shutdown()
        server.server_close()


def test_an_mcp_tool_call_answers_still_opening(environment, held_open):
    from mcp.client.client import Client

    from aicad.server import build_server

    server = _serve(environment / "project")
    try:
        mcp = build_server("", connect=f"http://127.0.0.1:{server.server_address[1]}")

        async def call():
            async with Client(mcp) as client:
                return await client.call_tool("list_parts", {})

        started = time.monotonic()
        answer = asyncio.run(call())
        assert time.monotonic() - started < 30
        assert answer.is_error
        text = " ".join(block.text for block in answer.content if getattr(block, "type", "") == "text")
        assert "still opening the project" in text
        assert "Send the same request again" in text
    finally:
        server.shutdown()
        server.server_close()


def test_a_project_that_does_not_open_says_why(environment, monkeypatch):
    def failing_start(self):
        raise GeometryError("The geometry process did not start. It exited with code 3.")

    monkeypatch.setattr(GeometryWorker, "_start", failing_start)
    server = _serve(environment / "project")
    try:
        deadline = time.monotonic() + 30
        while _state(server)["opening"]["error"] is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert "exited with code 3" in _state(server)["opening"]["error"]
        status, answer = _exchange(server, "POST", "/api/command", _box("never"))
        assert status == 400
        assert "did not open" in json.loads(answer)["error"]
        assert server.workspace.wait_open(1) == "The geometry process did not start. It exited with code 3."
    finally:
        server.shutdown()
        server.server_close()


def test_closing_while_the_project_opens_leaves_no_worker(environment, held_open):
    worker = GeometryWorker(environment / "project", wait=False)
    worker.close()
    held_open.set()
    worker.wait_open(120)
    assert worker._process is None


def test_the_tests_and_the_mcp_tools_still_get_an_open_project(environment):
    """Without open_in_background, make_server waits as before."""
    server = make_server(environment / "project", "127.0.0.1", 0, worker=True)
    try:
        assert "opening" not in server.workspace.state()
        assert server.workspace.wait_open(0) is None
    finally:
        server.server_close()


def test_the_page_waits_for_the_real_state():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    body = script[script.index("async function applyRefresh") :]
    assert body.index("renderOpening(body.result.opening)") < body.index("ui.state = body.result")
    opening = script[script.index("function renderOpening") : script.index("function renderBusyNote")]
    assert "Opening the project…" in opening
    assert "setTimeout" in opening and "refresh()" in opening


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(port, path, token=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("GET", path, headers={"X-Aicad-Token": token} if token else {})
    response = connection.getresponse()
    answer = response.read()
    connection.close()
    return response.status, answer


def test_the_terminal_and_the_page_answer_before_the_project_opens(environment):
    """The real `aicad app`. Its worker cannot open the project until the test has seen the page say it is opening.

    A busy machine can take a long time to start Python, so this checks the order, never a duration.
    """
    port = _free_port()
    release = environment / "release-the-worker"
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from aicad import app_worker\n"
        "real = app_worker.GeometryWorker._start\n"
        "def held(self):\n"
        f"    release = Path({str(release)!r})\n"
        "    deadline = time.monotonic() + 600\n"
        "    while not release.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.05)\n"
        "    real(self)\n"
        "app_worker.GeometryWorker._start = held\n"
        "from aicad.cli import main\n"
        f"sys.argv = ['aicad', 'app', '--workspace', {str(environment / 'project')!r}, '--no-browser', '--port', '{port}']\n"
        "sys.exit(main())\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdout.readline().strip() == "Opening the project, this can take a minute..."
        deadline = time.monotonic() + 240
        page = None
        while page is None:
            assert time.monotonic() < deadline, "the page never answered"
            assert process.poll() is None, "the app exited"
            try:
                status, page = _get(port, "/")
            except OSError:
                time.sleep(0.1)
        assert status == 200
        token = re.search(rb'name="aicad-token" content="([^"]+)"', page).group(1).decode("ascii")
        status, answer = _get(port, "/api/state", token)
        assert status == 200
        assert json.loads(answer)["result"]["opening"]["error"] is None
        assert not release.exists()

        release.write_text("go", encoding="utf-8")
        lines = []
        while "The project is open." not in lines:
            line = process.stdout.readline()
            assert line, f"the app stopped first: {lines}"
            lines.append(line.strip())
        status, answer = _get(port, "/api/state", token)
        assert status == 200
        assert "opening" not in json.loads(answer)["result"]
    finally:
        process.terminate()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
