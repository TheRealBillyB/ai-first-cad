# Documentation

Software docs live in this folder. Three masters are generated, and a pull request does not edit them:

- `docs/DECISIONS.md` is generated from one file per decision in `docs/decisions/`.
- `SECURITY.md` is generated from one file per guarantee in `docs/security/`.
- The tool table in `README.md` is generated from one file per tool in `docs/tools/`.

Add an entry file. Leave the master for the docs rebuild on `main`. The file format is in [CONTRIBUTING.md](../CONTRIBUTING.md).

`docs/ROADMAP.md` is hand-written. Edit that file directly.
