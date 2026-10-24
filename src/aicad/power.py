"""Quasi-static joint loads and actuator estimates.

Gravity and payloads are applied at each instance. A revolute joint sees the
moment of the supported weight about its axis. A prismatic joint sees the
component of that weight along its slide. Gear and linkage relations reflect
the driven load onto the driver when the driven joint has no actuator of its
own. Nothing here integrates dynamics: inertia, friction, and pressure drop
are omitted, and the report says so.
"""

from __future__ import annotations

import math
from typing import Any

from aicad.errors import GeometryError

GRAVITY = 9.81


def joint_loads(solved: dict, bodies: dict[str, dict]) -> list[dict]:
    """``bodies`` maps instance name to mass_kg, com_mm (world), and optional payload."""
    children = {joint["child"]: joint["name"] for joint in solved["joints"]}
    descendants = _descendants(solved)
    rows = []
    for joint in solved["joints"]:
        if joint["type"] == "fixed":
            continue
        supported = descendants[joint["child"]]
        origin = joint["origin_mm"]
        axis = _unit(joint["axis"])
        moment = [0.0, 0.0, 0.0]
        force = [0.0, 0.0, 0.0]
        for name in supported:
            body = bodies.get(name)
            if body is None:
                continue
            weight = [0.0, 0.0, -body["mass_kg"] * GRAVITY]
            moment = _add(moment, _cross(_sub(body["com_mm"], origin), weight))
            force = _add(force, weight)
            payload = float(body.get("payload_kg") or 0.0)
            if payload:
                at = body.get("payload_mm") or body["com_mm"]
                extra = [0.0, 0.0, -payload * GRAVITY]
                moment = _add(moment, _cross(_sub(at, origin), extra))
                force = _add(force, extra)
        torque_nmm = _dot(moment, axis)
        axis_force = _dot(force, axis)
        rows.append(
            {
                "joint": joint["name"],
                "type": joint["type"],
                "value": joint["value"],
                "slide": joint["slide"],
                "torque_nm": round(torque_nmm / 1000.0, 4),
                "force_n": round(-axis_force, 4),
                "supported": supported,
            }
        )
    return rows


def reflect(loads: list[dict], solved: dict, spec: dict, actuators: list[dict]) -> list[dict]:
    """Add driven-joint load onto a driver that is the one with an actuator."""
    by_name = {row["joint"]: dict(row) for row in loads}
    actuated = {item["joint"] for item in actuators}
    for relation in spec.get("relations") or []:
        driven = relation["driven"]
        driver = relation["driver"]
        if driven not in by_name or driver not in by_name:
            continue
        if driven in actuated:
            continue
        ratio = _instant_ratio(relation, solved, spec)
        driven_row = by_name[driven]
        driver_row = by_name[driver]
        # Virtual work with driven = ratio * driver. Mixed units convert mm and degrees.
        driven_slide = driven_row["type"] == "prismatic"
        driver_slide = driver_row["type"] == "prismatic"
        if driven_slide and not driver_slide:
            driver_row["torque_nm"] = round(
                driver_row["torque_nm"] - driven_row["force_n"] * ratio / 1000.0 * (180.0 / math.pi),
                4,
            )
        elif driver_slide and not driven_slide:
            driver_row["force_n"] = round(
                driver_row["force_n"] - driven_row["torque_nm"] * ratio * (math.pi / 180.0) * 1000.0,
                4,
            )
        elif driven_slide:
            driver_row["force_n"] = round(driver_row["force_n"] - ratio * driven_row["force_n"], 4)
        else:
            driver_row["torque_nm"] = round(driver_row["torque_nm"] - ratio * driven_row["torque_nm"], 4)
        driver_row["reflected_from"] = driven
    return list(by_name.values())


def actuators_for(spec: dict, supplied: list[dict] | None = None) -> list[dict]:
    source = supplied if supplied is not None else spec.get("actuators") or []
    cleaned = []
    joints = {joint["name"]: joint for joint in spec.get("joints") or []}
    for index, actuator in enumerate(source):
        if not isinstance(actuator, dict):
            raise GeometryError(f"Actuator {index} must be an object.")
        joint = str(actuator.get("joint") or "")
        if joint not in joints:
            raise GeometryError(f"Actuator {index} joint {joint!r} is not in this assembly.")
        if joints[joint]["type"] == "fixed":
            raise GeometryError(f"Actuator on {joint} cannot drive a fixed joint.")
        kind = str(actuator.get("kind") or actuator.get("type") or "")
        if kind not in ("hydraulic_cylinder", "electric"):
            raise GeometryError(f"Actuator on {joint} must be hydraulic_cylinder or electric.")
        record = {
            "name": str(actuator.get("name") or f"{kind}_{joint}"),
            "joint": joint,
            "kind": kind,
            "efficiency": float(actuator.get("efficiency") or 0.85),
        }
        if record["efficiency"] <= 0 or record["efficiency"] > 1:
            raise GeometryError(f"Actuator {record['name']} efficiency must be between 0 and 1.")
        if kind == "hydraulic_cylinder":
            record["bore_mm"] = float(actuator["bore_mm"])
            record["rod_mm"] = float(actuator.get("rod_mm") or 0.0)
            record["moment_arm_mm"] = float(actuator.get("moment_arm_mm") or 0.0)
            if record["bore_mm"] <= record["rod_mm"]:
                raise GeometryError(f"{record['name']}: the rod must be smaller than the bore.")
            if joints[joint]["type"] in ("revolute", "cylindrical") and record["moment_arm_mm"] <= 0:
                raise GeometryError(f"{record['name']} needs moment_arm_mm for a revolute joint.")
        else:
            record["gear_ratio"] = float(actuator.get("gear_ratio") or 1.0)
            record["voltage"] = float(actuator.get("voltage") or 24.0)
            record["torque_constant_nm_per_a"] = actuator.get("torque_constant_nm_per_a")
            record["force_constant_n_per_a"] = actuator.get("force_constant_n_per_a")
            if record["gear_ratio"] == 0:
                raise GeometryError(f"{record['name']} gear_ratio cannot be 0.")
        cleaned.append(record)
    return cleaned


