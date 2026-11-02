---
date: 2026-11-02
---

# A part in an assembly is not deleted

`delete_part` removed a part that a stored assembly still used and saved the project. Opening that project again failed, because loading checks that every assembly's parts exist, so the project could not be opened to fix it. `delete_part` now refuses a part that any assembly uses and names the assembly, the same way it refuses a part that something is mounted on. A project already saved in that state opens without the assembly and lists it under problems, so it can be rebuilt.
