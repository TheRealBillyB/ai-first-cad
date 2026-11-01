---
order: 1020
---

- **DB.** A plan build script runs in the part-script sandbox with the network off. Its directory is not the project, so it does not read or write the project. The project's time limit and the script byte limits still apply. A failed step leaves the previous model in place.
