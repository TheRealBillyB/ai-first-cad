"""glTF, OBJ, and COLLADA imports that stay in the file's folder.

The bytes are read with the shared file reader. A buffer, image, or material
library is used only when it is embedded or is one file name in the same
folder. A path that leaves the folder is refused before it is opened. The
solid is built from those bytes. No geometry reader is given the project path.
"""

from __future__ import annotations

import base64
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from aicad.errors import GeometryError
from aicad.kernel import MAX_STEP_BYTES
from aicad.paths import is_reserved_device_name

_MAX_TRIANGLES = 1_000_000
_MAX_FILES = 16
_OUTSIDE = "it refers to a file outside its folder. It was not read."


class _Embedded:
    def __init__(self, data_uri: str | None) -> None:
        self.data_uri = data_uri


class _Sibling:
    def __init__(self, name: str) -> None:
        self.name = name


def import_mesh_file(path: Path) -> object:
    """Read one glTF, OBJ, or COLLADA file into a solid."""
    file = Path(path)
    try:
        data = _read_mesh(file)
    except OSError as exc:
        raise GeometryError(f"Could not read mesh file {file.name}.") from exc
    return _import_mesh_bytes(file, data)


def _import_mesh_bytes(path: Path, data: bytes):
    if len(data) > MAX_STEP_BYTES:
        raise GeometryError(f"Mesh file {path.name} is too large.")
    suffix = path.suffix.lower()
    folder = path if path.is_absolute() else Path.cwd() / path
    folder = folder.parent
    if suffix in {".gltf", ".glb"}:
        vertices, triangles = _gltf_mesh(path.name, suffix, data, folder)
    elif suffix == ".obj":
        vertices, triangles = _obj_mesh(path.name, data, folder)
    elif suffix == ".dae":
        vertices, triangles = _collada_mesh(path.name, data, folder)
    else:
        raise GeometryError(f"Could not read mesh file {path.name}.")
    return _solid(path.name, vertices, triangles)


def _read_mesh(path: Path) -> bytes:
    from aicad.file_access import read_file

    return read_file(path, maximum=MAX_STEP_BYTES)


def _fail(name: str, reason: str) -> GeometryError:
    return GeometryError(f"Could not read mesh file {name}: {reason}")


def _classify(reference: str) -> _Embedded | _Sibling:
    """An embedded reference, or one file name in the same folder."""
    if _trim_escape(reference):
        raise GeometryError(_OUTSIDE)
    text = reference.strip()
    if text.lower().startswith("data:"):
        return _Embedded(text)
    file_part = text
    if file_part.startswith("#"):
        return _Embedded(None)
    if "#" in file_part:
        file_part = file_part.split("#", 1)[0]
        if _trim_escape(file_part):
            raise GeometryError(_OUTSIDE)
        file_part = file_part.strip()
    if file_part == "":
        return _Embedded(None)
    name = unquote(file_part)
    if _leaves_folder(name):
        raise GeometryError(_OUTSIDE)
    return _Sibling(name)


def _trim_escape(reference: str) -> bool:
    """True when a trailing space or dot would make Windows open another name."""
    for line in reference.splitlines() or [reference]:
        if not line.strip():
            continue
        if line.rstrip() != line:
            return True
        if line.strip() != line.strip().rstrip("."):
            return True
    return False


def _leaves_folder(name: str) -> bool:
    if name in {"", ".", ".."}:
        return True
    if any(char in name for char in "/\\:\x00"):
        return True
    if any(ord(char) < 32 for char in name):
        return True
    if name != name.rstrip(" ."):
        return True
    if len(name) > 255:
        return True
    if "://" in name or name.startswith("//"):
        return True
    return is_reserved_device_name(name)


def _load_named(folder: Path, names: list[str], main: str) -> dict[str, bytes]:
    """Read same-folder files. A name that leaves the folder was already refused."""
    loaded: dict[str, bytes] = {}
    queue = list(names)
    while queue:
        name = queue.pop(0)
        if name in loaded:
            continue
        if len(loaded) >= _MAX_FILES:
            raise _fail(main, "it names too many files.")
        try:
            loaded[name] = _read_mesh(folder / name)
        except OSError as exc:
            message = str(exc).lower()
            if "link" in message or "another name" in message:
                raise _fail(main, "a file beside it is a link and was not followed.") from exc
            raise _fail(main, "a file beside it could not be read.") from exc
        for extra in _nested_names(name, loaded[name], main):
            if extra not in loaded:
                queue.append(extra)
    return loaded


