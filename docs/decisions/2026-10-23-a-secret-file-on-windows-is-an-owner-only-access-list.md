---
date: 2026-10-23
order: 17
---

# A secret file on Windows is an owner-only access list

The mode bits from stat stay world-writable on Windows. The access list is what keeps the file private.
