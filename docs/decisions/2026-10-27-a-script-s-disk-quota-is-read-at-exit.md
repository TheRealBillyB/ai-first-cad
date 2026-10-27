---
date: 2026-10-27
---

# A script's disk quota is read at exit

On Linux and macOS the bytes a script has written are read again when that process exits, before it is collected. A run that finishes before the next periodic sample is still counted. The script does not run until that counter can be read.
