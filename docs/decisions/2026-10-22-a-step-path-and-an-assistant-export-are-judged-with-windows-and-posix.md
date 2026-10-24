---
date: 2026-10-22
order: 31
---

# A STEP path and an assistant export are judged with Windows and POSIX rules, and native stdout goes to `os.devnull`

A drive letter, a UNC path, or a backslash `..` has to be outside the project on every machine, and the descriptor restore has to work where the null device is `nul`.
