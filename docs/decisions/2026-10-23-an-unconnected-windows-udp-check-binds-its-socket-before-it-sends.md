---
date: 2026-10-23
order: 22
---

# An unconnected Windows UDP check binds its socket before it sends

A blocked implicit bind is reported as an invalid argument, and that datagram was not sent. Binding first makes the refusal an access error. An invalid-argument result is still not a closed network.
