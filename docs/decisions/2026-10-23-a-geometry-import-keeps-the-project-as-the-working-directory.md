---
date: 2026-10-23
order: 3
---

# A geometry import keeps the project as the working directory

The image library's attach routine points the process at the base interpreter, then puts the project back. That request is refused, so a relative file in the project still opens and a plugin file there is not loaded.
