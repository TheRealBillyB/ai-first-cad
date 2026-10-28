---
date: 2026-10-28
---

# A Windows script starts when the block is already there

Two scripts can start together. Creating the AppContainer profile can fail while that profile is already present, and installing the firewall block can fail because the block rule is already present. The start uses the profile that is already there, and an existing block rule is the block. A missing profile or a missing rule still refuses the script.
