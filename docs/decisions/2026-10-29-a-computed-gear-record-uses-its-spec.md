---
date: 2026-10-29
---

# A computed gear record uses its spec

`gear_data` stores the input spec on the record and also reports results under names the input uses. A bevel profile shift and a hypoid pinion spiral are results. `build_gear`, `pair_geometry`, and `mesh_placement` read the stored spec, so the solid and the pair match the first calculation. A profile shift written on a bevel or hypoid spec is still refused.
