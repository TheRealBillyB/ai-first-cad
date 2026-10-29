---
date: 2026-10-27
---

# The exit reading does not depend on waitid

The disk quota is read after the script has exited and before the process is collected. Collecting it first drops the write counter. When the system cannot report that exit without collecting the process, the exit is recognized another way: Linux reads `/proc`, macOS watches the process for exit, and Windows reads the process exit code. A report that the process is still running is not the only signal, so the wait does not run until the wall clock. The process is collected only after that reading.
