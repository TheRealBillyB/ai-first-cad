# Decisions

Choices that later work should keep, unless a new entry overturns them.

Add a new date as a section at the top. Add a row at the top of that day's table. Keep the rationale to one line.

## 2026-10-21

| Decision | Rationale |
| --- | --- |
| Design scripts and part scripts run in a Linux child with Landlock and seccomp | The file and network limits have to be real, and this kernel can apply them; if it cannot, the script does not run. |
| Opening a project loads script text and a saved STEP solid | A downloaded session must not execute Python just because it was opened. |
| The import allowlist is a clear error; Landlock is the boundary | build123d can reach past a Python import list, so the operating system is what confines the process. |
| Design-script permission lives outside the project | A script can write the project folder, so it must not be able to allow itself. |
| Network stays off unless the person turns it on | The assistant has no switch for this; the app button or `--script-network` does. |
| Limits are 120 seconds, 60 seconds of CPU, and 4 GiB | A test can lower them. Nothing can raise them. The parent kills the child at the wall-clock limit. |
| Part scripts are sandboxed but not opt-in | Making one solid is the normal path. A multi-step design script is the one the person agrees to, and the one that is logged. |
| MCP stays on stdio for this version | Cursor, Claude Desktop, and a generic stdio client can connect. HTTP and SSE wait. |
| `save_project` rewrites session.json; `open_project` switches folders | Edits are already saved. Opening does not run scripts. The CLI still takes `--workspace` per command. |
| AGPL-3.0-or-later | A service for humanity, so improvements stay shared: no paid license, and contributions use a DCO. |

## 2026-10-19

| Decision | Rationale |
| --- | --- |
| Python, with build123d on OpenCascade, behind our own layer | We need a real solid modeler, and keeping it behind one layer means a later engine change stays local. |
| MCP is the assistant's interface, next to the Python API and the CLI | An assistant has to call the same operations a person will, and MCP is how current assistants attach to a local program. |
| Open source, released later | The tool is meant for other people to use and extend; a public release waits until the first version holds together. |
| Millimeters everywhere | Printers, fasteners, and mechanical drawings are specified in millimeters; one unit avoids silent conversions. |
| Reuse open-source solvers | Sketch constraints, meshing, and stress already have maintained tools (SolveSpace, Gmsh, CalculiX, and their peers); we connect them and explain their answers. |
| Hard-body mechanical parts first | Brackets, frames, and robots are the motivating work; hoses, electronics, and drawings wait until a solid can be built, seen, and checked. |

## Related

- [Principles](PRINCIPLES.md)
- [Roadmap](ROADMAP.md)
