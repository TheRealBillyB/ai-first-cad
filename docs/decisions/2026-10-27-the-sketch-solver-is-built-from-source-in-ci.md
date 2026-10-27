---
date: 2026-10-27
---

# The sketch solver is built from source in CI

CI compiles python-solvespace 3.0.8 on Ubuntu, macOS, and Windows with an empty pip cache and no binary wheel. The build uses the installer's compiler flags. A C file has to be compiled with the standard that installer selected, and a C++ file keeps its own standard. MSVC is left unset, and that path is still compiled from source.
