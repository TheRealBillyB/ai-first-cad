"""Constraint sketches solved by SolveSpace.

An assistant describes entities and constraints. python-solvespace (the
SolveSpace geometric constraint solver) computes the coordinates. The sketch
report says whether that description is fully constrained, still free, or
contradictory, which is the feedback a person gets from a sketcher.

This module does not import build123d. Turning a solved sketch into a solid
happens in :mod:`aicad.kernel`.

Initial ``at`` coordinates are guesses, except ``fix``, which locks that
guess. A point-to-line distance is signed: positive is to the right as you
walk from the line's start to its end. A rectangle drawn counterclockwise
has its interior on the negative side, so an inset of 6 mm is the distance −6.
The plane is XY, YZ, XZ, an offset or angled datum, or a planar face. The
solver still works in that plane's own coordinates.
"""

from __future__ import annotations

import math
from typing import Any

from python_solvespace import ResultFlag, SolverSystem

from aicad.errors import GeometryError
from aicad.models import SketchReport

_SOLVER = "SolveSpace (python-solvespace)"
_ID = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class Sketch:
    """Build a sketch spec. Initial coordinates are guesses, not the design."""

    def __init__(self, plane: str | dict = "xy"):
        from aicad.sketch_plane import normalize_plane

        self.plane = normalize_plane(plane)
        self.entities: list[dict[str, Any]] = []
        self.constraints: list[dict[str, Any]] = []

    def to_spec(self) -> dict[str, Any]:
        return {
            "plane": self.plane,
            "entities": self.entities,
            "constraints": self.constraints,
        }

    def point(self, entity_id: str, x: float, y: float) -> Sketch:
        self.entities.append({"id": entity_id, "type": "point", "at": [float(x), float(y)]})
        return self

    def line(self, entity_id: str, start: str, end: str, construction: bool = False) -> Sketch:
        self.entities.append(
            {
                "id": entity_id,
                "type": "line",
                "start": start,
                "end": end,
                "construction": construction,
            }
        )
        return self

    def circle(self, entity_id: str, center: str, radius: float) -> Sketch:
        self.entities.append(
            {"id": entity_id, "type": "circle", "center": center, "radius": float(radius)}
        )
        return self

    def arc(self, entity_id: str, center: str, start: str, end: str) -> Sketch:
        self.entities.append(
            {"id": entity_id, "type": "arc", "center": center, "start": start, "end": end}
        )
        return self

    def three_point_arc(
        self,
        entity_id: str,
        start: str,
        middle: str,
        end: str,
        *,
        name: str | None = None,
    ) -> Sketch:
        """An arc through three points.

        The three points define the circle, the way a line's ends define the
        line. Pass ``name`` to also drive it with a radius dimension.
        """
        from aicad.sketch_arc import circle_through

        guessed = circle_through(self._guess_at(start), self._guess_at(middle), self._guess_at(end))
        if guessed is None:
            raise GeometryError(
                f"Arc {entity_id!r} needs three points that are not in a line."
            )
        _center, radius = guessed
        self.entities.append(
            {"id": entity_id, "type": "arc", "through": [start, middle, end]}
        )
        if name:
            self.radius(entity_id, radius, name=name)
        return self

    def tangent_arc(
        self,
        entity_id: str,
        line: str,
        end: str,
        *,
        at: str = "end",
        name: str | None = None,
    ) -> Sketch:
        """An arc that continues ``line`` and ends at ``end``.

        ``at`` is ``end`` (the line's drawn direction) or ``start``. The
        target has to leave the line, so a point sitting on the line is refused.
        """
        from aicad.sketch_arc import tangent_continuation

        line_entity = self._entity(line, "line")
        if at not in ("start", "end"):
            raise GeometryError(f"Arc {entity_id!r} continues a line from its start or its end.")
        frame = tangent_continuation(
            self._guess_at(line_entity["start"]),
            self._guess_at(line_entity["end"]),
            at == "end",
            self._guess_at(end),
        )
        if frame is None:
            raise GeometryError(
                f"Arc {entity_id!r} cannot continue {line!r}: that end point is on the line."
            )
        self.entities.append(
            {
                "id": entity_id,
                "type": "arc",
                "tangent_to": line,
                "end": end,
                "from": at,
            }
        )
        self.radius(entity_id, frame["radius"], name=name or f"{entity_id}_radius")
        return self

    def _entity(self, entity_id: str, kind: str) -> dict[str, Any]:
        for entity in self.entities:
            if entity.get("id") == entity_id and entity.get("type") == kind:
                return entity
        raise GeometryError(f"Sketch has no {kind} {entity_id!r}.")

    def _guess_at(self, entity_id: str) -> tuple[float, float]:
        entity = self._entity(entity_id, "point")
        at = entity.get("at")
        if not isinstance(at, (list, tuple)) or len(at) < 2:
            raise GeometryError(f"Point {entity_id!r} needs an at coordinate.")
        return (float(at[0]), float(at[1]))

    def constrain(self, **constraint: Any) -> Sketch:
        self.constraints.append(dict(constraint))
        return self

    def fix(self, entity: str) -> Sketch:
        return self.constrain(type="fix", entity=entity)

    def horizontal(self, entity: str) -> Sketch:
        return self.constrain(type="horizontal", entity=entity)

    def vertical(self, entity: str) -> Sketch:
        return self.constrain(type="vertical", entity=entity)

    def coincident(self, a: str, b: str) -> Sketch:
        return self.constrain(type="coincident", a=a, b=b)

    def parallel(self, a: str, b: str) -> Sketch:
        return self.constrain(type="parallel", a=a, b=b)

    def perpendicular(self, a: str, b: str) -> Sketch:
        return self.constrain(type="perpendicular", a=a, b=b)

    def equal(self, a: str, b: str) -> Sketch:
        return self.constrain(type="equal", a=a, b=b)

    def midpoint(self, point: str, line: str) -> Sketch:
        return self.constrain(type="midpoint", point=point, line=line)

    def tangent(self, a: str, b: str) -> Sketch:
        return self.constrain(type="tangent", a=a, b=b)

    def symmetric(self, a: str, b: str, about: str) -> Sketch:
        return self.constrain(type="symmetric", a=a, b=b, about=about)

    def distance(
        self,
        a: str,
        b: str,
        value: float,
        name: str | None = None,
        tolerance: float | dict | None = None,
    ) -> Sketch:
        item: dict[str, Any] = {"type": "distance", "a": a, "b": b, "value": float(value)}
        if name:
            item["name"] = name
        if tolerance is not None:
            item["tolerance"] = tolerance
        return self.constrain(**item)

    def diameter(
        self,
        entity: str,
        value: float,
        name: str | None = None,
        tolerance: float | dict | None = None,
    ) -> Sketch:
        item: dict[str, Any] = {"type": "diameter", "entity": entity, "value": float(value)}
        if name:
            item["name"] = name
        if tolerance is not None:
            item["tolerance"] = tolerance
        return self.constrain(**item)

    def radius(
        self,
        entity: str,
        value: float,
        name: str | None = None,
        tolerance: float | dict | None = None,
    ) -> Sketch:
        item: dict[str, Any] = {"type": "radius", "entity": entity, "value": float(value)}
        if name:
            item["name"] = name
        if tolerance is not None:
            item["tolerance"] = tolerance
        return self.constrain(**item)

    def angle(
        self,
        a: str,
        b: str,
        value: float,
        name: str | None = None,
        tolerance: float | dict | None = None,
    ) -> Sketch:
        item: dict[str, Any] = {"type": "angle", "a": a, "b": b, "value": float(value)}
        if name:
            item["name"] = name
        if tolerance is not None:
            item["tolerance"] = tolerance
        return self.constrain(**item)

    def pattern_on_circle(
        self,
        center: str,
        points: list[str],
        diameter: float,
        start_angle: float | None = None,
        name: str | None = None,
        tolerance: float | dict | None = None,
    ) -> Sketch:
        item: dict[str, Any] = {
            "type": "pattern_on_circle",
            "center": center,
            "points": list(points),
            "diameter": float(diameter),
        }
        if start_angle is not None:
            item["start_angle"] = float(start_angle)
        if name:
            item["name"] = name
        if tolerance is not None:
            item["tolerance"] = tolerance
        return self.constrain(**item)


