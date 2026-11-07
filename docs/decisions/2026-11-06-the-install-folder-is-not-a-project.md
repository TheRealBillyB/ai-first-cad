---
date: 2026-11-06
---

# The folder that holds the running aicad is not a project

aicad does not start a program inside an open project, so `aicad app --workspace .` run from the install folder made the CalculiX that `install.py --with-calculix` put in `.venv\calculix` look missing, and a design script there could have written to `.venv` and to aicad's own code. A project folder that is, or contains, the running Python environment or the aicad package is now refused when it opens, when a project is switched, and by Save As. The message names the install and says to pick or create a project folder, such as `aicad_workspace` beside it. The rule that skips programs inside a project stays as it is.
