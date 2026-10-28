---
date: 2026-10-27
---

# A file removed between samples stays in the quota

A file can be created and removed before the next timed look at the project directory. The directory is watched as it changes. On Linux and macOS the file is opened when its name appears, so its size can still be read after that name is gone. macOS uses the system directory watch and reads an open unlinked file the same way Linux does. Windows reads the size from the directory entry when the name changes, and does not keep the file open, so the script can still replace it.
