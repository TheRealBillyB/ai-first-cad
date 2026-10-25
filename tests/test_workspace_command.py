"""The assistant's command client stops reading a response past its cap."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aicad.app import HttpCommandClient, write_agent_token
from aicad.errors import GeometryError


class _CommandHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        name = json.loads(self.rfile.read(length))["name"]
        pad = b"x" * 400
        if name == "small":
            body = b'{"ok": true, "result": {"n": 1}}'
            code = 200
            send_length = True
        elif name == "stream":
            body = b'{"ok": true, "result": {"pad": "' + pad + b'"}}'
            code = 200
            send_length = False
        elif name == "http-error":
            body = b'{"error": "' + pad + b'"}'
            code = 500
            send_length = True
        else:
            body = b'{"ok": true, "result": {"pad": "' + pad + b'"}}'
            code = 200
            send_length = True
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if send_length:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, fmt: str, *args) -> None:
        return


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setattr("aicad.app._MAX_COMMAND_BYTES", 64, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CommandHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    write_agent_token(port, "session")
    return server, HttpCommandClient(f"http://{host}:{port}")


def test_a_small_command_response_is_returned(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    try:
        assert client.execute("small") == {"n": 1}
    finally:
        server.shutdown()
        server.server_close()


def test_a_command_response_stops_past_the_cap(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    try:
        with pytest.raises(GeometryError, match="too large"):
            client.execute("declared")
        with pytest.raises(GeometryError, match="too large"):
            client.execute("stream")
        with pytest.raises(GeometryError, match="too large"):
            client.execute("http-error")
    finally:
        server.shutdown()
        server.server_close()
