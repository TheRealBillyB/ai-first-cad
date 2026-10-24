---
date: 2026-10-23
---

# A plugin export refuses a Windows device name, including CONIN$ and CONOUT$

Built-in export already refused CON and NUL. A plugin export wrote those names, and CONIN$ and CONOUT$ were missing from the list.
