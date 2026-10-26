---
date: 2026-10-25
---

# A script log is not read past 1 MiB

Appending a row reads only the newest part of the log. A failure note reads only its last 500 bytes.
