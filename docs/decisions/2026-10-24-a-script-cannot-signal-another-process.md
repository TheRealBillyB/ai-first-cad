---
date: 2026-10-24
---

# A script cannot signal another process

Landlock can scope signals only on a newer kernel. The process filter blocks a signal call unless it names the script process that installed it, including from a child of that script, and the Landlock scope is used as well when the kernel has it.