def solve_sketch(spec: Sketch | dict, name: str | None = None) -> SketchReport:
    """Solve a sketch spec. A conflicting sketch still returns a report."""
    if isinstance(spec, Sketch):
        spec = spec.to_spec()
    if not isinstance(spec, dict):
        raise GeometryError("A sketch spec is an object with entities and constraints.")
    entities = spec.get("entities")
    constraints = spec.get("constraints")
    if not isinstance(entities, list) or not entities:
        raise GeometryError("A sketch needs a non-empty entities list.")
    if not isinstance(constraints, list):
        raise GeometryError("A sketch needs a constraints list. An empty list leaves every point free.")
    from aicad.sketch_plane import normalize_plane

    plane = normalize_plane(spec.get("plane", "xy"))
    spec = {**spec, "plane": plane}
    from aicad.models import require_millimeters

    require_millimeters(spec.get("units"), "A sketch")

    solver = _Solver()
    solver.build(entities, constraints)
    return solver.finish(name)


def set_spec_dimension(spec: dict, dimension: str, value: float) -> dict:
    """Return a copy of ``spec`` with one named driving dimension changed."""
    found = False
    constraints = []
    for constraint in spec.get("constraints", []):
        item = dict(constraint)
        if item.get("name") == dimension:
            found = True
            if "value" in item:
                item["value"] = float(value)
            elif "diameter" in item:
                item["diameter"] = float(value)
            else:
                raise GeometryError(
                    f"Dimension {dimension!r} has no value to change. "
                    "Name a distance, diameter, radius, angle, or pattern_on_circle."
                )
        constraints.append(item)
    if not found:
        names = sorted(
            {item.get("name") for item in spec.get("constraints", []) if item.get("name")}
        )
        known = ", ".join(names) or "(none)"
        raise GeometryError(f"No dimension named {dimension!r}. Named dimensions: {known}.")
    return {**spec, "constraints": constraints}


