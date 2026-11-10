"""The app answers on ::1 as well as 127.0.0.1, so a page opened at localhost does not stall.

A browser asks localhost for ::1 first. The app listened on 127.0.0.1 only, so
each request waited about 300 ms for the browser to fall back to IPv4, and
each request is a new connection. The second socket is bound to ::1 alone and
passes every request through the same host, origin, and token checks.
"""

from __future__ import annotations

import json
import socket
import threading
from http.client import HTTPConnection

import pytest

from aicad import app as app_module
from aicad.app import make_server


def _ipv6_loopback() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


needs_ipv6 = pytest.mark.skipif(not _ipv6_loopback(), reason="This computer has no IPv6 loopback.")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("AICAD_MESH_CACHE", str(tmp_path / "meshes"))
    return tmp_path


@pytest.fixture
def server(environment):
    served = make_server(environment / "project", "127.0.0.1", 0)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    yield served
    served.shutdown()
    served.server_close()
    thread.join(30)


def _ask(address, port, method, path, *, host=None, token=None, origin=True, body=None):
    """One request to ``address`` (``::1`` or ``127.0.0.1``), sending the Host and Origin a browser at localhost sends."""
    connection = HTTPConnection(f"[{address}]" if ":" in address else address, port, timeout=30)
    headers = {"Host": host or f"localhost:{port}"}
    if origin:
        headers["Origin"] = f"http://localhost:{port}"
    if token:
        headers["X-Aicad-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    peer = connection.sock.getpeername()[0] if connection.sock else None
    connection.close()
    return response.status, payload, peer


@needs_ipv6
def test_the_page_and_the_api_answer_on_ipv6_loopback(server):
    port = int(server.server_address[1])
    status, page, peer = _ask("::1", port, "GET", "/", origin=False)
    assert status == 200
    assert server.page_token in page.decode("utf-8")
    status, payload, _peer = _ask("::1", port, "GET", "/api/state", token=server.page_token)
    assert status == 200
    assert json.loads(payload)["result"]["parts"] == []
    # A command sent over ::1 changes the same project the IPv4 socket serves.
    box = {"name": "create_part_from_ops", "arguments": {"name": "plate", "ops": [{"op": "add", "shape": "box", "size": [4, 4, 4]}]}}
    status, payload, _peer = _ask("::1", port, "POST", "/api/command", token=server.page_token, body=box)
    assert status == 200, payload
    status, payload, _peer = _ask("127.0.0.1", port, "GET", "/api/state", token=server.page_token)
    assert [part["name"] for part in json.loads(payload)["result"]["parts"]] == ["plate"]


@needs_ipv6
def test_the_ipv6_socket_is_bound_to_loopback_alone(server):
    twin = server._twin
    assert twin is not None
    assert twin.server_address[0] == "::1"
    assert int(twin.server_address[1]) == int(server.server_address[1])


@needs_ipv6
def test_the_ipv6_socket_keeps_every_check(server):
    port = int(server.server_address[1])
    # No token, a wrong token, the assistant's token for a person's call.
    assert _ask("::1", port, "GET", "/api/state")[0] == 403
    assert _ask("::1", port, "GET", "/api/state", token="not-the-token")[0] == 403
    assert _ask("::1", port, "POST", "/api/settings", token=server.agent_token, body={})[0] == 403
    # A Host or an Origin that is not this app's.
    assert _ask("::1", port, "GET", "/", host=f"[::1]:{port}", origin=False)[0] == 403
    assert _ask("::1", port, "GET", "/", host=f"evil.example:{port}", origin=False)[0] == 403
    assert _ask("::1", port, "GET", "/", host=f"localhost:{port + 1}", origin=False)[0] == 403
    connection = HTTPConnection("[::1]", port, timeout=30)
    connection.request("GET", "/api/state", headers={"Host": f"localhost:{port}", "Origin": f"http://evil.example:{port}", "X-Aicad-Token": server.page_token})
    assert connection.getresponse().status == 403
    connection.close()


@needs_ipv6
def test_closing_the_app_closes_the_ipv6_socket(environment):
    served = make_server(environment / "project", "127.0.0.1", 0)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    port = int(served.server_address[1])
    assert _ask("::1", port, "GET", "/", origin=False)[0] == 200
    served.shutdown()
    served.server_close()
    thread.join(30)
    assert not thread.is_alive()
    with pytest.raises(OSError):
        _ask("::1", port, "GET", "/", origin=False)


def test_the_app_still_starts_when_ipv6_loopback_cannot_be_bound(environment, monkeypatch):
    def refused(_primary):
        raise OSError("no IPv6 here")

    monkeypatch.setattr(app_module, "_LoopbackTwin", refused)
    served = make_server(environment / "project", "127.0.0.1", 0)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        assert served._twin is None
        port = int(served.server_address[1])
        assert _ask("127.0.0.1", port, "GET", "/", origin=False)[0] == 200
    finally:
        served.shutdown()
        served.server_close()
        thread.join(30)
