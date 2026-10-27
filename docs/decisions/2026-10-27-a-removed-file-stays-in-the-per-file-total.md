---
date: 2026-10-27
---

# A removed file stays in the per-file total

The per-file total keeps the largest size a script's file reached. Removing that file before the next snapshot does not drop those bytes, and an open file that has already been unlinked is included on Linux.