class _Solver:
    def __init__(self) -> None:
        self.sys = SolverSystem()
        self.sys.set_group(1)
        self.wp = self.sys.create_2d_base()
        self.normal = self.sys.entity(0)
        self.sys.set_group(2)
        self.records: dict[str, dict[str, Any]] = {}
        self.constraint_names: list[str] = []
        self.fixed: set[str] = set()
        self.dimensions: list[dict[str, Any]] = []
        self.dimension_values: dict[str, tuple[str, float]] = {}

    def build(self, entities: list, constraints: list) -> None:
        for raw in entities:
            self._add_entity(raw)
        for index, raw in enumerate(constraints):
            self._add_constraint(raw, index)

    def finish(self, name: str | None) -> SketchReport:
        result = self.sys.solve()
        flag = ResultFlag(result)
        dof = int(self.sys.dof())
        conflicts = self._conflict_names()
        geometry = self._geometry()
        if flag == ResultFlag.OKAY and dof == 0:
            status = "fully_constrained"
            free: list[dict[str, object]] = []
            note = "Fully constrained. Every driving dimension is locked and no point can still move."
        elif flag == ResultFlag.OKAY:
            status = "under_constrained"
            free = self._free_entities()
            detail = ", ".join(f"{item['entity']} ({item['free_dof']} dof)" for item in free) or "unlisted"
            note = (
                f"Under-constrained: {dof} degrees of freedom remain. "
                f"Free: {detail}. Add a dimension or a geometric constraint before relying on the shape."
            )
        elif flag == ResultFlag.INCONSISTENT:
            status = "over_constrained"
            named = ", ".join(conflicts) or "the solver did not name them"
            note = (
                "Over-constrained or conflicting. The solver could not satisfy every constraint. "
                f"In conflict: {named}."
            )
            free = []
        elif flag == ResultFlag.DIDNT_CONVERGE:
            status = "didnt_converge"
            note = (
                "The solver did not converge. Move the initial `at` guesses closer to a shape "
                "that already almost satisfies the constraints."
            )
            free = []
        else:
            status = "too_many_unknowns"
            note = "The sketch has more free parameters than this solver invocation can handle."
            free = []
        return SketchReport(
            name=name,
            status=status,
            dof=dof,
            solver=_SOLVER,
            free=free,
            conflicts=conflicts,
            dimensions=self.dimensions,
            geometry=geometry,
            note=note,
        )

    def _add_entity(self, raw: dict) -> None:
        if not isinstance(raw, dict) or "id" not in raw or "type" not in raw:
            raise GeometryError("Each sketch entity needs an id and a type.")
        entity_id = raw["id"]
        if not isinstance(entity_id, str) or not _ID.match(entity_id):
            raise GeometryError(
                f"Sketch entity id {entity_id!r} must be a letter or underscore followed by letters, "
                "numbers, or underscores."
            )
        if entity_id in self.records:
            raise GeometryError(f"Sketch entity {entity_id!r} is already defined.")
        kind = raw["type"]
        if kind == "point":
            at = _pair(raw.get("at"), f"{entity_id}.at")
            ent = self.sys.add_point_2d(at[0], at[1], self.wp)
            self.records[entity_id] = {"kind": "point", "index": self._index(), "entity": ent}
            return
        if kind == "line":
            start = self._point(raw.get("start"), entity_id)
            end = self._point(raw.get("end"), entity_id)
            ent = self.sys.add_line_2d(start["entity"], end["entity"], self.wp)
            self.records[entity_id] = {
                "kind": "line",
                "index": self._index(),
                "entity": ent,
                "start": raw["start"],
                "end": raw["end"],
                "construction": bool(raw.get("construction", False)),
            }
            return
        if kind == "circle":
            center = self._point(raw.get("center"), entity_id)
            radius = float(raw.get("radius", 1.0))
            if radius <= 0:
                raise GeometryError(f"Circle {entity_id!r} needs a positive initial radius.")
            distance = self.sys.add_distance(radius, self.wp)
            ent = self.sys.add_circle(self.normal, center["entity"], distance, self.wp)
            self.records[entity_id] = {
                "kind": "circle",
                "index": self._index(),
                "entity": ent,
                "center": raw["center"],
                "radius_index": self.sys.entity_len() - 2,
                "radius_entity": distance,
                "construction": bool(raw.get("construction", False)),
            }
            # entity_len after circle includes the circle; the distance was added just before.
            # Re-find the distance index by walking back if needed.
            self.records[entity_id]["radius_index"] = self._index_of(distance)
            return
        if kind == "arc":
            self._add_arc(raw, entity_id)
            return
        raise GeometryError(
            f"Unknown entity type {kind!r} on {entity_id!r}. Use point, line, circle, or arc."
        )

    def _add_arc(self, raw: dict, entity_id: str) -> None:
        if "through" in raw:
            self._arc_through(raw, entity_id)
            return
        if "tangent_to" in raw:
            self._arc_tangent(raw, entity_id)
            return
        if "center" not in raw or "start" not in raw or "end" not in raw:
            raise GeometryError(
                f"Arc {entity_id!r} needs a center, a start, and an end, "
                "or three points, or a line to continue."
            )
        center = self._point(raw.get("center"), entity_id)
        start = self._point(raw.get("start"), entity_id)
        end = self._point(raw.get("end"), entity_id)
        ent = self.sys.add_arc(self.normal, center["entity"], start["entity"], end["entity"], self.wp)
        self.records[entity_id] = {
            "kind": "arc",
            "index": self._index(),
            "entity": ent,
            "center": raw["center"],
            "start": raw["start"],
            "end": raw["end"],
            "construction": bool(raw.get("construction", False)),
        }

    def _arc_through(self, raw: dict, entity_id: str) -> None:
        from aicad.sketch_arc import circle_through, three_point_order

        through = raw.get("through")
        if not isinstance(through, list) or len(through) != 3 or len(set(through)) != 3:
            raise GeometryError(f"Arc {entity_id!r} needs three different points.")
        start_id, middle_id, end_id = through
        start = self._point(start_id, entity_id)
        middle = self._point(middle_id, entity_id)
        end = self._point(end_id, entity_id)
        guessed = circle_through(
            self._guess_point(start_id),
            self._guess_point(middle_id),
            self._guess_point(end_id),
        )
        if guessed is None:
            raise GeometryError(f"Arc {entity_id!r} needs three points that are not in a line.")
        center_xy, _radius = guessed
        keep = three_point_order(
            center_xy,
            self._guess_point(start_id),
            self._guess_point(middle_id),
            self._guess_point(end_id),
        )
        if not keep:
            start_id, end_id = end_id, start_id
            start, end = end, start
        center_id = self._fresh_id(f"{entity_id}_center")
        center_ent = self.sys.add_point_2d(center_xy[0], center_xy[1], self.wp)
        self.records[center_id] = {"kind": "point", "index": self._index(), "entity": center_ent}
        arc = self.sys.add_arc(self.normal, center_ent, start["entity"], end["entity"], self.wp)
        self.records[entity_id] = {
            "kind": "arc",
            "index": self._index(),
            "entity": arc,
            "center": center_id,
            "start": start_id,
            "end": end_id,
            "construction": bool(raw.get("construction", False)),
        }
        # A point cannot be constrained onto an arc entity. Two radii of the
        # same circle are equal, and the arc already holds the start radius.
        ray_start = self.sys.add_line_2d(center_ent, start["entity"], self.wp)
        start_index = self._index()
        ray_middle = self.sys.add_line_2d(center_ent, middle["entity"], self.wp)
        middle_index = self._index()
        self.records[self._fresh_id(f"__{entity_id}_rs")] = {
            "kind": "line",
            "index": start_index,
            "entity": ray_start,
            "start": center_id,
            "end": start_id,
            "construction": True,
        }
        self.records[self._fresh_id(f"__{entity_id}_rm")] = {
            "kind": "line",
            "index": middle_index,
            "entity": ray_middle,
            "start": center_id,
            "end": middle_id,
            "construction": True,
        }
        self._bind(
            f"{entity_id}.through",
            lambda left=ray_start, right=ray_middle: self.sys.equal(left, right, self.wp),
        )

    def _arc_tangent(self, raw: dict, entity_id: str) -> None:
        from aicad.sketch_arc import tangent_continuation

        line = self._line(raw.get("tangent_to"), entity_id)
        end_id = raw.get("end")
        end = self._point(end_id, entity_id)
        which = raw.get("from", "end")
        if which not in ("start", "end"):
            raise GeometryError(f"Arc {entity_id!r} continues a line from its start or its end.")
        origin_id = line["end"] if which == "end" else line["start"]
        if origin_id == end_id:
            raise GeometryError(f"Arc {entity_id!r} needs an end that is not the line's {which}.")
        origin = self._point(origin_id, entity_id)
        frame = tangent_continuation(
            self._guess_point(line["start"]),
            self._guess_point(line["end"]),
            which == "end",
            self._guess_point(end_id),
        )
        if frame is None:
            raise GeometryError(
                f"Arc {entity_id!r} cannot continue {raw.get('tangent_to')!r}: that end point is on the line."
            )
        center_xy = frame["center"]
        center_id = self._fresh_id(f"{entity_id}_center")
        center_ent = self.sys.add_point_2d(center_xy[0], center_xy[1], self.wp)
        self.records[center_id] = {"kind": "point", "index": self._index(), "entity": center_ent}
        if frame["start_at_line"]:
            start_id, start = origin_id, origin
            arc_end_id, arc_end = end_id, end
        else:
            start_id, start = end_id, end
            arc_end_id, arc_end = origin_id, origin
        arc = self.sys.add_arc(self.normal, center_ent, start["entity"], arc_end["entity"], self.wp)
        self.records[entity_id] = {
            "kind": "arc",
            "index": self._index(),
            "entity": arc,
            "center": center_id,
            "start": start_id,
            "end": arc_end_id,
            "construction": bool(raw.get("construction", False)),
        }
        # The continuation owns the tangency. A second tangent constraint in the
        # spec would ask for the same condition again.
        self._bind(
            f"{entity_id}.tangent",
            lambda arc_ent=arc, line_ent=line["entity"]: self.sys.tangent(arc_ent, line_ent, self.wp),
        )

    def _fresh_id(self, base: str) -> str:
        candidate = base[:64]
        index = 2
        while candidate in self.records or not _ID.match(candidate):
            suffix = f"_{index}"
            candidate = base[: 64 - len(suffix)] + suffix
            index += 1
            if index > 1000:
                raise GeometryError(f"No free id for {base!r}.")
        return candidate

    def _add_constraint(self, raw: dict, index: int) -> None:
        if not isinstance(raw, dict) or "type" not in raw:
            raise GeometryError(f"Constraint {index} needs a type.")
        kind = raw["type"]
        label = raw.get("name") or raw.get("id") or f"{kind}_{index + 1}"
        if kind == "fix":
            record = self._record(raw.get("entity"), label)
            if record["kind"] != "point":
                raise GeometryError(f"{label}: fix applies to a point, not {record['kind']}.")
            self._bind(label, lambda: self.sys.dragged(record["entity"], self.wp))
            self.fixed.add(raw["entity"])
            return
        if kind in ("horizontal", "vertical"):
            record = self._record(raw.get("entity"), label)
            if record["kind"] != "line":
                raise GeometryError(f"{label}: {kind} applies to a line.")
            method = self.sys.horizontal if kind == "horizontal" else self.sys.vertical
            self._bind(label, lambda: method(record["entity"], self.wp))
            return
        if kind == "coincident":
            a = self._record(raw.get("a"), label)
            b = self._record(raw.get("b"), label)
            self._bind(label, lambda: self.sys.coincident(a["entity"], b["entity"], self.wp))
            return
        if kind == "parallel":
            a = self._line(raw.get("a"), label)
            b = self._line(raw.get("b"), label)
            self._bind(label, lambda: self.sys.parallel(a["entity"], b["entity"], self.wp))
            return
        if kind == "perpendicular":
            a = self._line(raw.get("a"), label)
            b = self._line(raw.get("b"), label)
            self._bind(label, lambda: self.sys.perpendicular(a["entity"], b["entity"], self.wp))
            return
        if kind == "equal":
            a = self._record(raw.get("a"), label)
            b = self._record(raw.get("b"), label)
            self._bind(label, lambda: self.sys.equal(a["entity"], b["entity"], self.wp))
            return
        if kind == "midpoint":
            point = self._record(raw.get("point"), label)
            line = self._line(raw.get("line"), label)
            self._bind(label, lambda: self.sys.midpoint(point["entity"], line["entity"], self.wp))
            return
        if kind == "tangent":
            self._tangent(raw, label)
            return
        if kind == "symmetric":
            self._symmetric(raw, label)
            return
        if kind == "distance":
            self._distance(raw, label)
            return
        if kind in ("diameter", "radius"):
            record = self._record(raw.get("entity"), label)
            if record["kind"] not in ("circle", "arc"):
                raise GeometryError(f"{label}: {kind} applies to a circle or an arc.")
            _length_unit(raw, label)
            value = _number(raw.get("value"), label)
            driven = value * 2 if kind == "radius" else value
            self._bind(label, lambda: self.sys.diameter(record["entity"], driven))
            self._remember_dimension(raw, label, kind, value, record_id=raw.get("entity"))
            return
        if kind == "angle":
            a = self._line(raw.get("a"), label)
            b = self._line(raw.get("b"), label)
            value = _number(raw.get("value"), label)
            self._bind(label, lambda: self.sys.angle(a["entity"], b["entity"], value, self.wp))
            self._remember_dimension(raw, label, "angle", value)
            return
        if kind == "pattern_on_circle":
            self._pattern(raw, label)
            return
        raise GeometryError(
            f"Unknown constraint type {kind!r}. Use fix, coincident, parallel, perpendicular, "
            "tangent, equal, horizontal, vertical, symmetric, midpoint, distance, angle, "
            "radius, diameter, or pattern_on_circle."
        )

    def _tangent(self, raw: dict, label: str) -> None:
        a = self._record(raw.get("a"), label)
        b = self._record(raw.get("b"), label)
        arc = a if a["kind"] == "arc" else b if b["kind"] == "arc" else None
        line = a if a["kind"] == "line" else b if b["kind"] == "line" else None
        circle = a if a["kind"] == "circle" else b if b["kind"] == "circle" else None
        if arc is not None and line is not None:
            self._bind(label, lambda: self.sys.tangent(arc["entity"], line["entity"], self.wp))
            return
        if circle is not None and line is not None:
            # A circle has no endpoint, so tangency is a point on the circle and the line,
            # with the radius perpendicular to the line.
            center = self.records[circle["center"]]
            cx, cy = self._guess_point(circle["center"])
            guess_r = abs(self.sys.params(circle["radius_entity"].params)[0])
            sx, sy = self._guess_point(line["start"])
            ex, ey = self._guess_point(line["end"])
            mx, my = (sx + ex) / 2, (sy + ey) / 2
            dx, dy = mx - cx, my - cy
            length = math.hypot(dx, dy) or 1.0
            px, py = cx + guess_r * dx / length, cy + guess_r * dy / length
            point_id = f"__{label}_touch"
            ent = self.sys.add_point_2d(px, py, self.wp)
            touch = {"kind": "point", "index": self._index(), "entity": ent}
            self.records[point_id] = touch
            radial = self.sys.add_line_2d(center["entity"], ent, self.wp)
            radial_rec = {"kind": "line", "index": self._index(), "entity": radial, "construction": True}
            self._bind(f"{label}.on_circle", lambda: self.sys.coincident(ent, circle["entity"], self.wp))
            self._bind(f"{label}.on_line", lambda: self.sys.coincident(ent, line["entity"], self.wp))
            self._bind(
                f"{label}.perpendicular",
                lambda: self.sys.perpendicular(radial, line["entity"], self.wp),
            )
            self.records[f"__{label}_radial"] = radial_rec
            return
        raise GeometryError(f"{label}: tangent needs an arc and a line, or a circle and a line.")

    def _symmetric(self, raw: dict, label: str) -> None:
        a = self._record(raw.get("a"), label)
        b = self._record(raw.get("b"), label)
        if a["kind"] != "point" or b["kind"] != "point":
            raise GeometryError(f"{label}: symmetric applies to two points.")
        about = raw.get("about")
        if about == "horizontal":
            self._bind(label, lambda: self.sys.symmetric_h(a["entity"], b["entity"], self.wp))
            return
        if about == "vertical":
            self._bind(label, lambda: self.sys.symmetric_v(a["entity"], b["entity"], self.wp))
            return
        line = self._line(about, label)
        self._bind(label, lambda: self.sys.symmetric(a["entity"], b["entity"], line["entity"], self.wp))

    def _distance(self, raw: dict, label: str) -> None:
        _length_unit(raw, label)
        value = _number(raw.get("value"), label)
        a = self._record(raw.get("a"), label)
        b = self._record(raw.get("b"), label)
        if a["kind"] == "point" and b["kind"] == "point":
            self._bind(label, lambda: self.sys.distance(a["entity"], b["entity"], value, self.wp))
        elif a["kind"] == "point" and b["kind"] == "line":
            self._bind(label, lambda: self.sys.distance(a["entity"], b["entity"], value, self.wp))
        elif a["kind"] == "line" and b["kind"] == "point":
            self._bind(label, lambda: self.sys.distance(b["entity"], a["entity"], value, self.wp))
        else:
            raise GeometryError(f"{label}: distance is point-to-point or point-to-line.")
        self._remember_dimension(raw, label, "distance", value)

    def _pattern(self, raw: dict, label: str) -> None:
        points = raw.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise GeometryError(f"{label}: pattern_on_circle needs at least two points.")
        center = self._record(raw.get("center"), label)
        if center["kind"] != "point":
            raise GeometryError(f"{label}: the pattern center must be a point.")
        _length_unit(raw, label)
        diameter = _number(raw.get("diameter"), label)
        if diameter <= 0:
            raise GeometryError(f"{label}: pattern diameter must be positive.")
        radius = diameter / 2
        cx, cy = self._guess_point(raw["center"])
        distance = self.sys.add_distance(radius, self.wp)
        circle = self.sys.add_circle(self.normal, center["entity"], distance, self.wp)
        circle_id = f"__{label}_circle"
        self.records[circle_id] = {
            "kind": "circle",
            "index": self._index(),
            "entity": circle,
            "center": raw["center"],
            "radius_entity": distance,
            "radius_index": self._index_of(distance),
            "construction": True,
        }
        self._bind(f"{label}.diameter", lambda: self.sys.diameter(circle, diameter))
        rays = []
        for point_id in points:
            point = self._record(point_id, label)
            if point["kind"] != "point":
                raise GeometryError(f"{label}: pattern members must be points.")
            self._bind(
                f"{label}.on.{point_id}",
                lambda point=point: self.sys.coincident(point["entity"], circle, self.wp),
            )
            ray = self.sys.add_line_2d(center["entity"], point["entity"], self.wp)
            rays.append(ray)
        start = raw.get("start_angle")
        if start is not None:
            start = float(start)
            ref = self.sys.add_point_2d(cx + max(radius, 1.0), cy, self.wp)
            ref_line = self.sys.add_line_2d(center["entity"], ref, self.wp)
            self._bind(f"{label}.datum_horizontal", lambda: self.sys.horizontal(ref_line, self.wp))
            self._bind(
                f"{label}.datum_length",
                lambda: self.sys.distance(center["entity"], ref, max(radius, 1.0), self.wp),
            )
            self._bind(
                f"{label}.start",
                lambda: self.sys.angle(ref_line, rays[0], start, self.wp),
            )
        step = 360.0 / len(points)
        # The closing angle is implied by the others. Constraining it too makes a conflict.
        for index in range(len(rays) - 1):
            first, second = rays[index], rays[index + 1]
            self._bind(
                f"{label}.spacing_{index + 1}",
                lambda first=first, second=second: self.sys.angle(first, second, step, self.wp),
            )
        self._remember_dimension(raw, label, "pattern_on_circle", diameter, record_id=circle_id)

    def _remember_dimension(
        self,
        raw: dict,
        label: str,
        kind: str,
        value: float,
        record_id: str | None = None,
    ) -> None:
        if "name" not in raw and "tolerance" not in raw:
            return
        given = raw.get("name")
        if isinstance(given, str) and given != "":
            from aicad.labels import has_control_line

            if has_control_line(given):
                raise GeometryError("A dimension name has to stay on one line.")
            # One name drives every constraint that uses it, so those
            # constraints have to be the same kind and the same value.
            previous = self.dimension_values.get(given)
            if previous is not None and previous != (kind, float(value)):
                raise GeometryError(
                    f"Dimension {given!r} is already {previous[0]} {previous[1]:g}. "
                    "Use that same value to drive another constraint, or give this one its own name."
                )
            self.dimension_values[given] = (kind, float(value))
        tolerance = _tolerance(raw.get("tolerance"))
        text = _dimension_text(kind, value, tolerance)
        name = given or label
        for existing in self.dimensions:
            if existing["name"] == name and existing["kind"] == kind and existing["value"] == value:
                return
        record = {
            "name": name,
            "kind": kind,
            "value": value,
            "tolerance": tolerance,
            "text": text,
            "entity": record_id or raw.get("entity") or raw.get("a"),
        }
        if "a" in raw:
            record["a"] = raw["a"]
        if "b" in raw:
            record["b"] = raw["b"]
        self.dimensions.append(record)

    def _bind(self, label: str, add) -> None:
        add()
        self.constraint_names.append(label)

    def _conflict_names(self) -> list[str]:
        names = []
        for handle in self.sys.failures():
            index = int(handle) - 1
            if 0 <= index < len(self.constraint_names):
                names.append(self.constraint_names[index])
            else:
                names.append(f"constraint_{handle}")
        return names

    def _geometry(self) -> dict[str, Any]:
        points = {}
        lines = {}
        circles = {}
        arcs = {}
        for entity_id, record in self.records.items():
            if entity_id.startswith("__") and record["kind"] == "point":
                continue
            if record["kind"] == "point":
                u, v = self.sys.params(record["entity"].params)
                points[entity_id] = {"x": round(float(u), 6), "y": round(float(v), 6)}
            elif record["kind"] == "line" and not entity_id.startswith("__"):
                lines[entity_id] = {
                    "start": points_of(self, record["start"]),
                    "end": points_of(self, record["end"]),
                    "construction": record.get("construction", False),
                }
            elif record["kind"] == "circle":
                center = points_of(self, record["center"])
                radius = float(self.sys.params(record["radius_entity"].params)[0])
                circles[entity_id] = {
                    "center": center,
                    "radius": round(abs(radius), 6),
                    "construction": record.get("construction", False) or entity_id.startswith("__"),
                }
            elif record["kind"] == "arc":
                arcs[entity_id] = {
                    "center": points_of(self, record["center"]),
                    "start": points_of(self, record["start"]),
                    "end": points_of(self, record["end"]),
                    "center_id": record["center"],
                    "start_id": record["start"],
                    "end_id": record["end"],
                    "construction": record.get("construction", False),
                }
        return {"points": points, "lines": lines, "circles": circles, "arcs": arcs}

    def _free_entities(self) -> list[dict[str, object]]:
        free = []
        for entity_id, record in self.records.items():
            if entity_id.startswith("__"):
                continue
            if record["kind"] == "point":
                if entity_id in self.fixed:
                    continue
                xy = self.sys.params(record["entity"].params)
                count = _motion_rank(self.sys, record["index"], (float(xy[0]), float(xy[1])))
                if count:
                    free.append(
                        {
                            "entity": entity_id,
                            "kind": "point",
                            "free_dof": count,
                            "note": (
                                f"{entity_id} can still move"
                                + (" in one direction." if count == 1 else " in the plane.")
                            ),
                        }
                    )
            elif record["kind"] == "circle" and "radius_index" in record:
                original = float(self.sys.params(record["radius_entity"].params)[0])
                if _radius_free(self.sys, record["radius_index"], original):
                    free.append(
                        {
                            "entity": entity_id,
                            "kind": "circle",
                            "free_dof": 1,
                            "note": f"{entity_id} has no radius or diameter dimension.",
                        }
                    )
        return free

    def _index(self) -> int:
        return self.sys.entity_len() - 1

    def _index_of(self, entity) -> int:
        for index in range(self.sys.entity_len()):
            candidate = self.sys.entity(index)
            if candidate.is_distance() and candidate.params == entity.params:
                return index
        raise GeometryError("Could not find a circle's radius parameter in the solver.")

    def _record(self, entity_id: str | None, label: str) -> dict:
        if not isinstance(entity_id, str) or entity_id not in self.records:
            known = ", ".join(sorted(self.records)) or "(none)"
            raise GeometryError(f"{label}: no entity {entity_id!r}. Entities: {known}.")
        return self.records[entity_id]

    def _point(self, entity_id: str | None, owner: str) -> dict:
        record = self._record(entity_id, owner)
        if record["kind"] != "point":
            raise GeometryError(f"{owner}: {entity_id!r} is a {record['kind']}, not a point.")
        return record

    def _line(self, entity_id: str | None, label: str) -> dict:
        record = self._record(entity_id, label)
        if record["kind"] != "line":
            raise GeometryError(f"{label}: {entity_id!r} is a {record['kind']}, not a line.")
        return record

    def _guess_point(self, entity_id: str) -> tuple[float, float]:
        record = self.records[entity_id]
        values = self.sys.params(record["entity"].params)
        return float(values[0]), float(values[1])


