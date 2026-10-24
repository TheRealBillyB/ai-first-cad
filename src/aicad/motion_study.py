"""Motion studies: drive joints over time and record interference.

A study is a timeline. ``duration_s`` is its length in seconds. ``steps`` is
how many poses are solved, including both ends. Time at step ``i`` is
``duration_s * i / (steps - 1)``.

A driver sets one joint from time. An expression is a formula in ``t``
(seconds), for example ``90 * t`` or ``10 * sin(pi * t)``. Names are ``t``,
``pi``, ``e``, and ``tau``. Functions are ``sin``, ``cos``, ``tan``, ``asin``,
``acos``, ``atan``, ``atan2``, ``sqrt``, ``abs``, ``min``, ``max``, ``hypot``,
``degrees``, and ``radians``. Keyframes are ``{time_s, value}`` with straight
lines between them, and the value is held outside the first and last time.
A cylindrical joint may also set ``slide`` in millimeters, as an expression
or as ``slide`` on each keyframe. Revolute values are degrees. Prismatic
values are millimeters. A fixed joint cannot be driven.

A coupling is a gear constraint between two joints:

    driven = ratio * driver + offset

The driven joint follows the driver after the drivers are applied, including
a driver that is itself coupled, up to a chain of eight. An assembly gear,
linkage, or cylinder that already drives that joint is left as it is; the
study does not add a second rule for it.

Joint limits are enforced by the joint solver. A command outside ``min`` or
``max`` is held on the stop, and that step records a limit hit. The posed
solid is the stopped one.

Interference uses the posed solids. The broad phase is the axis-aligned box:
a pair farther apart than ``clearance_mm`` cannot touch, so it is not
intersected. The narrow phase is the kernel boolean. Overlap reports the
intersection volume and the penetration depth. A gap shorter than
``clearance_mm`` is a clearance hit, and the depth is the shortfall. The two
instances of a joint, and pairs in ``ignore_collisions``, are allowed to touch.

The result record is ``motion_study.v1``. Later checks read it instead of
sampling the joints again. Each step has:

- ``time_s``: the sample time, rounded to 6 decimals. Drivers are evaluated
  at the exact fraction, before that rounding.
- ``joints``: the joint-solver rows after couplings and limits (value,
  commanded, slide, limit, origin, axis).
- ``poses``: each instance's 4×4 ``matrix`` and ``origin_mm``. The matrix is the exact rigid transform. A rounded rotation is not rigid, and the boolean then reports no volume for a real overlap.
- ``collisions``: ``a``, ``b``, ``interference_volume_mm3``,
  ``penetration_depth_mm``, ``gap_mm``, ``kind`` (``interference`` or
  ``clearance``), ``depth_method``, ``direction``, ``at_mm``.
- ``limits``: ``joint``, ``commanded``, ``enforced``, ``which`` (``min`` or
  ``max``), ``limit``, ``unit``.

The record also has ``first_collision``, ``collisions`` (every hit, with
``index`` and ``time_s``), ``colliding_steps``, ``first_limit``,
``limit_hits``, and ``limit_steps``. ``direction`` moves instance ``b`` out of
``a``, and the names are sorted. ``depth_method`` is ``separating_axis`` for
convex polyhedra, ``convex_hull`` when the depth is taken on a hull,
``separating_axis_sampled`` when there are too many edges for every pair,
``intersection_extent`` when the axis test misses a boolean hit, and
``clearance`` when only the gap is short. ``status`` is ``clear``, ``limit``,
``collision``, or ``collision_and_limit``.

``broad_phase_rejected`` and ``narrow_phase_pairs`` count the pairs skipped
and the pairs sent to the boolean.
"""

from __future__ import annotations

import ast
import copy
import math
import operator

from aicad import kernel
from aicad.assembly import posed_shapes
from aicad.errors import GeometryError
from aicad.honesty import VERIFIED_CHECK, finish
from aicad.kinematics import normalize as normalize_assembly
from aicad.kinematics import solve as solve_joints

SCHEMA = "motion_study.v1"
_MAX_STEPS = 10001
_MAX_CHAIN = 8
_VOLUME_MM3 = 1e-4
_NUMBER_LIMIT = 1_000_000.0
_EXPR_LIMIT = 400
_EXPR_DEPTH = 32

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_FUNCTIONS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "hypot": math.hypot,
    "degrees": math.degrees,
    "radians": math.radians,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_STUDY_KEYS = {"name", "duration_s", "duration_assumed", "steps", "clearance_mm", "drivers", "couplings"}
