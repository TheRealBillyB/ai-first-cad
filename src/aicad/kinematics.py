"""Joints, poses, and forward kinematics.

A structural joint (fixed, revolute, prismatic, cylindrical) is an edge in a
tree. Gear and linkage relations write one driven joint from a driver. A
cylinder relation writes two: the barrel angle and the rod extension, from
the in-plane pin distance of a revolute or cylindrical driver. A cylindrical
driver includes its slide. The barrel joint cannot tilt, so a side offset is
how far the eye misses the clevis and the stroke does not include it. That
keeps a hydraulic cylinder on its clevises without a spatial loop. A linkage
here is a planar four-bar map between revolute or cylindrical joints, and a
gear is a linear map between joint values. One relation drives a joint. A
cycle is refused. Relations run in dependency order, so a long chain reaches
the last joint.

Serial-chain inverse kinematics and a tool path are solved in ``aicad.chain``.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from aicad.errors import GeometryError

_STRUCTURAL = ("fixed", "revolute", "prismatic", "cylindrical")
_RELATIONS = ("gear", "linkage", "cylinder")
_MISSING = object()


def _stored_cables(raw) -> list:
    """Cable and tendon records with names that stay on one line."""

    stored = []
    for index, cable in enumerate(raw or []):
        if not isinstance(cable, dict):
            stored.append(copy.deepcopy(cable))
            continue
        item = copy.deepcopy(cable)
        if item.get("name") not in (None, ""):
            item["name"] = _one_line(item["name"], "A cable name")
        points = item.get("points")
        if isinstance(points, list):
            for point_index, point in enumerate(points):
                if isinstance(point, dict) and point.get("name") not in (None, ""):
                    point["name"] = _one_line(point["name"], "A tendon name")
        stored.append(item)
    return stored


def _one_line(value: object, label: str) -> str:
    from aicad.labels import require_one_line

    return require_one_line(value if isinstance(value, str) else "" if value is None else str(value), label, GeometryError)


def normalize(spec: dict) -> dict:
    """Return a cleaned assembly spec. Joint values are the stored commands."""
    if not isinstance(spec, dict):
        raise GeometryError("An assembly spec is an object.")
    from aicad.models import require_millimeters

    require_millimeters(spec.get("units"), "An assembly")
    raw_name = spec.get("name")
    if not isinstance(raw_name, str):
        raise GeometryError("An assembly needs a name.")
    name = _one_line(raw_name, "An assembly name")
    instances = spec.get("instances")
    if not isinstance(instances, list) or not instances:
        raise GeometryError(f"Assembly {name!r} needs at least one instance.")
    seen: set[str] = set()
    cleaned_instances = []
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict) or not instance.get("name") or not instance.get("part"):
            raise GeometryError(f"Instance {index} needs a name and a part.")
        ident = _one_line(instance["name"], "An instance name")
        if ident in seen:
            raise GeometryError(f"Two instances are named {ident!r}.")
        seen.add(ident)
        cleaned_instances.append(
            {
                "name": ident,
                "part": _one_line(instance["part"], "A part name"),
                "material": _one_line(instance.get("material") or "steel", "A material name"),
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
        jname = _one_line(joint.get("name") or f"joint_{index + 1}", "A joint name")
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
        for side in ("parent", "child"):
            mate_name = joint.get(f"{side}_mate")
            if mate_name in (None, ""):
                continue
            if not isinstance(mate_name, str) or not mate_name.strip():
                raise GeometryError(f"Joint {jname!r} {side}_mate must be a mate name.")
            record[f"{side}_mate"] = _one_line(mate_name, "A mate name")
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
        pose = _one_line(pose_name, "A pose name")
        poses[pose] = {_one_line(key, "A joint name"): float(value) for key, value in values.items()}
    motions = _motions(spec.get("motions"))
    ignore = []
    for pair in spec.get("ignore_collisions") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise GeometryError("ignore_collisions entries are [instance, instance].")
        ignore.append([_one_line(pair[0], "An instance name"), _one_line(pair[1], "An instance name")])
    _ordered_relations(relations)
    studies = _studies(spec.get("studies"), joints, relations)
    cleaned = {
        "name": str(name),
        "instances": cleaned_instances,
        "joints": joints,
        "relations": relations,
        "poses": poses,
        "motions": motions,
        "studies": studies,
        "cables": _stored_cables(spec.get("cables")),
        "actuators": copy.deepcopy(spec.get("actuators") or []),
        "ignore_collisions": ignore,
    }
    gear_train = _gear_train(spec, cleaned_instances, joints)
    if gear_train is not None:
        cleaned["gear_train"] = gear_train
    return cleaned


def _gear_train(spec: dict, instances: list[dict], joints: list[dict]):
    """Shafts and meshes, stored only when the assembly has a train."""
    if spec.get("gear_train") is None:
        return None
    from aicad.gears.train import normalize_gear_train

    return normalize_gear_train(spec["gear_train"], joints, [item["name"] for item in instances])


def _motions(raw) -> dict:
    """A name-to-motion mapping, or a list of motions that each have a name.

    Joints, cables, and actuators are lists of named objects. A motion list
    is the same shape. Anything else is an error, not an attribute failure.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        pairs = []
        for index, motion in enumerate(raw):
            if not isinstance(motion, dict):
                raise GeometryError(
                    "motions must map a name to a motion, or be a list of motions that each have a name."
                )
            name = motion.get("name")
            if not isinstance(name, str) or not name.strip():
                raise GeometryError(
                    f"Motion {index + 1} needs a name. "
                    "motions must map a name to a motion, or be a list of motions that each have a name."
                )
            pairs.append((name, motion))
    else:
        raise GeometryError(
            "motions must map a name to a motion, or be a list of motions that each have a name."
        )
    motions = {}
    for motion_name, motion in pairs:
        if not isinstance(motion, dict):
            raise GeometryError(f"Motion {motion_name!r} must be an object.")
        ident = _one_line(motion_name, "A motion name")
        if ident in motions:
            raise GeometryError(f"Two motions are named {ident!r}.")
        stored = copy.deepcopy(motion)
        stored["name"] = ident
        motions[ident] = stored
    return motions


