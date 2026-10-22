# One session: a shelf that was too thin

This is a real run against `aicad serve`. The assistant created a shelf, looked at the pictures, failed a wall check, thickened the shelf, and passed the same check. Numbers below are what the tools returned.

The project folder was empty. Design scripts were left off. This session uses the ordinary part tools, which is the right path for one solid.

## 1. Create the shelf

Tool: `create_part_from_script`

```python
part = Box(80, 30, 1.2)
```

The reply said the part `shelf` is valid and watertight, volume 2880 mm³, bounding box 80 × 30 × 1.2 mm. `built` was true. The reply also said to call `inspect_part` and look before the next edit.

## 2. Look, and check the wall

Tool: `inspect_part` with `min_wall_mm` 2.

The tool returned the JSON report and 8 PNG pictures (the standard views and the contact sheet). The report said:

- volume 2880 mm³
- size 80 × 30 × 1.2 mm
- wall estimate 1.2 mm, method `ray+section`
- `passed` false

The shelf is a 1.2 mm plate. The check asked for 2 mm, so it failed. The pictures show a thin plate, which matches the box.

## 3. Make it thicker

Tool: `update_part_script`

```python
part = Box(80, 30, 3)
```

The reply said volume 7200 mm³ and size 80 × 30 × 3 mm. Still valid and watertight.

## 4. Check again

Tool: `inspect_part` with `min_wall_mm` 2, again.

Eight pictures came back. The wall estimate was 3.0 mm, method `ray+section`, and `passed` was true.

The change was the thickness, from 1.2 mm to 3 mm, because the first check failed. The assistant did not need a design script for this. A design script is for a longer program that branches on a check inside one run. That tool stays off until you allow it, as described in [CONNECT.md](CONNECT.md).
