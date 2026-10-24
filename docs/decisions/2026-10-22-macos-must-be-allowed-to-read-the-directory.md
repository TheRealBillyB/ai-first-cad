---
date: 2026-10-22
order: 6
---

# macOS must be allowed to read the `/` directory

Without that read, a sandboxed script aborts instead of starting. The files inside other directories stay denied.
