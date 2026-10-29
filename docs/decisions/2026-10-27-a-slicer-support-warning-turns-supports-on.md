---
date: 2026-10-27
---

# A slicer support warning turns supports on

PrusaSlicer and OrcaSlicer can finish a toolpath and still warn that the print has floating regions, low bed adhesion, or some other reason to enable supports. The first toolpath is not the one that is kept. The same slicer is run again with supports turned on, and that toolpath is the result when it is written.
