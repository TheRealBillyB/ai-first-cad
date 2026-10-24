# Cables and tendons

`check_cables` measures routes on an assembly. Units are millimetres and newtons. The same call is the Python method, the `aicad cables` command, and the assistant tool.

A route is either a clip polyline or a tendon. The report says which. Neither one sags.

## Clip polyline

`waypoints` are points fixed on parts: `instance`, `at` in that part's frame, and `clip`. The path is the polyline through those points after the joints move. Bend radius is the circumradius of each three consecutive points. A repeated waypoint has no corner, so that radius is unlimited, the same as a straight run. Two spans that do not share a waypoint fail when the shortest distance between those segments is less than the cable diameter. Strain is `(length - rest length) / rest length`. Tension is stiffness times positive strain. Stiffness is `low`, `medium`, `high`, or a number of newtons per unit strain. A shorter route is slack, not compression.

## Tendon

`points` replaces `waypoints`. The first and last points are anchors. The points between them are pulleys, guides, or wraps, each fixed on a part.

```json
{
  "name": "tendon",
  "kind": "tendon",
  "diameter_mm": 2,
  "material": "steel",
  "min_bend_radius_mm": 8,
  "actuator_force_n": 20,
  "actuator_end": "end",
  "points": [
    {"role": "anchor", "instance": "frame", "at": [-50, 10, 0]},
    {
      "role": "pulley",
      "name": "sheave",
      "instance": "frame",
      "at": [0, 0, 0],
      "axis": [0, 0, 1],
      "radius_mm": 10,
      "wrap": "cw",
      "friction": 0.2
    },
    {"role": "anchor", "instance": "link", "at": [10, -50, 0]}
  ]
}
```

`radius_mm` is the radius of the cable centerline. `axis` is a direction in the part frame. `wrap` is `cw` or `ccw` by the right-hand rule about that axis after the part is posed. When `wrap` is omitted, the check keeps the combination of directions with the smaller total arc. `turns` adds whole extra revolutions on that sheave. A guide with no radius is a point the cable must pass through. A guide with `radius_mm` is a small sheave. A wrap is a post the cable bends around; the geometry is the same as a pulley.

Length is the straight spans plus `radius_mm` times the wrap angle in radians on each sheave. Rest length is `rest_length_mm`, or the length of the first sample when that field is omitted. A path more than 0.01 mm shorter than the rest length is slack. A path more than 0.01 mm longer is stretch. Strain uses the same change. The default strain limit is 0.02.

The bend radius of a pulley or a wrap is `radius_mm`, and only when the cable actually wraps. A guide's bend radius is the circumradius of the incoming point, the guide, and the outgoing point. A guide that sits on the incoming or outgoing point is not a corner. The default limit is eight times the diameter. A smaller radius fails.

`material` is a name from the material library. Elastic elongation is `actuator_force_n` times length, divided by Young's modulus times the circular area `π d² / 4`. It is not added to the path length.

`actuator_force_n` is the tension in the span at `actuator_end` (`end` by default, or `start`). Across a pulley, the tension on the far side is the near tension times `e^(-friction × wrap)`, with the wrap in radians. The capstan ratio of that pulley is `e^(friction × wrap)`. Friction of zero passes the tension through unchanged. A slack tendon carries no tension. The geometric ratio is still reported.

A collision is a centerline sample inside a solid, or closer to the surface than the cable radius plus `pinch_mm` (default 0.5). The sample step is the larger of 2 mm and the diameter. When several samples meet one part, the report keeps the closest clearance of that kind. A centerline inside the solid is kept ahead of a rub. The same choice is used for a clip polyline. Samples on a pulley's own arc, and samples next to a point on that point's part, are the seat and are not counted against that part. `kind` is `inside` or `rub`.

Fleet angle is the angle between a span and the pulley plane. It is reported. It is not a failure by itself. When two pulley axes are not parallel, the span between them is the straight line joining the tangents taken in each pulley plane, and the report says that span is approximate.

## Motion

With no sequence, the check uses the stored pose. `--sequence` is a motion, or a motion study when no motion has that name. `--study` names a study on the assembly. `--file` is an inline study. `--steps` replaces the study's pose count. The Python arguments are `sequence`, `motion`, `study`, and `steps`.

The result is a rough estimate. The assumptions and the limits are on the report.
