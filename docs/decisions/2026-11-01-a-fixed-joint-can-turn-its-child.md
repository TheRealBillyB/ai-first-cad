---
date: 2026-11-01
---

# A fixed joint can turn its child

A fixed joint takes an optional `rotation_deg` [x, y, z]. The child turns about its child anchor by that rotation, using the same order as a part placement, and then the child anchor goes onto the parent anchor. A catalog part built with its seat on z = 0, such as a flanged bearing, can be mounted facing the other way without drawing a second part. Robot export writes the same turn as the fixed joint's origin rotation. Other joint types refuse `rotation_deg`, because their motion already sets the child's orientation.
