---
order: 90
---

## What this version does

A script the assistant sends runs in a separate process. That process can read and write the project folder. It cannot use the network unless you turn that on yourself (`aicad app --script-network`, or **Allow network on the next script** in the app). It stops if it runs too long or uses too much memory. If this computer cannot apply those limits, the script is not run.
