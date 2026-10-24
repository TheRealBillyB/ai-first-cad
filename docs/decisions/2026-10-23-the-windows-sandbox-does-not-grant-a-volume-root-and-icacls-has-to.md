---
date: 2026-10-23
order: 37
---

# The Windows sandbox does not grant a volume root, and icacls has to finish

icacls waits for a person on the drive root, so that folder is skipped. Any other icacls call that does not finish is an error, and the sandbox does not change that folder.