def points_of(solver: _Solver, entity_id: str) -> dict[str, float]:
    record = solver.records[entity_id]
    u, v = solver.sys.params(record["entity"].params)
    return {"x": round(float(u), 6), "y": round(float(v), 6)}


def _motion_rank(sys: SolverSystem, index: int, original: tuple[float, float]) -> int:
    motions: list[tuple[float, float]] = []
    for axis in (0, 1):
        copy = sys.copy()
        copy.set_group(2)
        entity = copy.entity(index)
        values = list(copy.params(entity.params))
        values[axis] = original[axis] + 4.0
        copy.set_params(entity.params, values)
        if copy.solve() != ResultFlag.OKAY:
            continue
        solved = copy.params(entity.params)
        motion = (float(solved[0]) - original[0], float(solved[1]) - original[1])
        if math.hypot(*motion) > 0.25:
            motions.append(motion)
    if not motions:
        return 0
    base = motions[0]
    span = math.hypot(*base)
    for motion in motions[1:]:
        cross = abs(base[0] * motion[1] - base[1] * motion[0]) / span
        if cross > 0.35:
            return 2
    return 1


def _radius_free(sys: SolverSystem, index: int, original: float) -> bool:
    copy = sys.copy()
    copy.set_group(2)
    entity = copy.entity(index)
    copy.set_params(entity.params, [original + 2.0])
    if copy.solve() != ResultFlag.OKAY:
        return False
    solved = float(copy.params(entity.params)[0])
    return abs(solved - original) > 0.25


