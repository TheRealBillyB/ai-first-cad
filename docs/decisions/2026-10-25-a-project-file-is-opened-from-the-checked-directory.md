---
date: 2026-10-25
---

# A project file is opened from the checked directory

The read uses the same directory descriptor as a write, so a link swapped in after the check is not followed. A session also stops at a fixed size and nesting depth.
