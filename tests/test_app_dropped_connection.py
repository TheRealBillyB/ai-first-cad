"""A page that drops its live connection leaves the server quiet and running."""

import json
import socket
import struct
import threading
import time
from http.client import HTTPConnection

import pytest

from aicad.app import make_server


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    (tmp_path / "project").mkdir()
    running = make_server(tmp_path / "project", "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _open_events(server) -> socket.socket:
    host, port = server.server_address
    client = socket.create_connection((host, port), timeout=30)
    request = (
        "GET /api/events HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Origin: http://127.0.0.1:{port}\r\n"
        f"X-Aicad-Token: {server.page_token}\r\n"
        "\r\n"
    )
    client.sendall(request.encode("ascii"))
    received = b""
    while b": ok\n\n" not in received:
        chunk = client.recv(4096)
        assert chunk, f"the event stream closed early: {received!r}"
        received += chunk
    assert received.startswith(b"HTTP/1.0 200")
    return client


def _listening(server) -> int:
    with server.workspace._lock:
        return len(server.workspace._listeners)


def _wait_for_request_threads(deadline: float) -> None:
    # socketserver reports a failed request after the handler returns, so wait
    # for every request thread to end before reading stderr.
    while time.monotonic() < deadline:
        busy = [t for t in threading.enumerate() if "process_request_thread" in t.name and t.is_alive()]
        if not busy:
            return
        time.sleep(0.02)
    raise AssertionError("a request thread is still running")


@pytest.mark.parametrize("how", ["reset", "close"])
def test_dropped_event_stream_is_quiet(server, capsys, how):
    client = _open_events(server)
    assert _listening(server) == 1
    if how == "reset":
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.close()

    # Each command sends an event down the dead stream. The handler finds the
    # client gone on a write and lets go of its listener.
    deadline = time.monotonic() + 20
    while _listening(server) and time.monotonic() < deadline:
        server.workspace.execute("list_parts")
        time.sleep(0.05)
    assert _listening(server) == 0
    _wait_for_request_threads(deadline)

    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=30)
    connection.request(
        "GET",
        "/api/state",
        headers={"Origin": f"http://127.0.0.1:{port}", "X-Aicad-Token": server.page_token},
    )
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    assert response.status == 200
    assert payload["ok"] is True
    _wait_for_request_threads(time.monotonic() + 10)

    captured = capsys.readouterr()
    assert captured.err == ""
