---
date: 2026-10-26
---

# A project file is opened from a directory handle

The open, the size check, and the replacement all use that handle, so a link swapped in after the path was accepted is not followed on Windows, macOS, or Linux.