_DRIVER_KEYS = {"joint", "expression", "slide", "keyframes"}
_KEY_KEYS = {"time_s", "value", "slide"}
_COUPLING_KEYS = {"name", "type", "driver", "driven", "ratio", "offset"}


def normalize_studies(raw, joints: list[dict], relations: list[dict]) -> dict:
    """Clean the ``studies`` map on an assembly spec."""
    if not isinstance(raw, dict):
        raise GeometryError("studies must map a name to a study.")
    studies = {}
    for name, study in raw.items():
        ident = str(name).strip()
        if not ident:
            raise GeometryError("A motion study needs a name.")
        studies[ident] = normalize_study(study, joints, relations, name=ident)
    return studies


def normalize_study(study, joints: list[dict], relations: list[dict], name: str | None = None) -> dict:
    """Clean one study against the assembly joints and relations."""
    if not isinstance(study, dict):
        raise GeometryError("A motion study is an object.")
    unknown = set(study) - _STUDY_KEYS
    if unknown:
        raise GeometryError(
            f"Study {name or study.get('name')!r} has unknown fields: {', '.join(sorted(unknown))}."
        )
    ident = str(name or study.get("name") or "").strip()
    if not ident:
        raise GeometryError("A motion study needs a name.")
    if "duration_s" in study and study.get("duration_s") is not None:
        duration = _number(study.get("duration_s"), f"Study {ident!r} duration_s")
        if duration <= 0:
            raise GeometryError(f"Study {ident!r} duration_s must be positive.")
        assumed = bool(study.get("duration_assumed", False))
    else:
        duration = 1.0
        assumed = True
    steps = _require_steps(study.get("steps"), ident)
    clearance = 0.0 if study.get("clearance_mm") is None else _number(
        study.get("clearance_mm"), f"Study {ident!r} clearance_mm"
    )
    if clearance < 0:
        raise GeometryError(f"Study {ident!r} clearance_mm cannot be negative.")
    joint_index = {joint["name"]: joint for joint in joints}
    drivers = _drivers(study.get("drivers"), ident, joint_index)
    couplings = _couplings(study.get("couplings"), ident, joint_index)
    _conflicts(ident, drivers, couplings, relations)
    return {
        "name": ident,
        "duration_s": duration,
        "duration_assumed": assumed,
        "steps": steps,
        "clearance_mm": clearance,
        "drivers": drivers,
        "couplings": couplings,
    }


def study_by_name(spec: dict, name: str | None, inline: dict | None = None) -> dict | None:
    """The inline study, or the named study stored on the assembly."""
    if inline:
        study = copy.deepcopy(inline)
        if not study.get("name"):
            study["name"] = name or "study"
        return study
    if not name:
        return None
    studies = spec.get("studies") or {}
    if name not in studies:
        known = ", ".join(studies) or "(none)"
        raise GeometryError(f"No motion study {name!r}. Studies: {known}.")
    study = copy.deepcopy(studies[name])
    study["name"] = name
    return study


def sample_drivers(spec: dict, study: dict) -> list[dict]:
    """Driver commands at each step, before couplings and limits.

    Each item is ``index``, ``time_s``, and ``drivers``. A driver value is a
    number, or ``{value, slide}`` when the slide is driven too.
    """
    spec = normalize_assembly(spec)
    chosen = normalize_study(study, spec["joints"], spec["relations"], name=study.get("name"))
    samples = []
    for index, time_s in enumerate(_sample_times(chosen["duration_s"], chosen["steps"])):
        samples.append(
            {
                "index": index,
                "time_s": round(time_s, 6),
                "drivers": _rounded_commands(_commands(chosen, time_s)),
            }
        )
    return samples


