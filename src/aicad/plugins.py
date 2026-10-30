"""A small door for analyses, part libraries, and exporters.

Version 1 is deliberate and narrow. A plugin is a Python package that
registers one entry point in the ``aicad.plugins`` group and ships a static
``aicad.plugin.json``. aicad reads that file as data. Listing a plugin, or
showing its warning, does not import it.

A third-party plugin's code runs only after ``allow(..., accept=True)`` for
that package version and those declared permissions. Opening a design file
does not allow or load a plugin. A design script cannot allow, revoke, or
load one either. The permission names are a declaration the warning shows.
They are not a sandbox: once allowed, the package runs in this process.

A plugin ships with aicad only when its metadata directory is the one next
to this copy of aicad. The package name is not that check. Another package
with the same name still needs an allow, so its top-level code does not run
until then. If one package's entry points cannot be read, that package is
skipped and listed with a warning. The other packages are still listed.

A plugin cannot replace a built-in check, the standard-part catalog, or STEP
and STL export. Reports and files it produces are marked as coming from the
plugin.
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aicad.errors import AICADError

API_VERSION = 1
ENTRY_GROUP = "aicad.plugins"
MANIFEST_NAME = "aicad.plugin.json"
PERMISSIONS = ("project_files", "network", "checks")
PROVIDES = ("analysis", "parts", "exporter")
_RESERVED_FORMATS = frozenset({"step", "stl"})
_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CALL_WORKSPACE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "aicad_plugin_workspace",
    default=None,
)

_PERMISSION_PHRASES = {
    "project_files": "project files (the project folder, during a call you make)",
    "network": "network (the publisher says the plugin will use the network)",
    "checks": "checks (run an analysis on a part you name)",
}
_PROVIDES_PHRASES = {
    "analysis": "an analysis",
    "parts": "a part library",
    "exporter": "an exporter",
}


class PluginError(AICADError):
    """A plugin could not be listed, allowed, or run."""


class PluginConsentError(PluginError):
    """A third-party plugin is not allowed to run, so its code was not imported."""


@dataclass(frozen=True)
class PluginOffer:
    """What aicad knows about a plugin before importing it."""

    name: str
    distribution: str
    version: str
    publisher: str
    summary: str
    permissions: tuple[str, ...]
    provides: tuple[str, ...]
    third_party: bool
    stamp: str
    entry: str
    problem: str | None = None

    def to_dict(self, *, allowed: bool) -> dict[str, object]:
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "publisher": self.publisher,
            "summary": self.summary,
            "permissions": list(self.permissions),
            "provides": list(self.provides),
            "third_party": self.third_party,
            "allowed": allowed,
            "problem": self.problem,
        }


@dataclass(frozen=True)
class PartView:
    """A snapshot of one part passed into a plugin call.

    ``workspace`` is the project folder only when the manifest declared
    ``project_files``. ``step_file`` is set for an exporter: a temporary copy
    of the solid, valid only for the duration of that call. It is not the
    part stored in the design, and an analysis does not receive one.
    """

    name: str
    volume_mm3: float
    area_mm2: float
    center_mm: tuple[float, float, float]
    size_mm: tuple[float, float, float]
    valid: bool
    watertight: bool
    step_file: Path | None
    workspace: Path | None


@dataclass(frozen=True)
class _Registration:
    name: str
    summary: str
    call: object


class PluginHost:
    """The object ``register(host)`` receives. This is the whole version-1 API.

    ``workspace`` is ``None`` while ``register`` itself runs. During a later
    call it is the project folder only if the manifest declared
    ``project_files``.
    """

    def __init__(self, offer: PluginOffer):
        self.api_version = API_VERSION
        self.name = offer.name
        self.permissions = frozenset(offer.permissions)
        self._provides = set(offer.provides)
        self._analyses: dict[str, _Registration] = {}
        self._parts: dict[str, _Registration] = {}
        self._exporters: dict[str, _Registration] = {}

    @property
    def workspace(self) -> Path | None:
        if "project_files" not in self.permissions:
            return None
        return _CALL_WORKSPACE.get()

    def add_analysis(self, name: str, summary: str, run) -> None:
        """Register an analysis. ``run(part, options)`` returns a dict."""
        self._require_provide("analysis", "add_analysis")
        if "checks" not in self.permissions:
            raise PluginError(
                "add_analysis needs the 'checks' permission in aicad.plugin.json. "
                "That is the declaration the warning shows."
            )
        self._put(self._analyses, name, summary, run, "analysis")

    def add_part(self, kind: str, summary: str, build) -> None:
        """Register a part. ``build(options)`` returns a script that assigns ``part``."""
        self._require_provide("parts", "add_part")
        self._put(self._parts, kind, summary, build, "part")

    def add_exporter(self, format: str, summary: str, write) -> None:
        """Register an exporter. ``write(part, options)`` returns text or bytes.

        aicad writes the file. The names ``step`` and ``stl`` stay built in.
        """
        self._require_provide("exporter", "add_exporter")
        if format in _RESERVED_FORMATS:
            raise PluginError(
                f"Format {format!r} is built in. A plugin cannot replace STEP or STL export."
            )
        self._put(self._exporters, format, summary, write, "exporter")

    def _require_provide(self, kind: str, method: str) -> None:
        if kind not in self._provides:
            raise PluginError(
                f"{method} is not in this plugin's aicad.plugin.json provides list. "
                "The manifest is what the warning showed."
            )

    def _put(self, store: dict[str, _Registration], name: str, summary: str, call, label: str) -> None:
        _require_token(name, label)
        if not isinstance(summary, str) or not summary.strip():
            raise PluginError(f"The {label} {name!r} needs a one-line summary.")
        if len(summary) > 400:
            raise PluginError(f"The summary for {label} {name!r} is longer than 400 characters.")
        if not callable(call):
            raise PluginError(f"The {label} {name!r} must be a function.")
        if name in store:
            raise PluginError(f"Plugin {self.name!r} registered {label} {name!r} twice.")
        store[name] = _Registration(name, summary.strip(), call)

    def _finish(self, offer: PluginOffer) -> LoadedPlugin:
        missing = []
        if "analysis" in offer.provides and not self._analyses:
            missing.append("an analysis")
        if "parts" in offer.provides and not self._parts:
            missing.append("a part")
        if "exporter" in offer.provides and not self._exporters:
            missing.append("an exporter")
        if missing:
            joined = ", ".join(missing)
            raise PluginError(
                f"Plugin {offer.name!r} declared {joined} in aicad.plugin.json but did not register it."
            )
        return LoadedPlugin(offer, self)


class LoadedPlugin:
    """A plugin whose module has been imported after consent."""

    def __init__(self, offer: PluginOffer, host: PluginHost):
        self.name = offer.name
        self.publisher = offer.publisher
        self.distribution = offer.distribution
        self.version = offer.version
        self.permissions = frozenset(offer.permissions)
        self._analyses = dict(host._analyses)
        self._parts = dict(host._parts)
        self._exporters = dict(host._exporters)

    def _analysis(self, name: str) -> _Registration:
        return _lookup(self._analyses, self.name, "analysis", name)

    def _part(self, name: str) -> _Registration:
        return _lookup(self._parts, self.name, "part", name)

    def _exporter(self, name: str) -> _Registration:
        return _lookup(self._exporters, self.name, "exporter", name)


def discover() -> tuple[PluginOffer, ...]:
    """List installed plugins. This reads entry points and manifests. It does not import them.

    A package whose entry points cannot be read is skipped and reported.
    The other packages are still listed.
    """
    grouped: dict[str, list] = {}
    order: list[str] = []
    offers: list[PluginOffer] = []
    for dist in importlib.metadata.distributions():
        try:
            entries, problem = _group_entries(dist)
        except Exception:
            continue
        if problem is not None:
            offers.append(problem)
            continue
        for entry in entries:
            key = _distribution_key(entry)
            if key not in grouped:
                order.append(key)
                grouped[key] = []
            grouped[key].append(entry)
    for key in order:
        group = grouped[key]
        if len(group) > 1:
            for entry in group:
                offers.append(
                    _broken(
                        entry,
                        "This package registers more than one aicad plugin. "
                        "Version 1 of the door allows one. Nothing was imported.",
                    )
                )
            continue
        offers.append(_offer_from_entry_point(group[0]))
    offers.sort(key=lambda offer: offer.name)
    return tuple(offers)


def get(name: str) -> PluginOffer:
    """Return one discovered plugin. Raises if it is missing or ambiguous. Does not import it."""
    matches = [offer for offer in discover() if offer.name == name]
    if not matches:
        raise PluginError(
            f"No plugin named {name!r} is installed. "
            "`aicad plugins list` shows entry points. Nothing was run."
        )
    if len(matches) > 1:
        raise PluginError(
            f"More than one installed package registers plugin {name!r}. "
            "Uninstall one of them. Nothing was run."
        )
    return matches[0]


def _shown(value: object) -> str:
    """One printed line from a manifest or from package metadata."""

    from aicad.labels import plain_line

    return plain_line(value if isinstance(value, str) else "")


def warning_text(offer: PluginOffer) -> str:
    """The warning aicad shows before a third-party plugin can run.

    The text is built from the manifest. A plugin has no hook to replace it.
    Names and the summary are one line each.
    """
    if offer.problem:
        return (
            f"Plugin {_shown(offer.name)!r} cannot run.\n"
            f"{_shown(offer.problem)}\n"
            "Nothing was imported.\n"
        )
    if offer.third_party:
        header = f"Third-party plugin {_shown(offer.name)!r} runs only after you allow it."
    else:
        header = f"Plugin {_shown(offer.name)!r} ships with aicad. It runs only when you ask for it."
    asked = _human_permissions(offer.permissions)
    withheld = [item for item in PERMISSIONS if item not in offer.permissions]
    lines = [
        header,
        "",
        f"Publisher: {_shown(offer.publisher)}",
        f"Package: {_shown(offer.distribution)} {_shown(offer.version)}",
        f"The publisher's summary: {_shown(offer.summary)}",
        f"It adds: {_human_provides(offer.provides)}.",
        f"It asked for: {asked}.",
    ]
    if withheld:
        pretty = ", ".join(item.replace("_", " ") for item in withheld)
        lines.append(f"It did not ask for: {pretty}.")
    lines.extend(
        [
            "",
            "Allowing it runs that package's Python in this aicad process, "
            "including the top of the module, not only the function you name.",
            "The permission list is what the publisher declared in aicad.plugin.json. "
            "It is not a sandbox. aicad does not open a network connection for the plugin, "
            "and it does not block one either.",
            "This warning is written by aicad. The plugin cannot replace it.",
            "Opening a design file does not allow or load a plugin.",
        ]
    )
    if offer.third_party:
        lines.extend(
            [
                "",
                "To allow this exact package version and these declared permissions:",
                f"  aicad plugins allow {_shown(offer.name)} --yes",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _plugin_refused(action: str) -> PluginConsentError:
    return PluginConsentError(
        f"A design script cannot {action} a plugin. "
        "Plugin consent is a separate switch from design scripts. "
        "Allow a plugin yourself with aicad plugins allow, outside the script."
    )


def _refuse_if_sandboxed(action: str) -> None:
    """A design script must not grant or use plugin consent."""
    from aicad.sandbox_state import in_sandbox

    if in_sandbox():
        raise _plugin_refused(action)


def seal_sandbox_plugins() -> None:
    """Replace allow, load, and revoke for the rest of this process.

    Called only in the confined child, before the script runs. The replacements
    do not look at the environment, so clearing ``AICAD_SANDBOX_CHILD`` does
    not turn them back on.
    """
    global allow, revoke, load

    def _sealed(action: str):
        def refuse(*_args, **_kwargs):
            raise _plugin_refused(action)

        return refuse

    allow = _sealed("allow")
    revoke = _sealed("revoke")
    load = _sealed("load")


def consent_file() -> Path:
    """Where an allow is recorded. This is user config, never the design folder.

    A confined script reports the home config. Windows points ``XDG_CONFIG_HOME``
    at the project cache so libraries can write, and that cache is not consent.
    """
    import os

    from aicad.sandbox_state import in_sandbox

    if in_sandbox():
        from aicad.sandbox_state import account_home

        # The script home is a clean directory. Consent stays on the account
        # home that was remembered before that directory was installed.
        home = account_home()
        if home is None:
            home = Path.home()
        return home / ".config" / "aicad" / "plugins.json"
    override = os.environ.get("AICAD_PLUGIN_CONSENT", "").strip()
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "aicad" / "plugins.json"


def allow(name: str, *, accept: bool, consent_path: Path | None = None) -> PluginOffer:
    """Record consent for one third-party plugin. This does not import it.

    ``accept`` must be ``True``. Show :func:`warning_text` first. Consent is
    tied to the package version, the entry point, and the manifest. A later
    change to any of those requires a new allow.
    """
    _refuse_if_sandboxed("allow")
    offer = get(name)
    if offer.problem:
        raise PluginError(offer.problem + " Nothing was allowed.")
    if not offer.third_party:
        raise PluginError(
            f"Plugin {offer.name!r} ships with aicad. It runs when you call it, "
            "and it does not need a separate allow. Opening a design still does not load it."
        )
    if accept is not True:
        raise PluginConsentError(
            warning_text(offer)
            + "Pass accept=True only after showing this warning. "
            "The plugin was not allowed and was not run.\n"
        )
    _write_allow(offer, consent_path)
    return offer


def revoke(name: str, *, consent_path: Path | None = None) -> None:
    """Forget an allow. This does not import the plugin."""
    _refuse_if_sandboxed("revoke")
    path = Path(consent_path) if consent_path is not None else consent_file()
    data = _read_consent(path)
    data.setdefault("allowed", {}).pop(name, None)
    _atomic_json(path, data)


def load(name: str, *, consent_path: Path | None = None) -> LoadedPlugin:
    """Import one plugin and call its ``register`` function.

    A third-party plugin is imported only when a matching allow exists.
    Refusing consent happens before the import.
    """
    _refuse_if_sandboxed("load")
    offer = get(name)
    if offer.problem:
        raise PluginError(offer.problem + " Nothing was imported.")
    if offer.third_party and not _has_matching_consent(offer, consent_path):
        record = _consent_record(offer.name, consent_path)
        if record is None:
            tail = "This plugin is not allowed yet, so its code was not run.\n"
        else:
            tail = (
                "A previous allow does not match this package version or these permissions. "
                "The plugin was not run. Allow it again if you still want this version.\n"
            )
        raise PluginConsentError(warning_text(offer) + tail)
    module_name, separator, attr = offer.entry.partition(":")
    if not separator or not module_name or not attr:
        raise PluginError(
            f"Plugin {offer.name!r} has an unusable entry point {offer.entry!r}. Nothing was run."
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise PluginError(
            f"Plugin {offer.name!r} was allowed, but importing {module_name!r} failed: {exc}"
        ) from exc
    register = getattr(module, attr, None)
    if not callable(register):
        raise PluginError(
            f"Plugin {offer.name!r} entry point {offer.entry!r} is not a function."
        )
    host = PluginHost(offer)
    try:
        register(host)
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError(f"Plugin {offer.name!r} failed while registering: {exc}") from exc
    return host._finish(offer)


def run_analysis(
    plugin: str,
    analysis: str,
    part: str,
    options: dict | None = None,
    *,
    document,
    consent_path: Path | None = None,
) -> dict:
    """Run one analysis. The result is wrapped so it cannot look like a built-in check."""
    loaded = load(plugin, consent_path=consent_path)
    spec = loaded._analysis(analysis)
    opts = _options_dict(options)
    view, temp_dir = _part_view(document, part, permissions=loaded.permissions, step=False)
    try:
        result = _during(
            loaded.permissions,
            document.workspace,
            lambda: spec.call(view, opts),
        )
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError(f"Analysis {analysis!r} from plugin {plugin!r} failed: {exc}") from exc
    finally:
        _cleanup(temp_dir)
    if not isinstance(result, dict):
        raise PluginError(
            f"Analysis {analysis!r} must return a dict. The part was not given a plugin result to store."
        )
    from aicad.honesty import ROUGH_ESTIMATE, finish

    note = (
        f"This report came from plugin {loaded.name!r}, not from a built-in aicad check. "
        "It does not change the part or any built-in check."
    )
    return finish(
        {
            "plugin": loaded.name,
            "publisher": loaded.publisher,
            "analysis": analysis,
            "part": part,
            "from_plugin": True,
            "result": dict(result),
            "note": note,
            "assumptions": [note],
        },
        kind=ROUGH_ESTIMATE,
        name=analysis,
        what=(
            "A plugin analysis is a rough estimate. aicad did not compute it, "
            "and it is not a verified check of the model."
        ),
        limits=[
            "The plugin's own numbers stay under result. aicad does not recompute them.",
            "Allowing the plugin does not turn this report into a pass from a built-in check.",
        ],
    )


def insert_part(
    plugin: str,
    kind: str,
    name: str,
    options: dict | None = None,
    *,
    document,
    consent_path: Path | None = None,
):
    """Build a part from a plugin and save it as an ordinary part script.

    Reopening the design shows the saved solid and the script text. It does not
    run the script, and it does not import the plugin again.
    """
    loaded = load(plugin, consent_path=consent_path)
    spec = loaded._part(kind)
    opts = _options_dict(options)
    try:
        script = _during(
            loaded.permissions,
            document.workspace,
            lambda: spec.call(opts),
        )
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError(f"Part {kind!r} from plugin {plugin!r} failed: {exc}") from exc
    if not isinstance(script, str) or not script.strip():
        raise PluginError(
            "A part builder must return a Python script that assigns `part`. The part was not saved."
        )
    if len(script) > 200_000:
        raise PluginError("The part script is longer than 200000 characters. The part was not saved.")
    return document.create_part_from_script(name, script)


def export_with(
    plugin: str,
    format: str,
    part: str,
    path: str | Path,
    options: dict | None = None,
    *,
    document,
    consent_path: Path | None = None,
) -> dict:
    """Ask a plugin for file contents and write them inside the project.

    The plugin does not choose the path. ``step`` and ``stl`` are refused
    before the plugin is imported. A path outside the project is refused
    before the plugin is imported too.
    """
    if format in _RESERVED_FORMATS:
        raise PluginError(
            f"Format {format!r} is built in. Plugin exporters cannot replace STEP or STL."
        )
    from aicad.paths import refuse_reserved_device, require_export_path

    refuse_reserved_device(path, Path(document.workspace), "Export path", PluginError)
    destination = require_export_path(Path(document.workspace), path, "Export path", PluginError)
    loaded = load(plugin, consent_path=consent_path)
    spec = loaded._exporter(format)
    opts = _options_dict(options)
    view, temp_dir = _part_view(document, part, permissions=loaded.permissions, step=True)
    try:
        from aicad.paths import use_held_thread

        def _call_exporter():
            with use_held_thread(temp_dir):
                return spec.call(view, opts)

        payload = _during(
            loaded.permissions,
            document.workspace,
            _call_exporter,
        )
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError(f"Exporter {format!r} from plugin {plugin!r} failed: {exc}") from exc
    finally:
        _cleanup(temp_dir)
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    elif isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    else:
        raise PluginError(
            "An exporter must return text or bytes. aicad chooses the path and writes the file."
        )
    from aicad.paths import output_path, publish_bytes

    destination = output_path(document.workspace, path, "Export path", PluginError)
    publish_bytes(destination, data)
    return {
        "plugin": loaded.name,
        "publisher": loaded.publisher,
        "format": format,
        "part": part,
        "path": str(destination.resolve()),
        "bytes": len(data),
        "from_plugin": True,
        "note": "This file was written by the plugin. It is not the built-in STEP or STL export.",
    }


def add_cli(commands, common) -> None:
    """Register ``aicad plugins``. The subcommands do not open a design unless one is needed."""
    plugins = commands.add_parser(
        "plugins",
        help="List, allow, or run an installed plugin. Opening a design does not do this.",
    )
    sub = plugins.add_subparsers(dest="plugin_command", required=True)
    sub.add_parser("list", parents=[common], help="List installed plugins. Does not run them.")
    show = sub.add_parser("show", parents=[common], help="Print the warning for one plugin. Does not run it.")
    show.add_argument("plugin_name")
    allow_cmd = sub.add_parser(
        "allow",
        parents=[common],
        help="Allow one third-party plugin. Prints the warning. --yes records consent and still does not run it.",
    )
    allow_cmd.add_argument("plugin_name")
    allow_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Record consent for this package version and these declared permissions.",
    )
    revoke_cmd = sub.add_parser("revoke", parents=[common], help="Forget a previous allow. Does not run the plugin.")
    revoke_cmd.add_argument("plugin_name")
    run = sub.add_parser("run", parents=[common], help="Run one analysis from an allowed plugin.")
    run.add_argument("plugin_name")
    run.add_argument("analysis")
    run.add_argument("part")
    run.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    insert = sub.add_parser("insert", parents=[common], help="Insert a part from an allowed plugin library.")
    insert.add_argument("plugin_name")
    insert.add_argument("name")
    insert.add_argument("--kind", required=True)
    insert.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    export = sub.add_parser("export", parents=[common], help="Write a file with an allowed plugin exporter.")
    export.add_argument("plugin_name")
    export.add_argument("part")
    export.add_argument("--format", dest="export_format", required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")


def dispatch_cli(args) -> int:
    """Run a plugin subcommand. List, show, allow, and revoke do not open a design."""
    command = args.plugin_command
    if command == "list":
        return _emit(_list_payload())
    if command == "show":
        offer = get(args.plugin_name)
        sys.stdout.write(warning_text(offer))
        return 1 if offer.problem else 0
    if command == "allow":
        offer = get(args.plugin_name)
        text = warning_text(offer)
        if offer.problem:
            sys.stdout.write(text)
            return 1
        if not args.yes:
            sys.stdout.write(text)
            sys.stdout.write(
                "Not allowed. Run the same command with --yes to allow this exact version.\n"
            )
            return 2
        sys.stderr.write(text)
        allow(offer.name, accept=True)
        return _emit(
            {
                "allowed": True,
                "name": offer.name,
                "distribution": offer.distribution,
                "version": offer.version,
                "permissions": list(offer.permissions),
                "note": (
                    "Allowed. The plugin was not imported. "
                    "It runs when you ask for an analysis, a part, or an export."
                ),
            }
        )
    if command == "revoke":
        revoke(args.plugin_name)
        return _emit(
            {
                "revoked": args.plugin_name,
                "note": "The allow was removed. The plugin was not imported.",
            }
        )
    if command in {"run", "insert", "export"}:
        from aicad.document import Document

        document = Document(args.workspace)
        options = _parse_sets(args.sets)
        if command == "run":
            from aicad.honesty import attach_sheet

            report = run_analysis(
                args.plugin_name,
                args.analysis,
                args.part,
                options,
                document=document,
            )
            report = attach_sheet(
                report,
                document.workspace / "renders" / "sheets",
                f"plugin_{args.plugin_name}_{args.analysis}",
                root=document.workspace,
            )
            return _emit(report)
        if command == "insert":
            info = insert_part(
                args.plugin_name,
                args.kind,
                args.name,
                options,
                document=document,
            )
            payload = info.to_dict()
            payload["plugin"] = args.plugin_name
            payload["kind"] = args.kind
            payload["note"] = (
                "The part was built by the plugin and saved as a script. "
                "Opening this design later does not load the plugin."
            )
            return _emit(payload)
        return _emit(
            export_with(
                args.plugin_name,
                args.export_format,
                args.part,
                args.out,
                options,
                document=document,
            )
        )
    raise PluginError(f"Unknown plugin command {command!r}.")


def _list_payload() -> dict[str, object]:
    return {
        "plugins": [offer.to_dict(allowed=_is_allowed(offer)) for offer in discover()],
        "note": (
            "Listing plugins does not import them. "
            "A third-party plugin runs only after you allow it, and opening a design does not load one."
        ),
    }


def _emit(payload: dict) -> int:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _parse_sets(pairs: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise PluginError(f"Option {pair!r} must look like key=value.")
        key, value = pair.split("=", 1)
        if not key:
            raise PluginError(f"Option {pair!r} needs a name before the equals sign.")
        parsed[key] = value
    return parsed


def _options_dict(options: dict | None) -> dict:
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise PluginError("Plugin options must be a dict.")
    return dict(options)


def _during(permissions: frozenset[str], workspace: Path, fn):
    if "project_files" not in permissions:
        return fn()
    token = _CALL_WORKSPACE.set(Path(workspace))
    try:
        return fn()
    finally:
        _CALL_WORKSPACE.reset(token)


def _part_view(document, name: str, *, permissions: frozenset[str], step: bool) -> tuple[PartView, Path | None]:
    measurement = document.measure(name)
    center = measurement.center_of_mass_mm
    size = measurement.bounding_box.size
    temp_dir = None
    step_file = None
    if step:
        from aicad.paths import guest_path, open_held_directory

        temp_dir = open_held_directory("aicad-plugin-")
        step_name = f"{name}.step"
        if "/" in step_name or "\\" in step_name or step_name in {".", ".."}:
            step_name = "part.step"
        document.export(name, Path(temp_dir) / step_name, "step")
        step_file = guest_path(temp_dir, step_name)
    workspace = Path(document.workspace) if "project_files" in permissions else None
    view = PartView(
        name=name,
        volume_mm3=measurement.volume_mm3,
        area_mm2=measurement.area_mm2,
        center_mm=(center.x, center.y, center.z),
        size_mm=(size.x, size.y, size.z),
        valid=measurement.valid,
        watertight=measurement.watertight,
        step_file=step_file,
        workspace=workspace,
    )
    return view, temp_dir


def _cleanup(temp_dir: Path | None) -> None:
    if temp_dir is not None:
        from aicad.paths import close_held_directory

        close_held_directory(temp_dir)


def _lookup(store: dict[str, _Registration], plugin: str, label: str, name: str) -> _Registration:
    found = store.get(name)
    if found is None:
        known = ", ".join(store) or "(none)"
        raise PluginError(f"Plugin {plugin!r} has no {label} {name!r}. It registered: {known}.")
    return found


def _offer_from_entry_point(entry) -> PluginOffer:
    module_name, separator, _attr = (entry.value or "").partition(":")
    if not separator or not module_name or not _attr or "." in module_name:
        return _broken(
            entry,
            "Version 1 entry points must look like hello_plugin:register, "
            "a top-level module. Nothing was imported.",
        )
    try:
        text = _read_manifest_text(entry)
    except PluginError as exc:
        return _broken(entry, str(exc))
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        return _broken(entry, f"aicad.plugin.json is not valid JSON ({exc}). The plugin was not imported.")
    if not isinstance(manifest, dict):
        return _broken(entry, "aicad.plugin.json must be an object. The plugin was not imported.")
    problem, fields = _validate_manifest(manifest, entry.name)
    distribution, version = _distribution_identity(entry)
    if problem is not None or fields is None:
        return _broken(entry, problem or "This plugin's declaration could not be read.")
    stamp = _stamp(entry.name, entry.value, distribution, version, fields)
    return PluginOffer(
        name=entry.name,
        distribution=distribution,
        version=version,
        publisher=fields["publisher"],
        summary=fields["summary"],
        permissions=fields["permissions"],
        provides=fields["provides"],
        third_party=not _is_core_distribution(getattr(entry, "dist", None)),
        stamp=stamp,
        entry=entry.value,
        problem=None,
    )


def _broken(entry, problem: str) -> PluginOffer:
    distribution, version = _distribution_identity(entry)
    return PluginOffer(
        name=getattr(entry, "name", "") or "unknown",
        distribution=distribution,
        version=version,
        publisher="",
        summary="",
        permissions=(),
        provides=(),
        third_party=not _is_core_distribution(getattr(entry, "dist", None)),
        stamp="",
        entry=getattr(entry, "value", "") or "",
        problem=problem,
    )


def _core_metadata_dirs() -> frozenset[Path]:
    """Metadata directories that belong to this copy of aicad.

    They sit next to this file. A distribution is recognized by that
    location, not by its package name.
    """
    package_dir = Path(__file__).resolve().parent
    parent = package_dir.parent
    version = getattr(sys.modules.get("aicad"), "__version__", None)
    names = ["aicad.egg-info", "aicad.dist-info"]
    if isinstance(version, str) and version and all(part not in version for part in ("/", "\\", "..")):
        names.append(f"aicad-{version}.dist-info")
        names.append(f"aicad-{version}.egg-info")
    found: list[Path] = []
    for name in names:
        candidate = parent / name
        if candidate.is_dir():
            found.append(candidate.resolve())
    return frozenset(found)


def _is_core_distribution(dist) -> bool:
    """True when ``dist`` is the metadata for this install."""
    raw = getattr(dist, "_path", None)
    if raw is None:
        return False
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return False
    return resolved in _core_metadata_dirs()


def _group_entries(dist) -> tuple[list, PluginOffer | None]:
    """Entry points in this group for one distribution.

    A file that cannot be parsed skips that distribution and returns a
    warning. It does not stop the scan of the others.
    """
    try:
        text = dist.read_text("entry_points.txt")
    except Exception:
        return [], None
    if not text:
        return [], None
    try:
        parsed = list(importlib.metadata.EntryPoints._from_text_for(text, dist))
    except Exception:
        if ENTRY_GROUP not in text:
            return [], None
        return [], _unreadable_distribution(dist)
    return [item for item in parsed if item.group == ENTRY_GROUP], None


def _unreadable_distribution(dist) -> PluginOffer:
    distribution, version = _dist_name_version(dist)
    return PluginOffer(
        name=distribution or "unknown",
        distribution=distribution,
        version=version,
        publisher="",
        summary="",
        permissions=(),
        provides=(),
        third_party=not _is_core_distribution(dist),
        stamp="",
        entry="",
        problem=(
            "This package's entry points could not be read, so it was skipped. "
            "Nothing was imported."
        ),
    )


def _dist_name_version(dist) -> tuple[str, str]:
    try:
        name = _normalize_dist_name(dist.metadata["Name"])
        version = str(dist.version)
    except Exception:
        return "", ""
    return name, version


def _distribution_identity(entry) -> tuple[str, str]:
    dist = getattr(entry, "dist", None)
    if dist is None:
        return "", ""
    return _dist_name_version(dist)


def _distribution_key(entry) -> str:
    dist = getattr(entry, "dist", None)
    if dist is None:
        return f"missing:{getattr(entry, 'name', '')}:{getattr(entry, 'value', '')}"
    path = getattr(dist, "_path", None)
    if path is not None:
        return str(path)
    try:
        return str(dist.locate_file("METADATA"))
    except Exception:
        return f"{getattr(entry, 'name', '')}:{getattr(entry, 'value', '')}"


def _read_manifest_bytes(path: Path) -> str:
    """Read a manifest without following a link at that name."""

    from aicad.paths import read_nofollow_bytes

    data = read_nofollow_bytes(
        path,
        maximum=1_000_000,
        label="Plugin manifest",
        error=PluginError,
    )
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise PluginError(f"Could not read {MANIFEST_NAME}. The plugin was not imported.") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_manifest_text(entry) -> str:
    """Read ``aicad.plugin.json`` as data. This does not import the plugin.

    A normal install lists the file in the package RECORD. An editable install
    often does not, so the same file is read from beside the module.
    """
    dist = entry.dist
    if dist is not None and dist.files:
        matches = [item for item in dist.files if Path(str(item)).name == MANIFEST_NAME]
        if len(matches) > 1:
            raise PluginError(
                f"The package has {len(matches)} {MANIFEST_NAME} files. "
                "Version 1 allows one, read as data, so the plugin was not imported."
            )
        if len(matches) == 1:
            path = Path(matches[0].locate())
            try:
                return _read_manifest_bytes(path)
            except OSError as exc:
                raise PluginError(
                    f"Could not read {MANIFEST_NAME}: {exc}. The plugin was not imported."
                ) from exc
    return _manifest_beside_module(entry)


def _manifest_beside_module(entry) -> str:
    module_name = entry.value.split(":", 1)[0]
    spec = importlib.util.find_spec(module_name)
    origin = None if spec is None else spec.origin
    path = None if not origin else Path(origin).parent / MANIFEST_NAME
    if path is None or not path.is_file():
        raise PluginError(
            f"The package has no {MANIFEST_NAME} next to {module_name}. "
            "aicad will not import a plugin to find out what it wants."
        )
    try:
        return _read_manifest_bytes(path)
    except OSError as exc:
        raise PluginError(f"Could not read {MANIFEST_NAME}: {exc}. The plugin was not imported.") from exc


def _validate_manifest(manifest: dict, entry_name: str) -> tuple[str | None, dict | None]:
    if manifest.get("api") != API_VERSION:
        return (
            f"This plugin asks for API {manifest.get('api')!r}. "
            f"aicad speaks plugin API {API_VERSION}. Nothing was imported.",
            None,
        )
    name = manifest.get("name")
    if name != entry_name:
        return (
            f"aicad.plugin.json name is {name!r}, but the entry point is {entry_name!r}. "
            "Nothing was imported.",
            None,
        )
    if _TOKEN.fullmatch(entry_name or "") is None:
        return (
            f"Plugin name {entry_name!r} must be a short lowercase name, such as volume-note. "
            "Nothing was imported.",
            None,
        )
    publisher = manifest.get("publisher")
    summary = manifest.get("summary")
    if not isinstance(publisher, str) or not publisher.strip():
        return ("aicad.plugin.json needs a publisher, so the warning can say who published the pack.", None)
    if not isinstance(summary, str) or not summary.strip():
        return ("aicad.plugin.json needs a summary, so the warning can say what the pack is.", None)
    if len(publisher) > 200 or len(summary) > 400:
        return ("The publisher or the summary in aicad.plugin.json is too long.", None)
    publisher = _shown(publisher)
    summary = _shown(summary)
    if not publisher:
        return ("aicad.plugin.json needs a publisher, so the warning can say who published the pack.", None)
    if not summary:
        return ("aicad.plugin.json needs a summary, so the warning can say what the pack is.", None)
    permissions = _choice_list(manifest.get("permissions"), PERMISSIONS, "permissions")
    if isinstance(permissions, str):
        return (permissions, None)
    provides = _choice_list(manifest.get("provides"), PROVIDES, "provides")
    if isinstance(provides, str):
        return (provides, None)
    if not provides:
        return ("aicad.plugin.json provides must list analysis, parts, or exporter.", None)
    if "analysis" in provides and "checks" not in permissions:
        return (
            "An analysis needs the 'checks' permission in aicad.plugin.json. Nothing was imported.",
            None,
        )
    return None, {
        "publisher": publisher.strip(),
        "summary": summary.strip(),
        "permissions": permissions,
        "provides": provides,
    }


def _choice_list(value, allowed: tuple[str, ...], field: str) -> tuple[str, ...] | str:
    if not isinstance(value, list):
        return f"aicad.plugin.json field {field!r} must be a list. Nothing was imported."
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return f"Each {field} value must be a string. Nothing was imported."
        if item not in allowed:
            return (
                f"Unknown {field} value {item!r}. Version 1 allows: {', '.join(allowed)}. "
                "Nothing was imported."
            )
        if item in seen:
            return f"The {field} value {item!r} is listed twice. Nothing was imported."
        seen.append(item)
    return tuple(item for item in allowed if item in seen)


def _stamp(name: str, entry: str, distribution: str, version: str, fields: dict) -> str:
    payload = {
        "api": API_VERSION,
        "distribution": distribution,
        "version": version,
        "name": name,
        "entry": entry,
        "publisher": fields["publisher"],
        "summary": fields["summary"],
        "permissions": list(fields["permissions"]),
        "provides": list(fields["provides"]),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_dist_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _require_token(value: str, label: str) -> None:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise PluginError(
            f"{label} {value!r} must be a short lowercase name, such as volume or volume-note."
        )


def _read_consent(path: Path | None) -> dict:
    file = Path(path) if path is not None else consent_file()
    if not file.is_file():
        return {"version": 1, "allowed": {}}
    try:
        from aicad.file_access import read_text

        data = json.loads(read_text(file, maximum=1_000_000))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(
            f"The plugin consent file {file} could not be read ({exc}). No plugin was run."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("allowed", {}), dict):
        raise PluginError(f"The plugin consent file {file} is not a consent record. No plugin was run.")
    data.setdefault("allowed", {})
    return data


def _consent_record(name: str, consent_path: Path | None) -> dict | None:
    record = _read_consent(consent_path).get("allowed", {}).get(name)
    if isinstance(record, dict):
        return record
    return None


def _has_matching_consent(offer: PluginOffer, consent_path: Path | None) -> bool:
    record = _consent_record(offer.name, consent_path)
    if record is None:
        return False
    return (
        record.get("distribution") == offer.distribution
        and record.get("plugin_version") == offer.version
        and record.get("stamp") == offer.stamp
    )


def _is_allowed(offer: PluginOffer) -> bool:
    if offer.problem:
        return False
    if not offer.third_party:
        return True
    return _has_matching_consent(offer, None)


def _write_allow(offer: PluginOffer, consent_path: Path | None) -> None:
    path = Path(consent_path) if consent_path is not None else consent_file()
    data = _read_consent(path)
    data["version"] = 1
    data.setdefault("allowed", {})[offer.name] = {
        "distribution": offer.distribution,
        "plugin_version": offer.version,
        "stamp": offer.stamp,
        "publisher": offer.publisher,
        "permissions": list(offer.permissions),
        "allowed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _atomic_json(path, data)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from aicad.paths import publish_text

    publish_text(path, json.dumps(payload, indent=2) + "\n", root=path.parent)


def _human_permissions(permissions: tuple[str, ...]) -> str:
    if not permissions:
        return "nothing beyond being allowed to run when you ask"
    return "; ".join(_PERMISSION_PHRASES[name] for name in permissions)


def _human_provides(provides: tuple[str, ...]) -> str:
    if not provides:
        return "nothing"
    return ", ".join(_PROVIDES_PHRASES[name] for name in provides)


__all__ = [
    "API_VERSION",
    "ENTRY_GROUP",
    "MANIFEST_NAME",
    "PERMISSIONS",
    "PROVIDES",
    "LoadedPlugin",
    "PartView",
    "PluginConsentError",
    "PluginError",
    "PluginHost",
    "PluginOffer",
    "allow",
    "consent_file",
    "discover",
    "export_with",
    "get",
    "insert_part",
    "load",
    "revoke",
    "run_analysis",
    "warning_text",
]
