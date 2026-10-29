---
date: 2026-10-29
---

# A Windows part script is stopped before the check record

A design script that runs out of wall time stops its model process, then removes the check record. On Windows the part scripts that model started are not in the process list returned for other systems, and stopping the model first reparents them, so they still hold the record and the delete fails. They are listed and stopped while the model is still their parent, and the terminate right is the only right requested, because an AppContainer refuses a broader open.