def run_study(
    spec: dict,
    shapes: dict,
    study: dict,
    *,
    steps: int | None = None,
    clearance_mm: float | None = None,
) -> dict:
    """Pose ``spec`` through ``study`` and return a ``motion_study.v1`` record."""
    spec = normalize_assembly(spec)
    chosen = normalize_study(study, spec["joints"], spec["relations"], name=study.get("name"))
    if steps is not None:
        chosen["steps"] = _require_steps(steps, chosen["name"])
    if clearance_mm is not None:
        clearance = _number(clearance_mm, "clearance_mm")
        if clearance < 0:
            raise GeometryError("clearance_mm cannot be negative.")
        chosen["clearance_mm"] = clearance
    posed = _with_couplings(spec, chosen)
    times = _sample_times(chosen["duration_s"], chosen["steps"])
    step_rows = []
    flat_collisions = []
    flat_limits = []
    rejected = 0
    narrow = 0
    for index, time_s in enumerate(times):
        solved, placed = posed_shapes(posed, shapes, _commands(chosen, time_s))
        limits = [_limit_hit(joint) for joint in solved["joints"]]
        limits = [hit for hit in limits if hit is not None]
        collisions, step_rejected, step_narrow = _collisions(
            solved, placed, spec.get("ignore_collisions") or [], chosen["clearance_mm"]
        )
        rejected += step_rejected
        narrow += step_narrow
        shown_time = round(time_s, 6)
        for hit in limits:
            flat_limits.append({"index": index, "time_s": shown_time, **hit})
        for hit in collisions:
            flat_collisions.append({"index": index, "time_s": shown_time, **hit})
        step_rows.append(
            {
                "index": index,
                "time_s": shown_time,
                "joints": solved["joints"],
                "poses": {
                    instance["name"]: {
                        "part": instance["part"],
                        "origin_mm": instance["origin_mm"],
                        "matrix": instance["matrix"],
                    }
                    for instance in solved["instances"]
                },
                "limits": limits,
                "collisions": collisions,
                "broad_phase_rejected": step_rejected,
                "narrow_phase_pairs": step_narrow,
                "warnings": list(solved["warnings"]),
            }
        )
    colliding_steps = [step["index"] for step in step_rows if step["collisions"]]
    limit_steps = [step["index"] for step in step_rows if step["limits"]]
    first_collision = None
    if colliding_steps:
        step = step_rows[colliding_steps[0]]
        first_collision = {
            "index": step["index"],
            "time_s": step["time_s"],
            "joints": {joint["name"]: joint["value"] for joint in step["joints"]},
            "pairs": step["collisions"],
        }
    if flat_collisions and flat_limits:
        status = "collision_and_limit"
    elif flat_collisions:
        status = "collision"
    elif flat_limits:
        status = "limit"
    else:
        status = "clear"
    report = {
        "schema": SCHEMA,
        "assembly": spec["name"],
        "study": chosen,
        "status": status,
        "duration_s": chosen["duration_s"],
        "duration_assumed": chosen["duration_assumed"],
        "step_count": chosen["steps"],
        "clearance_mm": chosen["clearance_mm"],
        "colliding_steps": colliding_steps,
        "collision_count": len(flat_collisions),
        "first_collision": first_collision,
        "collisions": flat_collisions,
        "limit_steps": limit_steps,
        "limit_hit_count": len(flat_limits),
        "first_limit": flat_limits[0] if flat_limits else None,
        "limit_hits": flat_limits,
        "broad_phase_rejected": rejected,
        "narrow_phase_pairs": narrow,
        "steps": step_rows,
        "note": (
            "Each step is a pose from the joint solver. "
            "Collisions and limit hits are recorded on that pose."
        ),
        "assumptions": [
            "Samples are evenly spaced from the start of the timeline to the end.",
            "A gear coupling is driven = ratio × driver + offset, applied with the assembly relations.",
            "A command past a joint limit is held at the stop.",
            "The broad phase is the axis-aligned box. The narrow phase is the boolean intersection of the solids.",
            "Penetration depth for a convex solid is the minimum translation that separates it.",
            "Parts that share a joint are allowed to touch.",
            "This is not a contact force.",
        ],
    }
    return finish(
        report,
        kind=VERIFIED_CHECK,
        name="Motion study",
        what=(
            "Each step poses the assembly with the joint solver and intersects the modeled solids. "
            "Joint commands past a stop are held there and recorded."
        ),
        limits=[
            "Only the sampled steps are checked. A hit between steps is not seen.",
            "Parts joined to each other are allowed to touch, and so are pairs in ignore_collisions.",
            "Penetration depth is exact for convex polyhedra. A non-convex or curved solid uses the convex hull, so the depth can exceed the local overlap. The volume is the boolean of the modeled solids.",
            "A sampled direction set is used when a pair has too many edges. That depth is an upper bound.",
            "This is not a contact force.",
        ],
    )


