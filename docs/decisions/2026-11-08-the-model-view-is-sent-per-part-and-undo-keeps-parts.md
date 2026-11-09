---
date: 2026-11-08
---

# The model view is sent per part, and undo keeps the parts it did not change

The model view was one mesh of every part, so moving one part of a 38-part finger rig sent all 48 MB again, and undo threw every part away and built it again from the saved session, which for one undone move imported 35 STEP files, rebuilt two catalog bearings, measured and meshed every part, and took 17.6 s. The page now reads a short list of the built parts, each with its color and the tag of its own mesh, and fetches only a part whose tag it does not hold, then joins the parts into one mesh itself, so a move sends the moved part. Undo keeps each part whose saved record is exactly what it holds now, with its solid, measurement, and mesh, and a part whose record differs only in its placement keeps its solid and is placed again; a part that follows another part through a mount or a sketch on a face, a part made from a sketch that changed, and a part that failed to build are built again as before, and the result matches opening the restored session. On the same rig a move takes 1.3 s and sends 2.9 MB, and undoing it takes 1.4 s, nearly all of it meshing the one part that moved.
