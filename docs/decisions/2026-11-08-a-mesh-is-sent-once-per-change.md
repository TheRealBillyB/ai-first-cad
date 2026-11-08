---
date: 2026-11-08
---

# A mesh is made once per solid and sent once per change

The app rebuilt and resent its meshes far more often than they changed: every change cleared every part's mesh, so moving one part meshed all of them again, every refresh including the 2-second busy check encoded the whole scene again and sent it, and a slider release in Assembly rebuilt every instance's triangles in Python and sent all of them, which on a 38-part finger rig with 96 instances took 4.2 s and 45 MB per release and 10.8 s per move. A part's viewport mesh is now kept with the solid it was made from and made again only when that solid is replaced, so a move meshes only the moved part, whose face references name where its faces now are, and a joint change meshes nothing. Each `/api/mesh` answer is encoded once and tagged with a hash of its bytes; the page sends the tag it holds and an unchanged mesh is a 304 with no body, and the geometry worker sends the encoded bytes as they are and only when the app process does not already hold them. Assembly is two answers, each part's triangles in its own frame and each instance's matrix and color, so after a joint change the page reads only the pose and moves the pieces it already has: a release now takes 0.13 s and sends 21 KB, and a move takes 2.2 s. A move still sends the whole Model scene, and sending it per part is a later change.
