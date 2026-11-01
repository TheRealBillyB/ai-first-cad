---
date: 2026-11-01
---

# A plan part without its solid is built again

`start_model_from_plan` runs the approved scripts when a listed part is missing, has no solid, or is no longer that script. A saved hash that does not belong to that script is not treated as the approved solid. When every listed part is still that script and its solid, the scripts are not run again. A rebuild can be undone.
