---
date: 2026-11-01
---

# A section edge with material on both sides is not a wall

When a wall-thickness slice only touches a surface, for example along the tangent line of a hole, the section carries an edge with material on both sides. That edge is skipped instead of being read as a wall thinner than the smallest probe. An edge with no material on either side is still an upper bound that cannot pass a minimum.
