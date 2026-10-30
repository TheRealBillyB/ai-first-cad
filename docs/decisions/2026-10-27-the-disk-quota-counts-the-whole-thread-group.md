---
date: 2026-10-27
---

# The disk quota counts the whole thread group

On Linux the per-thread file `/proc/<tgid>/task/<tid>/io` is only that thread. The quota counts the whole process, so it reads the thread-group id and uses `/proc/<tgid>/io`. The same group is not added twice when more than one thread id is seen.
