"""Assistant HTTP calls do not use a proxy or a certificate file from the environment.

The marker is CANARY_HIT. A dead proxy and a certificate file that is not a
certificate do not steer the request.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aicad.app import HttpCommandClient, write_agent_token
from aicad.errors import GeometryError
from aicad.llm import _post, _ssl_context

_DURING: list[dict[str, str]] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        _DURING.append(dict(os.environ))
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({"ok": True, "result": {"pong": True}, "choices": [{"message": {"content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_a_certificate_file_from_the_environment_is_not_used(monkeypatch, tmp_path):
    bad = tmp_path / "canary.pem"
    bad.write_text("CANARY_HIT\n", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(bad))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bad))
    monkeypatch.setenv("CURL_CA_BUNDLE", str(bad))
    context = _ssl_context()
    assert context is not None
    assert bad.read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_a_proxy_does_not_steer_assistant_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    server = _server()
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        payload = _post(base + "/chat", {"ping": True}, {"Content-Type": "application/json"})
        assert payload["ok"] is True
        tokens = tmp_path / "tokens"
        monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tokens))
        write_agent_token(port, "session-token")
        result = HttpCommandClient(base).execute("ping", {})
        assert result["pong"] is True
    finally:
        server.shutdown()


def test_the_process_environment_stays_in_place(monkeypatch, tmp_path):
    bad = tmp_path / "canary.pem"
    bad.write_text("CANARY_HIT\n", encoding="utf-8")
    proxy = "http://127.0.0.1:9"
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("ALL_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("http_proxy", proxy)
    monkeypatch.setenv("https_proxy", proxy)
    monkeypatch.setenv("all_proxy", proxy)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("SSL_CERT_FILE", str(bad))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bad))
    monkeypatch.setenv("CURL_CA_BUNDLE", str(bad))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    before = dict(os.environ)
    gone: list[str] = []
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            current = dict(os.environ)
            for key, value in before.items():
                if current.get(key) != value:
                    gone.append(key)
                    return
            for key in current:
                if key not in before:
                    gone.append(key)
                    return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    _DURING.clear()
    server = _server()
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        payload = _post(base + "/chat", {"ping": True}, {"Content-Type": "application/json"})
        assert payload["ok"] is True
        write_agent_token(port, "session-token")
        result = HttpCommandClient(base).execute("ping", {})
        assert result["pong"] is True
        assert _ssl_context() is not None
    finally:
        server.shutdown()
        stop.set()
        watcher.join()
    assert gone == []
    assert dict(os.environ) == before
    assert _DURING
    for snapshot in _DURING:
        for key, value in before.items():
            assert snapshot.get(key) == value
    assert bad.read_text(encoding="utf-8") == "CANARY_HIT\n"


def test_a_cross_origin_redirect_is_not_followed():
    landed = {"hit": False}

    class Away(_Handler):
        def do_POST(self):
            landed["hit"] = True
            body = b'{"ok":true,"choices":[{"message":{"content":"CANARY_HIT"}}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    other = ThreadingHTTPServer(("127.0.0.1", 0), Away)
    other_thread = threading.Thread(target=other.serve_forever, daemon=True)
    other_thread.start()
    origin = _server()
    try:
        other_port = other.server_address[1]
        origin_port = origin.server_address[1]

        class Steer(_Handler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{other_port}/landed")
                self.end_headers()

        origin.RequestHandlerClass = Steer
        with pytest.raises(GeometryError) as raised:
            _post(f"http://127.0.0.1:{origin_port}/chat", {"ping": True}, {})
        assert "CANARY_HIT" not in str(raised.value)
        assert landed["hit"] is False
    finally:
        origin.shutdown()
        other.shutdown()


def test_a_same_origin_redirect_is_followed():
    server = _server()
    try:
        port = server.server_address[1]

        class Local(_Handler):
            def do_POST(self):
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/chat")
                    self.end_headers()
                    return
                super().do_POST()

            def do_GET(self):
                body = json.dumps(
                    {"ok": True, "result": {"pong": True}, "choices": [{"message": {"content": "ok"}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server.RequestHandlerClass = Local
        payload = _post(
            f"http://127.0.0.1:{port}/start",
            {"ping": True},
            {"Content-Type": "application/json"},
        )
        assert payload["ok"] is True
    finally:
        server.shutdown()
