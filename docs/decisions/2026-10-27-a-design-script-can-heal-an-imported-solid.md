---
date: 2026-10-27
---

# A design script can heal an imported solid

The STEP reader opens the null device so the parser's native log is discarded. The script file check treated that open as the script writing outside the project, so the read stopped and healing the imported solid never ran. The reader opens the null device itself. A script that names the device is still refused.
