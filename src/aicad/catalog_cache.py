"""Built catalog solids, kept on disk for this computer's account.

A ball bearing takes most of a second to build, because each ball is fused to
its races one at a time, and every open of a project built each one again.

A solid is stored in OpenCascade's own BREP format, which is exact, under a
key made of the request and a fingerprint of the catalog code and the geometry
libraries. A change to any of them makes a new key, so an old solid is never
used for a new definition.

The folder is ``~/.config/aicad/catalog``, beside this account's other aicad
files, and ``AICAD_CATALOG_CACHE`` names another. It is not in the project: a
shared project could otherwise carry a file that stands in for a catalog part.
Any failure to read or write the folder is ignored, and the solid is built.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

_CODE = ("library.py", "catalog_build.py", "catalog_data.py", "catalog_cache.py")


def folder() -> Path:
    override = os.environ.get("AICAD_CATALOG_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aicad" / "catalog"


@lru_cache(maxsize=1)
def _fingerprint() -> str:
    """The catalog code and the libraries that build it. A different one is a different solid."""
    digest = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in _CODE:
        digest.update(name.encode("utf-8"))
        digest.update((here / name).read_bytes())
    from importlib.metadata import packages_distributions, version

    try:
        distributions = packages_distributions()
    except Exception:
        distributions = {}
    for module in ("build123d", "bd_warehouse", "OCP"):
        for name in sorted(distributions.get(module) or [module]):
            try:
                digest.update(f"{module}:{name}={version(name)}".encode("utf-8"))
            except Exception:
                digest.update(f"{module}:{name}=?".encode("utf-8"))
    return digest.hexdigest()


def key(kind: str, size: str, length: float | None, fastener_type: str | None) -> str:
    request = json.dumps([kind, size, length, fastener_type, _fingerprint()], sort_keys=True)
    return hashlib.sha256(request.encode("utf-8")).hexdigest()[:40]


def read(name: str):
    """The solid stored under ``name``, or None."""
    path = folder() / f"{name}.brep"
    try:
        if not path.is_file():
            return None
        from build123d import Part
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools
        from OCP.TopoDS import TopoDS_Shape

        shape = TopoDS_Shape()
        if not BRepTools.Read_s(shape, str(path), BRep_Builder()) or shape.IsNull():
            return None
        return Part(shape)
    except Exception:
        return None


def write(name: str, solid) -> None:
    """Store ``solid`` under ``name``. A partly written file is never left under that name."""
    target = folder()
    try:
        from OCP.BRepTools import BRepTools

        target.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{name}-", suffix=".brep", dir=target)
        os.close(handle)
        try:
            if not BRepTools.Write_s(solid.wrapped, temporary):
                return
            os.replace(temporary, target / f"{name}.brep")
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    except Exception:
        return
