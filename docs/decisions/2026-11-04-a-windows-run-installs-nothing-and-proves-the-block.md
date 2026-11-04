---
date: 2026-11-04
---

# A Windows run installs nothing and proves the block

A run reads the permanent filters by key, then checks from inside the container that the network is refused. A missing filter stops the run before a datagram can leave, and the check inside the container still decides. A solver or other helper cannot check for itself, so a small Python process does it first in the same container. Administrators and CI take this same path, so CI tests what people get. This replaces the firewall timeouts and the recorded-rules file from the 2026-10-23 entries.
