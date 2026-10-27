---
date: 2026-10-27
---

# The exit reading does not depend on waitid

The disk quota is read after the script has exited and before the process is collected. Collecting it first drops the write counter. When the system cannot report that exit without collecting the process, a zombie is recognized another way: Linux reads `/proc`, macOS asks libproc, and Windows reads the process exit code. The process is collected only after that reading.