def posed_at(spec: dict, study: dict, frames: int) -> list[dict]:
    """Solved poses at ``frames`` times across the study, with no collision test.

    Each row is ``index``, ``time_s``, and ``solved``. ``time_s`` is the study
    clock, from 0 through ``duration_s``. Couplings and joint limits match the
    study record. ``frames`` replaces the study's own step count.
    """
    spec = normalize_assembly(spec)
    chosen = normalize_study(study, spec["joints"], spec["relations"], name=study.get("name"))
    count = _require_steps(frames, chosen["name"])
    posed = _with_couplings(spec, chosen)
    rows = []
    for index, time_s in enumerate(_sample_times(chosen["duration_s"], count)):
        solved = solve_joints(posed, _commands(chosen, time_s))
        rows.append({"index": index, "time_s": round(time_s, 6), "solved": solved})
    return rows


def _drivers(raw, study: str, joints: dict[str, dict]) -> list[dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not raw:
        raise GeometryError(f"Study {study!r} needs at least one joint driver.")
    drivers = []
    seen = set()
    for index, driver in enumerate(raw):
        if not isinstance(driver, dict):
            raise GeometryError(f"Study {study!r} driver {index} must be an object.")
        unknown = set(driver) - _DRIVER_KEYS
        if unknown:
            raise GeometryError(
                f"Study {study!r} driver {index} has unknown fields: {', '.join(sorted(unknown))}."
            )
        joint_name = str(driver.get("joint") or "")
        joint = joints.get(joint_name)
        if joint is None:
            known = ", ".join(joints) or "(none)"
            raise GeometryError(f"Study {study!r} drives unknown joint {joint_name!r}. Joints: {known}.")
        if joint_name in seen:
            raise GeometryError(f"Study {study!r} drives {joint_name!r} twice.")
        seen.add(joint_name)
        if joint["type"] == "fixed":
            raise GeometryError(f"Study {study!r} cannot drive fixed joint {joint_name!r}.")
        has_expression = "expression" in driver or "slide" in driver
        has_keys = "keyframes" in driver
        if has_expression and has_keys:
            raise GeometryError(
                f"Study {study!r} driver {joint_name!r} has both an expression and keyframes."
            )
        if has_keys:
            drivers.append(_keyframe_driver(driver, study, joint))
        elif "expression" in driver or "slide" in driver:
            drivers.append(_expression_driver(driver, study, joint))
        else:
            raise GeometryError(
                f"Study {study!r} driver {joint_name!r} needs an expression or keyframes."
            )
    return drivers


def _expression_driver(driver: dict, study: str, joint: dict) -> dict:
    record = {"joint": joint["name"]}
    if "expression" in driver:
        record["expression"] = _expression_text(driver.get("expression"), f"Study {study!r} {joint['name']}")
    if "slide" in driver:
        if joint["type"] != "cylindrical":
            raise GeometryError(
                f"Study {study!r} sets slide on {joint['name']!r}, which is {joint['type']}."
            )
        record["slide"] = _expression_text(driver.get("slide"), f"Study {study!r} {joint['name']} slide")
    return record


def _keyframe_driver(driver: dict, study: str, joint: dict) -> dict:
    frames = driver.get("keyframes")
    if not isinstance(frames, list) or not frames:
        raise GeometryError(f"Study {study!r} driver {joint['name']!r} needs keyframes.")
    cleaned = []
    uses_slide = False
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise GeometryError(f"Study {study!r} {joint['name']!r} keyframe {index} must be an object.")
        unknown = set(frame) - _KEY_KEYS
        if unknown:
            raise GeometryError(
                f"Study {study!r} {joint['name']!r} keyframe {index} has unknown fields: "
                f"{', '.join(sorted(unknown))}."
            )
        if "value" not in frame:
            raise GeometryError(f"Study {study!r} {joint['name']!r} keyframe {index} needs value.")
        item = {
            "time_s": _number(frame.get("time_s"), f"Study {study!r} {joint['name']!r} keyframe {index} time_s"),
            "value": _number(frame.get("value"), f"Study {study!r} {joint['name']!r} keyframe {index} value"),
        }
        if "slide" in frame:
            if joint["type"] != "cylindrical":
                raise GeometryError(
                    f"Study {study!r} sets slide on {joint['name']!r}, which is {joint['type']}."
                )
            item["slide"] = _number(frame.get("slide"), f"Study {study!r} {joint['name']!r} keyframe {index} slide")
            uses_slide = True
        cleaned.append(item)
    if uses_slide:
        for index, item in enumerate(cleaned):
            if "slide" not in item:
                raise GeometryError(
                    f"Study {study!r} {joint['name']!r} keyframe {index} needs slide, because another keyframe sets it."
                )
    for index in range(1, len(cleaned)):
        if cleaned[index]["time_s"] <= cleaned[index - 1]["time_s"]:
            raise GeometryError(f"Study {study!r} {joint['name']!r} keyframe times must increase.")
    return {"joint": joint["name"], "keyframes": cleaned}


def _couplings(raw, study: str, joints: dict[str, dict]) -> list[dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise GeometryError(f"Study {study!r} couplings must be a list.")
    couplings = []
    driven = set()
    for index, coupling in enumerate(raw):
        if not isinstance(coupling, dict):
            raise GeometryError(f"Study {study!r} coupling {index} must be an object.")
        unknown = set(coupling) - _COUPLING_KEYS
        if unknown:
            raise GeometryError(
                f"Study {study!r} coupling {index} has unknown fields: {', '.join(sorted(unknown))}."
            )
        kind = coupling.get("type") or "gear"
        if kind != "gear":
            raise GeometryError(
                f"Study {study!r} coupling {index} type {kind!r} is unknown. Use gear."
            )
        name = str(coupling.get("name") or f"coupling_{index + 1}")
        driver = str(coupling.get("driver") or "")
        follower = str(coupling.get("driven") or "")
        for joint_name in (driver, follower):
            if joint_name not in joints:
                raise GeometryError(f"Study {study!r} coupling {name!r} names unknown joint {joint_name!r}.")
            if joints[joint_name]["type"] == "fixed":
                raise GeometryError(f"Study {study!r} coupling {name!r} cannot use fixed joint {joint_name!r}.")
        if driver == follower:
            raise GeometryError(f"Study {study!r} coupling {name!r} cannot drive itself.")
        if follower in driven:
            raise GeometryError(f"Study {study!r} couples {follower!r} twice.")
        driven.add(follower)
        if "ratio" not in coupling or coupling.get("ratio") is None:
            raise GeometryError(f"Study {study!r} coupling {name!r} needs ratio.")
        couplings.append(
            {
                "name": name,
                "type": "gear",
                "driver": driver,
                "driven": follower,
                "ratio": _number(coupling.get("ratio"), f"Study {study!r} coupling {name!r} ratio"),
                "offset": 0.0
                if coupling.get("offset") is None
                else _number(coupling.get("offset"), f"Study {study!r} coupling {name!r} offset"),
            }
        )
    return couplings


def _conflicts(study: str, drivers: list[dict], couplings: list[dict], relations: list[dict]) -> None:
    relation_of = {}
    graph = {}
    for relation in relations:
        relation_of[relation["driven"]] = relation["name"]
        graph[relation["driven"]] = relation["driver"]
        if relation.get("type") == "cylinder":
            relation_of[relation["stroke"]] = relation["name"]
            graph[relation["stroke"]] = relation["driver"]
    coupled = {coupling["driven"]: coupling for coupling in couplings}
    for driver in drivers:
        joint_name = driver["joint"]
        if joint_name in relation_of:
            raise GeometryError(
                f"Study {study!r} drives {joint_name!r}, which relation {relation_of[joint_name]!r} already drives."
            )
        if joint_name in coupled:
            raise GeometryError(
                f"Study {study!r} drives {joint_name!r} and coupling {coupled[joint_name]['name']!r} drives it too."
            )
    for coupling in couplings:
        if coupling["driven"] in relation_of:
            raise GeometryError(
                f"Study {study!r} coupling {coupling['name']!r} drives {coupling['driven']!r}, "
                f"which relation {relation_of[coupling['driven']]!r} already drives."
            )
        graph[coupling["driven"]] = coupling["driver"]
    for coupling in couplings:
        seen = []
        node = coupling["driven"]
        while node in graph:
            if node in seen:
                raise GeometryError(f"Study {study!r} couplings cycle through {', '.join(seen)}.")
            seen.append(node)
            node = graph[node]
            if len(seen) > _MAX_CHAIN:
                raise GeometryError(f"Study {study!r} coupling chain is longer than {_MAX_CHAIN} joints.")


def _with_couplings(spec: dict, study: dict) -> dict:
    posed = copy.deepcopy(spec)
    posed["studies"] = {}
    for coupling in study["couplings"]:
        posed["relations"].append(
            {
                "name": coupling["name"],
                "type": "gear",
                "driver": coupling["driver"],
                "driven": coupling["driven"],
                "ratio": coupling["ratio"],
                "offset": coupling["offset"],
            }
        )
    return posed


def _commands(study: dict, time_s: float) -> dict:
    commands = {}
    for driver in study["drivers"]:
        joint_name = driver["joint"]
        if "keyframes" in driver:
            value = _interpolate(driver["keyframes"], time_s, "value")
            if "slide" in driver["keyframes"][0]:
                commands[joint_name] = {
                    "value": value,
                    "slide": _interpolate(driver["keyframes"], time_s, "slide"),
                }
            else:
                commands[joint_name] = value
            continue
        blob = {}
        if "expression" in driver:
            blob["value"] = _evaluate(driver["expression"], time_s)
        if "slide" in driver:
            blob["slide"] = _evaluate(driver["slide"], time_s)
        commands[joint_name] = blob["value"] if "slide" not in blob else blob
    return commands


def _rounded_commands(commands: dict) -> dict:
    rounded = {}
    for name, value in commands.items():
        if isinstance(value, dict):
            rounded[name] = {key: round(float(item), 6) for key, item in value.items()}
        else:
            rounded[name] = round(float(value), 6)
    return rounded


def _interpolate(frames: list[dict], time_s: float, field: str) -> float:
    if time_s <= frames[0]["time_s"]:
        return float(frames[0][field])
    if time_s >= frames[-1]["time_s"]:
        return float(frames[-1][field])
    for index in range(len(frames) - 1):
        start = frames[index]
        end = frames[index + 1]
        if start["time_s"] <= time_s <= end["time_s"]:
            span = end["time_s"] - start["time_s"]
            blend = 0.0 if span == 0 else (time_s - start["time_s"]) / span
            return float(start[field]) + (float(end[field]) - float(start[field])) * blend
    return float(frames[-1][field])


def _collisions(solved: dict, placed: dict, ignore: list, clearance: float) -> tuple[list[dict], int, int]:
    skip = set()
    for pair in ignore:
        skip.add(tuple(sorted((str(pair[0]), str(pair[1])))))
    for joint in solved["joints"]:
        skip.add(tuple(sorted((joint["parent"], joint["child"]))))
    names = sorted(placed)
    hits = []
    rejected = 0
    narrow = 0
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if (left, right) in skip:
                continue
            gap = kernel.aabb_gap_lower_bound(placed[left], placed[right])
            if gap > clearance + 1e-9:
                rejected += 1
                continue
            narrow += 1
            hit = _narrow(placed[left], placed[right], clearance)
            if hit is None:
                continue
            hits.append({"a": left, "b": right, **hit})
    hits.sort(key=lambda item: (item["a"], item["b"]))
    return hits, rejected, narrow


def _narrow(left, right, clearance: float) -> dict | None:
    overlap, distance, point_a, point_b = kernel.clearance_between(left, right)
    at = None if point_a is None else [point_a.x, point_a.y, point_a.z]
    if overlap > _VOLUME_MM3:
        depth = kernel.penetration_depth(left, right)
        return {
            "kind": "interference",
            "interference_volume_mm3": overlap,
            "penetration_depth_mm": depth["penetration_depth_mm"],
            "gap_mm": 0.0,
            "depth_method": depth["depth_method"],
            "direction": depth["direction"],
            "at_mm": at,
        }
    if distance + 1e-9 < clearance:
        return {
            "kind": "clearance",
            "interference_volume_mm3": 0.0,
            "penetration_depth_mm": round(clearance - distance, 6),
            "gap_mm": distance,
            "depth_method": "clearance",
            "direction": _gap_direction(point_a, point_b),
            "at_mm": at,
        }
    return None


def _gap_direction(point_a, point_b) -> list[float] | None:
    if point_a is None or point_b is None:
        return None
    vector = [point_b.x - point_a.x, point_b.y - point_a.y, point_b.z - point_a.z]
    length = math.sqrt(sum(component * component for component in vector))
    if length < 1e-12:
        return None
    return [round(component / length, 6) for component in vector]


def _limit_hit(joint: dict) -> dict | None:
    if joint.get("limit") != "clamped":
        return None
    commanded = joint["commanded"]
    enforced = joint["value"]
    if commanded < enforced or (commanded == enforced and enforced == joint.get("min")):
        which = "min"
    else:
        which = "max"
    return {
        "joint": joint["name"],
        "commanded": commanded,
        "enforced": enforced,
        "which": which,
        "limit": joint.get(which),
        "unit": joint["unit"],
    }


def _sample_times(duration: float, steps: int) -> list[float]:
    if steps == 1:
        return [0.0]
    times = []
    for index in range(steps):
        if index == steps - 1:
            times.append(float(duration))
        else:
            times.append(duration * index / (steps - 1))
    return times


def _require_steps(value, study: str) -> int:
    label = "steps" if study == "" else f"Study {study!r} steps"
    if value is None:
        raise GeometryError(f"Study {study!r} needs steps.")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{label} must be a whole number.")
    if isinstance(value, float) and not value.is_integer():
        raise GeometryError(f"{label} must be a whole number.")
    steps = int(value)
    if steps < 2:
        raise GeometryError(f"{label} must be at least 2.")
    if steps > _MAX_STEPS:
        raise GeometryError(f"{label} must be at most {_MAX_STEPS}.")
    return steps


def _expression_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeometryError(f"{label} expression must be a formula in t.")
    text = value.strip()
    if len(text) > _EXPR_LIMIT:
        raise GeometryError(f"{label} expression is longer than {_EXPR_LIMIT} characters.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise GeometryError(f"{label} expression {text!r} is not valid: {exc.msg}.") from exc
    _validate_expr(tree.body, text, label, 0)
    return text


def _validate_expr(node: ast.AST, text: str, label: str, depth: int) -> None:
    if depth > _EXPR_DEPTH:
        raise GeometryError(f"{label} expression is nested too deeply.")
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise GeometryError(f"{label} expression {text!r} must use numbers.")
        return
    if isinstance(node, ast.Name):
        if node.id != "t" and node.id not in _CONSTANTS:
            raise GeometryError(
                f"{label} expression {text!r} uses unknown name {node.id!r}. Use t, pi, and math functions."
            )
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        _validate_expr(node.operand, text, label, depth + 1)
        return
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        _validate_expr(node.left, text, label, depth + 1)
        _validate_expr(node.right, text, label, depth + 1)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
        if node.keywords:
            raise GeometryError(f"{label} expression functions take numbers only.")
        for arg in node.args:
            _validate_expr(arg, text, label, depth + 1)
        return
    raise GeometryError(f"{label} expression {text!r} uses syntax that is not allowed.")


def _evaluate(expression: str, time_s: float) -> float:
    tree = ast.parse(expression, mode="eval")
    try:
        value = _eval_node(tree.body, time_s, expression, 0)
    except GeometryError:
        raise
    except ZeroDivisionError as exc:
        raise GeometryError(f"Driver expression {expression!r} divides by zero at t={time_s}.") from exc
    except (ValueError, OverflowError) as exc:
        raise GeometryError(f"Driver expression {expression!r} is not finite at t={time_s}: {exc}.") from exc
    if isinstance(value, complex) or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"Driver expression {expression!r} is not a real number at t={time_s}.")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"Driver expression {expression!r} is not finite at t={time_s}.")
    return _number(number, f"Driver expression {expression!r} at t={time_s}")


def _eval_node(node: ast.AST, time_s: float, expression: str, depth: int):
    if depth > _EXPR_DEPTH:
        raise GeometryError(f"Driver expression {expression!r} is nested too deeply.")
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id == "t":
            return float(time_s)
        return float(_CONSTANTS[node.id])
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, time_s, expression, depth + 1)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, time_s, expression, depth + 1)
        right = _eval_node(node.right, time_s, expression, depth + 1)
        result = _BINOPS[type(node.op)](left, right)
        if isinstance(result, complex):
            raise GeometryError(f"Driver expression {expression!r} is not a real number at t={time_s}.")
        return result
    if isinstance(node, ast.Call):
        args = [_eval_node(arg, time_s, expression, depth + 1) for arg in node.args]
        return _FUNCTIONS[node.func.id](*args)
    raise GeometryError(f"Driver expression {expression!r} uses syntax that is not allowed.")


def _number(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be a finite number.") from exc
    if isinstance(value, bool) or not math.isfinite(number):
        raise GeometryError(f"{label} is not a finite number.")
    if abs(number) > _NUMBER_LIMIT:
        raise GeometryError(f"{label} is {number:.12g}, past the {_NUMBER_LIMIT:.12g} limit.")
    return number
