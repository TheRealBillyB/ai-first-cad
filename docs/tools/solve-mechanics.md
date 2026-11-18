---
order: 25
---

# `solve_mechanics`

Linear static solve. Returns the safety factor, the design review, the assumptions, and stress and displacement pictures. Sharp inside corners and inside radii under 1 mm that are pulled at least half as hard as anywhere in the part are listed in `stress_raisers`. When the peak sits at such a corner, the mesh is refined around it and `convergence` says whether the peak settles or keeps climbing (`refine_peak: false` skips the extra solves). `print_up`, the part's up direction on the print bed, gives the safety factor against the stress that crosses the layers and says when the main tension runs across them.
