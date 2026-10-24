---
date: 2026-10-24
---

# macOS geometry stays on the calling thread

OpenCascade worker threads get a 512 KiB stack on macOS, and a boolean or a mesh overflows it.
