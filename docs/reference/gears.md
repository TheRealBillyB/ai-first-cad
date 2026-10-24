# Gears

`aicad.gears` builds external spur, helical, straight bevel, and spiral bevel solids, and the same numbers the solid is drawn from. Units are millimetres. A hypoid gear (a shaft angle plus a nonzero offset) is not built. The module keeps an `offset` axis layout free so that family can be added without a new bore, hub, or report shape.

The Python calls are `gear_data`, `build_gear`, `pair_geometry`, and `mesh_placement`. The command line is `aicad gear`. The assistant tool is `create_gear`. A part stored this way has source `gear` and is rebuilt from the spec.

## Spur and helical

Standards: ISO 21771:2007 for the involute geometry, ISO 53:1998 for the basic rack.

`module_mm` is the normal module mn. The transverse module is mt = mn / cos β. The transverse pressure angle follows tan αt = tan αn / cos β. With tooth count z and profile shift x:

- reference diameter d = z · mt
- base diameter db = d · cos αt
- addendum ha = mn · (ha* + x), dedendum hf = mn · (hf* − x)
- tip diameter da = d + 2 ha, root diameter df = d − 2 hf
- transverse tooth thickness st = mt · (π/2 + 2 x tan αn), and sn = st · cos β

The half-angle of the tooth at radius r is

φ(r) = st / (2 r_p) + inv αt − inv αr

with inv α = tan α − α, αr = arccos(rb / r), and r_p = d / 2. The flank in the solid is that curve. A helical tooth carries it on a constant-lead helicoid. Positive β is right-hand: the profile turns counter-clockwise about +Z as z increases. The face is centred on z = 0.

ISO 53 racks, with ha* = 1 and a 20° pressure angle unless the spec overrides them:

| Rack | Dedendum hf* | Tip radius ρ* |
| --- | --- | --- |
| A | 1.25 | 0.38 |
| B | 1.25 | 0.30 |
| C | 1.25 | 0.25 |
| D | 1.40 | 0.39 |

The root fillet is the envelope of that tip radius in the transverse plane. On a helical gear the radius used there is the normal-plane ISO 53 radius, drawn as a circle. The involute flank is still the transverse involute.

Undercut is flagged when x is below hf* − ρ*(1 − sin αt) − z sin²αt / (2 cos β). The solid still builds; the fillet cuts into the involute.

A zero-backlash pair uses

inv αwt = inv αt + 2 tan αn · (x1 + x2) / (z1 + z2)

a = (d1 + d2) / 2 · cos αt / cos αwt

If inv αwt is negative, backlash remains when the base circles touch. There is no zero-backlash centre distance, and the pair is refused.

The transverse contact ratio is the length of the path of contact divided by the transverse base pitch. The overlap ratio is b sin|β| / (π mn), using the shorter face. External helical gears need opposite hands.

## Straight and spiral bevel

Standards: ISO 23509:2016 for intersecting axes (offset zero), and the Tredgold virtual cylindrical gear of ANSI/AGMA 2005-D03. Tooth depth is the Gleason split used for a 20-tooth pinion on a 40-tooth gear: whole depth 2.188 m_et for a straight bevel and 1.888 m_et for a spiral bevel, with no added constant. This is not a face-mill or face-hob simulation.

`module_mm` is the outer transverse module m_et. For shaft angle Σ and tooth ratio u = z2 / z1,

tan δ1 = sin Σ / (u + cos Σ), δ2 = Σ − δ1

The outer pitch diameter is d = m_et z. The outer cone distance is Re = d / (2 sin δ). For Σ = 90°, tan δ1 = z1 / z2 and Re = (m_et / 2) √(z1² + z2²). When `mate_teeth`, `ratio`, and `pitch_angle_deg` are given together, they have to describe that same cone.

Straight Gleason proportions, with member 1 the pinion:

- working depth 2.000 m_et, whole depth 2.188 m_et
- ha2 = m_et (0.540 + 0.460 (z1/z2)²), ha1 = 2 m_et − ha2
- dedendum hf = 2.188 m_et − ha

Spiral Gleason proportions, using the mean spiral angle βm:

- working depth 1.700 m_et, whole depth 1.888 m_et
- ha2 = m_et (0.460 + 0.390 (z1/z2)²), ha1 = 1.700 m_et − ha2
- dedendum hf = 1.888 m_et − ha
- tan αt = tan αn / cos βm

The outer tip diameter is da = d + 2 ha cos δ. The distance from the pitch apex to the crown is Re cos δ − ha sin δ. Virtual teeth are zv = z / cos δ, on a back-cone distance (d/2) / cos δ.