def _pair(value, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise GeometryError(f"{label} must be [x, y].")
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} must be [x, y].") from exc


def _length_unit(raw: dict, label: str) -> None:
    from aicad.models import require_millimeters

    for key in ("unit", "units"):
        if key in raw:
            require_millimeters(raw.get(key), label)


def _number(value, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} needs a numeric value.") from exc


def _tolerance(value) -> dict[str, float] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return {"plus": number, "minus": number}
    if isinstance(value, dict):
        plus = value.get("plus", value.get("+"))
        minus = value.get("minus", value.get("-"))
        if plus is None or minus is None:
            raise GeometryError("A tolerance object needs plus and minus, in millimeters.")
        return {"plus": float(plus), "minus": float(minus)}
    raise GeometryError("Tolerance is a number (symmetric) or {plus, minus}.")


def _dimension_text(kind: str, value: float, tolerance: dict[str, float] | None) -> str:
    number = f"{value:g}"
    if kind == "diameter" or kind == "pattern_on_circle":
        body = f"Ø{number}"
    elif kind == "radius":
        body = f"R{number}"
    elif kind == "angle":
        body = f"{number}°"
    else:
        body = number
    if tolerance is None:
        return body
    plus, minus = tolerance["plus"], tolerance["minus"]
    if abs(plus - minus) < 1e-9:
        return f"{body} ±{plus:g}"
    return f"{body} +{plus:g}/-{minus:g}"
