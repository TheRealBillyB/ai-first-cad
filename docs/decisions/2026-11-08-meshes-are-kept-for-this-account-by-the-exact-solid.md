---
date: 2026-11-08
---

# Meshes are kept for this account, by the exact solid

Opening a project meshed every part for the model view and tessellated every part again for the assembly view, which on a 38-part finger rig was 12 of the 19 seconds before both views were ready; a mesh is a function of its solid, so each one is now stored in `~/.config/aicad/meshes`, or `$AICAD_MESH_CACHE`, under a hash of the solid's exact BREP text and of the meshing code and the build123d, OpenCascade, and numpy versions, and opening the project again reads it, which brought the wait to 7.6 seconds. The hash leaves out each shape's line of flags, because meshing a part placed in the model view changes the flags of the unplaced part it shares faces with, and a key that changes anyway only misses a stored mesh and never finds another solid's. The folder is this account's, beside its other aicad files, and not the project's, so a shared project cannot carry a mesh that shows something its solid is not; the oldest files are removed past 1 GB, a file that cannot be read is meshed again, a folder that cannot be written only means meshing each time, and each test runs with a folder of its own.
