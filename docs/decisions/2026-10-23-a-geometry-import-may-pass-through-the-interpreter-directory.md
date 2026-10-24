---
date: 2026-10-23
order: 6
---

# A geometry import may pass through the interpreter directory

Native libraries change that directory and put the project back. A relative file in the project still has to open, and no other directory counts as staying put.
