---
date: 2026-11-03
---

# The Windows suite runs in three shards

Sandboxed tests run one at a time because a machine has one set of firewall rules, which took about 33 of the job's 40 minutes. Each shard is its own runner with its own rules, so the limit stays and no test is skipped.
