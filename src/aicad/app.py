"""Local workspace server. One process owns the document; the browser and the assistant share it.

``aicad app`` serves the UI. ``aicad serve --connect http://127.0.0.1:8765``
forwards MCP tools to that same command layer, so an external assistant and
the person edit one document and the viewport updates either way.

The server listens only on loopback. It answers ``localhost`` and ``127.0.0.1`` on the port it bound. A peer that is not loopback is refused.
A request line that names another host is refused. The assistant client does not follow a redirect.
A command has to name that origin. A read that names some other origin is
refused; the page's own reads often send no origin at all. Every API call
carries the session for this launch. The token in the page is the person
(``ui``). The token written for the assistant is MCP (``agent``). The request
body cannot choose ``source``. Responses refuse to be framed and ask the
browser not to send the address of a request onward. A project file is read from the
project directory, and that response tells the browser not to run the file.
A picture response, whose address carries the session, is not stored. Allowing design scripts,
granting the network for one script, saving settings, and starting a chat
require the page token. A program with a shell on this computer can still
read those tokens. This check stops another website. It is not a boundary
against that program.
"""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import queue
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from aicad.errors import AICADError, GeometryError
from aicad.llm import load_settings, open_assistant_url, public_settings, run_turn, save_settings
from aicad.studio import remember_project
from aicad.workspace import Workspace

_STATIC = Path(__file__).resolve().parent / "app_static"


def _static_files() -> dict[str, Path]:
    """The style sheets and scripts the app ships, by file name.

    Built from the folder listing, so a request can only pick one of these.
    A name such as ``C:x.js`` on Windows is not in the list.
    """
    global _STATIC_FILES
    if _STATIC_FILES is None:
        _STATIC_FILES = {
            entry.name: entry
            for entry in _STATIC.iterdir()
            if entry.suffix in (".css", ".js") and entry.is_file() and not entry.is_symlink()
        }
    return _STATIC_FILES


_STATIC_FILES: dict[str, Path] | None = None
_FILE_SUFFIXES = {".png", ".gif", ".svg", ".json", ".md", ".txt"}
# A project file shown in the workspace is read on the host. Past this it is refused.
_MAX_SERVED_BYTES = 32 * 1024 * 1024
_MAX_COMMAND_BYTES = 32 * 1024 * 1024
_TOKEN_HEADER = "X-Aicad-Token"
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}
# These change who may run code, who may use the network, or where a chat is sent.
# The page token is the person. The assistant token cannot call them.
_PERSON_POSTS = frozenset(
    {
        "/api/allow-scripts",
        "/api/script-network-once",
        "/api/settings",
        "/api/chat",
    }
)


def _bind_host_is_loopback(host: str) -> bool:
    """True when ``host`` is this computer's loopback, not a public interface."""
    text = host.strip().lower()
    if text == "localhost":
        return True
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _peer_is_loopback(host: str) -> bool:
    """True when the TCP peer is loopback, including an IPv4 address mapped in IPv6."""
    text = host.strip().lower()
    if text.startswith("::ffff:"):
        text = text[len("::ffff:") :]
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


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
    from aicad.paths import publish_text, restrict_private_file

    publish_text(path, token, root=path.parent, private=True)
    restrict_private_file(path)


def read_agent_token(port: int) -> str:
    path = _token_path(port)
    try:
        from aicad.file_access import read_text

        token = read_text(path, maximum=4096).strip()
    except OSError as exc:
        raise GeometryError(
            "Could not find the workspace session for that app. Start aicad app, then connect."
        ) from exc
    if not token:
        raise GeometryError("The workspace session for that app is empty. Start aicad app again.")
    return token


_OPEN_RECHECK_SECONDS = 5.0
# A browser that leaves mid-response. Windows raises ConnectionAbortedError.
# ConnectionRefusedError is not here: from a chat turn it is a real error.
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def _open_project_path(port: int) -> Path:
    return _token_dir() / f"{int(port)}.project"


