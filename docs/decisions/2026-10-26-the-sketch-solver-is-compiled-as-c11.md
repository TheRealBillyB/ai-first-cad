---
date: 2026-10-26
---

# The sketch solver is compiled as C11

GCC's C23 default removes a macro the solver's allocator uses. The installer asks for C11 unless the caller already chose C11 or C17, replaces a C23 choice, and leaves the MSVC default alone.