def estimate_sequence(
    frame_loads: list[dict],
    actuators: list[dict],
    duration_s: float,
) -> dict:
    """``frame_loads`` is a list of {time_s, joints: {name: {value, torque_nm, force_n, type}}}."""
    if duration_s <= 0:
        raise GeometryError("duration_s must be positive.")
    if len(frame_loads) < 1:
        raise GeometryError("Power needs at least one frame.")
    per_actuator = []
    electric_j = 0.0
    hydraulic_j = 0.0
    for actuator in actuators:
        samples = []
        previous = None
        energy = 0.0
        displaced = 0.0
        peak_pressure = 0.0
        peak_current = 0.0
        peak_power = 0.0
        peak_torque = 0.0
        peak_force = 0.0
        for frame in frame_loads:
            load = frame["joints"].get(actuator["joint"])
            if load is None:
                continue
            dt = 0.0 if previous is None else max(0.0, frame["time_s"] - previous["time_s"])
            rate = _rate(previous, load, dt)
            row = _one(actuator, load, rate)
            if previous is not None and dt > 0:
                mean_power = 0.5 * (previous["power_w"] + row["power_w"])
                energy += mean_power * dt
                displaced += abs(row.get("segment_volume_mm3") or 0.0)
            previous = {**row, "time_s": frame["time_s"], "value": load["value"], "slide": load.get("slide") or 0.0}
            peak_pressure = max(peak_pressure, row.get("pressure_mpa") or 0.0)
            peak_current = max(peak_current, abs(row.get("current_a") or 0.0))
            peak_power = max(peak_power, row["power_w"])
            peak_torque = max(peak_torque, abs(load["torque_nm"]))
            peak_force = max(peak_force, abs(load["force_n"]))
            samples.append({"time_s": frame["time_s"], **row})
        kind = actuator["kind"]
        if kind == "electric":
            electric_j += energy
        else:
            hydraulic_j += energy
        per_actuator.append(
            {
                "name": actuator["name"],
                "joint": actuator["joint"],
                "kind": kind,
                "peak_torque_nm": round(peak_torque, 4),
                "peak_force_n": round(peak_force, 3),
                "peak_pressure_mpa": round(peak_pressure, 4),
                "peak_current_a": round(peak_current, 4),
                "peak_power_w": round(peak_power, 3),
                "energy_j": round(energy, 3),
                "displaced_volume_mm3": round(displaced, 1),
                "samples": samples,
            }
        )
    voltage = next((item["voltage"] for item in actuators if item["kind"] == "electric"), None)
    battery = None
    if electric_j > 0 and voltage:
        wh = electric_j / 3600.0
        battery = {
            "energy_wh": round(wh, 5),
            "voltage": voltage,
            "charge_ah": round(wh / voltage, 6),
        }
    pump = {
        "energy_j": round(hydraulic_j, 3),
        "mean_power_w": round(hydraulic_j / duration_s, 3),
        "displaced_volume_mm3": round(sum(item["displaced_volume_mm3"] for item in per_actuator if item["kind"] == "hydraulic_cylinder"), 1),
    }
    return {
        "actuators": per_actuator,
        "pump": pump,
        "battery": battery,
        "duration_s": duration_s,
        "assumptions": _assumptions(),
    }


