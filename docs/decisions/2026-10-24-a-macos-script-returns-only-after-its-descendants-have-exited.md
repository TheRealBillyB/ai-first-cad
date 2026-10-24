---
date: 2026-10-24
---

# A macOS script returns only after its descendants have exited

Every descendant is stopped, the list is read again for a child created in that gap, and the call waits until those processes have exited. It returns only when a second look is empty. A listing that fails is not a clean run.
