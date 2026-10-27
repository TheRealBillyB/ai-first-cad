---
order: 410
separator: line
---

- A design script's disk quota counts the bytes it writes, including a file that was removed before the next look. The count is read again when the script exits, before the process is collected, so a run that finishes before the next look is still counted. That reading still happens when the system cannot report the exit without collecting the process.
