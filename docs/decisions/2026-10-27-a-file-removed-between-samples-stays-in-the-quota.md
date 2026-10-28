---
date: 2026-10-27
---

# A file removed between samples stays in the quota

A file can be created and removed before the next timed look at the project directory. The directory is watched as it changes, and the file is opened when its name appears so its size can still be read after that name is gone. macOS looks again as the directory changes, and reads an open unlinked file the same way Linux does. That watch does not query process files. Windows keeps a read handle that shares delete. Replacing a file unlinks the name immediately, so the script can still replace it, including a session and a STEP, while that handle keeps the size. The watch starts after the script process is created and before the script runs.
