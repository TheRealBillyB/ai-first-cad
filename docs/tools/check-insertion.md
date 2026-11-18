---
order: 36
---

# `check_insertion`

Check that each part can go in. In the order of the assembly's `insertion` list, each part moves in a straight line from outside the parts already in place to its seated pose, in steps of `step_mm` (default 1 mm), with those parts held still. The first collision is narrowed to 0.05 mm and reported: which parts, where, and how far from seated. A part with no stated direction slides along its prismatic joint, both ways tried. Parts that overlap when seated, such as a thread, are listed and not counted on the way in.
