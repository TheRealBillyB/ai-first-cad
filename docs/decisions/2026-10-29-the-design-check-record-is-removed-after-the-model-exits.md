---
date: 2026-10-29
---

# The design-check record is removed after the model exits

The process that records a design script's checks keeps that file open until the script finishes. Removing the file while the process still has it fails on Windows. The process and a part script it started are stopped first. A nested part script does not inherit the file. A folder the container can already modify is not walked again, and a firewall read-back from a process that is still running is reused, so those steps do not consume the script's time limit.
