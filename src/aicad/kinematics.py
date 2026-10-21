"""Joints, poses, and forward kinematics.

A structural joint (fixed, revolute, prismatic, cylindrical) is an edge in a
tree. Gear and linkage relations write one driven joint from a driver. A
cylinder relation writes two: the barrel angle and the rod extension, from
the pin-to-pin distance of a driver revolute. That keeps a hydraulic cylinder
on its clevises without a spatial loop. A linkage here is a planar four-bar
map, and a gear is a linear map between joint values.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from aicad.errors import GeometryError

_STRUCTURAL = ("fixed", "revolute", "prismatic", "cylindrical")
_RELATIONS = ("gear", "linkage", "cylinder")
_MISSING = object()


def normalize(spec: dict) -> dict:
    """Return a cleaned assembly spec. Joint values are the stored commands."""
    if not isinstance(spec, dict):
        raise GeometryError("An assembly spec is an object.")
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        raise GeometryError("An assembly needs a name.")
    instances = spec.get("instances")
    if not isinstance(instances, list) or not instances:
        raise GeometryError(f"Assembly {name!r} needs at least one instance.")
    seen: set[str] = set()
    cleaned_instances = []
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict) or not instance.get("name") or not instance.get("part"):
            raise GeometryError(f"Instance {index} needs a name and a part.")
        ident = str(instance["name"])
        if ident in seen:
            raise GeometryError(f"Two instances are named {ident!r}.")
        seen.add(ident)
        cleaned_instances.append(
            {
                "name": ident,
                "part": str(instance["part"]),
                "material": str(instance.get("material") or "steel"),
                "color": _color(instance.get("color")),
                "placement": _placement(instance.get("placement")),
                "payload_kg": _optional_float(instance.get("payload_kg"), 0.0),
                "payload_at": _point(instance.get("payload_at"), f"{ident} payload_at", default=None),
            }
        )
    joints = []
    joint_names: set[str] = set()
    children: set[str] = set()
    for index, joint in enumerate(spec.get("joints") or []):
        if not isinstance(joint, dict):
            raise GeometryError(f"Joint {index} must be an object.")
        kind = joint.get("type")
        if kind not in _STRUCTURAL:
            raise GeometryError(
                f"Joint {index} type {kind!r} is unknown. Use {', '.join(_STRUCTURAL)}."
            )
        jname = str(joint.get("name") or f"joint_{index + 1}")
        if jname in joint_names:
            raise GeometryError(f"Two joints are named {jname!r}.")
        parent = str(joint.get("parent") or "")
        child = str(joint.get("child") or "")
        if parent not in seen or child not in seen:
            raise GeometryError(f"Joint {jname!r} must name instances that are in the assembly.")
        if parent == child:
            raise GeometryError(f"Joint {jname!r} cannot connect an instance to itself.")
        if child in children:
            raise GeometryError(f"Instance {child!r} already has a parent joint.")
        children.add(child)
        joint_names.add(jname)
        record = {
            "name": jname,
            "type": kind,
            "parent": parent,
            "child": child,
            "parent_anchor": _point(joint.get("parent_anchor"), f"{jname} parent_anchor"),
            "child_anchor": _point(joint.get("child_anchor"), f"{jname} child_anchor"),
            "axis": _unit(_point(joint.get("axis"), f"{jname} axis", default=[0.0, 0.0, 1.0])),
            "value": _optional_float(joint.get("value"), 0.0),
            "slide": _optional_float(joint.get("slide"), 0.0),
        }
        if joint.get("min") is not None:
            record["min"] = float(joint["min"])
        if joint.get("max") is not None:
            record["max"] = float(joint["max"])
        if "min" in record and "max" in record and record["min"] > record["max"]:
            raise GeometryError(f"Joint {jname!r} has min greater than max.")
        joints.append(record)
    _roots_and_cycles(seen, joints)
    relations = []
    joint_index = {joint["name"]: joint for joint in joints}
    for index, relation in enumerate(spec.get("relations") or []):
        relations.append(_relation(relation, index, joint_index))
    poses = {}
    for pose_name, values in (spec.get("poses") or {}).items():
        if not isinstance(values, dict):
            raise GeometryError(f"Pose {pose_name!r} must map joint names to values.")
        poses[str(pose_name)] = {str(key): float(value) for key, value in values.items()}
    motions = {}
    for motion_name, motion in (spec.get("motions") or {}).items():
        if not isinstance(motion, dict):
            raise GeometryError(f"Motion {motion_name!r} must be an object.")
        motions[str(motion_name)] = copy.deepcopy(motion)
        motions[str(motion_name)]["name"] = str(motion_name)
    ignore = []
    for pair in spec.get("ignore_collisions") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise GeometryError("ignore_collisions entries are [instance, instance].")
        ignore.append([str(pair[0]), str(pair[1])])
    return {
        "name": str(name),
        "instances": cleaned_instances,
        "joints": joints,
        "relations": relations,
        "poses": poses,
        "motions": motions,
        "cables": copy.deepcopy(spec.get("cables") or []),
        "actuators": copy.deepcopy(spec.get("actuators") or []),
        "ignore_collisions": ignore,
    }


def solve(spec: dict, overrides: dict | None = None) -> dict:
    """Forward kinematics. ``overrides`` replace stored joint values before relations run."""
    spec = normalize(spec)
    overrides = overrides or {}
    commanded: dict[str, float] = {}
    slides: dict[str, float] = {}
    for joint in spec["joints"]:
        commanded[joint["name"]] = float(overrides.get(joint["name"], joint["value"]))
        if isinstance(overrides.get(joint["name"]), dict):
            blob = overrides[joint["name"]]
            commanded[joint["name"]] = float(blob.get("value", joint["value"]))
            slides[joint["name"]] = float(blob.get("slide", joint["slide"]))
        else:
            slides[joint["name"]] = float(joint["slide"])
    driven = set()
    for relation in spec["relations"]:
        driven.add(relation["driven"])
        if relation["type"] == "cylinder":
            driven.add(relation["stroke"])
    joint_index = {joint["name"]: joint for joint in spec["joints"]}
    warnings = []
    for _ in range(8):
        for relation in spec["relations"]:
            driver = commanded[relation["driver"]]
            if relation["type"] == "gear":
                commanded[relation["driven"]] = relation["ratio"] * driver + relation["offset"]
            elif relation["type"] == "linkage":
                commanded[relation["driven"]] = _four_bar(relation, driver)
            else:
                angle, extension, offset_mm = _cylinder(relation, joint_index, driver)
                commanded[relation["driven"]] = angle
                commanded[relation["stroke"]] = extension
                if offset_mm > 2.0:
                    note = (
                        f"{relation['name']} is {offset_mm:.1f} mm out of the plane of its pin. "
                        "The clevises do not share that plane, so the barrel angle is the in-plane part only."
                    )
                    if note not in warnings:
                        warnings.append(note)
    limit_notes = []
    limited: dict[str, float] = {}
    for joint in spec["joints"]:
        value = commanded[joint["name"]]
        clamped, which = _clamp(value, joint)
        limited[joint["name"]] = clamped
        if which:
            limit_notes.append(
                f"{joint['name']} commanded {value:.3f} {_unit_name(joint)} and was held at its {which} of {clamped:.3f}."
            )
    warnings.extend(limit_notes)
    worlds = _worlds(spec, limited, slides)
    joint_rows = []
    for joint in spec["joints"]:
        parent = worlds[joint["parent"]]
        origin = _transform_point(parent, joint["parent_anchor"])
        axis = _transform_vector(parent, joint["axis"])
        joint_rows.append(
            {
                "name": joint["name"],
                "type": joint["type"],
                "parent": joint["parent"],
                "child": joint["child"],
                "value": round(limited[joint["name"]], 4),
                "commanded": round(commanded[joint["name"]], 4),
                "slide": round(slides[joint["name"]], 4),
                "unit": _unit_name(joint),
                "min": joint.get("min"),
                "max": joint.get("max"),
                "limit": "ok" if limited[joint["name"]] == commanded[joint["name"]] else "clamped",
                "driven": joint["name"] in driven,
                "origin_mm": [round(component, 4) for component in origin],
                "axis": [round(component, 6) for component in axis],
            }
        )
    relation_rows = []
    for relation in spec["relations"]:
        row = {
            "name": relation["name"],
            "type": relation["type"],
            "driver": relation["driver"],
            "driven": relation["driven"],
            "driver_value": round(limited[relation["driver"]], 4),
            "driven_value": round(limited[relation["driven"]], 4),
            "ratio": relation.get("ratio"),
        }
        if relation["type"] == "cylinder":
            row["stroke"] = relation["stroke"]
            row["stroke_mm"] = round(limited[relation["stroke"]], 4)
            row["retracted_mm"] = relation["retracted_mm"]
        relation_rows.append(row)
    instances = []
    for instance in spec["instances"]:
        matrix = worlds[instance["name"]]
        instances.append(
            {
                "name": instance["name"],
                "part": instance["part"],
                "material": instance["material"],
                "payload_kg": instance["payload_kg"],
                "payload_at": instance["payload_at"],
                "color": instance.get("color"),
                "origin_mm": [round(matrix[row][3], 4) for row in range(3)],
                "matrix": [[round(value, 6) for value in row] for row in matrix],
            }
        )
    return {
        "name": spec["name"],
        "joints": joint_rows,
        "relations": relation_rows,
        "instances": instances,
        "warnings": warnings,
        "ignore_collisions": spec["ignore_collisions"],
    }


def expand_motion(spec: dict, motion: dict) -> list[dict]:
    """Joint-value samples for a sweep or a keyframe sequence."""
    spec = normalize(spec)
    if not isinstance(motion, dict):
        raise GeometryError("A motion is an object with a sweep or keyframes.")
    if motion.get("sweep"):
        sweep = motion["sweep"]
        joint = str(sweep.get("joint") or "")
        if joint not in {item["name"] for item in spec["joints"]}:
            raise GeometryError(f"Sweep joint {joint!r} is not in this assembly.")
        frames = int(sweep.get("frames") or motion.get("samples") or 5)
        if frames < 2:
            raise GeometryError("A sweep needs at least two frames.")
        start = float(sweep["start"])
        end = float(sweep["end"])
        samples = []
        for index in range(frames):
            blend = index / (frames - 1)
            samples.append({joint: start + (end - start) * blend})
        return _timed(samples, motion, frames)
    keyframes = motion.get("keyframes")
    if not isinstance(keyframes, list) or len(keyframes) < 2:
        raise GeometryError("A motion needs a sweep, or at least two keyframes.")
    targets = [_keyframe_values(spec, frame, index) for index, frame in enumerate(keyframes)]
    count = int(motion.get("samples") or len(targets))
    if count < 2:
        raise GeometryError("A motion needs at least two samples.")
    samples = []
    segments = len(targets) - 1
    for index in range(count):
        position = 0.0 if count == 1 else index / (count - 1) * segments
        left = min(int(math.floor(position)), segments - 1)
        blend = position - left
        if index == count - 1:
            left = segments - 1
            blend = 1.0
        samples.append(_blend(targets[left], targets[left + 1], blend))
    return _timed(samples, motion, count)


def apply_pose(spec: dict, pose: str) -> dict:
    spec = normalize(spec)
    if pose not in spec["poses"]:
        known = ", ".join(spec["poses"]) or "(none)"
        raise GeometryError(f"No pose {pose!r}. Poses: {known}.")
    updated = copy.deepcopy(spec)
    values = spec["poses"][pose]
    for joint in updated["joints"]:
        if joint["name"] in values:
            joint["value"] = float(values[joint["name"]])
    return updated


def matrix_from_placement(placement: dict) -> list[list[float]]:
    rotation = placement.get("rotation_deg") or [0.0, 0.0, 0.0]
    translation = placement.get("position") or [0.0, 0.0, 0.0]
    return _matrix(_euler(rotation[0], rotation[1], rotation[2]), translation)


def transform_point(matrix: list[list[float]], point: list[float]) -> list[float]:
    return _transform_point(matrix, point)


def _relation(relation: dict, index: int, joints: dict[str, dict]) -> dict:
    if not isinstance(relation, dict):
        raise GeometryError(f"Relation {index} must be an object.")
    kind = relation.get("type")
    if kind not in _RELATIONS:
        raise GeometryError(
            f"Relation {index} type {kind!r} is unknown. Use gear, linkage, or cylinder."
        )
    name = str(relation.get("name") or f"{kind}_{index + 1}")
    driver = str(relation.get("driver") or "")
    driven = str(relation.get("driven") or "")
    if driver not in joints or driven not in joints:
        raise GeometryError(f"Relation {name!r} must name joints in this assembly.")
    if driver == driven:
        raise GeometryError(f"Relation {name!r} cannot drive itself.")
    record = {"name": name, "type": kind, "driver": driver, "driven": driven}
    if kind == "gear":
        record["ratio"] = float(relation.get("ratio", 1.0))
        record["offset"] = float(relation.get("offset", 0.0))
    elif kind == "linkage":
        for key in ("ground_mm", "crank_mm", "coupler_mm", "rocker_mm"):
            if relation.get(key) is None:
                raise GeometryError(f"Linkage {name!r} needs {key}.")
            record[key] = float(relation[key])
            if record[key] <= 0:
                raise GeometryError(f"Linkage {name!r} {key} must be positive.")
        record["branch"] = str(relation.get("branch") or "open")
        if record["branch"] not in ("open", "crossed"):
            raise GeometryError(f"Linkage {name!r} branch must be open or crossed.")
    else:
        stroke = str(relation.get("stroke") or "")
        if stroke not in joints:
            raise GeometryError(f"Cylinder {name!r} needs a stroke joint that is in this assembly.")
        if stroke in (driver, driven):
            raise GeometryError(f"Cylinder {name!r} uses three different joints: driver, barrel, and stroke.")
        barrel = joints[driven]
        rod = joints[stroke]
        arm = joints[driver]
        if arm["type"] not in ("revolute", "cylindrical"):
            raise GeometryError(f"Cylinder {name!r} drives from a revolute. {driver!r} is {arm['type']}.")
        if barrel["type"] != "revolute":
            raise GeometryError(f"Cylinder {name!r} barrel joint {driven!r} must be revolute.")
        if rod["type"] != "prismatic":
            raise GeometryError(f"Cylinder {name!r} stroke joint {stroke!r} must be prismatic.")
        if arm["parent"] != barrel["parent"]:
            raise GeometryError(
                f"Cylinder {name!r} needs the arm joint and the barrel joint on the same parent instance."
            )
        if rod["parent"] != barrel["child"]:
            raise GeometryError(
                f"Cylinder {name!r} stroke joint must be parented to the barrel instance {barrel['child']!r}."
            )
        retracted = relation.get("retracted_mm")
        if retracted is None or float(retracted) <= 0:
            raise GeometryError(f"Cylinder {name!r} needs retracted_mm, the pin-to-pin length at stroke 0.")
        anchor = relation.get("moving_anchor")
        if anchor is None:
            raise GeometryError(
                f"Cylinder {name!r} needs moving_anchor, the clevis point in the driver child frame."
            )
        if relation.get("bore_axis") is None:
            bore = [1.0, 0.0, 0.0]
        else:
            bore = _unit(_point(relation.get("bore_axis"), f"{name} bore_axis"))
        along = sum(barrel["axis"][i] * bore[i] for i in range(3))
        if abs(along) > 1e-3:
            raise GeometryError(f"Cylinder {name!r} bore_axis must be perpendicular to the barrel pin.")
        record["stroke"] = stroke
        record["retracted_mm"] = float(retracted)
        record["moving_anchor"] = _point(anchor, f"{name} moving_anchor")
        record["bore_axis"] = bore
    return record


def _cylinder(relation: dict, joints: dict[str, dict], driver_degrees: float) -> tuple[float, float, float]:
    """Barrel angle (degrees), rod extension (mm), and the out-of-plane offset (mm).

    The moving clevis is a point on the driver child. The fixed clevis is the
    barrel joint's parent anchor. Both are in the shared parent frame. With
    the barrel pin as the axis, angle 0 points ``bore_axis`` along the barrel
    child. Extension is pin-to-pin length minus ``retracted_mm``.
    """
    driver = joints[relation["driver"]]
    barrel = joints[relation["driven"]]
    rotation = _rotation(driver["axis"], math.radians(driver_degrees))
    local = [
        relation["moving_anchor"][i] - driver["child_anchor"][i]
        for i in range(3)
    ]
    rotated = _matvec(rotation, local)
    point_b = [driver["parent_anchor"][i] + rotated[i] for i in range(3)]
    point_a = barrel["parent_anchor"]
    delta = [point_b[i] - point_a[i] for i in range(3)]
    length = math.sqrt(sum(component * component for component in delta))
    if length < 1e-6:
        raise GeometryError(f"Cylinder {relation['name']!r} has zero pin-to-pin length.")
    bore = relation["bore_axis"]
    axis = barrel["axis"]
    cross = [
        axis[1] * bore[2] - axis[2] * bore[1],
        axis[2] * bore[0] - axis[0] * bore[2],
        axis[0] * bore[1] - axis[1] * bore[0],
    ]
    hat = [component / length for component in delta]
    cosine = sum(hat[i] * bore[i] for i in range(3))
    sine = sum(hat[i] * cross[i] for i in range(3))
    if math.hypot(cosine, sine) < 1e-6:
        raise GeometryError(f"Cylinder {relation['name']!r} lies on its pin axis.")
    angle = math.degrees(math.atan2(sine, cosine))
    extension = length - relation["retracted_mm"]
    offset = abs(sum(delta[i] * axis[i] for i in range(3)))
    return angle, extension, offset


def _four_bar(relation: dict, driver_degrees: float) -> float:
    theta = math.radians(driver_degrees)
    crank = relation["crank_mm"]
    bx = crank * math.cos(theta)
    by = crank * math.sin(theta)
    dx = relation["ground_mm"]
    hits = _circles((bx, by), relation["coupler_mm"], (dx, 0.0), relation["rocker_mm"])
    if not hits:
        raise GeometryError(
            f"Linkage {relation['name']!r} cannot reach driver angle {driver_degrees:.2f}°. "
            "The coupler does not meet the rocker."
        )
    hits.sort(key=lambda point: point[1], reverse=relation["branch"] == "open")
    cx, cy = hits[0]
    return math.degrees(math.atan2(cy - 0.0, cx - dx))


def _circles(c0: tuple[float, float], r0: float, c1: tuple[float, float], r1: float) -> list[tuple[float, float]]:
    dx = c1[0] - c0[0]
    dy = c1[1] - c0[1]
    distance = math.hypot(dx, dy)
    if distance < 1e-9 or distance > r0 + r1 + 1e-6 or distance < abs(r0 - r1) - 1e-6:
        return []
    along = (r0 * r0 - r1 * r1 + distance * distance) / (2 * distance)
    height_sq = r0 * r0 - along * along
    height = math.sqrt(max(0.0, height_sq))
    mx = c0[0] + along * dx / distance
    my = c0[1] + along * dy / distance
    ox = -dy / distance * height
    oy = dx / distance * height
    return [(mx + ox, my + oy), (mx - ox, my - oy)]


def _worlds(spec: dict, values: dict[str, float], slides: dict[str, float]) -> dict[str, list[list[float]]]:
    children = {joint["child"]: joint for joint in spec["joints"]}
    instances = {instance["name"]: instance for instance in spec["instances"]}
    worlds: dict[str, list[list[float]]] = {}

    def visit(name: str, stack: set[str]) -> list[list[float]]:
        if name in worlds:
            return worlds[name]
        if name in stack:
            raise GeometryError(f"Assembly {spec['name']!r} has a cycle through {name!r}.")
        joint = children.get(name)
        if joint is None:
            worlds[name] = matrix_from_placement(instances[name]["placement"])
            return worlds[name]
        parent = visit(joint["parent"], stack | {name})
        local = _joint_matrix(joint, values[joint["name"]], slides[joint["name"]])
        worlds[name] = _multiply(parent, local)
        return worlds[name]

    for instance in spec["instances"]:
        visit(instance["name"], set())
    return worlds


def _joint_matrix(joint: dict, value: float, slide: float) -> list[list[float]]:
    axis = joint["axis"]
    rotation = _eye()
    travel = 0.0
    if joint["type"] in ("revolute", "cylindrical"):
        rotation = _rotation(axis, math.radians(value))
    if joint["type"] == "prismatic":
        travel = value
    if joint["type"] == "cylindrical":
        travel = slide
    anchor_c = joint["child_anchor"]
    rotated_anchor = _matvec(rotation, anchor_c)
    anchor_p = joint["parent_anchor"]
    translation = [
        anchor_p[0] + axis[0] * travel - rotated_anchor[0],
        anchor_p[1] + axis[1] * travel - rotated_anchor[1],
        anchor_p[2] + axis[2] * travel - rotated_anchor[2],
    ]
    return _matrix(rotation, translation)


def _roots_and_cycles(instances: set[str], joints: list[dict]) -> None:
    children = {joint["child"] for joint in joints}
    if not (instances - children):
        raise GeometryError("An assembly needs a grounded instance that no joint drives.")
    graph: dict[str, list[str]] = {name: [] for name in instances}
    for joint in joints:
        graph[joint["parent"]].append(joint["child"])
    seen: set[str] = set()

    def walk(name: str, stack: set[str]) -> None:
        if name in stack:
            raise GeometryError(f"Joint tree cycles through {name!r}.")
        if name in seen:
            return
        seen.add(name)
        for child in graph[name]:
            walk(child, stack | {name})

    for name in instances:
        walk(name, set())


def _keyframe_values(spec: dict, frame: dict, index: int) -> dict[str, float]:
    if not isinstance(frame, dict):
        raise GeometryError(f"Keyframe {index} must be an object.")
    values: dict[str, float] = {}
    if frame.get("pose"):
        pose = str(frame["pose"])
        if pose not in spec["poses"]:
            raise GeometryError(f"Keyframe {index} pose {pose!r} is not defined.")
        values.update(spec["poses"][pose])
    extra = frame.get("joints") or {}
    if not isinstance(extra, dict):
        raise GeometryError(f"Keyframe {index} joints must be an object.")
    for key, value in extra.items():
        values[str(key)] = float(value)
    if not values and "value" in frame and frame.get("joint"):
        values[str(frame["joint"])] = float(frame["value"])
    known = {joint["name"] for joint in spec["joints"]}
    for key, value in frame.items():
        if key in known and key not in values:
            values[str(key)] = float(value)
    unknown = [name for name in values if name not in known]
    if unknown:
        raise GeometryError(f"Keyframe {index} names unknown joints: {', '.join(unknown)}.")
    if not values:
        raise GeometryError(f"Keyframe {index} needs a pose or joint values.")
    return values


def _blend(left: dict[str, float], right: dict[str, float], blend: float) -> dict[str, float]:
    keys = set(left) | set(right)
    mixed = {}
    for key in keys:
        start = left.get(key, right[key])
        end = right.get(key, left[key])
        mixed[key] = start + (end - start) * blend
    return mixed


def _timed(samples: list[dict], motion: dict, count: int) -> list[dict]:
    duration = motion.get("duration_s")
    duration_s = 1.0 if duration is None else float(duration)
    if duration_s <= 0:
        raise GeometryError("duration_s must be positive.")
    frames = []
    for index, values in enumerate(samples):
        time_s = 0.0 if count == 1 else duration_s * index / (count - 1)
        frames.append(
            {
                "index": index,
                "time_s": round(time_s, 4),
                "duration_s": duration_s,
                "joints": {key: round(value, 4) for key, value in values.items()},
                "duration_assumed": duration is None,
            }
        )
    return frames


def _clamp(value: float, joint: dict) -> tuple[float, str | None]:
    if "min" in joint and value < joint["min"]:
        return joint["min"], "min"
    if "max" in joint and value > joint["max"]:
        return joint["max"], "max"
    return value, None


def _unit_name(joint: dict) -> str:
    if joint["type"] == "prismatic":
        return "mm"
    if joint["type"] == "fixed":
        return "fixed"
    if joint["type"] == "cylindrical":
        return "deg"
    return "deg"


def _placement(value: Any) -> dict:
    if value is None:
        return {"position": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0]}
    if not isinstance(value, dict):
        raise GeometryError("A root placement needs position and rotation_deg.")
    return {
        "position": _point(value.get("position"), "placement position"),
        "rotation_deg": _point(value.get("rotation_deg"), "placement rotation_deg"),
    }


def _point(value: Any, label: str, default: Any = _MISSING) -> list[float] | None:
    if value is None:
        if default is _MISSING:
            return [0.0, 0.0, 0.0]
        if default is None:
            return None
        return list(default)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(f"{label} must be three numbers.")
    return [float(component) for component in value]


def _color(value: Any) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError("An instance color is three numbers from 0 to 255.")
    channels = []
    for component in value:
        channel = float(component)
        if channel < 0 or channel > 255:
            raise GeometryError("An instance color channel is from 0 to 255.")
        channels.append(channel)
    return channels


def _optional_float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(component * component for component in vector))
    if length < 1e-12:
        raise GeometryError("A joint axis cannot be zero.")
    return [component / length for component in vector]


def _eye() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _euler(rx: float, ry: float, rz: float) -> list[list[float]]:
    """X, then Y, then Z, matching ``kernel.place``."""
    ax, ay, az = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx_m = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry_m = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz_m = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _matmat(rz_m, _matmat(ry_m, rx_m))


def _rotation(axis: list[float], angle: float) -> list[list[float]]:
    x, y, z = axis
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1.0 - c
    return [
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ]


def _matrix(rotation: list[list[float]], translation: list[float]) -> list[list[float]]:
    return [
        [rotation[0][0], rotation[0][1], rotation[0][2], translation[0]],
        [rotation[1][0], rotation[1][1], rotation[1][2], translation[1]],
        [rotation[2][0], rotation[2][1], rotation[2][2], translation[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    out = [[0.0] * 4 for _ in range(4)]
    for row in range(4):
        for col in range(4):
            out[row][col] = sum(a[row][k] * b[k][col] for k in range(4))
    return out


def _matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    ]


def _matmat(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3)]
        for row in range(3)
    ]


def _transform_point(matrix: list[list[float]], point: list[float]) -> list[float]:
    return [
        matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][2] * point[2] + matrix[0][3],
        matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][2] * point[2] + matrix[1][3],
        matrix[2][0] * point[0] + matrix[2][1] * point[1] + matrix[2][2] * point[2] + matrix[2][3],
    ]


def _transform_vector(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return _matvec(matrix, vector)
