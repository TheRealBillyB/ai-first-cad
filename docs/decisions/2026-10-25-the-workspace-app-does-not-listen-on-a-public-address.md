---
date: 2026-10-25
---

# The workspace app does not listen on a public address

A Host header can say localhost while the connection comes from another machine, so the bind address and the peer both have to be loopback.
