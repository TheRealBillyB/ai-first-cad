"""Redirects and odd host spellings do not reach another machine.

The marker is CANARY_HIT. A second local server records any request that
should not have been sent. The marker file is not opened.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aicad.app import HttpCommandClient, make_server, write_agent_token
from aicad.errors import GeometryError
from aicad.llm import _post


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    running = make_server(workspace, "127.0.0.1", 0)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.shutdown()
        running.server_close()


def _exchange(server, method, path, body=None, headers=None, token="page", origin=True):
    from http.client import HTTPConnection

    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=30)
    sent = dict(headers or {})
    if origin is True:
        sent.setdefault("Origin", f"http://127.0.0.1:{port}")
    elif origin:
        sent["Origin"] = origin
    if token == "page":
        sent.setdefault("X-Aicad-Token", server.page_token)
    elif token == "agent":
        sent.setdefault("X-Aicad-Token", server.agent_token)
    if body is not None and "Content-Type" not in sent:
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    connection.close()
    return status, payload


def _raw(server, head: str, body: bytes = b"") -> tuple[int, bytes]:
    host, port = server.server_address
    sock = socket.create_connection((host, port), timeout=30)
    try:
        sock.sendall(head.encode("ascii") + body)
        chunks = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
    finally:
        sock.close()
    data = b"".join(chunks)
    status = int(data.split(b" ", 2)[1])
    return status, data


class _Record:
    def __init__(self):
        self.hits: list[dict[str, str]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self):
        record = self

        class Handler(BaseHTTPRequestHandler):
            def _take(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                record.hits.append({key.lower(): value for key, value in self.headers.items()})
                body = b'{"ok": true, "choices": [{"message": {"content": "CANARY_HIT"}}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _take
            do_POST = _take

            def log_message(self, _format, *_args) -> None:
                return

        return Handler

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/taken"


def _redirector(location: str) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _send
        do_POST = _send

        def log_message(self, _format, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_a_redirect_does_not_receive_the_session(tmp_path, monkeypatch):
    canary = tmp_path / "SANDBOX_CANARY.txt"
    canary.write_text("CANARY_HIT\n", encoding="utf-8")
    collector = _Record()
    redirector, _thread = _redirector(collector.url)
    try:
        port = redirector.server_address[1]
        with pytest.raises(GeometryError):
            _post(
                f"http://127.0.0.1:{port}/chat/completions",
                {"ping": True},
                {"Authorization": "Bearer CANARY_HIT", "Content-Type": "application/json"},
            )
        monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
        write_agent_token(port, "CANARY_HIT")
        client = HttpCommandClient(f"http://127.0.0.1:{port}")
        with pytest.raises(GeometryError):
            client.execute("list_parts", {})
    finally:
        redirector.shutdown()
        redirector.server_close()
        collector.close()
    assert collector.hits == []
    assert canary.read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_a_file_url_is_not_opened(tmp_path):
    canary = tmp_path / "SANDBOX_CANARY.txt"
    canary.write_text("CANARY_HIT\n", encoding="utf-8")
    with pytest.raises(GeometryError):
        _post(canary.as_uri(), {"ping": True}, {"Authorization": "Bearer CANARY_HIT"})
    assert canary.read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_another_host_on_the_request_line_is_refused(server):
    port = int(server.server_address[1])
    body = json.dumps({"name": "list_parts"}).encode("utf-8")
    head = (
        f"POST http://evil.example/api/command HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Origin: http://127.0.0.1:{port}\r\n"
        f"X-Aicad-Token: {server.page_token}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    status, payload = _raw(server, head, body)
    assert status == 403
    assert server.page_token.encode("utf-8") not in payload
    assert b"CANARY_HIT" not in payload

    head = (
        f"GET //evil.example/ HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    status, payload = _raw(server, head)
    assert status in {403, 404}
    assert b'"ok": true' not in payload.lower()
    assert b"CANARY_HIT" not in payload


def test_odd_host_and_origin_spellings_are_refused(server):
    port = int(server.server_address[1])
    digits = str(port)
    underscored = digits[0] + "_" + digits[1:] if len(digits) > 1 else digits + "_0"
    refused = [
        {"headers": {"Host": f"2130706433:{port}"}},
        {"headers": {"Host": f"0177.0.0.1:{port}"}},
        {"headers": {"Host": f"127.1:{port}"}},
        {"headers": {"Host": f"localhost.:{port}"}},
        {"headers": {"Host": f"127.0.0.1.:{port}"}},
        {"headers": {"Host": f"[::1]:{port}"}},
        {"headers": {"Host": f"[::ffff:127.0.0.1]:{port}"}},
        {"headers": {"Host": f"127.0.0.1:{underscored}"}},
        {"headers": {"Host": f"127.0.0.1:+{port}"}},
        {"headers": {"Host": f"127.0.0.1:0{port}"}},
        {"origin": f"http://2130706433:{port}"},
        {"origin": f"http://127.0.0.1.:{port}"},
        {"origin": f"http://localhost.:{port}"},
        {"origin": f"http://evil.example@127.0.0.1:{port}"},
        {"origin": f"http://127.0.0.1:{port}@evil.example"},
        {"origin": f"http://127.0.0.1:0{port}"},
        {"origin": f"http://127.0.0.1:{port}%00.evil.example"},
        {"origin": f"http://[::ffff:127.0.0.1]:{port}"},
        {"origin": f"http://127.0.0.1:{port}/extra"},
    ]
    for item in refused:
        status, payload = _exchange(
            server,
            "POST",
            "/api/command",
            body=json.dumps({"name": "list_parts"}),
            headers=item.get("headers"),
            origin=item.get("origin", True),
        )
        assert status == 403, item
        assert server.page_token.encode("utf-8") not in payload
        assert server.agent_token.encode("utf-8") not in payload

    status, payload = _raw(
        server,
        f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\t\r\nConnection: close\r\n\r\n",
    )
    assert status == 403
    assert server.page_token.encode("utf-8") not in payload

    status, _payload = _exchange(server, "GET", "/", token=None, origin=None)
    assert status == 200


def test_a_forwarded_host_is_not_contacted(server):
    collector = _Record()
    try:
        port = int(server.server_address[1])
        other = collector.server.server_address[1]
        status, payload = _exchange(
            server,
            "GET",
            "/",
            headers={
                "X-Forwarded-Host": f"evil.example:{other}",
                "Forwarded": f"host=evil.example:{other}",
            },
            token=None,
            origin=None,
        )
        assert status == 200
    finally:
        collector.close()
    assert collector.hits == []


def test_the_workspace_client_rejects_a_smuggled_url(server):
    port = int(server.server_address[1])
    for base in (
        f"http://2130706433:{port}",
        f"http://127.0.0.1:{port}/api",
        f"http://127.0.0.1:{port}?next=http://evil.example",
        f"http://127.0.0.1:{port}#@evil.example",
        f"http://evil.example@{port}",
        f"http://user@127.0.0.1:{port}",
        f"file://127.0.0.1:{port}/",
    ):
        with pytest.raises(GeometryError):
            HttpCommandClient(base)
