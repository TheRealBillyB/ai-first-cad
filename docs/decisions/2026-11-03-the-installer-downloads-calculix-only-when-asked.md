---
date: 2026-11-03
---

# The installer downloads CalculiX only when asked

Windows had no working install: the linked release page has no releases, and conda's Library folder on PATH stops powershell.exe. `install.py --with-calculix` reuses the pinned, checksummed CI build, which stays an unmodified, separate GPL-2.0-only program.
