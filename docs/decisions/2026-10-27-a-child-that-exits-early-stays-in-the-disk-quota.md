---
date: 2026-10-27
---

# A child that exits early stays in the disk quota

A child can write its bytes and exit before the parent opens a counter for that process. On Linux, collecting the child adds those logical writes to the script, and the script does that before it exits. On macOS the logical writes are read while the child is still a zombie, including when a Python wait collects it. The script's own counter is still read at exit.
