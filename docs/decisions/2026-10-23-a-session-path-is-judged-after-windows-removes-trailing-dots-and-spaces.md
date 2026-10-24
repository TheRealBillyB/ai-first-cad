---
date: 2026-10-23
---

# A session path is judged after Windows removes trailing dots and spaces

``.. .`` is ``..`` once Win32 trims the name. An export already refused that path. Opening the session now refuses it before the file is read.
