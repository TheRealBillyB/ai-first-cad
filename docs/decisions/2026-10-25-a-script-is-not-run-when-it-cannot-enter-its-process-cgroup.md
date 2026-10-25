---
date: 2026-10-25
---

# A script is not run when it cannot enter its process cgroup

A failed move would leave the script outside `pids.max`, so the script is not run.
