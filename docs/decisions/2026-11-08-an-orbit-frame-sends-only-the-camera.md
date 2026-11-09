---
date: 2026-11-08
---

# An orbit frame sends only the camera

Every orbit and pan frame used to copy the positions, normals and colors again, rebuild the face highlight by looking up each triangle's face in the selection, rebuild the edge arrays point by point, and send all six arrays to the GPU with `bufferData`, which on a 38-part finger rig of 257 thousand triangles took a median of 24 ms of page time per frame though only the camera had moved. Each array the solid is drawn from is now kept with what it was made from and made again only when that changes: positions from the mesh and the drag offset, normals and colors from the mesh, the face highlight from the mesh, the part, the selected faces by their contents, and live mode with its part, edge positions from the mesh and the drag offset, and the edge highlight from the mesh, the part and the selected edges. Each buffer is sent an array only when it is not the one it already holds. A highlight follows a pick, a part change, live mode and a drag as before. An orbit frame now takes 0.1 ms, a face pick sends only the face highlight and takes 1.6 ms, and a Move drag still makes the positions again each frame, taking 7 ms.
