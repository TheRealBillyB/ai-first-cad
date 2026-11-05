---
date: 2026-11-05
---

# The Windows installer offers the sandbox setup

Without the setup, no design script or solve runs on Windows, so the installer explains that and asks at the end. The default is no. No answer fails the install, because modeling works without it, and an install with no one at the keyboard does not ask. `aicad sandbox-setup --status` is the one read-only check that the installer, the docs, and a person can all use. The elevated step's reason travels in its exit code, so a failure can be named without an elevated process writing a file.
