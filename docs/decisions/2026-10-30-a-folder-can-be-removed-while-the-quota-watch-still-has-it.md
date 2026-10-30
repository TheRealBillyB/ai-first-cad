---
date: 2026-10-30
---

# A folder can be removed while the quota watch still has it

On Windows a script removes a file name immediately while the disk-quota watch still holds that file or is scanning its folder, so the folder can be removed, and the watch still counts the bytes with the same coverage and per-change scan.