def _nested_names(name: str, payload: bytes, main: str) -> list[str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".mtl":
        refs = _mtl_files(_text(payload))
    elif suffix == ".dae":
        refs = _collada_refs(payload, main)
    elif suffix == ".gltf":
        try:
            refs = _gltf_refs(json.loads(_text(payload)))
        except json.JSONDecodeError:
            return []
    else:
        return []
    found = []
    for reference in refs:
        kind = _classify(reference)
        if isinstance(kind, _Sibling):
            found.append(kind.name)
    return found


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _decode_data_uri(uri: str, main: str) -> bytes:
    header, separator, payload = uri.partition(",")
    if separator != "," or not header.lower().startswith("data:"):
        raise _fail(main, "an embedded value could not be read.")
    try:
        if ";base64" in header.lower():
            return base64.b64decode(payload, validate=True)
        return unquote(payload).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise _fail(main, "an embedded value could not be read.") from exc


def _sibling_names(references: list[str], main: str) -> list[str]:
    names = []
    for reference in references:
        try:
            kind = _classify(reference)
        except GeometryError as exc:
            raise _fail(main, str(exc)) from exc
        if isinstance(kind, _Sibling):
            names.append(kind.name)
    return names


def _gltf_mesh(main: str, suffix: str, data: bytes, folder: Path):
    document, binary = _gltf_document(main, suffix, data)
    references = _gltf_refs(document)
    loaded = _load_named(folder, _sibling_names(references, main), main)
    buffers = _gltf_buffers(main, document, binary, loaded)
    return _gltf_triangles(main, document, buffers)


def _gltf_document(main: str, suffix: str, data: bytes) -> tuple[dict, bytes | None]:
    if suffix == ".glb":
        return _glb_chunks(main, data)
    try:
        document = json.loads(_text(data))
    except json.JSONDecodeError as exc:
        raise _fail(main, "the mesh text could not be read.") from exc
    if not isinstance(document, dict):
        raise _fail(main, "the mesh text could not be read.")
    return document, None


def _glb_chunks(main: str, data: bytes) -> tuple[dict, bytes | None]:
    if len(data) < 20 or data[:4] != b"glTF":
        raise _fail(main, "the mesh text could not be read.")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2 or length != len(data):
        raise _fail(main, "the mesh text could not be read.")
    offset = 12
    document = None
    binary = None
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset : offset + chunk_length]
        offset += chunk_length
        if len(chunk) != chunk_length:
            raise _fail(main, "the mesh text could not be read.")
        if chunk_type == 0x4E4F534A:
            try:
                document = json.loads(chunk.decode("utf-8").rstrip(" \x00"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise _fail(main, "the mesh text could not be read.") from exc
        elif chunk_type == 0x004E4942:
            binary = chunk
    if not isinstance(document, dict):
        raise _fail(main, "the mesh text could not be read.")
    return document, binary


def _gltf_refs(document: dict) -> list[str]:
    refs = []
    for key in ("buffers", "images"):
        items = document.get(key) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("uri"), str):
                refs.append(item["uri"])
    return refs


def _gltf_buffers(main: str, document: dict, binary: bytes | None, loaded: dict[str, bytes]) -> list[bytes]:
    buffers = []
    declared = document.get("buffers") or []
    if not isinstance(declared, list):
        raise _fail(main, "the mesh text could not be read.")
    for index, item in enumerate(declared):
        if not isinstance(item, dict):
            raise _fail(main, "the mesh text could not be read.")
        uri = item.get("uri")
        if not isinstance(uri, str):
            if binary is None or index != 0:
                raise _fail(main, "an embedded value could not be read.")
            buffers.append(binary)
            continue
        kind = _classify(uri)
        if isinstance(kind, _Embedded):
            if kind.data_uri is None:
                raise _fail(main, "an embedded value could not be read.")
            buffers.append(_decode_data_uri(kind.data_uri, main))
        else:
            payload = loaded.get(kind.name)
            if payload is None:
                raise _fail(main, "a file beside it could not be read.")
            buffers.append(payload)
    return buffers


def _gltf_triangles(main: str, document: dict, buffers: list[bytes]):
    views = document.get("bufferViews") or []
    accessors = document.get("accessors") or []
    meshes = document.get("meshes") or []
    if not isinstance(views, list) or not isinstance(accessors, list) or not isinstance(meshes, list):
        raise _fail(main, "the mesh text could not be read.")
    positions = []
    indices = []
    base = 0
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            mode = primitive.get("mode", 4)
            if mode != 4:
                raise _fail(main, "the mesh is not made of triangles.")
            attributes = primitive.get("attributes") or {}
            if not isinstance(attributes, dict) or "POSITION" not in attributes:
                continue
            points = _accessor_values(main, accessors, views, buffers, int(attributes["POSITION"]))
            if points.ndim != 2 or points.shape[1] < 3:
                raise _fail(main, "the mesh text could not be read.")
            points = points[:, :3]
            if "indices" in primitive and primitive["indices"] is not None:
                raw = _accessor_values(main, accessors, views, buffers, int(primitive["indices"])).reshape(-1)
                tris = raw.astype(np.int64).reshape(-1, 3) + base
            else:
                count = points.shape[0]
                if count % 3 != 0:
                    raise _fail(main, "the mesh is not made of triangles.")
                tris = np.arange(base, base + count, dtype=np.int64).reshape(-1, 3)
            positions.append(points)
            indices.append(tris)
            base += points.shape[0]
    if not positions:
        raise _fail(main, "the mesh has no triangles.")
    vertices = np.vstack(positions)
    triangles = np.vstack(indices)
    _limit_triangles(main, triangles)
    return vertices, triangles


_COMPONENT = {
    5120: ("<i1", 1),
    5121: ("<u1", 1),
    5122: ("<i2", 2),
    5123: ("<u2", 2),
    5125: ("<u4", 4),
    5126: ("<f4", 4),
}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _accessor_values(main: str, accessors: list, views: list, buffers: list[bytes], index: int) -> np.ndarray:
    if index < 0 or index >= len(accessors) or not isinstance(accessors[index], dict):
        raise _fail(main, "the mesh text could not be read.")
    accessor = accessors[index]
    if "bufferView" not in accessor:
        raise _fail(main, "the mesh text could not be read.")
    view_index = int(accessor["bufferView"])
    if view_index < 0 or view_index >= len(views) or not isinstance(views[view_index], dict):
        raise _fail(main, "the mesh text could not be read.")
    view = views[view_index]
    buffer_index = int(view.get("buffer", -1))
    if buffer_index < 0 or buffer_index >= len(buffers):
        raise _fail(main, "the mesh text could not be read.")
    component = _COMPONENT.get(int(accessor.get("componentType", 0)))
    width = _WIDTH.get(str(accessor.get("type", "")))
    if component is None or width is None:
        raise _fail(main, "the mesh text could not be read.")
    count = int(accessor.get("count", 0))
    if count < 0 or count > _MAX_TRIANGLES * 4:
        raise _fail(main, "the mesh has too many triangles.")
    code, size = component
    packed = width * size
    stride = int(view.get("byteStride") or accessor.get("byteStride") or packed)
    if stride < packed:
        raise _fail(main, "the mesh text could not be read.")
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    if stride == packed:
        need = count * packed
        blob = _slice_buffer(main, buffers[buffer_index], start, need)
        values = np.frombuffer(blob, dtype=np.dtype(code), count=count * width)
    else:
        values = np.empty(count * width, dtype=np.dtype(code))
        for item in range(count):
            blob = _slice_buffer(main, buffers[buffer_index], start + item * stride, packed)
            values[item * width : (item + 1) * width] = np.frombuffer(blob, dtype=np.dtype(code), count=width)
    return np.array(values, dtype=np.float64).reshape(count, width)


def _slice_buffer(main: str, buffer: bytes, start: int, need: int) -> bytes:
    if start < 0 or need < 0 or start + need > len(buffer):
        raise _fail(main, "the mesh text could not be read.")
    return bytes(buffer[start : start + need])


def _obj_mesh(main: str, data: bytes, folder: Path):
    text = _text(data)
    libraries = []
    for line in text.splitlines():
        tokens = _obj_tokens(line)
        if tokens and tokens[0] == "mtllib":
            libraries.extend(tokens[1:])
    loaded = _load_named(folder, _sibling_names(libraries, main), main)
    images = []
    for name, payload in loaded.items():
        if Path(name).suffix.lower() == ".mtl":
            images.extend(_mtl_files(_text(payload)))
    _load_named(folder, _sibling_names(images, main), main)
    return _obj_triangles(main, text)


def _obj_tokens(line: str) -> list[str]:
    comment = line.find("#")
    if comment >= 0:
        line = line[:comment]
    return line.split()


def _mtl_files(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        tokens = _obj_tokens(line)
        if not tokens:
            continue
        command = tokens[0]
        if not (command.startswith("map_") or command in {"bump", "disp", "decal", "refl"}):
            continue
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            try:
                if float(token):
                    continue
            except ValueError:
                pass
            else:
                continue
            found.append(token)
    return found


def _obj_triangles(main: str, text: str):
    vertices = []
    triangles = []
    for line in text.splitlines():
        tokens = _obj_tokens(line)
        if not tokens:
            continue
        if tokens[0] == "v" and len(tokens) >= 4:
            try:
                vertices.append([float(tokens[1]), float(tokens[2]), float(tokens[3])])
            except ValueError as exc:
                raise _fail(main, "the mesh text could not be read.") from exc
        elif tokens[0] == "f" and len(tokens) >= 4:
            corners = []
            for token in tokens[1:]:
                index = token.split("/")[0]
                if not index:
                    raise _fail(main, "the mesh text could not be read.")
                try:
                    number = int(index)
                except ValueError as exc:
                    raise _fail(main, "the mesh text could not be read.") from exc
                if number < 0:
                    number = len(vertices) + number + 1
                if number < 1 or number > len(vertices):
                    raise _fail(main, "the mesh text could not be read.")
                corners.append(number - 1)
            for third in range(2, len(corners)):
                triangles.append((corners[0], corners[third - 1], corners[third]))
    if not vertices or not triangles:
        raise _fail(main, "the mesh has no triangles.")
    _limit_triangles(main, triangles)
    return np.asarray(vertices, dtype=np.float64), np.asarray(triangles, dtype=np.int64)


def _collada_mesh(main: str, data: bytes, folder: Path):
    references = _collada_refs(data, main)
    _load_named(folder, _sibling_names(references, main), main)
    try:
        root = _collada_root(data, main)
    except GeometryError:
        raise
    return _collada_triangles(main, root)


def _collada_refs(data: bytes, main: str) -> list[str]:
    root = _collada_root(data, main)
    refs = []
    for element in root.iter():
        for key, value in element.attrib.items():
            if _xml_name(key) in {"url", "uri", "source", "target"} and value.strip():
                refs.append(value)
        if _xml_name(element.tag) == "init_from" and (element.text or "").strip():
            refs.append(element.text)
    return refs


def _collada_root(data: bytes, main: str):
    text = _text(data)
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise _fail(main, _OUTSIDE)
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise _fail(main, "the mesh text could not be read.") from exc


def _xml_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _collada_triangles(main: str, root):
    sources: dict[str, tuple[list[float], int]] = {}
    for element in root.iter():
        if _xml_name(element.tag) != "source":
            continue
        source_id = element.attrib.get("id")
        if not source_id:
            continue
        floats: list[float] = []
        stride = 3
        for child in element.iter():
            if _xml_name(child.tag) == "float_array" and child.text:
                try:
                    floats = [float(item) for item in child.text.split()]
                except ValueError as exc:
                    raise _fail(main, "the mesh text could not be read.") from exc
            elif _xml_name(child.tag) == "accessor":
                stride = int(child.attrib.get("stride") or stride)
        sources[source_id] = (floats, stride)
    vertices_id: dict[str, str] = {}
    for element in root.iter():
        if _xml_name(element.tag) != "vertices":
            continue
        ident = element.attrib.get("id")
        for child in list(element):
            if _xml_name(child.tag) == "input" and child.attrib.get("semantic") == "POSITION":
                source = child.attrib.get("source", "")
                if ident and source.startswith("#"):
                    vertices_id[ident] = source[1:]
    positions = []
    triangles = []
    for element in root.iter():
        if _xml_name(element.tag) != "triangles":
            continue
        inputs = []
        corner_text = ""
        for child in list(element):
            if _xml_name(child.tag) == "input":
                source = child.attrib.get("source", "")
                inputs.append((child.attrib.get("semantic", ""), source[1:] if source.startswith("#") else source, int(child.attrib.get("offset") or 0)))
            elif _xml_name(child.tag) == "p" and child.text:
                corner_text = child.text
        position_input = next((item for item in inputs if item[0] in {"VERTEX", "POSITION"}), None)
        if position_input is None or not corner_text:
            continue
        semantic, source_id, offset = position_input
        if semantic == "VERTEX":
            source_id = vertices_id.get(source_id, "")
        if source_id not in sources:
            raise _fail(main, "the mesh text could not be read.")
        floats, stride = sources[source_id]
        if stride < 3 or len(floats) < stride:
            raise _fail(main, "the mesh text could not be read.")
        width = max(item[2] for item in inputs) + 1
        try:
            raw = [int(item) for item in corner_text.split()]
        except ValueError as exc:
            raise _fail(main, "the mesh text could not be read.") from exc
        if width < 1 or len(raw) % width != 0:
            raise _fail(main, "the mesh text could not be read.")
        base = len(positions)
        count = len(floats) // stride
        for index in range(count):
            start = index * stride
            positions.append(floats[start : start + 3])
        chosen = raw[offset::width]
        if len(chosen) % 3 != 0:
            raise _fail(main, "the mesh is not made of triangles.")
        for index in range(0, len(chosen), 3):
            corners = chosen[index : index + 3]
            if any(corner < 0 or corner >= count for corner in corners):
                raise _fail(main, "the mesh text could not be read.")
            triangles.append((base + corners[0], base + corners[1], base + corners[2]))
    if not positions or not triangles:
        raise _fail(main, "the mesh has no triangles.")
    _limit_triangles(main, triangles)
    return np.asarray(positions, dtype=np.float64), np.asarray(triangles, dtype=np.int64)


def _limit_triangles(main: str, triangles) -> None:
    if len(triangles) > _MAX_TRIANGLES:
        raise _fail(main, f"the mesh has too many triangles. The limit is {_MAX_TRIANGLES}.")


def _solid(main: str, vertices: np.ndarray, triangles: np.ndarray):
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_MakeSolid,
        BRepBuilderAPI_Sewing,
    )
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt
    from build123d.topology import Shape as BuildShape

    from aicad import kernel

    if len(triangles) == 0:
        raise _fail(main, "the mesh has no triangles.")
    sewing = BRepBuilderAPI_Sewing(1e-6)
    for triangle in triangles:
        corners = []
        for index in triangle:
            if index < 0 or index >= len(vertices):
                raise _fail(main, "the mesh text could not be read.")
            point = vertices[int(index)]
            corners.append(gp_Pnt(float(point[0]), float(point[1]), float(point[2])))
        polygon = BRepBuilderAPI_MakePolygon(corners[0], corners[1], corners[2], True)
        if not polygon.IsDone():
            continue
        face = BRepBuilderAPI_MakeFace(polygon.Wire())
        if face.IsDone():
            sewing.Add(face.Face())
    sewing.Perform()
    sewn = sewing.SewedShape()
    if sewn.ShapeType() != TopAbs_SHELL:
        raise _fail(main, "the mesh did not make a closed solid.")
    solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(sewn))
    if not solid.IsDone():
        raise _fail(main, "the mesh did not make a closed solid.")
    try:
        made = kernel._require_solid(BuildShape.cast(solid.Solid()))
    except GeometryError as exc:
        raise _fail(main, "the mesh did not make a closed solid.") from exc
    return kernel.Shape(made)
