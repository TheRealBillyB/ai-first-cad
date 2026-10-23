"""Prefer the installed aicad package over a folder of the same name.

Loaded by absolute path from site-packages during startup. A project file
with the same name is not consulted.
"""

from __future__ import annotations

import sys
from pathlib import Path


class _Finder:
    aicad_install_finder = True

    def __init__(self) -> None:
        self._ready = False
        self._init: Path | None = None

    def find_spec(self, fullname, path, target=None):
        if fullname != "aicad" or path is not None:
            return None
        init = self._installed_init()
        if init is None:
            return None
        from importlib.util import spec_from_file_location

        return spec_from_file_location(
            fullname,
            init,
            submodule_search_locations=[str(init.parent)],
        )

    def _installed_init(self) -> Path | None:
        if self._ready:
            return self._init
        self._ready = True
        self._init = _locate_installed_init()
        return self._init


def install() -> None:
    """Install the finder once. Later calls do nothing."""
    if any(getattr(item, "aicad_install_finder", False) for item in sys.meta_path):
        return
    sys.meta_path.insert(0, _Finder())


def _locate_installed_init() -> Path | None:
    import importlib.metadata as metadata

    try:
        dist = metadata.distribution("aicad")
    except metadata.PackageNotFoundError:
        return None
    direct = Path(str(dist.locate_file("aicad/__init__.py")))
    if direct.is_file():
        return direct
    record = dist.read_text("RECORD") or ""
    for line in record.splitlines():
        relative = line.split(",", 1)[0]
        if not relative.startswith("__editable__") or not relative.endswith(".pth"):
            continue
        pth = _on_path(relative)
        if pth is None:
            continue
        found = _init_from_editable(pth)
        if found is not None:
            return found
    return None


def _on_path(relative: str) -> Path | None:
    for entry in sys.path:
        if not isinstance(entry, str) or not entry:
            continue
        candidate = Path(entry) / relative
        if candidate.is_file():
            return candidate
    return None


def _init_from_editable(pth: Path) -> Path | None:
    try:
        text = pth.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or entry.startswith("import ") or entry.startswith("import\t"):
            continue
        init = Path(entry) / "aicad" / "__init__.py"
        if init.is_file():
            return init
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        module = stripped.split()[1].split(";", 1)[0].strip()
        if not module:
            continue
        found = _mapping_init(pth.with_name(module + ".py"))
        if found is not None:
            return found
    return None


def _mapping_init(source: Path) -> Path | None:
    if not source.is_file():
        return None
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = "MAPPING = "
    start = text.find(marker)
    if start < 0:
        return None
    literal = text[start + len(marker):]
    end = literal.find("\n")
    if end >= 0:
        literal = literal[:end]
    try:
        mapping = _literal(literal)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(mapping, dict):
        return None
    raw = mapping.get("aicad")
    if not isinstance(raw, str) or not raw:
        return None
    init = Path(raw) / "__init__.py"
    if init.is_file():
        return init
    return None


def _literal(literal: str):
    import ast

    return ast.literal_eval(literal)