def _one(actuator: dict, load: dict, rate: dict) -> dict:
    efficiency = actuator["efficiency"]
    if actuator["kind"] == "hydraulic_cylinder":
        force = abs(load["force_n"])
        if load["type"] in ("revolute", "cylindrical"):
            arm = actuator["moment_arm_mm"]
            force = abs(load["torque_nm"]) * 1000.0 / arm
            travel = abs(rate["value_per_s"]) * (math.pi / 180.0) * arm
        else:
            travel = abs(rate["value_per_s"])
        extending = rate["value_per_s"] >= 0
        bore = actuator["bore_mm"]
        rod = actuator["rod_mm"]
        area = math.pi / 4.0 * (bore ** 2 if extending or rod <= 0 else (bore ** 2 - rod ** 2))
        pressure = force / area
        flow = area * travel
        power = pressure * flow / 1000.0 / efficiency
        segment = area * abs(rate["delta_value"]) * (
            (math.pi / 180.0) * actuator["moment_arm_mm"] if load["type"] in ("revolute", "cylindrical") else 1.0
        )
        return {
            "force_n": round(force, 3),
            "pressure_mpa": round(pressure, 4),
            "flow_mm3_s": round(flow, 2),
            "power_w": round(power, 3),
            "area_mm2": round(area, 2),
            "extending": extending,
            "segment_volume_mm3": segment,
        }
    ratio = actuator["gear_ratio"]
    if load["type"] == "prismatic":
        constant = actuator.get("force_constant_n_per_a")
        if not constant:
            raise GeometryError(f"{actuator['name']} needs force_constant_n_per_a for a prismatic joint.")
        motor_force = abs(load["force_n"]) / (abs(ratio) * efficiency)
        current = motor_force / float(constant)
        speed = abs(rate["value_per_s"]) / 1000.0
        power = abs(load["force_n"]) * speed / efficiency
    else:
        constant = actuator.get("torque_constant_nm_per_a")
        if not constant:
            raise GeometryError(f"{actuator['name']} needs torque_constant_nm_per_a.")
        motor_torque = abs(load["torque_nm"]) / (abs(ratio) * efficiency)
        current = motor_torque / float(constant)
        omega = abs(rate["value_per_s"]) * math.pi / 180.0
        power = abs(load["torque_nm"]) * omega / efficiency
    return {
        "current_a": round(current, 4),
        "voltage": actuator["voltage"],
        "electrical_power_w": round(actuator["voltage"] * current, 3),
        "power_w": round(power, 3),
        "segment_volume_mm3": 0.0,
    }


def _rate(previous: dict | None, load: dict, dt: float) -> dict:
    if previous is None or dt <= 0:
        return {"value_per_s": 0.0, "delta_value": 0.0}
    delta = float(load["value"]) - float(previous["value"])
    return {"value_per_s": delta / dt, "delta_value": delta}


def _instant_ratio(relation: dict, solved: dict, spec: dict) -> float:
    if relation["type"] == "gear":
        return float(relation.get("ratio") or 1.0)
    driver = next(joint["value"] for joint in solved["joints"] if joint["name"] == relation["driver"])
    step = 0.5
    if relation["type"] == "cylinder":
        from aicad.kinematics import _cylinder

        joints = {joint["name"]: joint for joint in spec["joints"]}
        slide = next(
            joint["slide"] for joint in solved["joints"] if joint["name"] == relation["driver"]
        )
        high, _extension, _offset = _cylinder(relation, joints, driver + step, slide)
        low, _extension, _offset = _cylinder(relation, joints, driver - step, slide)
        return (high - low) / (2 * step)
    from aicad.kinematics import _four_bar

    high = _four_bar(relation, driver + step)
    low = _four_bar(relation, driver - step)
    return (high - low) / (2 * step)


def _descendants(solved: dict) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for joint in solved["joints"]:
        children.setdefault(joint["parent"], []).append(joint["child"])
    cache: dict[str, list[str]] = {}

    def walk(name: str) -> list[str]:
        if name in cache:
            return cache[name]
        found = [name]
        for child in children.get(name, []):
            found.extend(walk(child))
        cache[name] = found
        return found

    for instance in solved["instances"]:
        walk(instance["name"])
    return cache


def _assumptions() -> list[str]:
    return [
        "Loads are quasi-static. Inertia, friction, seal drag, and line pressure drop are not included.",
        f"Gravity is {GRAVITY} m/s² along −Z. Mass is volume times the material density, plus payload_kg at payload_at or at the center of mass.",
        "A revolute torque is the moment of supported weight about the joint axis, in N·m. A prismatic force is the opposing component of that weight along the slide.",
        "A gear or linkage reflects the driven load onto the driver when the driven joint has no actuator. Ratio is d(driven)/d(driver).",
        "A hydraulic cylinder turns torque into force with moment_arm_mm. Pressure is force over piston area. Extend uses the bore; retract uses bore minus rod.",
        "Flow is area times the speed of the piston. Power is pressure times flow divided by efficiency. 1 MPa · mm³/s = 0.001 W.",
        "An electric motor torque is the joint torque divided by gear ratio and efficiency. Current is that torque divided by the torque constant. Power is joint torque times speed, divided by efficiency. Braking energy is counted as spent, not regenerated.",
        "Battery energy is the sum of electric actuator energy. Charge is that energy divided by the stated voltage.",
    ]


def _unit(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(component * component for component in vector)) or 1.0
    return [component / length for component in vector]


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _add(a, b) -> list[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _sub(a, b) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
