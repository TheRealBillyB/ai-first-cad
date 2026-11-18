---
date: 2026-11-17
order: 1
---

# A peak at a sharp inside corner is refined, and its climb is reported

A sharp inside corner has no finite stress in a linear model, so the number at it depends on the mesh. A strength solve now finds sharp inside corners and inside radii under 1 mm from the CAD edges, and lists those pulled at least half as hard as anywhere in the part. When the peak sits at one, the mesh is refined in a ball at that corner (and the next two hottest) up to three times, halving the element size, and each reading is reported. A peak that does not settle is called not converged, the headline is the highest reading, and the review cannot pass. Each extra solve keeps the solver's own time limit, which is not raised; a refinement that stops says why. With `print_up`, the across-layer safety factor is against the stress that actually crosses the layers instead of the peak von Mises stress.
