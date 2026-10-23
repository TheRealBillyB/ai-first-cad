"""Local workspace server. One process owns the document; the browser and the assistant share it.

``aicad app`` serves the UI. ``aicad serve --connect http://127.0.0.1:8765``
forwards MCP tools to that same command layer, so an external assistant and
the person edit one document and the viewport updates either way.

The server answers only ``localhost`` and ``127.0.0.1`` on the port it bound.
A command has to name that origin. A read that names some other origin is
refused; the page's own reads often send no origin at all. Every API call
carries the session for this launch. The token in the page is the person
(``ui``). The token written for the assistant is MCP (``agent``). The request
body cannot choose ``source``. A program with a shell on this computer can
still read those tokens. This check stops another website. It is not a
boundary against that program.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import secrets
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
_TOKEN_HEADER = "X-Aicad-Token"
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


class RequestRejected(Exception):
    """The workspace server refused the connection before running a command."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _token_dir() -> Path:
    override = os.environ.get("AICAD_SERVER_TOKENS")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aicad" / "servers"


def _token_path(port: int) -> Path:
    return _token_dir() / f"{int(port)}.token"


def write_agent_token(port: int, token: str) -> None:
    """Remember the assistant's session for this port. The file is the user's."""
    path = _token_path(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)


def read_agent_token(port: int) -> str:
    path = _token_path(port)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise GeometryError(
            "Could not find the workspace session for that app. Start aicad app, then connect."
        ) from exc
    if not token:
        raise GeometryError("The workspace session for that app is empty. Start aicad app again.")
    return token


def remove_agent_token(port: int) -> None:
    try:
        _token_path(port).unlink()
    except OSError:
        return


def _token_matches(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    try:
        return secrets.compare_digest(presented, expected)
    except (TypeError, ValueError):
        return False


def _split_host(value: str) -> tuple[str, int | None]:
    text = value.strip()
    if text.startswith("["):
        return "", None
    host, separator, port_text = text.rpartition(":")
    if not separator:
        return text.lower(), None
    try:
        return host.lower(), int(port_text)
    except ValueError:
        return host.lower(), None


def _origin_is_local(origin: str, port: int) -> bool:
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "http"
        and host in _LOCAL_HOSTS
        and parsed.port == port
        and parsed.path in ("", "/")
        and not parsed.username
        and not parsed.query
        and not parsed.fragment
    )


class _WorkspaceServer(ThreadingHTTPServer):
    page_token: str
    agent_token: str

    def server_close(self) -> None:
        port = int(self.server_address[1])
        super().server_close()
        remove_agent_token(port)


class HttpCommandClient:
    """Forwards commands to a running ``aicad app``.

    This connection is the assistant. The app decides that. The ``source``
    argument is ignored, so a caller cannot relabel the request as the person.
    """

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        parsed = urlparse(self.base)
        if parsed.scheme != "http" or (parsed.hostname or "").lower() not in _LOCAL_HOSTS or not parsed.port:
            raise GeometryError("Connect to the workspace app at http://127.0.0.1:8765.")
        self._origin = f"http://{parsed.hostname}:{parsed.port}"
        self._token = read_agent_token(parsed.port)

    def execute(self, name: str, arguments: dict | None = None, source: str = "agent") -> dict:
        del source
        body = json.dumps({"name": name, "arguments": arguments or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/api/command",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Origin": self._origin,
                _TOKEN_HEADER: self._token,
            },
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


def make_server(workspace: str | Path, host: str = "127.0.0.1", port: int = 8765) -> _WorkspaceServer:
    session = Workspace(workspace)
    remember_project(session.root)
    handler = _handler(session)
    server = _WorkspaceServer((host, port), handler)
    server.workspace = session  # type: ignore[attr-defined]
    server.page_token = secrets.token_urlsafe(32)
    server.agent_token = secrets.token_urlsafe(32)
    write_agent_token(int(server.server_address[1]), server.agent_token)
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
                self._guard()
                if path in ("/", "/index.html"):
                    self._page()
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
            except RequestRejected as exc:
                self._error(403, exc.message)
            except AICADError as exc:
                self._error(400, str(exc))
            except Exception as exc:  # a viewer bug should not kill the server thread
                self._error(500, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                self._guard()
                body = self._read_json()
                if parsed.path == "/api/command":
                    # ``source`` in the body is ignored. The token says who called.
                    result = self.workspace.execute(
                        str(body.get("name") or ""),
                        body.get("arguments") or {},
                        source=self._caller(),
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
            except RequestRejected as exc:
                self._error(403, exc.message)
            except AICADError as exc:
                self._error(400, str(exc))
            except Exception as exc:
                self._error(500, str(exc))

        def _guard(self) -> None:
            self._check_host()
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                # An image tag cannot send Origin. The session token still has to match.
                picture = path == "/api/file" and self.command == "GET"
                self._check_origin(required=not picture)
                self._check_token()
            else:
                self._check_origin(required=False)

        def _check_host(self) -> None:
            port = int(self.server.server_address[1])
            host, host_port = _split_host(self.headers.get("Host") or "")
            if host not in _LOCAL_HOSTS or host_port != port:
                raise RequestRejected("This workspace only answers to localhost.")

        def _check_origin(self, required: bool) -> None:
            origin = self.headers.get("Origin")
            port = int(self.server.server_address[1])
            if origin is None:
                # Chrome omits Origin on a same-origin read. A POST always sends it,
                # and a command with no origin is refused.
                if required and self.command not in ("GET", "HEAD"):
                    raise RequestRejected("That request came from another site.")
                return
            if not _origin_is_local(origin, port):
                raise RequestRejected("That request came from another site.")

        def _presented_token(self) -> str:
            header = self.headers.get(_TOKEN_HEADER) or ""
            if header:
                return header
            parsed = urlparse(self.path)
            if parsed.path == "/api/file" and self.command == "GET":
                return parse_qs(parsed.query).get("token", [""])[0]
            return ""

        def _caller(self) -> str:
            token = self._presented_token()
            if _token_matches(token, self.server.page_token):
                return "ui"
            if _token_matches(token, self.server.agent_token):
                return "agent"
            raise RequestRejected("This request is missing the workspace session.")

        def _check_token(self) -> None:
            self._caller()

        def _page(self) -> None:
            text = (_STATIC / "index.html").read_text(encoding="utf-8")
            if "__AICAD_TOKEN__" not in text:
                self._error(500, "The workspace page is missing its session slot.")
                return
            data = text.replace("__AICAD_TOKEN__", self.server.page_token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

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
            if path.suffix.lower() in {".md", ".txt"} and self._caller() not in {"ui", "cli", "api"}:
                self._marked_note(path, mime)
                return
            self._bytes(path, mime)

        def _marked_note(self, path: Path, mime: str) -> None:
            from aicad.workspace import _fence_note

            root = os.path.realpath(self.workspace.root)
            resolved = os.path.realpath(path)
            if resolved.startswith(root + os.sep):
                try:
                    text = Path(resolved).read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    self._bytes(Path(resolved), mime)
                    return
            else:
                raise GeometryError("That file is outside the project.")
            marked = _fence_note({"markdown": text})["markdown"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(marked)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(marked)

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
