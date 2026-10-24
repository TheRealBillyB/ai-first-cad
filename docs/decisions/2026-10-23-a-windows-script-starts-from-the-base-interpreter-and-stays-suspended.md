---
date: 2026-10-23
order: 26
---

# A Windows script starts from the base interpreter and stays suspended until the job holds it

The virtual-environment redirector starts a second process in its own job. The script is resumed only after the sandbox job holds it and its token is an AppContainer.
