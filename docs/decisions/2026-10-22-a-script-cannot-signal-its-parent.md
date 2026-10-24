---
date: 2026-10-22
order: 15
---

# A script cannot signal its parent

Linux Landlock scope blocks signals leaving the sandbox; macOS and Windows refuse `os.kill` of another process in the child.
