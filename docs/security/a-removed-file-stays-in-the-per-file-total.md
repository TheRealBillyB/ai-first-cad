---
order: 415
separator: line
---

- A file a script removes is still counted at the size it reached, including toward the per-file cap. A file that appears and is removed between timer samples is counted too. macOS counts an unlinked file that is still open. Windows counts the file from the directory change.
