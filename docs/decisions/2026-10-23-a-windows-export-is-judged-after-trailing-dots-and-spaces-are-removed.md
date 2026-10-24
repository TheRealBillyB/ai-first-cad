---
date: 2026-10-23
order: 18
---

# A Windows export is judged after trailing dots and spaces are removed

``.. .`` is ``..`` once Win32 trims the name. That path is refused before anything is written.
