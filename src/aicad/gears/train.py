"""Speed and torque for a gear train.

The assembly stores the train: shafts, meshes, which shafts are held, and any
open differential. Tooth counts come from gears the generator built. An
internal ring is a tooth count on the shaft, because that solid is not built.

An external mesh reverses the two shafts relative to their carrier. An
internal mesh turns them the same way. A carrier of zero is the housing, so
a fixed-axis mesh is the ordinary case. Gears listed on one shaft share one
speed. An idler is one gear on its own shaft between two meshes, so its tooth
count drops out of the overall ratio.

A planetary mesh names the carrier shaft. With the ring held, the carrier
turns at ``z_sun / (z_sun + z_ring)`` of the sun.

An open differential is its own element, not two ordinary meshes. The two
output speeds average to the carrier speed, so in a turn one speeds up as
much as the other slows down. The outputs share the carrier torque. A mesh
that drives the carrier supplies that torque, so the carrier is not a second
external load unless the drive lists it as an output.

The speed solve is a set of linear equations on the shaft speeds. One answer
is determined. A free shaft is under-constrained. Speeds that disagree are
over-constrained. Torque is solved only after the speeds are determined.
Positive torque is applied to the shaft in the positive speed direction.
Power is that torque times the speed. At efficiency 1 the powers sum to zero.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

from aicad.errors import GeometryError
from aicad.honesty import ROUGH_ESTIMATE, VERIFIED_CHECK, finish

_SHAFT_KEYS = {"name", "gears", "joint"}
_GEAR_KEYS = {"name", "instance", "teeth"}
_MESH_KEYS = {"name", "a", "b", "kind", "carrier", "efficiency"}
_DIFF_KEYS = {"name", "carrier", "left", "right", "efficiency"}
_TRAIN_KEYS = {"shafts", "meshes", "fixed", "differentials"}
_DRIVE_KEYS = {"inputs", "speeds", "outputs", "idle"}
_INPUT_KEYS = {"shaft", "speed_rpm", "torque_nm"}
_SPEED_KEYS = {"shaft", "speed_rpm"}
_KINDS = {"external": 1, "internal": -1}


def normalize_gear_train(raw, joints: list | None = None, instances: list | None = None) -> dict:
    """Clean a gear train. Tooth counts of generated gears are checked at solve time."""
    if not isinstance(raw, dict):
        raise GeometryError(
            "A gear train must be an object of shafts, meshes, fixed members, and differentials."
        )
    _reject_unknown(raw, _TRAIN_KEYS, "A gear train")
    joint_index = None if joints is None else {joint["name"]: joint for joint in joints}
    instance_names = None if instances is None else set(instances)

    shafts = []
    seen_shafts: set[str] = set()
    seen_gears: set[str] = set()
    gear_shaft: dict[str, str] = {}
    for index, shaft in enumerate(_list(raw.get("shafts"), "shafts")):
        if not isinstance(shaft, dict):
            raise GeometryError(f"Shaft {index + 1} must be an object.")
        _reject_unknown(shaft, _SHAFT_KEYS, f"Shaft {index + 1}")
        name = _ident(shaft.get("name"), f"Shaft {index + 1}")
        if name in seen_shafts:
            raise GeometryError(f"Two shafts are named {name!r}.")
        seen_shafts.add(name)
        gears = []
        for gear_index, gear in enumerate(_list(shaft.get("gears") or [], f"Shaft {name} gears")):
            gears.append(_gear(gear, gear_index, name, seen_gears, gear_shaft, instance_names))
        record: dict[str, Any] = {"name": name, "gears": gears}
        joint_name = shaft.get("joint")
        if joint_name not in (None, ""):
            joint_name = _ident(joint_name, f"Shaft {name} joint")
            if joint_index is not None:
                joint = joint_index.get(joint_name)
                if joint is None:
                    known = ", ".join(joint_index) or "(none)"
                    raise GeometryError(
                        f"Shaft {name!r} joint {joint_name!r} is not in this assembly. Joints: {known}."
                    )
                if joint["type"] not in ("revolute", "cylindrical"):
                    raise GeometryError(
                        f"Shaft {name!r} joint {joint_name!r} is {joint['type']}. "
                        "A shaft turns on a revolute or a cylindrical joint."
                    )
            record["joint"] = joint_name
        shafts.append(record)
    if not shafts:
        raise GeometryError("A gear train needs at least one shaft.")

    meshes = []
    seen_meshes: set[str] = set()
    for index, mesh in enumerate(_list(raw.get("meshes") or [], "meshes")):
        if not isinstance(mesh, dict):
            raise GeometryError(f"Mesh {index + 1} must be an object.")
        _reject_unknown(mesh, _MESH_KEYS, f"Mesh {index + 1}")
        name = _ident(mesh.get("name") or f"mesh_{index + 1}", f"Mesh {index + 1}")
        if name in seen_meshes:
            raise GeometryError(f"Two meshes are named {name!r}.")
        seen_meshes.add(name)
        left = _ident(mesh.get("a"), f"Mesh {name} a")
        right = _ident(mesh.get("b"), f"Mesh {name} b")
        if left not in gear_shaft or right not in gear_shaft:
            missing = left if left not in gear_shaft else right
            known = ", ".join(sorted(gear_shaft)) or "(none)"
            raise GeometryError(f"Mesh {name!r} gear {missing!r} is not on a shaft. Gears: {known}.")
        if gear_shaft[left] == gear_shaft[right]:
            raise GeometryError(
                f"Mesh {name!r} joins two gears on shaft {gear_shaft[left]!r}. A mesh connects two shafts."
            )
        kind = str(mesh.get("kind") or "external").strip().lower().replace("-", "_").replace(" ", "_")
        if kind not in _KINDS:
            raise GeometryError(f"Mesh {name!r} kind {kind!r} is unknown. Use external or internal.")
        carrier = mesh.get("carrier")
        if carrier not in (None, ""):
            carrier = _ident(carrier, f"Mesh {name} carrier")
            if carrier not in seen_shafts:
                raise GeometryError(f"Mesh {name!r} carrier {carrier!r} is not a shaft.")
            if carrier in (gear_shaft[left], gear_shaft[right]):
                raise GeometryError(
                    f"Mesh {name!r} carrier {carrier!r} is one of the meshing shafts. "
                    "The carrier is the arm the mesh orbits with."
                )
        else:
            carrier = None
        meshes.append(
            {
                "name": name,
                "a": left,
                "b": right,
                "kind": kind,
                "carrier": carrier,
                "efficiency": _efficiency(mesh.get("efficiency"), f"Mesh {name}"),
            }
        )

    differentials = []
    seen_diffs: set[str] = set()
    for index, item in enumerate(_list(raw.get("differentials") or [], "differentials")):
        if not isinstance(item, dict):
            raise GeometryError(f"Differential {index + 1} must be an object.")
        _reject_unknown(item, _DIFF_KEYS, f"Differential {index + 1}")
        name = _ident(item.get("name") or f"differential_{index + 1}", f"Differential {index + 1}")
        if name in seen_diffs:
            raise GeometryError(f"Two differentials are named {name!r}.")
        seen_diffs.add(name)
        carrier = _ident(item.get("carrier"), f"Differential {name} carrier")
        left = _ident(item.get("left"), f"Differential {name} left")
        right = _ident(item.get("right"), f"Differential {name} right")
        for label, shaft_name in (("carrier", carrier), ("left", left), ("right", right)):
            if shaft_name not in seen_shafts:
                raise GeometryError(f"Differential {name!r} {label} {shaft_name!r} is not a shaft.")
        if len({carrier, left, right}) < 3:
            raise GeometryError(
                f"Differential {name!r} needs three different shafts: carrier, left, and right."
            )
        differentials.append(
            {
                "name": name,
                "carrier": carrier,
                "left": left,
                "right": right,
                "efficiency": _efficiency(item.get("efficiency"), f"Differential {name}"),
            }
        )

    fixed = []
    seen_fixed: set[str] = set()
    for index, name in enumerate(_list(raw.get("fixed") or [], "fixed")):
        ident = _ident(name, f"Fixed member {index + 1}")
        if ident not in seen_shafts:
            raise GeometryError(f"Fixed member {ident!r} is not a shaft.")
        if ident in seen_fixed:
            raise GeometryError(f"Shaft {ident!r} is listed fixed twice.")
        seen_fixed.add(ident)
        fixed.append(ident)

    if not meshes and not differentials:
        raise GeometryError("A gear train needs a mesh or an open differential.")
    return {"shafts": shafts, "meshes": meshes, "fixed": fixed, "differentials": differentials}


def solve_gear_train(assembly: dict, drive: dict, gear_specs: dict | None = None) -> dict:
    """Speed and torque of every shaft.

    ``gear_specs`` maps an instance name to the generated gear spec. A member
    with its own ``teeth`` does not need one. Those two counts have to agree
    when both are present.
    """
    if not isinstance(assembly, dict):
        raise GeometryError("A gear train solve needs the assembly.")
    raw = assembly.get("gear_train")
    if not raw:
        raise GeometryError(
            f"Assembly {assembly.get('name', '')!r} has no gear train. "
            "Add shafts, meshes, and the fixed members to the assembly."
        )
    instance_names = None
    if assembly.get("instances") is not None:
        instance_names = [
            item["name"] if isinstance(item, dict) else str(item) for item in assembly["instances"]
        ]
    train = normalize_gear_train(raw, assembly.get("joints"), instance_names)
    teeth = _teeth(train, gear_specs or {})
    command = _drive(drive, train)
    return _report(assembly.get("name"), train, command, teeth)


def _report(assembly_name, train: dict, drive: dict, teeth: dict[str, int]) -> dict:
    shaft_names = [shaft["name"] for shaft in train["shafts"]]
    index = {name: position for position, name in enumerate(shaft_names)}
    gear_shaft = {gear["name"]: shaft["name"] for shaft in train["shafts"] for gear in shaft["gears"]}
    rows = _speed_rows(train, drive, teeth, gear_shaft, index, len(shaft_names))
    reduced, pivots, conflicts = _rref(rows, len(shaft_names))
    speeds, determined, basis = _speeds_from(reduced, pivots, len(shaft_names))
    status, note, dof = _speed_status(shaft_names, determined, basis, conflicts, drive)
    roles = _roles(train, drive, gear_shaft)
    reference = drive["inputs"][0] if len(drive["inputs"]) == 1 else None
    torque = _torques(train, drive, teeth, gear_shaft, roles, speeds, determined, status)

    shaft_rows = []
    for position, shaft in enumerate(train["shafts"]):
        name = shaft["name"]
        row: dict[str, Any] = {
            "name": name,
            "role": roles[name],
            "joint": shaft.get("joint"),
            "gears": [
                {"name": gear["name"], "teeth": teeth[gear["name"]], "instance": gear.get("instance")}
                for gear in shaft["gears"]
            ],
            "speed_rpm": _number(speeds[position]) if determined[position] and status != "over_constrained" else None,
            "determined": bool(determined[position]) and status != "over_constrained",
        }
        ratio = None
        if row["determined"] and reference is not None and reference["speed"] != 0:
            ratio = speeds[position] / reference["speed"]
        row["speed_ratio"] = None if ratio is None else _number(ratio)
        row["speed_ratio_exact"] = None if ratio is None else _exact(ratio)
        torque_value = torque["shafts"].get(name)
        row["torque_nm"] = None if torque_value is None else _number(torque_value)
        if torque_value is not None and row["determined"]:
            row["power_w"] = round(float(torque_value * speeds[position]) * 2.0 * math.pi / 60.0, 6)
        else:
            row["power_w"] = None
        shaft_rows.append(row)

    meshes = []
    for mesh in train["meshes"]:
        sense = _KINDS[mesh["kind"]]
        ratio = Fraction(-teeth[mesh["a"]], sense * teeth[mesh["b"]])
        meshes.append(
            {
                "name": mesh["name"],
                "a": mesh["a"],
                "b": mesh["b"],
                "shaft_a": gear_shaft[mesh["a"]],
                "shaft_b": gear_shaft[mesh["b"]],
                "teeth_a": teeth[mesh["a"]],
                "teeth_b": teeth[mesh["b"]],
                "kind": mesh["kind"],
                "carrier": mesh["carrier"],
                "efficiency": mesh["efficiency"],
                "relative_ratio_exact": _exact(ratio),
            }
        )
    differentials = [
        {
            "name": item["name"],
            "carrier": item["carrier"],
            "left": item["left"],
            "right": item["right"],
            "efficiency": item["efficiency"],
            "constraint": "The two output speeds average to the carrier speed.",
        }
        for item in train["differentials"]
    ]
    lossy = _lossy(train)
    text = note if torque["note"] is None else f"{note} {torque['note']}"
    payload = {
        "assembly": assembly_name,
        "status": status,
        "degrees_of_freedom": dof,
        "note": text,
        "reference_shaft": None if reference is None else reference["shaft"],
        "free_shafts": [] if status == "over_constrained" else [shaft_names[i] for i, flag in enumerate(determined) if not flag],
        "conflicts": conflicts,
        "shafts": shaft_rows,
        "meshes": meshes,
        "differentials": differentials,
        "fixed": list(train["fixed"]),
        "housing_torque_nm": None if torque["housing"] is None else _number(torque["housing"]),
        "torque_balance_nm": None if torque["balance"] is None else _number(torque["balance"]),
        "power_balance_w": None if torque["power"] is None else round(float(torque["power"]) * 2.0 * math.pi / 60.0, 6),
        "torque_status": torque["status"],
        "assumptions": _assumptions(),
    }
    if status == "determined" and torque["status"] == "determined" and not lossy:
        kind = VERIFIED_CHECK
    elif lossy:
        kind = ROUGH_ESTIMATE
    else:
        kind = VERIFIED_CHECK
    what = (
        "Rigid-body speed and torque from the tooth counts and the mesh graph. "
        "A fixed member is held. An open differential keeps the average of its two output speeds equal to the carrier."
    )
    if status == "under_constrained":
        what = "The shaft speeds are not determined. " + what
    elif status == "over_constrained":
        what = "The stated speeds disagree. " + what
    return finish(payload, kind=kind, name=str(assembly_name or "gear train"), what=what, limits=_limits())


def _speed_rows(train, drive, teeth, gear_shaft, index, width):
    rows = []
    for name in train["fixed"]:
        coeffs = _zeros(width)
        coeffs[index[name]] = Fraction(1)
        rows.append(_row(f"shaft {name} fixed at 0 rpm", coeffs, Fraction(0)))
    for item in drive["inputs"]:
        coeffs = _zeros(width)
        coeffs[index[item["shaft"]]] = Fraction(1)
        rows.append(_row(f"shaft {item['shaft']} driven at {_exact(item['speed'])} rpm", coeffs, item["speed"]))
    for item in drive["speeds"]:
        coeffs = _zeros(width)
        coeffs[index[item["shaft"]]] = Fraction(1)
        rows.append(_row(f"shaft {item['shaft']} set to {_exact(item['speed'])} rpm", coeffs, item["speed"]))
    for mesh in train["meshes"]:
        sense = _KINDS[mesh["kind"]]
        coeffs = _zeros(width)
        left = gear_shaft[mesh["a"]]
        right = gear_shaft[mesh["b"]]
        za = teeth[mesh["a"]]
        zb = teeth[mesh["b"]]
        coeffs[index[left]] += Fraction(za)
        coeffs[index[right]] += Fraction(sense * zb)
        carrier = mesh["carrier"]
        if carrier is not None:
            coeffs[index[carrier]] -= Fraction(za + sense * zb)
        where = "the housing" if carrier is None else f"carrier {carrier}"
        label = (
            f"mesh {mesh['name']}: {mesh['a']} ({za} teeth) and {mesh['b']} ({zb} teeth) "
            f"is an {mesh['kind']} mesh about {where}"
        )
        rows.append(_row(label, coeffs, Fraction(0)))
    for item in train["differentials"]:
        coeffs = _zeros(width)
        coeffs[index[item["left"]]] = Fraction(1)
        coeffs[index[item["right"]]] = Fraction(1)
        coeffs[index[item["carrier"]]] = Fraction(-2)
        rows.append(
            _row(
                f"open differential {item['name']}: the two output speeds have to average to the carrier speed",
                coeffs,
                Fraction(0),
            )
        )
    return rows


def _speed_status(names, determined, basis, conflicts, drive):
    if conflicts:
        detail = " ".join(conflicts)
        return "over_constrained", f"The train is over-constrained. The speeds cannot all be true. {detail}.", None
    if basis:
        motions = "; ".join(_motion_phrase(names, vector) for vector in basis)
        freedom = "1 degree of freedom" if len(basis) == 1 else f"{len(basis)} degrees of freedom"
        listed = ", ".join(names[i] for i, flag in enumerate(determined) if not flag)
        note = (
            f"The train is under-constrained by {freedom}. {motions}. "
            f"Give a speed for one of: {listed}."
        )
        return "under_constrained", note, len(basis)
    bits = []
    if len(drive["inputs"]) == 1:
        bits.append(f"{drive['inputs'][0]['shaft']} is the driven member")
    for name in sorted(drive["fixed_names"]):
        bits.append(f"{name} is fixed")
    note = "The train is determined."
    if bits:
        note = note + " " + ". ".join(bits) + "."
    return "determined", note, 0


def _roles(train, drive, gear_shaft) -> dict[str, str]:
    incidence = {shaft["name"]: 0 for shaft in train["shafts"]}
    mesh_carriers: set[str] = set()
    for mesh in train["meshes"]:
        incidence[gear_shaft[mesh["a"]]] += 1
        incidence[gear_shaft[mesh["b"]]] += 1
        if mesh["carrier"]:
            mesh_carriers.add(mesh["carrier"])
    diff_sides: set[str] = set()
    diff_carriers: set[str] = set()
    for item in train["differentials"]:
        diff_sides.add(item["left"])
        diff_sides.add(item["right"])
        diff_carriers.add(item["carrier"])
    roles = {}
    for shaft in train["shafts"]:
        name = shaft["name"]
        if name in drive["input_names"]:
            roles[name] = "input"
        elif name in drive["fixed_names"]:
            roles[name] = "fixed"
        elif name in drive["outputs"]:
            roles[name] = "output"
        elif name in drive["idle"]:
            roles[name] = "idle"
        elif name in diff_sides:
            roles[name] = "output"
        elif name in mesh_carriers and name not in diff_carriers:
            roles[name] = "output"
        elif name in diff_carriers and incidence[name] == 0:
            roles[name] = "output"
        elif name in diff_carriers:
            # A mesh on the carrier already applies its torque. An extra
            # external load would leave the axle torques free.
            roles[name] = "idle"
        elif incidence[name] == 1:
            roles[name] = "output"
        else:
            roles[name] = "idle"
    return roles


def _torques(train, drive, teeth, gear_shaft, roles, speeds, determined, speed_status):
    empty = {
        "status": "not_solved",
        "note": None,
        "shafts": {},
        "housing": None,
        "balance": None,
        "power": None,
    }
    if speed_status != "determined":
        empty["note"] = "Torque is not solved until the speeds are determined."
        return empty
    given = [item for item in drive["inputs"] if item["torque"] is not None]
    missing = [item["shaft"] for item in drive["inputs"] if item["torque"] is None]
    if missing and given:
        raise GeometryError(
            "Give torque_nm for every driven shaft, or leave every torque out. "
            f"Missing torque on {', '.join(missing)}."
        )
    if not given:
        empty["note"] = "No input torque was given, so torque is not solved."
        return empty
    elements = _elements(train, teeth, gear_shaft)
    speed_of = {
        shaft["name"]: speeds[position] for position, shaft in enumerate(train["shafts"]) if determined[position]
    }
    ideal = _torque_system(elements, train, roles, drive, speed_of, factors=None)
    if ideal["status"] != "determined" or not _lossy(train):
        return ideal
    factors = _receiver_factors(elements, ideal["lambda"], speed_of)
    lossy = _torque_system(elements, train, roles, drive, speed_of, factors)
    lossy["note"] = "Mesh efficiency scales the delivered torque. Power that does not reach the outputs is heat."
    return lossy


def _elements(train, teeth, gear_shaft):
    elements = []
    for mesh in train["meshes"]:
        sense = _KINDS[mesh["kind"]]
        za = Fraction(teeth[mesh["a"]])
        zb = Fraction(sense * teeth[mesh["b"]])
        elements.append(
            {
                "id": f"mesh:{mesh['name']}",
                "efficiency": _as_fraction(mesh["efficiency"]),
                "terms": [
                    {"shaft": gear_shaft[mesh["a"]], "coeff": za, "key": "a"},
                    {"shaft": gear_shaft[mesh["b"]], "coeff": zb, "key": "b"},
                    {"shaft": mesh["carrier"], "coeff": -(za + zb), "key": "carrier"},
                ],
            }
        )
    for item in train["differentials"]:
        elements.append(
            {
                "id": f"diff:{item['name']}",
                "efficiency": _as_fraction(item["efficiency"]),
                "terms": [
                    {"shaft": item["left"], "coeff": Fraction(1), "key": "left"},
                    {"shaft": item["right"], "coeff": Fraction(1), "key": "right"},
                    {"shaft": item["carrier"], "coeff": Fraction(-2), "key": "carrier"},
                ],
            }
        )
    return elements


def _torque_system(elements, train, roles, drive, speed_of, factors):
    shaft_names = [shaft["name"] for shaft in train["shafts"]]
    variables = [element["id"] for element in elements]
    unknown = [name for name in shaft_names if roles[name] in ("output", "fixed")]
    variables.extend(f"torque:{name}" for name in unknown)
    columns = {name: position for position, name in enumerate(variables)}
    known = {item["shaft"]: item["torque"] for item in drive["inputs"] if item["torque"] is not None}
    for name, role in roles.items():
        if role == "idle":
            known[name] = Fraction(0)
    rows = []
    for name in shaft_names:
        coeffs = _zeros(len(variables))
        for element in elements:
            column = columns[element["id"]]
            for term in element["terms"]:
                if term["shaft"] != name:
                    continue
                eta = Fraction(1) if factors is None else factors[(element["id"], term["key"])]
                coeffs[column] += term["coeff"] * eta
        if name in known:
            rows.append(_row(f"shaft {name} torque balance", coeffs, -known[name]))
        else:
            coeffs[columns[f"torque:{name}"]] += Fraction(1)
            rows.append(_row(f"shaft {name} torque balance", coeffs, Fraction(0)))
    reduced, pivots, conflicts = _rref(rows, len(variables))
    values, flags, _basis = _speeds_from(reduced, pivots, len(variables))
    if conflicts:
        return {
            "status": "over_constrained",
            "note": "The torques are over-constrained. " + " ".join(conflicts) + ".",
            "shafts": {},
            "housing": None,
            "balance": None,
            "power": None,
            "lambda": {},
        }
    if any(not flag for flag in flags):
        free = [variables[i] for i, flag in enumerate(flags) if not flag]
        return {
            "status": "under_constrained",
            "note": "The torques are under-constrained. " + f"These are still free: {', '.join(free)}.",
            "shafts": {},
            "housing": None,
            "balance": None,
            "power": None,
            "lambda": {},
        }
    solved = {variables[i]: values[i] for i in range(len(variables))}
    torques = {name: known[name] if name in known else solved[f"torque:{name}"] for name in shaft_names}
    housing = Fraction(0)
    for element in elements:
        for term in element["terms"]:
            if term["shaft"] is not None:
                continue
            eta = Fraction(1) if factors is None else factors[(element["id"], term["key"])]
            housing -= solved[element["id"]] * term["coeff"] * eta
    if factors is not None:
        housing = -sum(torques.values(), Fraction(0))
    balance = sum(torques.values(), Fraction(0)) + housing
    power = sum((torques[name] * speed_of[name] for name in shaft_names), Fraction(0))
    return {
        "status": "determined",
        "note": None,
        "shafts": torques,
        "housing": housing,
        "balance": balance,
        "power": power,
        "lambda": {element["id"]: solved[element["id"]] for element in elements},
    }


def _receiver_factors(elements, lambdas, speed_of):
    factors = {}
    for element in elements:
        lam = lambdas[element["id"]]
        powers = []
        for term in element["terms"]:
            if term["shaft"] is None:
                powers.append(Fraction(0))
            else:
                powers.append(lam * term["coeff"] * speed_of[term["shaft"]])
        if any(power > 0 for power in powers):
            for term, power in zip(element["terms"], powers):
                factors[(element["id"], term["key"])] = element["efficiency"] if power > 0 else Fraction(1)
        else:
            for term in element["terms"]:
                factors[(element["id"], term["key"])] = Fraction(1)
    return factors


def _teeth(train, gear_specs: dict) -> dict[str, int]:
    teeth = {}
    for shaft in train["shafts"]:
        for gear in shaft["gears"]:
            stated = gear.get("teeth")
            instance = gear.get("instance")
            counted = None
            if instance:
                spec = gear_specs.get(instance)
                if spec:
                    counted = int(spec["teeth"])
                elif stated is None:
                    raise GeometryError(
                        f"Gear {gear['name']!r} on {instance!r} is not a generated gear. "
                        "Build it with the gear generator, or state teeth for a member that generator does not build."
                    )
            if stated is not None and counted is not None and int(stated) != counted:
                raise GeometryError(
                    f"Gear {gear['name']!r} states {int(stated)} teeth, and the generated gear has {counted}."
                )
            value = counted if counted is not None else stated
            if value is None:
                raise GeometryError(f"Gear {gear['name']!r} needs teeth or a generated gear.")
            teeth[gear["name"]] = int(value)
    return teeth


def _gear(gear, index: int, shaft: str, seen: set[str], gear_shaft: dict[str, str], instances) -> dict:
    if isinstance(gear, str):
        gear = {"name": gear, "instance": gear}
    if not isinstance(gear, dict):
        raise GeometryError(f"Shaft {shaft} gear {index + 1} must be a name or an object.")
    _reject_unknown(gear, _GEAR_KEYS, f"Shaft {shaft} gear {index + 1}")
    instance = gear.get("instance")
    if instance in (None, ""):
        instance = None
    else:
        instance = _ident(instance, f"Shaft {shaft} gear {index + 1} instance")
        if instances is not None and instance not in instances:
            known = ", ".join(sorted(instances)) or "(none)"
            raise GeometryError(
                f"Shaft {shaft} gear instance {instance!r} is not in this assembly. Instances: {known}."
            )
    name = gear.get("name")
    if name in (None, ""):
        if instance is None:
            raise GeometryError(f"Shaft {shaft} gear {index + 1} needs a name or an instance.")
        name = instance
    name = _ident(name, f"Shaft {shaft} gear {index + 1}")
    if name in seen:
        raise GeometryError(f"Two gears are named {name!r}.")
    seen.add(name)
    gear_shaft[name] = shaft
    record: dict[str, Any] = {"name": name}
    if instance is not None:
        record["instance"] = instance
    if gear.get("teeth") is not None:
        record["teeth"] = _tooth_count(gear.get("teeth"), name)
    elif instance is None:
        raise GeometryError(
            f"Gear {name!r} needs teeth. An internal ring is a tooth count, because that solid is not generated."
        )
    return record


def _drive(raw, train) -> dict:
    if not isinstance(raw, dict):
        raise GeometryError(
            "The drive must be an object with inputs and, for a differential in a turn, speeds."
        )
    _reject_unknown(raw, _DRIVE_KEYS, "The drive")
    shafts = {shaft["name"] for shaft in train["shafts"]}
    fixed = set(train["fixed"])
    inputs = []
    seen: set[str] = set()
    for index, item in enumerate(_list(raw.get("inputs") or [], "inputs")):
        if not isinstance(item, dict):
            raise GeometryError(f"Input {index + 1} must be an object.")
        _reject_unknown(item, _INPUT_KEYS, f"Input {index + 1}")
        name = _ident(item.get("shaft"), f"Input {index + 1} shaft")
        if name not in shafts:
            raise GeometryError(f"Input shaft {name!r} is not in this train.")
        if name in fixed:
            raise GeometryError(f"Shaft {name!r} is fixed, so it cannot be driven.")
        if name in seen:
            raise GeometryError(f"Shaft {name!r} is driven twice.")
        seen.add(name)
        torque = item.get("torque_nm")
        inputs.append(
            {
                "shaft": name,
                "speed": _fraction(item.get("speed_rpm"), f"Input {name} speed_rpm"),
                "torque": None if torque is None else _fraction(torque, f"Input {name} torque_nm"),
            }
        )
    speeds = []
    seen_speeds = set(seen)
    for index, item in enumerate(_list(raw.get("speeds") or [], "speeds")):
        if not isinstance(item, dict):
            raise GeometryError(f"Speed {index + 1} must be an object.")
        _reject_unknown(item, _SPEED_KEYS, f"Speed {index + 1}")
        name = _ident(item.get("shaft"), f"Speed {index + 1} shaft")
        if name not in shafts:
            raise GeometryError(f"Speed shaft {name!r} is not in this train.")
        if name in seen_speeds:
            raise GeometryError(f"Shaft {name!r} already has a speed.")
        seen_speeds.add(name)
        speeds.append({"shaft": name, "speed": _fraction(item.get("speed_rpm"), f"Shaft {name} speed_rpm")})
    outputs = _name_set(raw.get("outputs") or [], "outputs", shafts)
    idle = _name_set(raw.get("idle") or [], "idle", shafts)
    overlap = outputs & idle
    if overlap:
        raise GeometryError(f"A shaft cannot be both an output and idle: {', '.join(sorted(overlap))}.")
    if not inputs and not speeds and not fixed:
        raise GeometryError("Give an input speed, or fix a member. Nothing here sets the speed.")
    return {
        "inputs": inputs,
        "speeds": speeds,
        "outputs": outputs,
        "idle": idle,
        "input_names": {item["shaft"] for item in inputs},
        "fixed_names": fixed,
    }


def _rref(rows: list[dict], width: int):
    matrix = [{"label": row["label"], "coeffs": list(row["coeffs"]), "rhs": row["rhs"]} for row in rows]
    pivot_row = 0
    pivots: dict[int, int] = {}
    for column in range(width):
        pivot = None
        for index in range(pivot_row, len(matrix)):
            if matrix[index]["coeffs"][column] != 0:
                pivot = index
                break
        if pivot is None:
            continue
        matrix[pivot_row], matrix[pivot] = matrix[pivot], matrix[pivot_row]
        lead = matrix[pivot_row]["coeffs"][column]
        matrix[pivot_row]["coeffs"] = [value / lead for value in matrix[pivot_row]["coeffs"]]
        matrix[pivot_row]["rhs"] = matrix[pivot_row]["rhs"] / lead
        for index in range(len(matrix)):
            if index == pivot_row:
                continue
            factor = matrix[index]["coeffs"][column]
            if factor == 0:
                continue
            matrix[index]["coeffs"] = [
                value - factor * other
                for value, other in zip(matrix[index]["coeffs"], matrix[pivot_row]["coeffs"])
            ]
            matrix[index]["rhs"] = matrix[index]["rhs"] - factor * matrix[pivot_row]["rhs"]
        pivots[column] = pivot_row
        pivot_row += 1
    conflicts = []
    for row in matrix:
        if all(value == 0 for value in row["coeffs"]) and row["rhs"] != 0:
            conflicts.append(row["label"])
    return matrix, pivots, conflicts


def _speeds_from(matrix, pivots, width):
    free = [column for column in range(width) if column not in pivots]
    determined = [False] * width
    values = [Fraction(0)] * width
    for column, row_index in pivots.items():
        row = matrix[row_index]
        if any(row["coeffs"][item] != 0 for item in free):
            continue
        determined[column] = True
        values[column] = row["rhs"]
    basis = []
    for free_column in free:
        vector = [Fraction(0)] * width
        vector[free_column] = Fraction(1)
        for column, row_index in pivots.items():
            vector[column] = -matrix[row_index]["coeffs"][free_column]
        basis.append(vector)
    return values, determined, basis


def _motion_phrase(names, vector) -> str:
    pairs = [(name, vector[index]) for index, name in enumerate(names) if vector[index] != 0]
    if not pairs:
        return "a free motion with no shaft speed"
    scale = pairs[0][1]
    pairs = [(name, coeff / scale) for name, coeff in pairs]
    if len(pairs) == 2 and pairs[0][1] == 1 and pairs[1][1] == -1:
        return f"{pairs[0][0]} speeds up as much as {pairs[1][0]} slows down"
    bits = [f"{name} {_exact(coeff)}" for name, coeff in pairs]
    return "shafts change together: " + ", ".join(bits)


def _assumptions() -> list[str]:
    return [
        "Tooth counts are the generated gear's count, or the count stated for a member the generator does not build.",
        "Shafts are rigid. Gears on one shaft share one speed.",
        "An external mesh turns the two gears in opposite directions relative to the carrier. An internal mesh turns them the same way. The ratio is the tooth counts. A fixed-axis mesh uses the housing as its carrier.",
        "An open differential sets the average of the two output speeds equal to the carrier speed. The outputs share the carrier torque. In a turn, one output speeds up as much as the other slows down.",
        "Efficiency is a factor on the torque delivered through that mesh or differential. 1 keeps power. It is not a measured loss.",
        "Bearings, seals, tooth bending, and shaft twist are omitted. Inertia is omitted. The result is a steady speed and a steady torque.",
        "Positive speed is the shaft's positive direction. Positive torque is applied to the shaft in that same direction. A load the shaft drives has the opposite torque. Power is torque times speed.",
        "The housing torque is the moment the mounts react. At efficiency 1 it cancels the sum of the shaft torques.",
    ]


def _limits() -> list[str]:
    return [
        "This is the rigid-body ratio of the stated meshes. It does not check that the solids touch or that the centre distance matches.",
        "Backlash is not in the speed.",
        "An efficiency other than 1 is a factor you supply.",
        "An internal gear is a tooth count. The gear generator builds external teeth only.",
        "One planet stands for a set of equal planets. They share one speed, and the torque is the total.",
        "Two ordinary meshes do not make an open differential. Use the differential element so one output can speed up as the other slows down.",
    ]


def _lossy(train) -> bool:
    for mesh in train["meshes"]:
        if mesh["efficiency"] != 1:
            return True
    for item in train["differentials"]:
        if item["efficiency"] != 1:
            return True
    return False


def _tooth_count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"Gear {name!r} teeth must be an integer.")
    if isinstance(value, float) and not float(value).is_integer():
        raise GeometryError(f"Gear {name!r} teeth must be an integer.")
    teeth = int(value)
    if teeth < 1 or teeth > 100000:
        raise GeometryError(f"Gear {name!r} teeth must be from 1 to 100000.")
    return teeth


def _efficiency(value, label: str) -> float:
    if value is None:
        return 1.0
    number = _fraction(value, f"{label} efficiency")
    if number <= 0 or number > 1:
        raise GeometryError(f"{label} efficiency must be greater than 0 and at most 1.")
    return float(number)


def _name_set(raw, label: str, shafts: set[str]) -> set[str]:
    names = set()
    for index, name in enumerate(_list(raw, label)):
        ident = _ident(name, f"{label} {index + 1}")
        if ident not in shafts:
            raise GeometryError(f"{label} shaft {ident!r} is not in this train.")
        if ident in names:
            raise GeometryError(f"Shaft {ident!r} is listed in {label} twice.")
        names.add(ident)
    return names


def _fraction(value, label: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{label} must be a number.")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GeometryError(f"{label} must be finite.")
        return Fraction(value).limit_denominator(10**12)
    return Fraction(value)


def _as_fraction(value: float) -> Fraction:
    return Fraction(value).limit_denominator(10**12)


def _ident(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeometryError(f"{label} must be a name.")
    return value.strip()


def _list(value, label: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise GeometryError(f"{label} must be a list.")
    return value


def _reject_unknown(record: dict, allowed: set[str], label: str) -> None:
    extra = sorted(key for key in record if key not in allowed)
    if extra:
        known = ", ".join(sorted(allowed))
        raise GeometryError(f"{label} has unknown fields: {', '.join(extra)}. Fields: {known}.")


def _row(label: str, coeffs: list[Fraction], rhs: Fraction) -> dict:
    return {"label": label, "coeffs": coeffs, "rhs": rhs}


def _zeros(width: int) -> list[Fraction]:
    return [Fraction(0) for _ in range(width)]


def _exact(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _number(value: Fraction) -> float:
    return round(float(value), 6)
