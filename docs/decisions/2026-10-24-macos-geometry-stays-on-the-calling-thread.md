---
date: 2026-10-24
---

# macOS geometry stays on the calling thread

OpenCascade worker threads get a 512 KiB stack on macOS, so geometry stays on the caller and a Python caller gets an 8 MiB stack.
