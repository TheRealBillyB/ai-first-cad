---
date: 2026-10-24
---

# A script is not run when another thread already exists

The process filter is installed on every thread that already exists. Geometry libraries are imported after that, and if a thread was already running the script is not run.
