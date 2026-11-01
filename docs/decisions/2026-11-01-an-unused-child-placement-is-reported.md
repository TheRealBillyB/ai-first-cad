---
date: 2026-11-01
---

# An unused child placement is reported

An instance that is the child of a joint takes its pose from that joint, so its own placement is not used. When such an instance has a non-zero position or rotation, solving the assembly adds a warning that names the instance and the joint and says the placement is not used. The pose itself is unchanged.