The solid takes that virtual spur, wraps it onto the back cone, and rules it from the inner cone distance to the outer one. The outer end face is the back cone. The inner end is closed on the toe side of the inner profile. Reported face and root angles are the Gleason parallel-clearance angles (the addendum angle equals the mate dedendum angle). Those angles are callouts; the solid is the ruled back-cone tooth.

A spiral is a logarithmic spiral of constant angle βm,

θ(R) − θ(Rm) = tan βm / sin δ · ln(R / Rm)

Positive βm is right-hand. It is not a circular-cutter trace. A pair needs opposite hands. The transverse contact ratio is the virtual spur pair at the middle of the face both gears share, with addendum scaled by that radius over Re. The overlap ratio is the spiral's rotation along that shared face divided by the angular pitch.

Do not pass `profile_shift` on a bevel. The addendum split above is the shift, and the two members sum to zero.

## Bore, hub, keyway, spline

The axis is +Z and tooth 0 is centred on +X. A cylindrical face runs from −b/2 to +b/2. A bevel has its pitch apex at the origin and the heel toward +Z.

- `bore_mm` is a plain hole. It has to stay inside the root.
- `hub` is `{diameter_mm, length_mm, side}`. `front` and `heel` protrude toward +Z. `back` and `toe` protrude toward −Z. `both` does each.
- `keyway` is a DIN 6885-1:1968 parallel key. The width and the hub depth t2 come from the bore diameter. The slot is centred on +X. A bore of 22 mm takes the 6 mm key.
- `spline` `iso14` is an ISO 14:1982 straight-sided hub spline, light or medium series, from Table 1. The hole is the minor diameter and the grooves of width B run out to the major diameter. Medium 8 × 56 × 65 uses major diameter 65.
- `spline` `din5480` is a DIN 5480-1:2006 involute hub spline, 30°, with dB = m (z + 2x + 1.1), hub tip da2 = m (z + 2x − 0.9), and hub root df2 = dB. The space width equals the mating shaft tooth thickness m (π/2 + 2 x tan 30°). The flank is that involute. The root corner is sharp; the 0.16 m fillet radius is recorded and not cut as a trochoid.

A keyway and a spline are not combined. A spline sets its own hole; `bore_mm` has to match that hole if both are given.

## Pair placement

`mesh_placement` puts a cylindrical pinion at the origin and the mate on +X at the zero-backlash centre distance, turned about Z so a tooth space faces the pinion tooth.

A bevel pair keeps both pitch apexes at the origin. The mate is rotated about Y by the shaft angle, after a spin about its own axis that puts a tooth space on the common pitch generator. In the placement record that is `[0, shaft_angle, spin]`, applied as X, then Y, then Z.

## Gear train

`aicad.gears.train` solves shaft speed and torque for a train stored on the assembly. The command is `aicad gear-train`. The assistant tool is `solve_gear_train`. Tooth counts come from gears this module built. An internal ring is a stated tooth count, because that solid is not built.

A shaft lists the gears fixed to it, and may name the revolute or cylindrical joint that turns it. A mesh names two gears. `kind` is `external` (opposite directions) or `internal` (the same direction). `carrier` is the shaft the mesh orbits with. Omit it and the carrier is the housing, which is a fixed-axis mesh. `efficiency` defaults to 1. `fixed` names shafts held at zero speed.

Relative to the carrier, an external mesh of tooth counts z_a and z_b gives

(ω_b − ω_c) / (ω_a − ω_c) = −z_a / z_b

An internal mesh gives +z_a / z_b. Gears on one shaft share one speed, so a compound train multiplies the stage ratios. An idler is one gear between two meshes, and its tooth count cancels.

A planetary mesh names the carrier. With the ring fixed,

ω_carrier / ω_sun = z_sun / (z_sun + z_ring)

An open differential is `{carrier, left, right}`, not two ordinary meshes. The constraint is

(ω_left + ω_right) / 2 = ω_carrier

Straight ahead, both outputs match the carrier. In a turn, one output speeds up as much as the other slows down. The two outputs share the carrier torque. Give the carrier speed and one output speed. The carrier alone leaves one degree of freedom, and the report says the train is under-constrained. Speeds that do not average are over-constrained.

The drive is `{inputs: [{shaft, speed_rpm, torque_nm}], speeds: [{shaft, speed_rpm}]}`. Speed is revolutions per minute. Torque is newton-metres, applied to the shaft in the positive direction. A load the shaft drives has the opposite sign. At efficiency 1 the shaft powers sum to zero, and the housing reaction cancels the shaft torques. An efficiency below 1 scales the torque delivered through that mesh. The report's `status` is `determined`, `under_constrained`, or `over_constrained`.

## Not in this module

Tolerance grades, backlash other than the zero-backlash nominal, a hypoid offset, and a circular face-mill or face-hob path are not generated here. The train solver does not check that the solids touch.
