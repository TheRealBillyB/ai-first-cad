---
order: 290
separator: line
---

- **X.** A project `.env`, pip or uv configuration, `pyproject.toml`, or `requirements.txt` does not change the process environment. A module in the project does not replace the standard library, and `sitecustomize` there does not run.
