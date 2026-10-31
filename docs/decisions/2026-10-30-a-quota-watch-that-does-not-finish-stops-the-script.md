---
date: 2026-10-30
---

# A quota watch that does not finish stops the script

The watch is asked to stop and woken. The run waits a short time. If the watch is still in the tree, the script is stopped and the job folder stays until that watch leaves, so cleanup does not race it and the run does not wait forever.
