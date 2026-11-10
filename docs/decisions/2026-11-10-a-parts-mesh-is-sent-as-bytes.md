---
date: 2026-11-10
---

# A part's mesh is sent as bytes

A part's mesh was sent as JSON, and the page turned each number into a 32-bit array as soon as it had parsed it, so on a 38-part finger rig the largest part cost 45 ms to write on the server and 8 ms to parse and copy on the page after every move, undo, and redo. The answer to `/api/mesh?part=` is now bytes: a 4-byte length, a JSON header of that length with the faces, the edges, the bounding box, and the name, type, and count of each array, and then the positions, normals, and triangle faces as 4-byte little-endian numbers, which are the same 32-bit values the page made before. The header is padded so that the arrays start on a multiple of 4 bytes, and the page reads them where they lie. Bytes that are shorter or longer than the header says are an error on the page, never a part with some of its triangles. The scene list, the assembly pieces, the pose, and every error are still JSON, and the tag of a part is still a hash of the bytes sent. On the rig the largest part went from 3.1 MB to 1.8 MB, all 38 parts from 35.3 MB to 21.5 MB, writing the largest part from 45 ms to 8 ms, and reading it on the page from 8 ms to under 1 ms.
