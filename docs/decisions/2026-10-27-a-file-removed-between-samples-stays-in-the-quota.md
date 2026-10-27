---
date: 2026-10-27
---

# A file removed between samples stays in the quota

A file can be created and removed before the next timed look at the project directory. The directory is watched as it changes, and the file is opened when its name appears so its size can still be read after that name is gone. macOS reads an open unlinked file the same way Linux does. Windows takes the size from the directory change instead of waiting for the next timer.
