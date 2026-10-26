"""Export an assembly as URDF and MJCF.

Meshes stay in millimeters and are scaled by 0.001 in the robot file, so a
simulator sees meters. Mass is kilograms and inertia is about the center of
mass in kg·m². A joint's origin is the pose at value 0. A revolute, prismatic,
or fixed child link frame sits on the incoming pin, so the next joint is the
parent anchor measured in that frame. Gear, linkage, and cylinder relations
are comments: a URDF tree cannot enforce them. A name is escaped so a quote
stays inside its attribute and both files stay one XML document.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from aicad import fea, kernel
from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, finish
from aicad.kinematics import matrix_from_placement

_EFFORT = 10.0
_VELOCITY = 1.0
_TINY_MASS = 1e-4
_TINY_INERTIA = 1e-9
_XML_QUOTES = {'"': "&quot;", "'": "&apos;"}


def export_robot(spec: dict, shapes: dict, out_dir: Path) -> dict:
    """Write ``robot.urdf``, ``robot.xml`` (MJCF), and ``meshes/*.stl``."""
    if not isinstance(spec, dict) or not spec.get("instances"):
        raise GeometryError("A robot export needs an assembly with instances.")
    out_dir = Path(out_dir)
    mesh_dir = _mesh_directory(out_dir)
    links = []
    written: dict[str, str] = {}
    for instance in spec["instances"]:
        part = instance["part"]
        if part not in shapes:
            raise GeometryError(f"Instance {instance['name']!r} needs a shape for part {part!r}.")
        if part not in written:
            filename = f"{_safe(part)}.stl"
            kernel.export_stl_file(shapes[part], mesh_dir / filename)
            written[part] = filename
        links.append(_link(instance, shapes[part], written[part]))
    _apply_child_offsets(spec, links)
    joints = _joints(spec, links)
    name = str(spec["name"])
    from aicad.paths import publish_text

    urdf_path = out_dir / "robot.urdf"
    mjcf_path = out_dir / "robot.xml"
    publish_text(urdf_path, _urdf(name, links, joints, spec), root=out_dir)
    publish_text(mjcf_path, _mjcf(name, links, joints, spec), root=out_dir)
    validation = validate_robot(urdf_path, mjcf_path)
    loaded = validation["mujoco"]["status"] == "loaded" or validation["pybullet"]["status"] == "loaded"
    report = {
        "assembly": name,
        "urdf": str(urdf_path.resolve()),
        "mjcf": str(mjcf_path.resolve()),
        "meshes": [str((mesh_dir / name).resolve()) for name in sorted(set(written.values()))],
        "links": [
            {
                "name": link["name"],
                "part": link["part"],
                "mass_kg": link["mass_kg"],
                "center_m": link["center_m"],
                "inertia_kg_m2": link["inertia"],
            }
            for link in links
        ],
        "joints": [
            {
                "name": joint["name"],
                "type": joint["urdf_type"],
                "parent": joint["parent"],
                "child": joint["child"],
                "axis": joint["axis"],
                "lower": joint["lower"],
                "upper": joint["upper"],
                "limit_assumed": joint["limit_assumed"],
            }
            for joint in joints
            if joint["urdf_type"] != "world"
        ],
        "validation": validation,
        "assumptions": _assumptions(spec),
        "note": _note(name, links, joints, validation, loaded),
    }
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Robot export",
        what="Mass and inertia use the model volume and a handbook density. Joint effort and velocity in the file are placeholders.",
        limits=[
            "Gear, linkage, and cylinder relations are comments. A simulator will not enforce them.",
            "Effort and velocity are not the power estimate.",
            "A missing MuJoCo or PyBullet install still writes the files and says they were not stepped.",
        ],
    )


def _mesh_directory(out_dir: Path) -> Path:
    """A real ``meshes`` folder under ``out_dir``. A link is not followed.

    ``out_dir`` itself may not exist yet. The nearest existing parent is the
    anchor, and missing folders under it are created. A symlink or junction
    in that path is not followed, including one whose target stays inside
    ``out_dir``.
    """
    from aicad.paths import output_dir as make_dir

    anchor = out_dir
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    return make_dir(anchor, out_dir / "meshes", "Robot meshes", GeometryError)


def validate_robot(urdf_path: Path, mjcf_path: Path) -> dict:
    """Load the export in MuJoCo or PyBullet and take a few steps, when either is installed."""
    return {"mujoco": _try_mujoco(mjcf_path), "pybullet": _try_pybullet(urdf_path)}


def _link(instance: dict, shape, mesh_file: str) -> dict:
    material = fea.lookup_material(str(instance.get("material") or "steel"))
    density = float(material["density_g_cm3"])
    props = kernel.solid_inertia(shape)
    volume = float(props["volume_mm3"])
    if volume <= 0:
        raise GeometryError(f"Part {instance['part']!r} has no volume, so it has no mass to export.")
    mass = volume * density * 1e-6
    center = [float(value) / 1000.0 for value in props["center_mm"]]
    scale = density * 1e-12
    inertia = [[float(value) * scale for value in row] for row in props["inertia_mm5"]]
    payload = float(instance.get("payload_kg") or 0.0)
    if payload > 0:
        at = instance.get("payload_at") or [0.0, 0.0, 0.0]
        mass, center, inertia = _with_payload(mass, center, inertia, payload, at)
    inertia = _ensure_positive(inertia)
    return {
        "name": instance["name"],
        "part": instance["part"],
        "mesh": mesh_file,
        "mass_kg": mass,
        "center_m": center,
        "inertia": inertia,
        "placement": matrix_from_placement(instance.get("placement") or {}),
        "offset_m": [0.0, 0.0, 0.0],
    }


def _apply_child_offsets(spec: dict, links: list[dict]) -> None:
    """Put a joint at the anchor and shift the child mesh back into the part frame.

    The link frame of a revolute, prismatic, or fixed child sits on the pin.
    A cylindrical joint keeps the part frame and shifts the prismatic origin instead.
    """
    by_name = {link["name"]: link for link in links}
    for joint in spec["joints"]:
        if joint["type"] == "cylindrical":
            continue
        anchor = [float(value) / 1000.0 for value in joint["child_anchor"]]
        by_name[joint["child"]]["offset_m"] = [-anchor[0], -anchor[1], -anchor[2]]


def _joints(spec: dict, links: list[dict] | None = None) -> list[dict]:
    """Structural joints at value 0, plus a fixed joint from world to each grounded part.

    A child link frame sits on the incoming pin. The next joint's origin is the
    parent anchor in that frame, so a pin away from the part origin does not
    shorten the following link. A grounded part and a parent held by a
    cylindrical joint keep the part frame, and that offset is zero.
    """
    offsets = {link["name"]: link["offset_m"] for link in (links or [])}
    children = {joint["child"] for joint in spec["joints"]}
    rows = []
    for instance in spec["instances"]:
        if instance["name"] in children:
            continue
        xyz, rpy = _origin(matrix_from_placement(instance.get("placement") or {}))
        rows.append(
            {
                "name": f"world_to_{instance['name']}",
                "urdf_type": "fixed",
                "parent": "world",
                "child": instance["name"],
                "xyz": xyz,
                "rpy": rpy,
                "axis": [0.0, 0.0, 1.0],
                "lower": None,
                "upper": None,
                "limit_assumed": False,
                "kind": "world",
            }
        )
    for joint in spec["joints"]:
        rows.extend(_one_joint(joint, offsets.get(joint["parent"], [0.0, 0.0, 0.0])))
    return rows


def _one_joint(joint: dict, parent_offset: list[float] | None = None) -> list[dict]:
    axis = [float(value) for value in joint["axis"]]
    shift = [float(value) for value in (parent_offset or (0.0, 0.0, 0.0))]
    anchor = [float(joint["parent_anchor"][index]) / 1000.0 + shift[index] for index in range(3)]
    child_anchor = [float(value) / 1000.0 for value in joint["child_anchor"]]
    kind = joint["type"]
    if kind == "fixed":
        return [
            _row(joint["name"], "fixed", joint["parent"], joint["child"], anchor, [0.0, 0.0, 0.0], axis, None, None, False, "fixed")
        ]
    if kind == "revolute":
        lower, upper, assumed = _angle_limits(joint)
        return [
            _row(joint["name"], "revolute", joint["parent"], joint["child"], anchor, [0.0, 0.0, 0.0], axis, lower, upper, assumed, "revolute")
        ]
    if kind == "prismatic":
        lower, upper, assumed = _length_limits(joint, -0.2, 0.2)
        return [
            _row(joint["name"], "prismatic", joint["parent"], joint["child"], anchor, [0.0, 0.0, 0.0], axis, lower, upper, assumed, "prismatic")
        ]
    spin = f"{joint['name']}_spin"
    lower, upper, assumed = _angle_limits(joint)
    slide_lower, slide_upper, _slide_assumed = _length_limits({}, -0.2, 0.2)
    slide = [-child_anchor[0], -child_anchor[1], -child_anchor[2]]
    return [
        _row(f"{joint['name']}_revolute", "revolute", joint["parent"], spin, anchor, [0.0, 0.0, 0.0], axis, lower, upper, assumed, "cylindrical"),
        _row(
            f"{joint['name']}_prismatic",
            "prismatic",
            spin,
            joint["child"],
            slide,
            [0.0, 0.0, 0.0],
            axis,
            slide_lower,
            slide_upper,
            True,
            "cylindrical",
        ),
    ]


def _row(name, urdf_type, parent, child, xyz, rpy, axis, lower, upper, assumed, kind) -> dict:
    return {
        "name": name,
        "urdf_type": urdf_type,
        "parent": parent,
        "child": child,
        "xyz": xyz,
        "rpy": rpy,
        "axis": axis,
        "lower": lower,
        "upper": upper,
        "limit_assumed": assumed,
        "kind": kind,
    }


def _urdf(name: str, links: list[dict], joints: list[dict], spec: dict) -> str:
    lines = [
        '<?xml version="1.0"?>',
        f'<robot name="{_xml_attr(name)}">',
        "  <link name=\"world\"/>",
    ]
    spin_links = {joint["child"] for joint in joints if joint["kind"] == "cylindrical" and joint["urdf_type"] == "revolute"}
    for spin in sorted(spin_links):
        lines.append(_inertial_link(spin, _TINY_MASS, [0, 0, 0], _eye_inertia(_TINY_INERTIA), None))
    for link in links:
        lines.append(
            _inertial_link(
                link["name"],
                link["mass_kg"],
                _add_vec(link["center_m"], link["offset_m"]),
                link["inertia"],
                link["mesh"],
                link["offset_m"],
            )
        )
    for joint in joints:
        lines.append(_urdf_joint(joint))
    for relation in spec.get("relations") or []:
        lines.append(f"  <!-- {_xml_comment('relation ' + str(relation.get('type')) + ': ' + _relation_text(relation))} -->")
    lines.append("</robot>")
    lines.append("")
    return "\n".join(lines)


def _inertial_link(name: str, mass: float, center, inertia, mesh: str | None, visual_offset=None) -> str:
    ixx, iyy, izz = inertia[0][0], inertia[1][1], inertia[2][2]
    ixy, ixz, iyz = inertia[0][1], inertia[0][2], inertia[1][2]
    visual = ""
    if mesh:
        offset = _xyz(visual_offset or [0.0, 0.0, 0.0])
        visual = (
            "    <visual>\n"
            f"      <origin xyz=\"{offset}\" rpy=\"0 0 0\"/>\n"
            "      <geometry><mesh filename=\"meshes/"
            + _xml_attr(mesh)
            + "\" scale=\"0.001 0.001 0.001\"/></geometry>\n"
            "    </visual>\n"
            "    <collision>\n"
            f"      <origin xyz=\"{offset}\" rpy=\"0 0 0\"/>\n"
            "      <geometry><mesh filename=\"meshes/"
            + _xml_attr(mesh)
            + "\" scale=\"0.001 0.001 0.001\"/></geometry>\n"
            "    </collision>\n"
        )
    return (
        f"  <link name=\"{_xml_attr(name)}\">\n"
        "    <inertial>\n"
        f"      <origin xyz=\"{_xyz(center)}\" rpy=\"0 0 0\"/>\n"
        f"      <mass value=\"{_num(mass)}\"/>\n"
        f"      <inertia ixx=\"{_num(ixx)}\" ixy=\"{_num(ixy)}\" ixz=\"{_num(ixz)}\" "
        f"iyy=\"{_num(iyy)}\" iyz=\"{_num(iyz)}\" izz=\"{_num(izz)}\"/>\n"
        "    </inertial>\n"
        + visual
        + "  </link>"
    )


def _urdf_joint(joint: dict) -> str:
    limit = ""
    if joint["urdf_type"] in ("revolute", "prismatic"):
        limit = (
            f"    <limit lower=\"{_num(joint['lower'])}\" upper=\"{_num(joint['upper'])}\" "
            f"effort=\"{_num(_EFFORT)}\" velocity=\"{_num(_VELOCITY)}\"/>\n"
        )
    axis = ""
    if joint["urdf_type"] != "fixed":
        axis = f"    <axis xyz=\"{_xyz(joint['axis'])}\"/>\n"
    return (
        f"  <joint name=\"{_xml_attr(joint['name'])}\" type=\"{joint['urdf_type']}\">\n"
        f"    <parent link=\"{_xml_attr(joint['parent'])}\"/>\n"
        f"    <child link=\"{_xml_attr(joint['child'])}\"/>\n"
        f"    <origin xyz=\"{_xyz(joint['xyz'])}\" rpy=\"{_xyz(joint['rpy'])}\"/>\n"
        + axis
        + limit
        + "  </joint>"
    )


def _mjcf(name: str, links: list[dict], joints: list[dict], spec: dict) -> str:
    by_parent: dict[str, list[dict]] = {}
    for joint in joints:
        by_parent.setdefault(joint["parent"], []).append(joint)
    link_by_name = {link["name"]: link for link in links}
    lines = [
        '<?xml version="1.0"?>',
        f'<mujoco model="{_xml_attr(name)}">',
        '  <compiler angle="degree" eulerseq="zyx" meshdir="meshes"/>',
        '  <option gravity="0 0 -9.81" timestep="0.002"/>',
        "  <asset>",
    ]
    seen = set()
    for link in links:
        if link["mesh"] in seen:
            continue
        seen.add(link["mesh"])
        lines.append(
            f'    <mesh name="{_xml_attr(_safe(link["part"]))}" file="{_xml_attr(link["mesh"])}" scale="0.001 0.001 0.001"/>'
        )
    lines.append("  </asset>")
    lines.append("  <worldbody>")
    for joint in by_parent.get("world", []):
        lines.extend(_mjcf_body(joint, by_parent, link_by_name, 2))
    lines.append("  </worldbody>")
    for relation in spec.get("relations") or []:
        lines.append(f"  <!-- {_xml_comment('relation ' + str(relation.get('type')) + ': ' + _relation_text(relation))} -->")
    lines.append("</mujoco>")
    lines.append("")
    return "\n".join(lines)


def _mjcf_body(joint: dict, by_parent: dict, links: dict, indent: int) -> list[str]:
    pad = "  " * indent
    child = joint["child"]
    link = links.get(child)
    pos = _xyz(joint["xyz"])
    # eulerseq zyx consumes yaw, pitch, roll, in degrees. URDF rpy is radians.
    roll, pitch, yaw = (math.degrees(angle) for angle in joint["rpy"])
    euler = _xyz([yaw, pitch, roll])
    lines = [f'{pad}<body name="{_xml_attr(child)}" pos="{pos}" euler="{euler}">']
    if link:
        inertia = link["inertia"]
        full = _xyz(
            [inertia[0][0], inertia[1][1], inertia[2][2], inertia[0][1], inertia[0][2], inertia[1][2]]
        )
        lines.append(
            f'{pad}  <inertial pos="{_xyz(_add_vec(link["center_m"], link["offset_m"]))}" mass="{_num(link["mass_kg"])}" fullinertia="{full}"/>'
        )
        lines.append(
            f'{pad}  <geom type="mesh" mesh="{_xml_attr(_safe(link["part"]))}" pos="{_xyz(link["offset_m"])}"/>'
        )
    else:
        lines.append(
            f'{pad}  <inertial pos="0 0 0" mass="{_num(_TINY_MASS)}" '
            f'diaginertia="{_num(_TINY_INERTIA)} {_num(_TINY_INERTIA)} {_num(_TINY_INERTIA)}"/>'
        )
    if joint["urdf_type"] == "revolute":
        lines.append(
            f'{pad}  <joint name="{_xml_attr(joint["name"])}" type="hinge" axis="{_xyz(joint["axis"])}" '
            f'range="{_num(math.degrees(joint["lower"]))} {_num(math.degrees(joint["upper"]))}" limited="true"/>'
        )
    elif joint["urdf_type"] == "prismatic":
        lines.append(
            f'{pad}  <joint name="{_xml_attr(joint["name"])}" type="slide" axis="{_xyz(joint["axis"])}" '
            f'range="{_num(joint["lower"])} {_num(joint["upper"])}" limited="true"/>'
        )
    for child_joint in by_parent.get(child, []):
        lines.extend(_mjcf_body(child_joint, by_parent, links, indent + 1))
    lines.append(f"{pad}</body>")
    return lines


def _try_mujoco(path: Path) -> dict:
    try:
        staged = _stage_model(path, mjcf=True)
    except GeometryError as exc:
        return {"status": "failed", "note": str(exc)}
    try:
        try:
            import mujoco
        except ImportError:
            return {
                "status": "skipped",
                "note": "mujoco is not installed. pip install 'aicad[robot]' loads this MJCF and steps it.",
            }
        try:
            model = mujoco.MjModel.from_xml_path(str(staged))
            data = mujoco.MjData(model)
            for _ in range(5):
                mujoco.mj_step(model, data)
        except Exception as exc:
            return {"status": "failed", "note": _simulator_note(exc)}
        return {"status": "loaded", "nq": int(model.nq), "nbody": int(model.nbody), "steps": 5}
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)


def _try_pybullet(path: Path) -> dict:
    try:
        staged = _stage_model(path, mjcf=False)
    except GeometryError as exc:
        return {"status": "failed", "note": str(exc)}
    try:
        try:
            import pybullet as bullet
        except ImportError:
            return {
                "status": "skipped",
                "note": "pybullet is not installed. The URDF is still written.",
            }
        client = bullet.connect(bullet.DIRECT)
        try:
            bullet.loadURDF(str(staged), useFixedBase=True, physicsClientId=client)
            for _ in range(5):
                bullet.stepSimulation(physicsClientId=client)
        except Exception as exc:
            return {"status": "failed", "note": _simulator_note(exc)}
        finally:
            bullet.disconnect(client)
        return {"status": "loaded", "steps": 5}
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)


_MAX_MODEL_BYTES = 64 * 1024 * 1024


def _simulator_note(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) > 300:
        text = text[:300]
    return text or "The simulator did not load the robot."


def _model_bytes(root: Path, relative: Path) -> bytes:
    """Read one robot file from ``root``. A link is not followed."""
    from aicad.file_access import read_from_root
    from aicad.paths import _TooLarge

    try:
        return read_from_root(Path(root), Path(relative), _MAX_MODEL_BYTES)
    except _TooLarge as exc:
        raise GeometryError(f"{Path(relative).name} is too large.") from exc
    except OSError as exc:
        raise GeometryError(f"{Path(relative).name} was not opened.") from exc


def _one_component(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
        or "\x00" in name
    ):
        raise GeometryError("A mesh file was not opened.")


def _refuse_external(element: ET.Element) -> None:
    tag = element.tag.rsplit("}", 1)[-1].lower()
    if tag in {"include", "import", "external"}:
        raise GeometryError("A mesh file was not opened.")
    for value in element.attrib.values():
        if not isinstance(value, str):
            continue
        if "://" in value or value.startswith(("/", "\\")) or ".." in value or "\x00" in value:
            raise GeometryError("A mesh file was not opened.")


def _mesh_components(data: bytes, *, mjcf: bool) -> list[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise GeometryError("The robot file was not opened.") from exc
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise GeometryError("The robot file was not opened.")
    try:
        document = ET.fromstring(text)
    except ET.ParseError as exc:
        raise GeometryError("The robot file was not opened.") from exc
    names: list[str] = []
    for element in document.iter():
        _refuse_external(element)
        tag = element.tag.rsplit("}", 1)[-1]
        if mjcf and tag == "compiler" and element.attrib.get("meshdir", "meshes") != "meshes":
            raise GeometryError("A mesh file was not opened.")
        if tag != "mesh":
            continue
        if mjcf:
            component = element.attrib.get("file", "")
            _one_component(component)
            names.append(component)
            continue
        filename = element.attrib.get("filename", "")
        prefix = "meshes/"
        if not filename.startswith(prefix):
            raise GeometryError("A mesh file was not opened.")
        component = filename[len(prefix) :]
        _one_component(component)
        names.append(component)
    return names


def _stage_model(path: Path, *, mjcf: bool) -> Path:
    """Copy the model and its meshes into a private directory.

    The simulator then opens that copy. A link in the export folder is not
    followed, and a mesh name has to be one file under ``meshes``.
    """
    path = Path(path)
    root = path.parent
    data = _model_bytes(root, Path(path.name))
    names = _mesh_components(data, mjcf=mjcf)
    temporary = Path(tempfile.mkdtemp(prefix="aicad-robot-"))
    try:
        (temporary / "meshes").mkdir()
        destination = temporary / path.name
        destination.write_bytes(data)
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            mesh = _model_bytes(root, Path("meshes") / name)
            (temporary / "meshes" / name).write_bytes(mesh)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _with_payload(mass, center, inertia, payload, at_mm):
    point = [float(value) / 1000.0 for value in at_mm]
    total = mass + payload
    combined = [(mass * center[i] + payload * point[i]) / total for i in range(3)]
    shifted = _parallel(inertia, mass, [center[i] - combined[i] for i in range(3)])
    point_inertia = _parallel(_zeros(), payload, [point[i] - combined[i] for i in range(3)])
    return total, combined, _add(shifted, point_inertia)


def _parallel(inertia, mass, offset):
    squared = sum(value * value for value in offset)
    out = [[0.0, 0.0, 0.0] for _ in range(3)]
    for row in range(3):
        for col in range(3):
            extra = mass * ((squared if row == col else 0.0) - offset[row] * offset[col])
            out[row][col] = inertia[row][col] + extra
    return out


def _ensure_positive(inertia):
    out = [list(row) for row in inertia]
    for index in range(3):
        if out[index][index] < 1e-12:
            out[index][index] = 1e-12
    return out


def _zeros():
    return [[0.0, 0.0, 0.0] for _ in range(3)]


def _add(left, right):
    return [[left[row][col] + right[row][col] for col in range(3)] for row in range(3)]


def _add_vec(left, right):
    return [left[index] + right[index] for index in range(3)]


def _eye_inertia(value: float):
    return [[value, 0.0, 0.0], [0.0, value, 0.0], [0.0, 0.0, value]]


def _origin(matrix):
    xyz = [matrix[row][3] / 1000.0 for row in range(3)]
    rotation = [[matrix[row][col] for col in range(3)] for row in range(3)]
    return xyz, list(_rpy(rotation))


def _rpy(rotation):
    """Roll, pitch, yaw for R = Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    pitch = math.asin(max(-1.0, min(1.0, -rotation[2][0])))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2][1], rotation[2][2])
        yaw = math.atan2(rotation[1][0], rotation[0][0])
    else:
        roll = 0.0
        yaw = math.atan2(-rotation[0][1], rotation[1][1])
    return roll, pitch, yaw


def _angle_limits(joint):
    assumed = "min" not in joint or "max" not in joint
    lower = math.radians(float(joint["min"])) if "min" in joint else -math.pi
    upper = math.radians(float(joint["max"])) if "max" in joint else math.pi
    return lower, upper, assumed


def _length_limits(joint, default_lower, default_upper):
    assumed = "min" not in joint or "max" not in joint
    lower = float(joint["min"]) / 1000.0 if "min" in joint else default_lower
    upper = float(joint["max"]) / 1000.0 if "max" in joint else default_upper
    return lower, upper, assumed


def _relation_text(relation: dict) -> str:
    if relation.get("type") == "gear":
        return (
            f"{relation.get('driven')} = {relation.get('ratio')} * {relation.get('driver')} "
            f"+ {relation.get('offset')}. Not enforced in the URDF."
        )
    if relation.get("type") == "cylinder":
        return (
            f"hydraulic cylinder: {relation.get('driver')} sets the barrel {relation.get('driven')} "
            f"and the rod extension {relation.get('stroke')} "
            f"(retracted {relation.get('retracted_mm')} mm). Not enforced in the URDF."
        )
    return (
        f"planar four-bar from {relation.get('driver')} to {relation.get('driven')}. "
        "Not enforced in the URDF."
    )


def _assumptions(spec: dict) -> list[str]:
    lines = [
        "Meshes are millimeters. The robot file scales them by 0.001, so the simulator works in meters.",
        "Mass is kilograms from the part volume and the instance material. Inertia is about the center of mass, in kg·m².",
        "A payload_kg on an instance is a point mass at payload_at, included in that link.",
        "Joint origins are the pose at value 0 and slide 0. The assembly's stored angle is not baked in.",
        "Revolute limits are radians in the URDF and degrees in the MJCF. Prismatic limits are meters. A missing limit becomes ±180° or ±200 mm.",
        "effort 10 and velocity 1 are placeholders. They are not the power estimate.",
        "A cylindrical joint is a revolute plus a prismatic, with a small intermediate link so a simulator accepts the mass.",
        "Gear, linkage, and cylinder relations are XML comments. The exported tree does not enforce them.",
        "Every grounded part is fixed to a world link, so the file has one root.",
    ]
    if spec.get("relations"):
        lines.append(
            "This assembly has gear, linkage, or cylinder relations. A simulator will not move the driven joint for you."
        )
    return lines


def _note(name, links, joints, validation, loaded) -> str:
    moving = [joint for joint in joints if joint["urdf_type"] in ("revolute", "prismatic")]
    check = "A simulator loaded the file and stepped." if loaded else "No simulator is installed, so the file was not stepped."
    if validation["mujoco"]["status"] == "failed" or validation["pybullet"]["status"] == "failed":
        check = "A simulator rejected the file. Read validation."
    return (
        f"Exported {name}: {len(links)} links, {len(moving)} moving joints. {check}"
    )


def _xml_chars(value: object) -> str:
    """Characters XML 1.0 can store. Anything else stops the export."""

    text = str(value)
    for char in text:
        code = ord(char)
        if code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF:
            continue
        raise GeometryError("A robot export name contains a character XML cannot store.")
    return text


def _xml_attr(value: object) -> str:
    """An attribute value. A quote stays inside the attribute."""

    return escape(_xml_chars(value), _XML_QUOTES)


def _xml_comment(value: object) -> str:
    """Comment text. ``--`` cannot appear, and the text cannot end with ``-``."""

    text = _xml_chars(value)
    while "--" in text:
        text = text.replace("--", "- -")
    if text.endswith("-"):
        text += " "
    return text


def _safe(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name))
    return cleaned or "part"


def _num(value) -> str:
    return f"{float(value):.8g}"


def _xyz(values) -> str:
    return " ".join(_num(value) for value in values)


def urdf_root(path: Path) -> ET.Element:
    """The URDF document. The file is read from its directory, and a link is not followed."""
    data = _model_bytes(Path(path).parent, Path(path.name))
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise GeometryError("The robot file was not opened.") from exc
    if "<!doctype" in text.lower() or "<!entity" in text.lower():
        raise GeometryError("The robot file was not opened.")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise GeometryError("The robot file was not opened.") from exc