def _studies(raw, joints: list[dict], relations: list[dict]) -> dict:
    """Motion studies stored on the assembly. The schema lives in motion_study."""
    if not raw:
        return {}
    from aicad.motion_study import normalize_studies

    return normalize_studies(raw, joints, relations)


def solve(spec: dict, overrides: dict | None = None) -> dict:
    """Forward kinematics. ``overrides`` replace stored joint values before relations run.

    A number overrides the joint angle or the prismatic travel. A cylindrical
    joint also accepts ``{"value": degrees, "slide": millimeters}``. A joint
    stop is applied before a gear, linkage, or cylinder reads that joint.
    """
    spec = normalize(spec)
    overrides = overrides or {}
    commanded: dict[str, float] = {}
    slides: dict[str, float] = {}
    for joint in spec["joints"]:
        raw = overrides.get(joint["name"], joint["value"])
        # A cylindrical joint carries an angle and a slide. Check the shape
        # before float(): float(dict) raises TypeError and the slide is ignored.
        if isinstance(raw, dict):
            try:
                commanded[joint["name"]] = float(raw.get("value", joint["value"]))
                slides[joint["name"]] = float(raw.get("slide", joint["slide"]))
            except (TypeError, ValueError) as exc:
                raise GeometryError(
                    f"Override for {joint['name']} needs a numeric value and slide."
                ) from exc
        else:
            try:
                commanded[joint["name"]] = float(raw)
            except (TypeError, ValueError) as exc:
                raise GeometryError(f"Override for {joint['name']} must be a number.") from exc
            slides[joint["name"]] = float(joint["slide"])
    driven = set()
    for relation in spec["relations"]:
        driven.add(relation["driven"])
        if relation["type"] == "cylinder":
            driven.add(relation["stroke"])
    joint_index = {joint["name"]: joint for joint in spec["joints"]}
    # Stops apply to the driver before a relation runs. Using the unclamped
    # command puts the driven joint at ratio times a pose the driver never reaches.
    active = dict(commanded)
    for joint in spec["joints"]:
        if joint["name"] in driven:
            continue
        active[joint["name"]], _which = _clamp(commanded[joint["name"]], joint)
    warnings = []
    for relation in _ordered_relations(spec["relations"]):
        driver = active[relation["driver"]]
        if relation["type"] == "gear":
            raw_value = relation["ratio"] * driver + relation["offset"]
            commanded[relation["driven"]] = raw_value
            active[relation["driven"]], _which = _clamp(raw_value, joint_index[relation["driven"]])
        elif relation["type"] == "linkage":
            raw_value = _four_bar(relation, driver)
            commanded[relation["driven"]] = raw_value
            active[relation["driven"]], _which = _clamp(raw_value, joint_index[relation["driven"]])
        else:
            angle, extension, offset_mm = _cylinder(
                relation, joint_index, driver, slides[relation["driver"]]
            )
            commanded[relation["driven"]] = angle
            active[relation["driven"]], _which = _clamp(angle, joint_index[relation["driven"]])
            commanded[relation["stroke"]] = extension
            active[relation["stroke"]], _which = _clamp(extension, joint_index[relation["stroke"]])
            if offset_mm > 2.0:
                note = (
                    f"{relation['name']} is {offset_mm:.1f} mm out of the plane of its pin. "
                    "The stroke is the in-plane length. The eye misses the clevis by that offset."
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
                # Exact. Rounding the rotation shears the solid, and the boolean then misses a real overlap.
                "matrix": [list(row) for row in matrix],
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
        joint_row = next(item for item in spec["joints"] if item["name"] == joint)
        start = _sweep_bound(sweep, joint_row, "start", "min")
        end = _sweep_bound(sweep, joint_row, "end", "max")
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


def _sweep_bound(sweep: dict, joint: dict, key: str, stop: str) -> float:
    if key in sweep and sweep[key] is not None:
        try:
            return float(sweep[key])
        except (TypeError, ValueError) as exc:
            raise GeometryError(f"Sweep {key} must be a number.") from exc
    if stop in joint:
        return float(joint[stop])
    raise GeometryError(f"Sweep joint {joint['name']!r} needs {key}, or a {stop} stop.")


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


def _ordered_relations(relations: list[dict]) -> list[dict]:
    """Drivers before the joints they drive. A joint has one relation, and a cycle is refused."""
    produced: dict[str, int] = {}
    for index, relation in enumerate(relations):
        written = [relation["driven"]]
        if relation["type"] == "cylinder":
            written.append(relation["stroke"])
        for joint_name in written:
            previous = produced.get(joint_name)
            if previous is not None:
                other = relations[previous]["name"]
                raise GeometryError(
                    f"Relation {relation['name']!r} drives {joint_name!r}, which {other!r} already drives."
                )
            produced[joint_name] = index
    followers: dict[int, list[int]] = {index: [] for index in range(len(relations))}
    incoming = [0] * len(relations)
    for index, relation in enumerate(relations):
        source = produced.get(relation["driver"])
        if source is None:
            continue
        followers[source].append(index)
        incoming[index] += 1
    queue = [index for index, count in enumerate(incoming) if count == 0]
    ordered: list[int] = []
    while queue:
        index = queue.pop(0)
        ordered.append(index)
        for follower in followers[index]:
            incoming[follower] -= 1
            if incoming[follower] == 0:
                queue.append(follower)
    if len(ordered) != len(relations):
        names = [relations[index]["name"] for index in range(len(relations)) if index not in set(ordered)]
        raise GeometryError(
            f"Relations cycle through {', '.join(names)}. "
            "A joint cannot drive itself through other relations."
        )
    return [relations[index] for index in ordered]


def _relation(relation: dict, index: int, joints: dict[str, dict]) -> dict:
    if not isinstance(relation, dict):
        raise GeometryError(f"Relation {index} must be an object.")
    kind = relation.get("type")
    if kind not in _RELATIONS:
        raise GeometryError(
            f"Relation {index} type {kind!r} is unknown. Use gear, linkage, or cylinder."
        )
    name = _one_line(relation.get("name") or f"{kind}_{index + 1}", "A relation name")
    driver = str(relation.get("driver") or "")
    driven = str(relation.get("driven") or "")
    if driver not in joints or driven not in joints:
        raise GeometryError(f"Relation {name!r} must name joints in this assembly.")
    if driver == driven:
        raise GeometryError(f"Relation {name!r} cannot drive itself.")
    record = {"name": name, "type": kind, "driver": driver, "driven": driven}
    if kind == "gear":
        for joint_name in (driver, driven):
            if joints[joint_name]["type"] == "fixed":
                raise GeometryError(f"Gear {name!r} cannot turn fixed joint {joint_name!r}.")
        record["ratio"] = float(relation.get("ratio", 1.0))
        record["offset"] = float(relation.get("offset", 0.0))
    elif kind == "linkage":
        for joint_name in (driver, driven):
            if joints[joint_name]["type"] not in ("revolute", "cylindrical"):
                raise GeometryError(
                    f"Linkage {name!r} maps an angle in degrees. "
                    f"{joint_name!r} is {joints[joint_name]['type']}."
                )
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
            raise GeometryError(
                f"Cylinder {name!r} drives from a revolute or a cylindrical joint. {driver!r} is {arm['type']}."
            )
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


def _cylinder(
    relation: dict,
    joints: dict[str, dict],
    driver_degrees: float,
    slide_mm: float = 0.0,
) -> tuple[float, float, float]:
    """Barrel angle (degrees), rod extension (mm), and the out-of-plane offset (mm).

    The moving clevis is a point on the driver child. The fixed clevis is the
    barrel joint's parent anchor. Both are in the shared parent frame. With
    the barrel pin as the axis, angle 0 points ``bore_axis`` along the barrel
    child. Extension is the in-plane pin distance minus ``retracted_mm``. The
    barrel cannot tilt, so the out-of-plane offset is the distance the eye
    misses the clevis and is not part of the stroke. A cylindrical driver also
    shifts that clevis by its slide along the joint axis, the same travel the
    joint matrix uses. A revolute has no slide, and a caller that omits the
    type leaves the clevis unshifted.
    """
    driver = joints[relation["driver"]]
    barrel = joints[relation["driven"]]
    rotation = _rotation(driver["axis"], math.radians(driver_degrees))
    local = [
        relation["moving_anchor"][i] - driver["child_anchor"][i]
        for i in range(3)
    ]
    rotated = _matvec(rotation, local)
    travel = float(slide_mm) if driver.get("type") == "cylindrical" else 0.0
    driver_axis = driver["axis"]
    point_b = [
        driver["parent_anchor"][i] + rotated[i] + driver_axis[i] * travel
        for i in range(3)
    ]
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
    offset = abs(sum(delta[i] * axis[i] for i in range(3)))
    in_plane = math.sqrt(max(0.0, length * length - offset * offset))
    extension = in_plane - relation["retracted_mm"]
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


def _color(value: Any) -> list[int | float] | None:
    """Keep ints and floats apart so a byte and a fraction stay different.

    The picture rule lives in ``assembly._byte_channels``. Storing every
    channel as a float would make ``(1, 1, 0)`` indistinguishable from
    ``(1.0, 1.0, 0.0)`` after the assembly is saved.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _hex_color(value)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError("An instance color is #rrggbb, three ints from 0 to 255, or three floats from 0 to 1.")
    from aicad.assembly import _byte_channels

    _byte_channels(value)
    stored: list[int | float] = []
    for component in value:
        if isinstance(component, int) and not isinstance(component, bool):
            stored.append(int(component))
        else:
            stored.append(float(component))
    return stored


def _hex_color(value: str) -> list[int | float]:
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    digits = "0123456789abcdefABCDEF"
    if len(text) == 3 and all(ch in digits for ch in text):
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in digits for ch in text):
        raise GeometryError("An instance color is #rrggbb, three ints from 0 to 255, or three floats from 0 to 1.")
    return [int(text[index : index + 2], 16) for index in (0, 2, 4)]


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
    """Degrees about X, then Y, then Z, matching ``kernel.place``.

    The matrix is ``Rx @ Ry @ Rz``, the same composition as a placed part.
    """
    ax, ay, az = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx_m = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry_m = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz_m = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _matmat(rx_m, _matmat(ry_m, rz_m))


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
