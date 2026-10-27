---
date: 2026-10-27
---

# Print meshing skips the texture atlas

Print checks and pictures discard texture coordinates. Packing those coordinates overflows the call stack on a housing that holds two spur gears, so the mesh is built without an atlas.
