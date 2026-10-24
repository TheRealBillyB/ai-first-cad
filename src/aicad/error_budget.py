"""Tip position error of a serial chain.

The tool point moves, to first order, by

    δp = J δq

``J`` is the positional Jacobian from :func:`aicad.chain.positional_jacobian`:
millimeters of tool motion per degree of a revolute joint, or per millimeter
of a prismatic joint. Each column is the same joint-frame derivative the
inverse-kinematics solver uses. A bound ``δq`` is one of three joint sources:

- encoder: half of one step
- backlash: the full gap, from one flank to the other
- stiffness: the absolute stated load divided by the stiffness

A gear mesh contributes its circumferential backlash divided by the operating
pitch radius of the named member. That arc is the mesh check's number.

A link contributes a small screw at a probe. In the link frame the probe
translates by ``u`` and rotates by a small ``ω``. In the world frame

    δp = R u + (R ω) × (p_tool − p_probe)

which is the rigid-body Jacobian of that screw. ``u`` and ``ω`` are either
stated or taken from the strength check: the displacement of the node nearest
the probe, and a rotation fit when three probes are given.

The worst case tries every sign of those independent vectors and keeps the
longest result. The root-sum-square is the square root of the sum of the
squared lengths. It is not a confidence interval, and a root-sum-square inside
the tolerance does not pass a worst case that is outside it.

The record is ``position_error.v1``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from aicad.chain import positional_jacobian, solve_kinematics
from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, finish
from aicad.kernel import finite_number, finite_vec3
from aicad.kinematics import _transform_point, _transform_vector, _worlds, solve as solve_pose

SCHEMA = "position_error.v1"
_MAX_POSES = 201
_SIGN_LIMIT = 16
_MESH_OVERLAP_MM = 0.02
_PROBE_FLOOR_MM = 1.0

_REQUEST_KEYS = {
    "tool",
    "at",
    "rotation_deg",
    "joints",
    "slides",
    "tolerance_mm",
    "poses",
    "path",
    "sources",
    "links",
}
_SOURCE_KEYS = {"encoder", "backlash", "stiffness", "angle", "slide"}
_CHANNEL_KEYS = {"encoder", "backlash", "stiffness"}
_ENCODER_KEYS = {"counts", "ratio", "resolution_deg", "resolution_mm", "mm_per_rev"}
_BACKLASH_KEYS = {"deg", "arcmin", "rad", "mm", "radius_mm", "mesh"}
_MESH_KEYS = {
    "pinion",
    "gear",
    "member",
    "centre_distance_mm",
    "shaft_angle_deg",
    "offset_mm",
    "pinion_phase_deg",
    "gear_phase_deg",
    "steps",
    "backlash_mm",
}
_STIFFNESS_KEYS = {"nmm_per_rad", "torque_nmm", "nm_per_rad", "torque_nm", "n_per_mm", "force_n"}
_LINK_KEYS = {"at", "translation_mm", "rotation_rad", "fea", "probes_mm"}
_POSE_KEYS = {"name", "joints", "slides"}

_LIMITS = [
    "The map is the derivative at the pose. A large angle is not solved again as a second configuration.",
    "An encoder bound is half of one step. A backlash bound is the full gap, one flank to the other.",
    "Spur, helical, and bevel backlash is the mesh check's circumferential arc divided by the operating pitch radius of the named member. A bevel uses the mid-face virtual spur, which turns with the real gear. An offset pair is not converted.",
    "Stiffness uses the absolute stated load. The worst case then points that deflection either way.",
    "Link rotation is zero unless a rotation is stated or three probe points are given. The strength check is the link in its own frame, so a world-fixed load is not rebuilt at each pose.",
    "A degraded strength-check mesh is too stiff to support a pass. The nearest node is used only when it lies within two mesh sizes of the probe, and at least 1 mm.",
    "The worst case tries every sign combination of up to 16 contributions. Beyond that it is the sum of the magnitudes, an upper bound on the combination.",
    "Root-sum-square is the square root of the sum of the squared contribution lengths. It is not a statistical interval, and it does not grant a pass.",
    "Thermal growth, bearing runout, and an assembly stack-up are omitted unless they are stated as a joint bound or a link deflection.",
]


def check_position_error(
    spec: dict,
    shapes: dict | None,
    request: dict,
    out_dir: Any = None,
) -> dict:
    """Worst-case and root-sum-square tip error against ``tolerance_mm``.

    ``shapes`` maps a part name to its solid. The strength check needs that
    solid. A stated joint bound or a stated link screw does not.
    """
    if not isinstance(request, dict):
        raise GeometryError("A position error request is an object.")
    _unknown(set(request) - _REQUEST_KEYS, "Position error request")
    if request.get("poses") is not None and request.get("path") is not None:
        raise GeometryError("Pass poses or a path, not both.")
    spec_name = spec.get("name") if isinstance(spec, dict) else None
    tool = request.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise GeometryError("Position error needs a tool instance.")
    tool = tool.strip()
    at = [0.0, 0.0, 0.0] if request.get("at") is None else list(finite_vec3(request.get("at"), "Tool point"))
    rotation_deg = _rotation(request.get("rotation_deg"))
    tolerance = finite_number(request.get("tolerance_mm"), "tolerance_mm", positive=True)
    joints = _number_map(request.get("joints"), "joints")
    slides = _number_map(request.get("slides"), "slides")
    from aicad.kinematics import normalize

    spec = normalize(spec)
    chain_names = _chain_joint_names(spec, tool)
    chain_instances = _chain_instances(spec, tool)
    sources = _sources(spec, request.get("sources"), chain_names)
    links = _link_requests(spec, request.get("links"), tool, at, chain_instances)
    if not sources and not links:
        raise GeometryError("Name an encoder, a backlash, a joint stiffness, or a link deflection.")
    warnings: list[str] = []
    assumptions = [
        f"Tool {tool} is at { _fmt_vec(at) } mm on {spec['name']}.",
        (
            "Each joint contribution is one positional-Jacobian column times its bound. "
            "An angular column is millimeters per degree. A sliding column is millimeters per millimeter."
        ),
        f"The tolerance is {tolerance:.6g} mm. The pass uses the worst case, not the root-sum-square.",
    ]
    resolved_links, link_notes, link_warnings = _resolve_links(spec, shapes, links, out_dir)
    assumptions.extend(link_notes)
    warnings.extend(link_warnings)
    samples, path_status = _samples(spec, tool, at, rotation_deg, joints, slides, request)
    if len(samples) > _MAX_POSES:
        raise GeometryError(f"A position budget evaluates at most {_MAX_POSES} poses.")
    poses = [
        _evaluate_pose(
            spec,
            sample,
            tool,
            at,
            rotation_deg,
            sources,
            resolved_links,
            tolerance,
            warnings,
        )
        for sample in samples
    ]
    for source in sources:
        assumptions.append(source["assumption"])
        if source.get("warning"):
            warnings.append(source["warning"])
    report = _summarize(spec_name or spec["name"], tool, at, rotation_deg, tolerance, poses, path_status, warnings)
    assumptions.append(report["summary"])
    if report["rss_peak_pose"] != report["worst_pose"]:
        assumptions.append(
            f"The largest root-sum-square is {report['rss_peak_mm']:.4f} mm at {report['rss_peak_pose']}."
        )
    report["assumptions"] = _unique(assumptions)
    report["warnings"] = _unique(warnings)
    return finish(
        report,
        kind=ROUGH_ESTIMATE,
        name="Position error",
        what=(
            "The tip position error is the positional Jacobian times each joint bound, "
            "plus a small screw for each link deflection. "
            "A rough estimate of those stated errors, not a measured pose of a physical arm."
        ),
        limits=_LIMITS,
    )


def _samples(spec, tool, at, rotation_deg, joints, slides, request) -> tuple[list[dict], str | None]:
    if request.get("path") is not None:
        traced = solve_kinematics(
            spec,
            None,
            {
                "tool": tool,
                "at": at,
                "rotation_deg": rotation_deg,
                "joints": joints,
                "slides": slides,
                "path": request["path"],
            },
        )
        path = traced["path"]
        base_values, base_slides, _posed = _commands(spec, joints, slides)
        samples = []
        for step in path["steps"]:
            if not step.get("joints"):
                samples.append(
                    {
                        "name": f"path-{step['index']}",
                        "values": None,
                        "slides": None,
                        "requested_joints": {},
                        "note": step.get("message") or "This path step did not reach a pose.",
                    }
                )
                continue
            values = dict(base_values)
            slide_values = dict(base_slides)
            values.update(step["joints"])
            commanded = step.get("commanded") or {}
            slide_values.update(commanded.get("slides") or {})
            samples.append(
                {
                    "name": f"path-{step['index']}",
                    "values": values,
                    "slides": slide_values,
                    "requested_joints": dict(step["joints"]),
                    "note": None,
                }
            )
        return samples, path.get("status")
    if request.get("poses") is None:
        values, slide_values, posed = _commands(spec, joints, slides)
        return [
            {
                "name": "command",
                "values": values,
                "slides": slide_values,
                "posed": posed,
                "requested_joints": dict(joints),
                "note": None,
            }
        ], None
    raw = request["poses"]
    if not isinstance(raw, list) or not raw:
        raise GeometryError("poses must be a non-empty list of pose names or joint objects.")
    samples = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        name, overlay_joints, overlay_slides = _pose_item(spec, joints, slides, item, index)
        if name in seen:
            raise GeometryError(f"Two poses are named {name!r}.")
        seen.add(name)
        values, slide_values, posed = _commands(spec, overlay_joints, overlay_slides)
        samples.append(
            {
                "name": name,
                "values": values,
                "slides": slide_values,
                "posed": posed,
                "requested_joints": overlay_joints,
                "note": None,
            }
        )
    return samples, None


def _pose_item(spec, joints, slides, item, index) -> tuple[str, dict, dict]:
    if isinstance(item, str):
        if item not in spec["poses"]:
            known = ", ".join(sorted(spec["poses"])) or "(none)"
            raise GeometryError(f"No pose {item!r}. Poses: {known}.")
        merged = dict(joints)
        merged.update(spec["poses"][item])
        return item, merged, dict(slides)
    if not isinstance(item, dict):
        raise GeometryError("A pose is a name or an object with joints.")
    _unknown(set(item) - _POSE_KEYS, "Pose")
    name = item.get("name")
    if name is None:
        name = f"pose-{index + 1}"
    if not isinstance(name, str) or not name.strip():
        raise GeometryError("A pose name must be text.")
    merged = dict(joints)
    merged.update(_number_map(item.get("joints"), f"Pose {name} joints"))
    merged_slides = dict(slides)
    merged_slides.update(_number_map(item.get("slides"), f"Pose {name} slides"))
    return name.strip(), merged, merged_slides


def _commands(spec, joints, slides) -> tuple[dict, dict, dict]:
    overrides: dict[str, Any] = dict(joints)
    for name, slide in slides.items():
        current = overrides.get(name)
        if isinstance(current, dict):
            current = dict(current)
            current["slide"] = slide
            overrides[name] = current
        elif current is None:
            overrides[name] = {"slide": slide}
        else:
            overrides[name] = {"value": current, "slide": slide}
    posed = solve_pose(spec, overrides)
    values = {row["name"]: float(row["value"]) for row in posed["joints"]}
    slide_values = {row["name"]: float(row["slide"]) for row in posed["joints"]}
    return values, slide_values, posed


def _evaluate_pose(spec, sample, tool, at, rotation_deg, sources, links, tolerance, warnings) -> dict:
    name = sample["name"]
    if sample["values"] is None:
        return {
            "name": name,
            "status": "inconclusive",
            "complete": False,
            "joints": None,
            "slides": None,
            "position_mm": None,
            "jacobian": [],
            "rank": None,
            "singular_values": [],
            "contributions": [],
            "worst_case_mm": None,
            "worst_case_vector_mm": None,
            "signs": None,
            "combination": None,
            "rss_mm": None,
            "rss_vector_mm": None,
            "findings": [sample["note"] or "This pose was not solved."],
            "notes": [],
            "chain": [],
        }
    jacobian = positional_jacobian(spec, sample["values"], sample["slides"], tool, at, rotation_deg)
    columns = {(item["joint"], item["field"]): item for item in jacobian["columns"]}
    worlds = _worlds(spec, sample["values"], sample["slides"])
    tool_position = np.asarray(jacobian["position_mm"], dtype=float)
    contributions = []
    findings = []
    notes = []
    complete = True
    for source in sources:
        column = columns.get((source["joint"], source["field"]))
        if column is None:
            raise GeometryError(f"Joint {source['joint']} has no column on the chain to {tool}.")
        vector = np.asarray(column["vector_mm"], dtype=float) * float(source["bound"])
        contributions.append(_contribution(source, column["vector_mm"], vector, used=True))
    for link in links:
        vector = None
        if link.get("translation") is not None and link.get("rotation") is not None:
            vector = _screw(worlds[link["link"]], link, tool_position)
        if link.get("blocks_pass") or not link["used"]:
            complete = False
            if link.get("finding"):
                findings.append(link["finding"])
        contributions.append(_link_contribution(link, vector, used=bool(link["used"])))
    span = min(3, len(jacobian["columns"]))
    if jacobian["columns"] and jacobian["rank"] < span:
        notes.append(
            f"Jacobian rank is {jacobian['rank']} of {span}, so some joint columns share one line at this pose."
        )
    posed = sample.get("posed") or {}
    for warning in posed.get("warnings") or []:
        if warning not in warnings:
            warnings.append(warning)
    _driven_notes(spec, sample, notes)
    vectors = [np.asarray(item["vector_mm"], dtype=float) for item in contributions if item["used"]]
    worst, worst_vector, signs, combination = _worst(vectors)
    rss_vector, rss = _rss(vectors)
    if combination == "sum_of_magnitudes":
        notes.append("More than 16 contributions, so the worst case is the sum of the magnitudes.")
    status = _pose_status(complete, worst, tolerance)
    if status == "fail":
        findings.append(_decision_sentence("fail", worst, name, tolerance))
    elif status == "inconclusive":
        findings.append("This pose is missing a contribution, so it cannot pass.")
    signed = None
    if signs is not None:
        signed = [
            {"id": item["id"], "sign": int(sign)}
            for item, sign in zip((item for item in contributions if item["used"]), signs)
        ]
    return {
        "name": name,
        "status": status,
        "complete": complete,
        "joints": {key: _num(value) for key, value in sample["values"].items()},
        "slides": {key: _num(value) for key, value in sample["slides"].items()},
        "position_mm": _vec(jacobian["position_mm"]),
        "jacobian": [
            {
                "joint": item["joint"],
                "field": item["field"],
                "unit": item["unit"],
                "driven": item["driven"],
                "vector_mm": _vec(item["vector_mm"]),
            }
            for item in jacobian["columns"]
        ],
        "rank": jacobian["rank"],
        "singular_values": [_num(value) for value in jacobian["singular_values"]],
        "contributions": contributions,
        "worst_case_mm": None if worst is None else _num(worst),
        "worst_case_vector_mm": None if worst_vector is None else _vec(worst_vector),
        "signs": signed,
        "combination": combination,
        "rss_mm": None if rss is None else _num(rss),
        "rss_vector_mm": None if rss_vector is None else _vec(rss_vector),
        "findings": findings,
        "notes": notes,
        "chain": jacobian["chain"],
    }


def _pose_status(complete: bool, worst: float | None, tolerance: float) -> str:
    if worst is not None and worst > float(tolerance) + 1e-9:
        return "fail"
    if not complete or worst is None:
        return "inconclusive"
    return "pass"


def _contribution(source, column, vector, *, used: bool) -> dict:
    row = {
        "id": source["id"],
        "kind": source["kind"],
        "joint": source["joint"],
        "field": source["field"],
        "link": None,
        "bound": _num(source["bound"]),
        "unit": source["unit"],
        "detail": source["detail"],
        "used": used,
        "column_mm": _vec(column),
        "vector_mm": _vec(vector) if used else None,
        "magnitude_mm": _num(float(np.linalg.norm(vector))) if used else None,
    }
    if source.get("mesh"):
        row["mesh"] = source["mesh"]
    return row


def _link_contribution(link, vector, *, used: bool) -> dict:
    return {
        "id": link["id"],
        "kind": "link",
        "joint": None,
        "field": None,
        "link": link["link"],
        "bound": None,
        "unit": "mm",
        "detail": link["detail"],
        "used": used,
        "column_mm": None,
        "vector_mm": None if vector is None else _vec(vector),
        "magnitude_mm": None if vector is None else _num(float(np.linalg.norm(vector))),
        "translation_mm": _vec(link["translation"]) if link.get("translation") is not None else None,
        "rotation_rad": _vec(link["rotation"]) if link.get("rotation") is not None else None,
        "origin_mm": _vec(link["origin"]) if link.get("origin") is not None else None,
        "fea": link.get("fea"),
    }


def _screw(world, link, tool_position) -> np.ndarray:
    translation = np.asarray(_transform_vector(world, link["translation"]), dtype=float)
    rotation = np.asarray(_transform_vector(world, link["rotation"]), dtype=float)
    origin = np.asarray(_transform_point(world, link["origin"]), dtype=float)
    return translation + np.cross(rotation, tool_position - origin)


def _worst(vectors: list[np.ndarray]) -> tuple[float | None, list | None, list | None, str | None]:
    if not vectors:
        return 0.0, [0.0, 0.0, 0.0], [], "signs"
    columns = np.column_stack(vectors)
    count = int(columns.shape[1])
    if count > _SIGN_LIMIT:
        total = float(np.sum(np.linalg.norm(columns, axis=0)))
        return total, None, None, "sum_of_magnitudes"
    masks = np.arange(1 << count, dtype=np.int64)
    bits = (masks[:, None] >> np.arange(count, dtype=np.int64)) & 1
    signs = np.where(bits == 1, 1.0, -1.0)
    totals = signs @ columns.T
    lengths = np.linalg.norm(totals, axis=1)
    index = int(np.argmax(lengths))
    return float(lengths[index]), totals[index].tolist(), signs[index].astype(int).tolist(), "signs"


def _rss(vectors: list[np.ndarray]) -> tuple[list | None, float | None]:
    if not vectors:
        return [0.0, 0.0, 0.0], 0.0
    columns = np.column_stack(vectors)
    squares = np.sum(columns * columns, axis=1)
    rss_vector = np.sqrt(squares)
    return rss_vector.tolist(), float(np.linalg.norm(rss_vector))


def _summarize(assembly, tool, at, rotation_deg, tolerance, poses, path_status, warnings) -> dict:
    ranked = [pose for pose in poses if pose["worst_case_mm"] is not None]
    if ranked:
        # Equal keys keep the earlier pose.
        worst_pose = max(ranked, key=lambda pose: pose["worst_case_mm"])
        rss_pose = max(ranked, key=lambda pose: pose["rss_mm"] if pose["rss_mm"] is not None else -1.0)
    else:
        worst_pose = None
        rss_pose = None
    if any(pose["status"] == "fail" for pose in poses):
        status = "fail"
    elif any(pose["status"] == "inconclusive" for pose in poses):
        status = "inconclusive"
    else:
        status = "pass"
    if worst_pose is None:
        summary = "No pose produced a tip error."
        worst_mm = None
        worst_vector = None
        rss_mm = None
        rss_vector = None
        worst_name = None
    else:
        worst_mm = worst_pose["worst_case_mm"]
        worst_vector = worst_pose["worst_case_vector_mm"]
        rss_mm = worst_pose["rss_mm"]
        rss_vector = worst_pose["rss_vector_mm"]
        worst_name = worst_pose["name"]
        summary = _decision_sentence(status, worst_mm, worst_name, tolerance)
    findings = []
    if status != "pass":
        findings.append(summary)
    for pose in poses:
        for finding in pose["findings"]:
            if finding not in findings and finding != summary:
                findings.append(f"{pose['name']}: {finding}")
    if path_status not in (None, "solved", "singular"):
        warnings.append(f"The tool path status is {path_status}.")
    chain = next((pose["chain"] for pose in poses if pose.get("chain")), [])
    return {
        "schema": SCHEMA,
        "assembly": assembly,
        "tool": {"instance": tool, "at": _vec(at), "rotation_deg": _vec(rotation_deg)},
        "tolerance_mm": _num(tolerance),
        "status": status,
        "summary": summary,
        "worst_case_mm": worst_mm,
        "worst_case_vector_mm": worst_vector,
        "worst_pose": worst_name,
        "rss_mm": rss_mm,
        "rss_vector_mm": rss_vector,
        "rss_peak_mm": None if rss_pose is None else rss_pose["rss_mm"],
        "rss_peak_pose": None if rss_pose is None else rss_pose["name"],
        "path_status": path_status,
        "pose_count": len(poses),
        "contributions": [] if worst_pose is None else worst_pose["contributions"],
        "poses": poses,
        "findings": findings,
        "chain": chain,
    }


def _decision_sentence(status: str, worst: float, pose: str, tolerance: float) -> str:
    amount = f"{worst:.4f}"
    limit = f"{tolerance:.4f}"
    if status == "fail":
        return f"Worst-case tip error is {amount} mm at {pose}, past the {limit} mm tolerance."
    if status == "inconclusive":
        return (
            f"Worst-case tip error of the contributions that were computed is {amount} mm at {pose}. "
            f"The tolerance is {limit} mm, and a missing contribution blocks a pass."
        )
    return f"Worst-case tip error is {amount} mm at {pose}, within the {limit} mm tolerance."


def _driven_notes(spec, sample, notes) -> None:
    driven = {relation["driven"] for relation in spec["relations"]}
    for relation in spec["relations"]:
        if relation["type"] == "cylinder":
            driven.add(relation["stroke"])
    requested = sample.get("requested_joints") or {}
    for name in requested:
        if name not in driven:
            continue
        solved = sample["values"].get(name)
        if solved is None:
            continue
        if abs(float(requested[name]) - float(solved)) > 1e-3:
            notes.append(
                f"{name} is driven. The budget uses the relation value {solved:.4f}, not {float(requested[name]):.4f}."
            )


def _sources(spec, raw, chain_names) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise GeometryError("sources must map a joint name to its encoder, backlash, and stiffness.")
    joints = {joint["name"]: joint for joint in spec["joints"]}
    rows = []
    for name, source in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise GeometryError("A source joint name must be text.")
        joint = joints.get(name)
        if joint is None:
            known = ", ".join(sorted(joints)) or "(none)"
            raise GeometryError(f"No joint {name!r}. Joints: {known}.")
        if name not in chain_names:
            raise GeometryError(f"Joint {name!r} is not on the chain to the tool.")
        if joint["type"] == "fixed":
            raise GeometryError(f"Joint {name!r} is fixed, so it has no error column.")
        if not isinstance(source, dict):
            raise GeometryError(f"Sources for {name!r} must be an object.")
        _unknown(set(source) - _SOURCE_KEYS, f"Sources for {name}")
        rows.extend(_channels(joint, source))
    return rows


def _channels(joint, source) -> list[dict]:
    name = joint["name"]
    if "angle" in source or "slide" in source:
        if joint["type"] != "cylindrical":
            raise GeometryError(f"Joint {name!r} is not cylindrical. Put the source on the joint, not under angle or slide.")
        extra = set(source) - {"angle", "slide"}
        if extra:
            raise GeometryError(f"Joint {name!r} uses angle and slide. Move {', '.join(sorted(extra))} under those.")
        rows = []
        if "angle" in source:
            rows.extend(_one_channel(joint, source["angle"], "value", True, "angle"))
        if "slide" in source:
            rows.extend(_one_channel(joint, source["slide"], "slide", False, "slide"))
        if not rows:
            raise GeometryError(f"Joint {name!r} needs a source under angle or slide.")
        return rows
    angular = joint["type"] in ("revolute", "cylindrical")
    if joint["type"] == "cylindrical":
        raise GeometryError(f"Cylindrical joint {name!r} needs angle and slide. A slide is not an angle.")
    return _one_channel(joint, source, "value", angular, None)


def _one_channel(joint, source, field, angular, label) -> list[dict]:
    name = joint["name"]
    where = name if label is None else f"{name} {label}"
    if not isinstance(source, dict):
        raise GeometryError(f"Sources for {where} must be an object.")
    _unknown(set(source) - _CHANNEL_KEYS, f"Sources for {where}")
    if not source:
        raise GeometryError(f"Joint {where} has no encoder, backlash, or stiffness.")
    rows = []
    if "encoder" in source:
        rows.append(_encoder(name, field, angular, source["encoder"], where))
    if "backlash" in source:
        rows.append(_backlash(name, field, angular, source["backlash"], where))
    if "stiffness" in source:
        rows.append(_stiffness(name, field, angular, source["stiffness"], where))
    return rows


def _encoder(joint, field, angular, raw, where) -> dict:
    if not isinstance(raw, dict):
        raise GeometryError(f"Encoder on {where} must be an object.")
    _unknown(set(raw) - _ENCODER_KEYS, f"Encoder on {where}")
    has_step = "resolution_deg" in raw or "resolution_mm" in raw
    has_counts = "counts" in raw
    if has_step == has_counts:
        raise GeometryError(f"Encoder on {where} needs resolution_deg, resolution_mm, or counts, and only one of those.")
    if "ratio" in raw and not has_counts:
        raise GeometryError(f"Encoder ratio on {where} belongs with counts.")
    if "mm_per_rev" in raw and (angular or not has_counts):
        raise GeometryError(f"mm_per_rev on {where} is the travel per revolution of a sliding joint with counts.")
    suffix = "" if field == "value" else ".slide"
    if has_step:
        if angular:
            if "resolution_mm" in raw:
                raise GeometryError(f"Encoder on {where} is an angle. Use resolution_deg or counts.")
            step = finite_number(raw["resolution_deg"], f"{where} resolution_deg", positive=True, bound=None, unit="deg")
            unit = "deg"
        else:
            if "resolution_deg" in raw:
                raise GeometryError(f"Encoder on {where} is a length. Use resolution_mm or counts.")
            step = finite_number(raw["resolution_mm"], f"{where} resolution_mm", positive=True, bound=None)
            unit = "mm"
        bound = step / 2.0
        detail = f"half of a {step:.6g} {unit} step"
    else:
        counts = _counts(raw["counts"], where)
        ratio = finite_number(raw.get("ratio", 1.0), f"{where} encoder ratio", positive=True, bound=None, unit="1")
        if angular:
            step = 360.0 / (counts * ratio)
            unit = "deg"
            detail = f"half of 360/({counts:.0f}×{ratio:.6g}) degrees"
        else:
            lead = finite_number(raw["mm_per_rev"], f"{where} mm_per_rev", positive=True, bound=None)
            step = lead / (counts * ratio)
            unit = "mm"
            detail = f"half of {lead:.6g}/({counts:.0f}×{ratio:.6g}) mm"
        bound = step / 2.0
    warning = None
    if unit == "deg" and bound > 5.0:
        warning = f"{where} encoder bound is {bound:.3f} deg, which is large for the linear map."
    return _bound_row(
        f"{joint}{suffix}.encoder",
        "encoder",
        joint,
        field,
        bound,
        unit,
        f"{where} encoder: {detail} is {bound:.6g} {unit}.",
        detail,
        warning,
    )


def _backlash(joint, field, angular, raw, where) -> dict:
    if not isinstance(raw, dict):
        raise GeometryError(f"Backlash on {where} must be an object.")
    _unknown(set(raw) - _BACKLASH_KEYS, f"Backlash on {where}")
    kinds = [key for key in ("deg", "arcmin", "rad", "mm", "mesh") if key in raw]
    if len(kinds) != 1:
        raise GeometryError(f"Backlash on {where} needs one of deg, arcmin, rad, mm, or mesh.")
    suffix = "" if field == "value" else ".slide"
    mesh = None
    warning = None
    if "mesh" in raw:
        if not angular:
            raise GeometryError(f"A sliding joint {where} takes backlash in millimeters, not a gear mesh.")
        bound, mesh, warning = _mesh_angle(raw["mesh"], where)
        unit = "deg"
        detail = (
            f"circumferential backlash {mesh['circumferential_mm']:.6g} mm "
            f"at radius {mesh['radius_mm']:.6g} mm is {bound:.6g} deg"
        )
    elif "mm" in raw and not angular:
        if "radius_mm" in raw:
            raise GeometryError(f"Backlash on sliding joint {where} is the gap in millimeters. Drop radius_mm.")
        bound = finite_number(raw["mm"], f"{where} backlash_mm", bound=None)
        if bound < 0:
            raise GeometryError(f"Backlash on {where} is a gap and cannot be negative.")
        unit = "mm"
        detail = f"the full gap of {bound:.6g} mm"
    elif "mm" in raw:
        if "radius_mm" not in raw:
            raise GeometryError(f"Backlash in millimeters on {where} needs radius_mm, the radius of that arc.")
        arc = finite_number(raw["mm"], f"{where} backlash_mm", bound=None)
        if arc < 0:
            raise GeometryError(f"Backlash on {where} is a gap and cannot be negative.")
        radius = finite_number(raw["radius_mm"], f"{where} backlash radius_mm", positive=True, bound=None)
        bound = math.degrees(arc / radius)
        unit = "deg"
        detail = f"{arc:.6g} mm at radius {radius:.6g} mm is {bound:.6g} deg"
    elif "deg" in raw:
        if not angular:
            raise GeometryError(f"Backlash on sliding joint {where} is a length. Use mm.")
        bound = finite_number(raw["deg"], f"{where} backlash_deg", bound=None, unit="deg")
        if bound < 0:
            raise GeometryError(f"Backlash on {where} is a gap and cannot be negative.")
        unit = "deg"
        detail = f"the full gap of {bound:.6g} deg"
    elif "arcmin" in raw:
        if not angular:
            raise GeometryError(f"Backlash on sliding joint {where} is a length. Use mm.")
        arcmin = finite_number(raw["arcmin"], f"{where} backlash_arcmin", bound=None, unit="arcmin")
        if arcmin < 0:
            raise GeometryError(f"Backlash on {where} is a gap and cannot be negative.")
        bound = arcmin / 60.0
        unit = "deg"
        detail = f"{arcmin:.6g} arcmin is {bound:.6g} deg, the full gap"
    else:
        if not angular:
            raise GeometryError(f"Backlash on sliding joint {where} is a length. Use mm.")
        radians = finite_number(raw["rad"], f"{where} backlash_rad", bound=None, unit="rad")
        if radians < 0:
            raise GeometryError(f"Backlash on {where} is a gap and cannot be negative.")
        bound = math.degrees(radians)
        unit = "deg"
        detail = f"{radians:.6g} rad is {bound:.6g} deg, the full gap"
    if unit == "deg" and bound > 5.0 and warning is None:
        warning = f"{where} backlash bound is {bound:.3f} deg, which is large for the linear map."
    row = _bound_row(
        f"{joint}{suffix}.backlash",
        "backlash",
        joint,
        field,
        bound,
        unit,
        f"{where} backlash: {detail}.",
        detail,
        warning,
    )
    if mesh is not None:
        row["mesh"] = mesh
    return row


def _mesh_angle(raw, where) -> tuple[float, dict, str | None]:
    if not isinstance(raw, dict):
        raise GeometryError(f"Mesh backlash on {where} must be an object.")
    _unknown(set(raw) - _MESH_KEYS, f"Mesh backlash on {where}")
    member = raw.get("member")
    if member not in ("pinion", "gear"):
        raise GeometryError(f"Mesh backlash on {where} needs member pinion or gear, the wheel on this joint.")
    pinion = raw.get("pinion")
    gear = raw.get("gear")
    if not isinstance(pinion, dict) or not isinstance(gear, dict):
        raise GeometryError(f"Mesh backlash on {where} needs a pinion spec and a gear spec.")
    from aicad.gears.geometry import gear_data
    from aicad.gears.meshcheck import check_pair

    left = gear_data(pinion)
    right = gear_data(gear)
    if left["axis_layout"] == "offset" or right["axis_layout"] == "offset":
        raise GeometryError(
            "An offset pair reports backlash along the tooth, not an arc on a pitch circle. "
            "Pass backlash in degrees, or backlash_mm with radius_mm."
        )
    mounting = {key: raw[key] for key in ("centre_distance_mm", "shaft_angle_deg", "offset_mm", "pinion_phase_deg", "gear_phase_deg") if key in raw}
    report = check_pair(pinion, gear, mounting or None, raw.get("backlash_mm"), raw.get("steps"))
    circumferential = (report.get("backlash") or {}).get("circumferential_mm")
    public = {
        "status": report.get("status"),
        "axis_layout": report.get("axis_layout"),
        "member": member,
        "circumferential_mm": None if circumferential is None else _num(circumferential),
        "normal_mm": (report.get("backlash") or {}).get("normal_mm"),
        "centre_distance_mm": report.get("centre_distance_mm"),
        "findings": list(report.get("findings") or []),
    }
    if circumferential is None:
        raise GeometryError(f"The mesh check on {where} did not define a circumferential backlash.")
    if float(circumferential) < -_MESH_OVERLAP_MM:
        raise GeometryError(
            f"Circumferential backlash on {where} is {float(circumferential):.4f} mm, so the flanks overlap. "
            "There is no free gap to budget."
        )
    arc = max(float(circumferential), 0.0)
    radius = _operating_radius(left, right, member, report)
    public["radius_mm"] = _num(radius)
    bound = math.degrees(arc / radius)
    public["angle_deg"] = _num(bound)
    warning = None
    if public["findings"]:
        warning = f"Mesh check on {where}: " + " ".join(public["findings"])
    return bound, public, warning


def _operating_radius(left, right, member, report) -> float:
    layout = report.get("axis_layout")
    if layout == "parallel":
        centre = float(report["centre_distance_mm"])
        teeth = float(left["teeth"] if member == "pinion" else right["teeth"])
        total = float(left["teeth"]) + float(right["teeth"])
    elif layout == "intersecting":
        centre = report.get("virtual_centre_distance_mm")
        if centre is None:
            raise GeometryError("The bevel mesh did not report a virtual centre distance.")
        centre = float(centre)
        from aicad.gears.geometry import virtual_spur_at

        outer = min(float(left["outer_cone_distance_mm"]), float(right["outer_cone_distance_mm"]))
        face = min(float(left["face_width_mm"]), float(right["face_width_mm"]))
        mid = outer - face / 2.0
        section_p = virtual_spur_at(left, mid)
        section_g = virtual_spur_at(right, mid)
        teeth = float(section_p["z"] if member == "pinion" else section_g["z"])
        total = float(section_p["z"]) + float(section_g["z"])
    else:
        raise GeometryError(
            "An offset pair reports backlash along the tooth, not an arc on a pitch circle. "
            "Pass backlash in degrees, or backlash_mm with radius_mm."
        )
    if total == 0 or centre <= 0:
        raise GeometryError("The operating pitch radius is not defined for this mesh.")
    radius = abs(centre * teeth / total)
    if radius < 1e-9:
        raise GeometryError("The operating pitch radius is zero, so backlash is not an angle.")
    return radius


def _stiffness(joint, field, angular, raw, where) -> dict:
    if not isinstance(raw, dict):
        raise GeometryError(f"Stiffness on {where} must be an object.")
    _unknown(set(raw) - _STIFFNESS_KEYS, f"Stiffness on {where}")
    torsional = "nmm_per_rad" in raw or "nm_per_rad" in raw or "torque_nmm" in raw or "torque_nm" in raw
    linear = "n_per_mm" in raw or "force_n" in raw
    if torsional and linear:
        raise GeometryError(f"Stiffness on {where} is either a torsion or a slide, not both.")
    suffix = "" if field == "value" else ".slide"
    if angular:
        if not torsional or linear:
            raise GeometryError(f"Angular joint {where} needs nmm_per_rad and torque_nmm, or nm_per_rad and torque_nm.")
        if "nmm_per_rad" in raw or "torque_nmm" in raw:
            if "nm_per_rad" in raw or "torque_nm" in raw:
                raise GeometryError(f"Stiffness on {where} uses one unit pair, not both.")
            rate = finite_number(raw.get("nmm_per_rad"), f"{where} nmm_per_rad", positive=True, bound=None, unit="N·mm/rad")
            load = finite_number(raw.get("torque_nmm"), f"{where} torque_nmm", bound=None, unit="N·mm")
            detail = f"{abs(load):.6g} N·mm / {rate:.6g} N·mm/rad"
        else:
            rate = finite_number(raw.get("nm_per_rad"), f"{where} nm_per_rad", positive=True, bound=None, unit="N·m/rad")
            load = finite_number(raw.get("torque_nm"), f"{where} torque_nm", bound=None, unit="N·m")
            detail = f"{abs(load):.6g} N·m / {rate:.6g} N·m/rad"
        radians = abs(load) / rate
        bound = math.degrees(radians)
        unit = "deg"
        detail = f"{detail} is {radians:.6g} rad ({bound:.6g} deg)"
    else:
        if not linear or torsional:
            raise GeometryError(f"Sliding joint {where} needs n_per_mm and force_n.")
        rate = finite_number(raw.get("n_per_mm"), f"{where} n_per_mm", positive=True, bound=None, unit="N/mm")
        load = finite_number(raw.get("force_n"), f"{where} force_n", bound=None, unit="N")
        bound = abs(load) / rate
        unit = "mm"
        detail = f"{abs(load):.6g} N / {rate:.6g} N/mm is {bound:.6g} mm"
    warning = None
    if unit == "deg" and bound > 5.0:
        warning = f"{where} stiffness bound is {bound:.3f} deg, which is large for the linear map."
    return _bound_row(
        f"{joint}{suffix}.stiffness",
        "stiffness",
        joint,
        field,
        bound,
        unit,
        f"{where} stiffness: {detail}.",
        detail,
        warning,
    )


def _bound_row(ident, kind, joint, field, bound, unit, assumption, detail, warning) -> dict:
    return {
        "id": ident,
        "kind": kind,
        "joint": joint,
        "field": field,
        "bound": float(bound),
        "unit": unit,
        "assumption": assumption,
        "detail": detail,
        "warning": warning,
        "mesh": None,
    }


def _link_requests(spec, raw, tool, tool_at, chain_instances) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise GeometryError("links must map an instance name to a deflection or a strength-check setup.")
    instances = {item["name"]: item for item in spec["instances"]}
    rows = []
    for name, source in raw.items():
        if not isinstance(name, str) or name not in instances:
            known = ", ".join(sorted(instances)) or "(none)"
            raise GeometryError(f"No instance {name!r} for a link deflection. Instances: {known}.")
        if name not in chain_instances:
            raise GeometryError(f"Instance {name!r} is not on the chain to {tool}.")
        if not isinstance(source, dict):
            raise GeometryError(f"Link {name!r} must be an object.")
        _unknown(set(source) - _LINK_KEYS, f"Link {name}")
        stated = "translation_mm" in source or "rotation_rad" in source
        fea = "fea" in source
        if stated == fea:
            raise GeometryError(f"Link {name!r} needs a stated deflection or a strength-check setup, and not both.")
        if "probes_mm" in source and not fea:
            raise GeometryError(f"probes_mm on {name!r} belongs with the strength check.")
        origin, anchor = _anchor(spec, name, source.get("at"), tool, tool_at)
        row = {"link": name, "origin": origin, "anchor": anchor, "id": f"{name}.deflection"}
        if stated:
            translation = [0.0, 0.0, 0.0] if "translation_mm" not in source else list(finite_vec3(source.get("translation_mm"), f"{name} translation_mm"))
            rotation = [0.0, 0.0, 0.0] if "rotation_rad" not in source else list(_rotation_rad(source.get("rotation_rad"), f"{name} rotation_rad"))
            row.update(
                {
                    "used": True,
                    "blocks_pass": False,
                    "translation": translation,
                    "rotation": rotation,
                    "detail": (
                        f"stated screw at {anchor}: translation {_fmt_vec(translation)} mm, "
                        f"rotation {_fmt_vec(rotation)} rad"
                    ),
                    "finding": None,
                    "warning": _large_screw(name, translation, rotation),
                    "fea": None,
                }
            )
        else:
            if not isinstance(source["fea"], dict):
                raise GeometryError(f"Strength check on {name!r} must be an object with material, fixes, and loads.")
            probes = source.get("probes_mm")
            if probes is None:
                probes = [origin]
            row.update({"used": False, "fea_setup": source["fea"], "probes_mm": probes, "part": instances[name]["part"]})
        rows.append(row)
    return rows


def _resolve_links(spec, shapes, links, out_dir) -> tuple[list[dict], list[str], list[str]]:
    notes = []
    warnings = []
    resolved = []
    for link in links:
        if "fea_setup" not in link:
            notes.append(f"{link['link']} deflection: {link['detail']}.")
            resolved.append(link)
            continue
        solved = _solve_link(shapes, link, out_dir)
        resolved.append(solved)
        if solved.get("assumption"):
            notes.append(solved["assumption"])
        if solved.get("warning"):
            warnings.append(solved["warning"])
        for line in solved.get("strength_assumptions") or []:
            notes.append(f"{link['link']}: {line}")
    return resolved, notes, warnings


def _solve_link(shapes, link, out_dir) -> dict:
    from aicad.fea import solve_part

    part = link["part"]
    if not shapes or part not in shapes:
        raise GeometryError(f"Link {link['link']!r} uses part {part!r}, which has no solid for the strength check.")
    from pathlib import Path

    destination = None if out_dir is None else Path(out_dir) / link["link"]
    report = solve_part(
        shapes[part],
        link["fea_setup"],
        out_dir=destination,
        stem="fea",
        part_name=link["link"],
        probes_mm=link["probes_mm"],
    )
    public = {
        "status": report.status,
        "degraded": bool((report.mesh or {}).get("degraded")),
        "max_displacement_mm": report.max_displacement_mm,
        "min_safety_factor": report.min_safety_factor,
        "review": None if report.review is None else report.review.get("status"),
        "probes": None if report.probes is None else list(report.probes),
        "material": (report.material or {}).get("id"),
        "warnings": list(report.warnings),
        "views": [{"name": view.name, "path": view.path} for view in report.views],
    }
    base = {
        "link": link["link"],
        "origin": link["origin"],
        "anchor": link["anchor"],
        "id": link["id"],
        "fea": public,
        "strength_assumptions": list(report.assumptions),
        "part": part,
    }
    if report.status != "solved" or not report.probes:
        base.update(
            {
                "used": False,
                "blocks_pass": True,
                "translation": None,
                "rotation": None,
                "detail": report.note,
                "finding": f"{link['link']} strength check did not return a probe displacement. {report.note}",
                "warning": None,
                "assumption": f"{link['link']} strength check status is {report.status}.",
            }
        )
        return base
    mesh_size = float((report.mesh or {}).get("size_mm") or 0.0)
    limit = max(2.0 * mesh_size, _PROBE_FLOOR_MM)
    far = [probe for probe in report.probes if float(probe["distance_mm"]) > limit + 1e-9]
    if far:
        base.update(
            {
                "used": False,
                "blocks_pass": True,
                "translation": None,
                "rotation": None,
                "detail": f"nearest node is {far[0]['distance_mm']:.3f} mm from the probe",
                "finding": (
                    f"{link['link']} probe is {far[0]['distance_mm']:.3f} mm from the nearest node, "
                    f"past the {limit:.3f} mm limit, so that displacement is not used."
                ),
                "warning": None,
                "assumption": f"{link['link']} probe was rejected: the nearest node is too far from the point.",
            }
        )
        return base
    translation = list(report.probes[0]["displacement_mm"])
    rotation = [0.0, 0.0, 0.0]
    rotation_note = "One probe, so the link rotation is zero."
    if len(report.probes) == 3:
        fitted, problem = _fit_rotation(report.probes)
        if fitted is None:
            rotation_note = problem or "The link rotation was not fit."
            base.update(
                {
                    "used": False,
                    "blocks_pass": True,
                    "translation": translation,
                    "rotation": None,
                    "detail": rotation_note,
                    "finding": f"{link['link']}: {rotation_note}",
                    "warning": None,
                    "assumption": f"{link['link']}: {rotation_note}",
                }
            )
            return base
        rotation = fitted
        rotation_note = f"rotation fit {_fmt_vec(rotation)} rad"
    degraded = public["degraded"]
    warning = None
    if degraded:
        warning = f"{link['link']} used a degraded mesh. Displacement from it is low, so it cannot support a pass."
    detail = (
        f"strength check at {link['anchor']}: node { _fmt_vec(report.probes[0]['node_mm']) } mm "
        f"moved {_fmt_vec(translation)} mm. {rotation_note}"
    )
    # A degraded mesh still contributes, so an over-tolerance result can fail.
    # It cannot support a pass: the elements are too stiff and the deflection is low.
    base.update(
        {
            "used": True,
            "blocks_pass": degraded,
            "translation": translation,
            "rotation": rotation,
            "origin": list(report.probes[0]["node_mm"]),
            "detail": detail,
            "finding": warning,
            "warning": warning,
            "assumption": f"{link['link']} deflection: {detail}.",
        }
    )
    return base


def _fit_rotation(probes: list[dict]) -> tuple[list[float] | None, str | None]:
    """Least squares ω from u ≈ ω × r on the two arms of a three-point probe."""
    origin = np.asarray(probes[0]["node_mm"], dtype=float)
    displacement = np.asarray(probes[0]["displacement_mm"], dtype=float)
    rows = []
    rhs = []
    arms = []
    for probe in probes[1:]:
        arm = np.asarray(probe["node_mm"], dtype=float) - origin
        arms.append(arm)
        if float(np.linalg.norm(arm)) < 0.5:
            return None, "A probe node is closer than 0.5 mm to the origin node, so the rotation was not fit."
        delta = np.asarray(probe["displacement_mm"], dtype=float) - displacement
        rows.append(_minus_skew(arm))
        rhs.append(delta)
    if float(np.linalg.norm(np.cross(arms[0], arms[1]))) < 1e-6 * float(np.linalg.norm(arms[0]) * np.linalg.norm(arms[1])):
        return None, "The three probe nodes are colinear, so the rotation was not fit."
    matrix = np.vstack(rows)
    omega, *_rest = np.linalg.lstsq(matrix, np.concatenate(rhs), rcond=None)
    return [float(component) for component in omega], None


def _minus_skew(arm: np.ndarray) -> np.ndarray:
    rx, ry, rz = (float(component) for component in arm)
    return np.array(
        [
            [0.0, rz, -ry],
            [-rz, 0.0, rx],
            [ry, -rx, 0.0],
        ],
        dtype=float,
    )


def _anchor(spec, link, at, tool, tool_at) -> tuple[list[float], str]:
    if at in (None, "tool"):
        if link == tool:
            return list(tool_at), "tool"
        if at == "tool":
            raise GeometryError(f"Link {link!r} is not the tool, so at: tool is in another instance's frame.")
        outgoing = [joint for joint in spec["joints"] if joint["parent"] == link]
        if len(outgoing) != 1:
            raise GeometryError(f"Link {link!r} needs at: one joint name, or tool when this instance is the tool.")
        return list(outgoing[0]["parent_anchor"]), outgoing[0]["name"]
    if not isinstance(at, str) or not at.strip():
        raise GeometryError(f"Link {link!r} at must be a joint name or tool.")
    at = at.strip()
    if at == "tool":
        if link != tool:
            raise GeometryError(f"Link {link!r} is not the tool, so at: tool is in another instance's frame.")
        return list(tool_at), "tool"
    joint = next((item for item in spec["joints"] if item["name"] == at), None)
    if joint is None:
        raise GeometryError(f"Link {link!r} names joint {at!r}, which is not in the assembly.")
    if joint["parent"] != link:
        raise GeometryError(f"Joint {at!r} is not parented on {link!r}. The probe has to sit in that link's frame.")
    return list(joint["parent_anchor"]), joint["name"]


def _chain_joint_names(spec, tool) -> set[str]:
    from aicad.chain import _chain

    return {joint["name"] for joint in _chain(spec, tool)}


def _chain_instances(spec, tool) -> set[str]:
    from aicad.chain import _chain

    names = {tool}
    for joint in _chain(spec, tool):
        names.add(joint["parent"])
        names.add(joint["child"])
    return names


def _large_screw(name, translation, rotation) -> str | None:
    if float(np.linalg.norm(rotation)) > 0.05:
        return f"{name} rotation is large for a linear screw."
    if float(np.linalg.norm(translation)) > 5.0:
        return f"{name} translation is large for a linear screw."
    return None


def _counts(value, where) -> float:
    counts = finite_number(value, f"{where} encoder counts", positive=True, bound=None, unit="counts")
    if abs(counts - round(counts)) > 1e-6:
        raise GeometryError(f"Encoder counts on {where} must be a whole number.")
    return float(round(counts))


def _rotation(value) -> list[float]:
    if value is None:
        return [0.0, 0.0, 0.0]
    return [float(component) for component in finite_vec3(value, "Tool rotation_deg", unit="deg")]


def _rotation_rad(value, label) -> tuple[float, float, float]:
    return finite_vec3(value, label, bound=None, unit="rad")


def _number_map(value, label) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise GeometryError(f"{label} must map names to numbers.")
    cleaned = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise GeometryError(f"{label} names must be text.")
        cleaned[key] = finite_number(item, f"{label} {key}", bound=None, unit="1")
    return cleaned


def _unknown(names: set[str], label: str) -> None:
    if names:
        raise GeometryError(f"{label} has unknown fields: {', '.join(sorted(names))}.")


def _num(value: float) -> float:
    return round(float(value), 9)


def _vec(value) -> list[float]:
    return [_num(component) for component in value]


def _fmt_vec(value) -> str:
    return "[" + ", ".join(f"{float(component):.6g}" for component in value) + "]"


def _unique(items: list[str]) -> list[str]:
    ordered = []
    for item in items:
        if item and item not in ordered:
            ordered.append(item)
    return ordered
