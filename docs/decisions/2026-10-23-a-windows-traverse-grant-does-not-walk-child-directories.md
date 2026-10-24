---
date: 2026-10-23
order: 35
---

# A Windows traverse grant does not walk child directories

The named security call propagated access to every child and did not return. The entry is written on that directory's handle only, a parent that already allows traverse is skipped, and the probe result is kept for the process.
