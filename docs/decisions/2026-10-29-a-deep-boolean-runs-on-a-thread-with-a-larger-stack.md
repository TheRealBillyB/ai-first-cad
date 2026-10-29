---
date: 2026-10-29
---

# A deep boolean runs on a thread with a larger stack

On macOS a pytest worker keeps the process stack, about 8 MiB, and OpenCascade's own pool threads are 512 KiB. A rod-handler boolean or mesh recurses further than that and the worker exits with no traceback. That call is pinned to one pool thread and, when the caller is smaller than 16 MiB, runs on a Python thread that has the geometry stack.
