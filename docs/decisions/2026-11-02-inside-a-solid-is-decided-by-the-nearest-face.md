---
date: 2026-11-02
---

# Inside a solid is decided by the nearest face

The ray classifier can call a point several millimetres outside a swept or lofted solid inside when its ray grazes a tangent seam, which made a cable check report a line clear of a swept tube as inside it; for a single solid whose nearest boundary point lies inside a face, the side of that face's outward normal now decides, and the classifier still decides on edges, vertices, the surface itself, and compounds. The gap is measured to the solid's shells, because the extrema tool given a bare solid runs the same classifier first and returns 0 for a point it calls inside.
