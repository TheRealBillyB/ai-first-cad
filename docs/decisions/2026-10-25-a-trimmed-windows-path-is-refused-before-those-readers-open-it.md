---
date: 2026-10-25
---

# A trimmed Windows path is refused before a record, a STEP name, or a workspace file is opened

``....\....\hidden`` is ``..\..\hidden`` after Win32 removes trailing dots. Those readers now use the same check as a session path.
