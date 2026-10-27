---
order: 410
separator: line
---

- A design script's disk quota counts the bytes it writes, including a file that was removed before the next look. The count is read again when the script exits, so a run that finishes before the next look is still counted.