def _record_open_project(port: int, root: Path) -> None:
    path = _open_project_path(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    from aicad.paths import publish_text

    publish_text(path, str(root), root=path.parent, private=True)


def _app_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def other_app_on_project(root: Path, own_port: int) -> int | None:
    """The port of another running app with this project open. A record left by a crash is removed."""
    from aicad.file_access import read_text

    wanted = os.path.normcase(str(root))
    try:
        records = sorted(_token_dir().glob("*.project"))
    except OSError:
        return None
    for record in records:
        if not record.stem.isdigit() or int(record.stem) == int(own_port):
            continue
        try:
            if os.path.normcase(read_text(record, maximum=65536).strip()) != wanted:
                continue
        except OSError:
            continue
        if _app_is_listening(int(record.stem)):
            return int(record.stem)
        record.unlink(missing_ok=True)
    return None


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


def _port_number(text: str) -> int | None:
    """A decimal port, written the way a browser sends it.

    ``int`` accepts underscores and trailing spaces. Those spellings are not
    the port on this server.
    """

    if not text.isascii() or not text.isdigit():
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    if text != str(number) or number > 65535:
        return None
    return number


def _split_host(value: str) -> tuple[str, int | None]:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "", None
    text = value.strip()
    if text.startswith("["):
        return "", None
    host, separator, port_text = text.rpartition(":")
    if not separator:
        return text.lower(), None
    return host.lower(), _port_number(port_text)


def _origin_is_local(origin: str, port: int) -> bool:
    if any(ord(char) < 32 or ord(char) == 127 for char in origin):
        return False
    parsed = urlparse(origin)
    try:
        parsed_port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    netloc = parsed.netloc
    _host, separator, raw_port = netloc.rpartition(":")
    if not separator or _port_number(raw_port) != port:
        return False
    return (
        parsed.scheme == "http"
        and host in _LOCAL_HOSTS
        and parsed_port == port
        and parsed.path in ("", "/")
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


class _WorkspaceServer(ThreadingHTTPServer):
    page_token: str
    agent_token: str
    log_commands = False
    _open_root: Path | None = None
    _other_app_port: int | None = None
    _open_lock = threading.Lock()
    _open_checked_at = 0.0

    def project_also_open_at(self, root: Path) -> int | None:
        """The port of another app on this project.

        Read again at most every few seconds, so the first app learns of a second
        one and the warning goes when that app closes. The page sees it on its next refresh.
        """
        with self._open_lock:
            now = time.monotonic()
            changed = root != self._open_root
            if changed or now - self._open_checked_at >= _OPEN_RECHECK_SECONDS:
                port = int(self.server_address[1])
                self._open_root = root
                self._other_app_port = other_app_on_project(root, port)
                self._open_checked_at = now
                if changed:
                    _record_open_project(port, root)
            return self._other_app_port

    def handle_error(self, request, client_address) -> None:
        # A page that reloads or closes drops its connection. That is normal.
        if isinstance(sys.exc_info()[1], _CLIENT_GONE):
            return
        super().handle_error(request, client_address)

    def process_request(self, request, client_address) -> None:
        # This request's geometry runs on the thread started below.
        from aicad.thread_stack import prepare_geometry_thread_stack

        prepare_geometry_thread_stack()
        super().process_request(request, client_address)

    def server_close(self) -> None:
        port = int(self.server_address[1])
        super().server_close()
        close = getattr(self.workspace, "close", None)
        if close is not None:
            close()
        remove_agent_token(port)
        _open_project_path(port).unlink(missing_ok=True)


class HttpCommandClient:
    """Forwards commands to a running ``aicad app``.

    This connection is the assistant. The app decides that. The ``source``
    argument is ignored, so a caller cannot relabel the request as the person.
    """

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        parsed = urlparse(self.base)
        try:
            port = parsed.port
        except ValueError:
            port = None
        if (
            parsed.scheme != "http"
            or (parsed.hostname or "").lower() not in _LOCAL_HOSTS
            or port is None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise GeometryError("Connect to the workspace app at http://127.0.0.1:8765.")
        self._origin = f"http://{parsed.hostname}:{port}"
        self._token = read_agent_token(port)

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
            with open_assistant_url(request, timeout=300) as response:
                payload = json.loads(_bounded_body(response, _MAX_COMMAND_BYTES).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _bounded_body(exc, _MAX_COMMAND_BYTES).decode("utf-8", errors="replace")
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


def _bounded_body(response, maximum: int) -> bytes:
    """Read at most ``maximum`` bytes. A larger response is not kept."""

    declared = response.headers.get("Content-Length") if response.headers is not None else None
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            size = None
        if size is not None and size > maximum:
            raise GeometryError("The workspace response is too large.")
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        block = response.read(min(65536, remaining))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    data = b"".join(chunks)
    if len(data) > maximum:
        raise GeometryError("The workspace response is too large.")
    return data


_LOG_WHO = {"ui": "person", "agent": "assistant"}
_LOG_ERROR_CHARS = 200
_LOG_NAME_CHARS = 80


def _log_text(text: str, limit: int) -> str:
    """One line of plain ASCII, so any Windows console code page can print it."""
    first = (text.strip().splitlines() or [""])[0].strip()
    plain = "".join(ch if " " <= ch <= "~" else "?" for ch in first)
    if len(plain) > limit:
        plain = plain[: limit - 3].rstrip() + "..."
    return plain


def _log_command(who: str, name: str, outcome: str) -> None:
    """Print one line for a command. Arguments and results are never printed."""
    stamp = time.strftime("%H:%M:%S")
    label = _log_text(name, _LOG_NAME_CHARS) or "(no name)"
    print(f"{stamp} {_LOG_WHO.get(who, who)} {label} {outcome}", flush=True)


def _second_app_warning(port: int) -> str:
    return (
        f"This project is also open in another aicad app at http://127.0.0.1:{port}/. "
        "Each app keeps its own copy, so one can overwrite what the other saved. Close one of them."
    )


def serve_app(workspace: str | Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    # Plain ASCII, because a Windows console code page may not have an ellipsis.
    print("Opening the project, this can take a minute...", flush=True)
    from aicad.sandbox import warn_design_scripts

    warn_design_scripts(workspace)
    # The page answers while the project opens, and fills in when it is ready.
    server = make_server(workspace, host, port, worker=True, open_in_background=True, log_commands=True)
    url = f"http://{host}:{port}/"
    print(f"aicad workspace at {url}", flush=True)
    threading.Thread(target=_say_when_open, args=(server.workspace,), name="aicad-open-note", daemon=True).start()
    other = server.project_also_open_at(server.workspace.root)
    if other is not None:
        print(_second_app_warning(other), file=sys.stderr, flush=True)
    print("An external assistant can attach with: aicad serve --connect " + url.rstrip("/"), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _say_when_open(session) -> None:
    problem = session.wait_open()
    if problem is None:
        print("The project is open.", flush=True)
    else:
        print(f"The project did not open. {problem}", file=sys.stderr, flush=True)


def make_server(
    workspace: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    worker: bool = False,
    open_in_background: bool = False,
    log_commands: bool = False,
) -> _WorkspaceServer:
    """The app's server. With ``worker``, geometry runs in its own process. See app_worker.

    With ``open_in_background`` as well, this returns before the project has
    opened, so the page can say that it is opening.

    With ``log_commands``, each command prints a line when it starts and when it ends.
    """
    if not _bind_host_is_loopback(host):
        raise GeometryError(
            "The workspace app only listens on this computer. Use 127.0.0.1, localhost, or ::1."
        )
    from aicad.thread_stack import prepare_geometry_thread_stack

    prepare_geometry_thread_stack()
    if worker:
        from aicad.app_worker import GeometryWorker

        session = GeometryWorker(workspace, wait=not open_in_background)
    else:
        session = Workspace(workspace)
    remember_project(session.root)
    handler = _handler(session)
    server = _WorkspaceServer((host, port), handler)
    server.workspace = session  # type: ignore[attr-defined]
    server.log_commands = log_commands
    server.page_token = secrets.token_urlsafe(32)
    server.agent_token = secrets.token_urlsafe(32)
    write_agent_token(int(server.server_address[1]), server.agent_token)
    server.project_also_open_at(session.root)
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
                    state = self.workspace.state()
                    other = self.server.project_also_open_at(self.workspace.root)
                    state["project_warning"] = _second_app_warning(other) if other is not None else ""
                    self._json({"ok": True, "result": state})
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
                if parsed.path in _PERSON_POSTS:
                    self._require_person()
                body = self._read_json()
                if parsed.path == "/api/command":
                    # ``source`` in the body is ignored. The token says who called.
                    result = self._run_command(
                        str(body.get("name") or ""),
                        body.get("arguments") or {},
                        self._caller(),
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
                self._discard_unread_body()
                self._error(403, exc.message)
            except AICADError as exc:
                self._discard_unread_body()
                self._error(400, str(exc))
            except Exception as exc:
                self._discard_unread_body()
                self._error(500, str(exc))

        def _run_command(self, name: str, arguments, source: str):
            if not self.server.log_commands:
                return self.workspace.execute(name, arguments, source=source)
            _log_command(source, name, "started")
            started = time.monotonic()
            try:
                result = self.workspace.execute(name, arguments, source=source)
            except BaseException as exc:
                elapsed = time.monotonic() - started
                reason = _log_text(str(exc), _LOG_ERROR_CHARS) or type(exc).__name__
                _log_command(source, name, f"failed after {elapsed:.1f} s: {reason}")
                raise
            _log_command(source, name, f"ok after {time.monotonic() - started:.1f} s")
            return result

        def _guard(self) -> None:
            self._check_target()
            self._check_host()
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                # An image tag cannot send Origin. The session token still has to match.
                picture = path == "/api/file" and self.command == "GET"
                self._check_origin(required=not picture)
                self._check_token()
            else:
                self._check_origin(required=False)

        def _check_target(self) -> None:
            parsed = urlparse(self.path)
            if parsed.scheme or parsed.netloc or parsed.path.startswith("//") or "\\" in parsed.path:
                raise RequestRejected("This workspace only answers to localhost.")

        def _check_host(self) -> None:
            peer = self.client_address[0] if self.client_address else ""
            if not _peer_is_loopback(str(peer)):
                raise RequestRejected("This workspace only answers to localhost.")
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

        def _require_person(self) -> None:
            if self._caller() != "ui":
                raise RequestRejected("Only the person at this workspace can do that.")

        def _page(self) -> None:
            from aicad.file_access import read_text

            text = read_text(_STATIC / "index.html", maximum=2_000_000)
            if "__AICAD_TOKEN__" not in text:
                self._error(500, "The workspace page is missing its session slot.")
                return
            if "__AICAD_VERSION__" not in text:
                self._error(500, "The workspace page is missing its version slot.")
                return
            from aicad._version import __version__

            data = (
                text.replace("__AICAD_TOKEN__", self.server.page_token)
                .replace("__AICAD_VERSION__", __version__)
                .encode("utf-8")
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _security_headers(self, *, isolate: bool = False) -> None:
            self.send_header("X-Frame-Options", "DENY")
            if isolate:
                policy = "default-src 'none'; script-src 'none'; object-src 'none'; frame-ancestors 'none'"
            else:
                policy = "frame-ancestors 'none'"
            self.send_header("Content-Security-Policy", policy)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")

        def _chat(self, body: dict) -> None:
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                raise GeometryError("Send messages, a list of {role, content}.")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._security_headers()
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
            except _CLIENT_GONE:
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
            self._security_headers()
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
            except (ConnectionError, TimeoutError):
                return
            finally:
                unsubscribe()

        def _workspace_file(self, rel: str) -> None:
            from aicad.paths import is_reparse, read_checked_bytes, shared_file, windows_trim_escapes

            if not rel or ".." in Path(rel).parts or windows_trim_escapes(rel):
                raise GeometryError("That file is outside the project.")
            candidate = Path(rel)
            if candidate.is_absolute():
                raise GeometryError("That file is outside the project.")
            root = self.workspace.root.resolve()
            lexical = root / rel
            try:
                path = lexical.resolve()
            except OSError as exc:
                raise GeometryError("That file is outside the project.") from exc
            if path != root and root not in path.parents:
                raise GeometryError("That file is outside the project.")
            current = root
            for part in candidate.parts:
                current = current / part
                if is_reparse(current):
                    raise GeometryError("That file is a link and was not opened.")
            if shared_file(lexical):
                raise GeometryError("That file has another name and was not opened.")
            if path.suffix.lower() not in _FILE_SUFFIXES or not path.is_file():
                raise GeometryError("That file is not available to the workspace view.")
            normalized = os.path.realpath(path)
            root_name = os.path.realpath(root)
            if not normalized.startswith(root_name + os.sep):
                raise GeometryError("That file is outside the project.")
            try:
                data = read_checked_bytes(
                    root,
                    candidate,
                    maximum=_MAX_SERVED_BYTES,
                    label="That file",
                    error=GeometryError,
                )
            except GeometryError as exc:
                message = str(exc)
                if "too large" in message:
                    raise GeometryError("That file is too large.") from exc
                if "another name" in message:
                    raise GeometryError("That file has another name and was not opened.") from exc
                if "link" in message:
                    raise GeometryError("That file is a link and was not opened.") from exc
                if "outside" in message:
                    raise GeometryError("That file is outside the project.") from exc
                raise GeometryError("That file is not available to the workspace view.") from exc
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix.lower() in {".md", ".txt"} and self._caller() not in {"ui", "cli", "api"}:
                self._marked_note(data, mime)
                return
            self._send_file(data, mime)

        def _marked_note(self, data: bytes, mime: str) -> None:
            from aicad.workspace import _fence_note

            try:
                text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError:
                self._send_file(data, mime)
                return
            marked = _fence_note({"markdown": text})["markdown"].encode("utf-8")
            self._send_file(marked, "text/plain; charset=utf-8")

        def _send_file(self, data: bytes, mime: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers(isolate=True)
            self.end_headers()
            self.wfile.write(data)

        def _static(self, name: str) -> None:
            # Only a file the app ships is served. The request names it, but the
            # path comes from the app's own list, so no name can reach another file.
            path = _static_files().get(name)
            if path is None:
                self._error(404, "Not found.")
                return
            mime = "text/css; charset=utf-8" if path.suffix == ".css" else "text/javascript; charset=utf-8"
            self._bytes(path, mime)

        def _bytes(self, path: Path, mime: str, *, cache: str = "no-cache") -> None:
            from aicad.file_access import read_file

            try:
                data = read_file(path, maximum=8 * 1024 * 1024)
            except OSError:
                self._error(404, "Not found.")
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status: int, message: str) -> None:
            try:
                if self._caller() == "agent":
                    from aicad.paths import hide_project_path

                    message = hide_project_path(message, self.workspace.root)
            except Exception:
                pass
            data = json.dumps({"ok": False, "error": message}).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()
                self.wfile.write(data)
            except _CLIENT_GONE:
                return  # the client left. There is no one to answer.

        def _content_length(self) -> int | None:
            """How many body bytes the client declared.

            A missing header is an empty body. A negative number, or a value
            that is not an integer, is not a size: ``read(-1)`` would read
            until the client closes and ignore the 2 MB cap.
            """
            raw = self.headers.get("Content-Length")
            if raw is None:
                return 0
            try:
                length = int(raw)
            except (TypeError, ValueError):
                return None
            if length < 0:
                return None
            return length

        def _discard_unread_body(self) -> None:
            """Read a POST body that a refusal did not use.

            A refusal runs before the body is read. Leaving those bytes unread
            makes Windows reset the socket, so the client never sees the status.
            A length that is not a size is drained only from bytes already
            received, so a client that never closes cannot hold this thread.
            """
            if getattr(self, "_body_taken", False):
                return
            self._body_taken = True
            length = self._content_length()
            if length is None:
                self._drain_already_received(2_000_000)
                return
            remaining = min(length, 2_000_000)
            while remaining:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)

        def _drain_already_received(self, limit: int) -> None:
            remaining = limit
            sock = self.connection
            try:
                sock.setblocking(False)
            except OSError:
                return
            try:
                while remaining:
                    try:
                        chunk = self.rfile.read(min(remaining, 65536))
                    except (BlockingIOError, InterruptedError):
                        break
                    if not chunk:
                        break
                    remaining -= len(chunk)
            finally:
                try:
                    sock.setblocking(True)
                except OSError:
                    pass

        def _read_json(self) -> dict:
            length = self._content_length()
            if length is None:
                raise GeometryError("Content-Length is not a size.")
            if length > 2_000_000:
                raise GeometryError("Request is too large.")
            self._body_taken = True
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
