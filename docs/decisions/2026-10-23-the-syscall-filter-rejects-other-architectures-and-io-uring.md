---
date: 2026-10-23
order: 29
---

# The syscall filter rejects other architectures and io_uring

A 32-bit socket call and io_uring both opened a socket while the network was off.
