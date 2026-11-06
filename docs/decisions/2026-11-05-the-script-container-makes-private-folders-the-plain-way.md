---
date: 2026-11-05
---

# Inside the Windows script container, a private folder is made the plain way and checked

Python 3.11.10 and later make `os.mkdir(path, 0o700)` an owner-only folder on Windows, an AppContainer may not create a folder with an explicit access list, and `tempfile.mkdtemp` then retried "Access denied" until the job's CPU limit, so no script or solve ran. Inside a container only, such a folder is now made the plain way and kept only when its inherited list names no one beyond SYSTEM, Administrators, the owner, this account, and this container, and `tempfile.TMP_MAX` is lowered so a refusal ends at once. A run's job folder inherits the project's grant on Windows. CI's Windows test jobs use 3.11.9, which predates this, so it was found and checked by hand.
