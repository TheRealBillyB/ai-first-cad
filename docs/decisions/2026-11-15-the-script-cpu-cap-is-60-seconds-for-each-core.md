---
date: 2026-11-15
---

# The script CPU cap is 60 seconds for each core

This replaces the flat 60 seconds of CPU in the 2026-10-21 limits. Every platform counts the CPU time of all of a script's threads, and OpenCascade runs a boolean on every core, so a build that took 5 seconds on the wall clock was stopped on a 24-core computer, and the same build had more room on a smaller one. The cap is now 60 seconds times the cores the script can run on. The parent counts the cores and passes the cap to the script. The 120-second wall clock and the 4 GiB memory cap are unchanged, a test can still lower the CPU cap, and nothing can raise it. A stop at a cap now names both caps.
