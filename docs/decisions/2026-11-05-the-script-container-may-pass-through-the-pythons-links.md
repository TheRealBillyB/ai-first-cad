---
date: 2026-11-05
---

# The script container may pass through the links on its Python's own paths

uv names its Python with a junction to the exact release, and a venv records that name, but the sandbox granted the release folder resolved, so a container could not look at the junction itself and Python's start was refused there. Each junction or symbolic link on a granted path or the interpreter's own paths now gets read and traverse for that container on the link alone, with `icacls /L`, which opens nothing the target folder's own grants do not.
