---
date: 2026-11-05
---

# A container child that cannot start exits instead of showing a dialog

A Python refused while it started in the stricter container showed "Application Error 0xc0000022" on the person's desktop and stayed alive, and the probe waited 60 seconds and reported STILL_ACTIVE (259). The app now sets SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX and SEM_NOOPENFILEERRORBOX before it starts a child, which inherits them, a probe that runs out of time is stopped and says so, and a known start-up failure is named.
