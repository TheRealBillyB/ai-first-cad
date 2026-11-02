---
date: 2026-11-01
---

# Cable checks skip parts out of reach

`check_cables` samples each tendon and clipped cable every 2 mm and used to ask every part in the assembly for its exact distance to every sample. A long run past many parts made that the slow step. A sample now skips a part when its distance to the part's bounding box is already at least the pinch limit (line radius plus `pinch_mm`). A solid lies inside its box, so the exact distance could only be larger, and the reported collisions and rubs are unchanged.
