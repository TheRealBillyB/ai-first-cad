---
date: 2026-10-31
---

# Approving a plan builds its solids

A plan may store build steps, each a part name and a script that assigns `part`. The script is data. Opening the project does not run it. When the person approves that revision, the app runs each script in the part-script sandbox, with the network off, and builds those parts. The approval receipt covers the scripts. A later edit does not match that receipt, so the edited script does not run. A failed step names the step and leaves the previous model in place. A plan with no build steps still only hands over its dimensions and assumptions. The assistant cannot approve.
