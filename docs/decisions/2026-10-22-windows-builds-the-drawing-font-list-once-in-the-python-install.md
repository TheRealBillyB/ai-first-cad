---
date: 2026-10-22
order: 4
---

# Windows builds the drawing-font list once, in the Python install

The container cannot read the user-profile copy, and building that list inside every script is slow.
