---
date: 2026-10-27
---

# A script can heal the solid it built

`heal` required the kernel's shape wrapper. A part script or a design script holds the solid it just built, so the call failed before sewing. `heal` accepts that solid. The result is still a kernel shape, and a part script assigns `part` from `healed.impl`.
