---
order: 34
---

# `create_assembly` / `update_assembly` / `get_assembly` / `list_assemblies`

Place parts, replace a spec (a spec that does not solve leaves the previous assembly), and read joint values. motions maps a name to a motion, or is a list of motions that each have a name. `insertion` lists the parts in the order they go in, each with the way it moves as it goes in, for `check_insertion`. An int color is three bytes from 0 to 255, so (1, 1, 0) stays nearly black. A float color from 0 to 1 is a fraction of full scale, so (1.0, 1.0, 0.0) is yellow. A float above 1 is a byte already on that scale.
