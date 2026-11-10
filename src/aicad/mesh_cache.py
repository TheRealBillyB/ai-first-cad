"""Meshes kept on disk for this computer's account, keyed by the exact solid.

Opening a project meshed every part again: on a 38-part finger rig that was
most of the wait before the model showed, and the assembly view tessellated the
same solids again. A mesh is a function of the solid, so it is stored under a
hash of the solid's exact BREP text, without any triangulation, and of the
meshing code and geometry library versions. Opening the project again reads it.

The key is kept on the solid once taken. Meshing a solid changes the flag line
of each shape it shares, such as an unplaced part and the same part placed, so
those lines are left out of the key. A key that changes anyway only misses the
stored mesh; it never finds another solid's.

The folder is ``~/.config/aicad/meshes``, beside this account's other aicad
files, and ``AICAD_MESH_CACHE`` names another. It is not in the project, so a
shared project cannot carry a mesh that shows something its solid is not. The
oldest files are removed past 1 GB. A failure to read or write it only means
the mesh is made again.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from aicad import kernel

_CODE = ("kernel.py", "refs.py", "mesh_cache.py")
_FLAGS = re.compile(rb"(?m)^[01]{7}\r?$")
_LIMIT_BYTES = 1024 * 1024 * 1024
_PRUNE_EVERY = 64
_writes = 0
_writes_lock = threading.Lock()


def folder() -> Path:
    override = os.environ.get("AICAD_MESH_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "aicad" / "meshes"


def viewport_mesh(shape: kernel.Shape) -> dict:
    """``kernel.viewport_mesh``, read from disk when this exact solid was meshed before.

    A placed solid is not meshed or looked up at all. Its mesh is the mesh of
    the solid it was placed from, carried by the same move.
    """
    placed = kernel.moved_from(shape)
    if placed is not None:
        base, rotation, translation = placed
        return kernel.moved_viewport_mesh(_viewport_arrays(base), rotation, translation, shape)
    return _stored_viewport_mesh(shape)


def _viewport_arrays(shape: kernel.Shape) -> kernel.ViewportArrays:
    """The solid's mesh as arrays. Kept on the solid, so a rebuilt solid, a new object, starts without one."""
    kept = getattr(shape, "_viewport_arrays", None)
    if kept is None:
        kept = kernel.viewport_arrays(_stored_viewport_mesh(shape))
        shape._viewport_arrays = kept
    return kept


def _stored_viewport_mesh(shape: kernel.Shape) -> dict:
    key = content_key(shape)
    path = folder() / f"{key}.viewport.json"
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        pass
    mesh = kernel.viewport_mesh(shape)
    _write(path, json.dumps(mesh, separators=(",", ":")).encode("utf-8"))
    return mesh


def tessellate(shape: kernel.Shape) -> kernel.TriangleMesh:
    """``kernel.tessellate``, read from disk when this exact solid was tessellated before."""
    key = content_key(shape)
    path = folder() / f"{key}.tessellation.npz"
    try:
        with np.load(path, allow_pickle=False) as stored:
            counts = stored["edge_counts"]
            points = stored["edge_points"]
            edges = np.split(points, np.cumsum(counts)[:-1]) if len(counts) else []
            return kernel.TriangleMesh(
                vertices=stored["vertices"],
                normals=stored["normals"],
                triangles=stored["triangles"],
                edges=[edge.copy() for edge in edges],
            )
    except (OSError, ValueError, KeyError):
        pass
    mesh = kernel.tessellate(shape)
    buffer = io.BytesIO()
    edge_points = np.concatenate(mesh.edges) if mesh.edges else np.zeros((0, 3))
    np.savez(
        buffer,
        vertices=mesh.vertices,
        normals=mesh.normals,
        triangles=mesh.triangles,
        edge_points=edge_points,
        edge_counts=np.array([len(edge) for edge in mesh.edges], dtype=np.int64),
    )
    _write(path, buffer.getvalue())
    return mesh


def content_key(shape: kernel.Shape) -> str:
    """The solid's exact geometry and the meshing code, as a file name. Kept on the solid."""
    kept = getattr(shape, "_content_key", None)
    if kept is not None:
        return kept
    from OCP.BRepTools import BRepTools
    from OCP.TopTools import TopTools_FormatVersion

    stream = io.BytesIO()
    BRepTools.Write_s(shape.impl.wrapped, stream, False, False, TopTools_FormatVersion.TopTools_FormatVersion_CURRENT)
    digest = hashlib.blake2b(digest_size=20)
    digest.update(_fingerprint().encode("ascii"))
    # Each shape's line of seven flags (free, modified, checked, ...) is bookkeeping,
    # and meshing a solid that shares its faces with this one changes it.
    digest.update(_FLAGS.sub(b"-", stream.getvalue()))
    shape._content_key = digest.hexdigest()
    return shape._content_key


@lru_cache(maxsize=1)
def _fingerprint() -> str:
    """The meshing code and the libraries under it. A different one makes different meshes."""
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
    for module in ("build123d", "OCP", "numpy"):
        for name in sorted(distributions.get(module) or [module]):
            try:
                digest.update(f"{module}:{name}={version(name)}".encode("utf-8"))
            except Exception:
                digest.update(f"{module}:{name}=?".encode("utf-8"))
    return digest.hexdigest()


def _write(path: Path, data: bytes) -> None:
    """Store ``data`` at ``path``. A partly written file is never left under that name."""
    global _writes
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    except OSError:
        return
    with _writes_lock:
        _writes += 1
        due = _writes % _PRUNE_EVERY == 1
    if due:
        _prune(path.parent)


def _prune(where: Path) -> None:
    """Remove the oldest stored meshes once the folder is past its limit."""
    try:
        entries = []
        for entry in os.scandir(where):
            if entry.is_file(follow_symlinks=False) and not entry.name.startswith(".writing-"):
                stat = entry.stat(follow_symlinks=False)
                entries.append((stat.st_mtime, stat.st_size, entry.path))
    except OSError:
        return
    total = sum(size for _mtime, size, _path in entries)
    if total <= _LIMIT_BYTES:
        return
    for _mtime, size, path in sorted(entries):
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        if total <= _LIMIT_BYTES * 0.8:
            return
