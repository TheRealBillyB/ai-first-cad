---
date: 2026-11-05
---

# Windows scripts and solves skip the stricter container and use a normal AppContainer

The stricter container (LPAC) failed twice on every run and every run fell back, so it now is not tried, and the network block, the firewall rules, and the in-container network check are unchanged. First, Python exits with 0xC0000022 while it starts in LPAC, because registry keys it reads at start grant read to every app package and to Windows' registryRead capability but not to the restricted packages; adding registryRead lets it start. Second, importing aicad then fails in LPAC, because the chain build123d, sklearn, joblib, asyncio loads asyncio's _overlapped, which creates a socket, and LPAC refuses a socket without a network capability (WinError 10013); internetClient would allow it but removes the no-network-capability check, so it was not added. LPAC can be revisited after 1.0 with registryRead and an import path that does not create a socket.
