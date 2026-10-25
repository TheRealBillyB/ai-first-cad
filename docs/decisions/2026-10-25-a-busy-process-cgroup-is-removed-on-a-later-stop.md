---
date: 2026-10-25
---

# A busy process cgroup is removed on a later stop

A directory that is still busy after the script stops is remembered and removed the next time a cgroup is prepared or released.
