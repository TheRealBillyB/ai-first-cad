# Decisions

Choices that later work should keep, unless a new entry overturns them.

Add a new date as a section at the top. Add a row at the top of that day's table. Keep the rationale to one line.

## 2026-10-21

| Decision | Rationale |
| --- | --- |
| A plugin that links aicad is AGPL-3.0-or-later | Combining an add-on with the core keeps the improvements shared. |
| Plugin permissions are a declaration, not a sandbox | Once allowed, a pack runs in-process; the design-script sandbox is a separate control. |
| A third-party plugin runs only after an explicit allow | Opening a design must not run an unverified pack, and the warning names the publisher and the permissions it declared. |
| Plugin API version 1 is entry points plus a static manifest | The host can list a pack and warn before importing it, and a breaking change has to bump the version. |
| A verified check is a kernel property of the model; a rough estimate samples, meshes, or uses a handbook number | A pass has to say which of those it is, or the number gets quoted as a proof. |
| A failed Gmsh mesh is retried finer, then healed, then defeatured, and a simplified mesh cannot pass | The thin-wall crash and the stiff linear-tet frequency both hid the real result. |
| CalculiX stays a separate ccx process | CalculiX is GPL-2.0-only and cannot be linked into this AGPL program. |
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
