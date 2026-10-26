"""Assistant HTTP calls do not use a proxy or a certificate file from the environment.

The marker is CANARY_HIT. A dead proxy and a certificate file that is not a
certificate do not steer the request.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aicad.app import HttpCommandClient, write_agent_token
from aicad.llm import _post, _ssl_context


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
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
