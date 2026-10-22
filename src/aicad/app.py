"""Local workspace server. One process owns the document; the browser and the assistant share it.

``aicad app`` serves the UI. ``aicad serve --connect http://127.0.0.1:8765``
forwards MCP tools to that same command layer, so an external assistant and
the person edit one document and the viewport updates either way.
"""

from __future__ import annotations

import json
import mimetypes
import queue
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from aicad.errors import AICADError, GeometryError
from aicad.llm import load_settings, public_settings, run_turn, save_settings
from aicad.studio import remember_project
from aicad.workspace import Workspace

_STATIC = Path(__file__).resolve().parent / "app_static"
_FILE_SUFFIXES = {".png", ".gif", ".svg", ".json", ".md", ".txt"}


class HttpCommandClient:
    """Forwards commands to a running ``aicad app``."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def execute(self, name: str, arguments: dict | None = None, source: str = "mcp") -> dict:
        body = json.dumps({"name": name, "arguments": arguments or {}, "source": source}).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/api/command",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                message = detail
            raise GeometryError(str(message)) from exc
        except urllib.error.URLError as exc:
            raise GeometryError(f"Could not reach the workspace app at {self.base}. {exc.reason}") from exc
        if not payload.get("ok"):
            raise GeometryError(str(payload.get("error") or "The workspace command failed."))
        return payload.get("result") or {}


def serve_app(workspace: str | Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    from aicad.sandbox import warn_design_scripts

    warn_design_scripts(workspace)
    server = make_server(workspace, host, port)
    url = f"http://{host}:{port}/"
    print(f"aicad workspace at {url}", flush=True)
    print("An external assistant can attach with: aicad serve --connect " + url.rstrip("/"), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def make_server(workspace: str | Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    session = Workspace(workspace)
    remember_project(session.root)
    handler = _handler(session)
    server = ThreadingHTTPServer((host, port), handler)
    server.workspace = session  # type: ignore[attr-defined]
    return server


def _handler(session: Workspace):
    class Handler(BaseHTTPRequestHandler):
        workspace = session
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path in ("/", "/index.html"):
                    self._bytes(_STATIC / "index.html", "text/html; charset=utf-8")
                elif path == "/favicon.ico":
                    self._bytes(_STATIC / "favicon.png", "image/png")
                elif path.startswith("/static/"):
                    self._static(path[len("/static/") :])
                elif path == "/api/state":
                    self._json({"ok": True, "result": self.workspace.state()})
                elif path == "/api/commands":
                    self._json({"ok": True, "result": self.workspace.commands()})
                elif path == "/api/mesh":
                    query = parse_qs(parsed.query)
                    scene = query.get("scene", [""])[0]
                    assembly_name = query.get("assembly", [""])[0]
                    if scene in ("1", "true"):
                        self._json({"ok": True, "result": self.workspace.scene_mesh()})
                    elif assembly_name:
                        self._json({"ok": True, "result": self.workspace.assembly_mesh(assembly_name)})
                    else:
                        name = query.get("part", [""])[0]
                        self._json({"ok": True, "result": self.workspace.mesh(name)})
                elif path == "/api/settings":
                    self._json({"ok": True, "result": public_settings()})
                elif path == "/api/file":
                    rel = parse_qs(parsed.query).get("path", [""])[0]
                    self._workspace_file(rel)
                elif path == "/api/events":
                    self._events()
                else:
                    self._error(404, "Not found.")
            except AICADError as exc:
                self._error(400, str(exc))
            except Exception as exc:  # a viewer bug should not kill the server thread
                self._error(500, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                body = self._read_json()
                if parsed.path == "/api/command":
                    result = self.workspace.execute(
                        str(body.get("name") or ""),
                        body.get("arguments") or {},
                        source=str(body.get("source") or "ui"),
                    )
                    self._json({"ok": True, "revision": self.workspace.geometry_revision, "result": result})
                elif parsed.path == "/api/settings":
                    self._json({"ok": True, "result": save_settings(body)})
                elif parsed.path == "/api/chat":
                    self._chat(body)
                elif parsed.path == "/api/allow-scripts":
                    from aicad.sandbox import allow_design_scripts

                    self._json({"ok": True, "result": allow_design_scripts(self.workspace.root)})
                elif parsed.path == "/api/script-network-once":
                    self._json({"ok": True, "result": self.workspace.allow_script_network_once()})
                else:
                    self._error(404, "Not found.")
            except AICADError as exc:
                self._error(400, str(exc))
            except Exception as exc:
                self._error(500, str(exc))

        def _chat(self, body: dict) -> None:
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                raise GeometryError("Send messages, a list of {role, content}.")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(kind: str, payload: dict) -> None:
                data = json.dumps({"kind": kind, **payload}).encode("utf-8")
                self.wfile.write(b"data: " + data + b"\n\n")
                self.wfile.flush()

            try:
                run_turn(self.workspace, messages, emit, settings=load_settings())
                emit("done", {})
            except AICADError as exc:
                emit("error", {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return

        def _events(self) -> None:
            inbox: queue.Queue = queue.Queue()

            def push(event: dict) -> None:
                inbox.put(event)

            unsubscribe = self.workspace.listen(push)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(b": ok\n\n")
                self.wfile.flush()
                while True:
                    try:
                        event = inbox.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            finally:
                unsubscribe()

        def _workspace_file(self, rel: str) -> None:
            if not rel or ".." in Path(rel).parts:
                raise GeometryError("That file is outside the project.")
            candidate = Path(rel)
            path = candidate.resolve() if candidate.is_absolute() else (self.workspace.root / rel).resolve()
            root = self.workspace.root.resolve()
            if path != root and root not in path.parents:
                raise GeometryError("That file is outside the project.")
            if path.suffix.lower() not in _FILE_SUFFIXES or not path.is_file():
                raise GeometryError("That file is not available to the workspace view.")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._bytes(path, mime)

        def _static(self, name: str) -> None:
            if not name or "/" in name or ".." in name:
                self._error(404, "Not found.")
                return
            path = _STATIC / name
            if not path.is_file():
                self._error(404, "Not found.")
                return
            mime = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
            self._bytes(path, mime)

        def _bytes(self, path: Path, mime: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str) -> None:
            data = json.dumps({"ok": False, "error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 2_000_000:
                raise GeometryError("Request is too large.")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise GeometryError(f"Request body is not JSON. {exc}") from exc
            if not isinstance(payload, dict):
                raise GeometryError("Request body must be a JSON object.")
            return payload

    return Handler


__all__ = ["HttpCommandClient", "make_server", "serve_app"]
