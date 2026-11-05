---
date: 2026-11-05
---

# The app answers before the project has opened

With the worker, a 10-part project left the browser closed and the terminal silent for 36 s while its first state was built, which looked like a hang. The page now answers at once and says the project is opening, an assistant gets a "still opening, nothing was changed" answer it can retry, and tests and the MCP server still get an open project before their first request.
