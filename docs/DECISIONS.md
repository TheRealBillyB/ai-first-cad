# Decisions

Choices that later work should keep, unless a new entry overturns them.

Add a new date as a section at the top. Add a row at the top of that day's table. Keep the rationale to one line.

## 2026-10-22

| Decision | Rationale |
| --- | --- |
| A thin region is coarse when its elements are thinner than a third of the mesh size | Aspect ratio alone still passed a 0.05 mm foil at 0.4 mm and a 0.3 mm rib. The 2.4 mm gusset at 1.5 mm stays measurable. |
| A wall probe that only lands on the opposite face is not used; a smaller step is tried | A step of exactly 0.01 mm sits on the inner face of a 0.01 mm wall, so that wall was reported as unmeasurable. |
| A STEP path and an assistant export are judged with Windows and POSIX rules, and native stdout goes to `os.devnull` | A drive letter, a UNC path, or a backslash `..` has to be outside the project on every machine, and the descriptor restore has to work where the null device is `nul`. |
| A mesh with one element through a thin wall, or with very flat elements, cannot pass a strength check | The shell's bounding box looked solid, and a coarse mesh reported a safety factor that was not real. |
| A wall ray starts 0.01 mm inside the face, and a hit that has left the solid is discarded | A 0.05 mm shell was reported as the far side of the cavity and passed a 1 mm check. |
| A number that is not finite, or a size past 1 km, is rejected before the session changes | A NaN placement was saved by the next command, and a 1e15 mm box was stored as a solid. |
| Opening a session reports a bad part and still loads the others | One corrupt placement used to make every other part unreachable. |
| A STEP path stored in the session has to stay inside the project | An absolute path or `..` was imported on open, and the file was outside the project. |
| Notes returned to the assistant are wrapped as untrusted text, and an assistant export stays in the project | Project text is data. A tool result must not be able to write a STEP outside the folder. |
| Native geometry code cannot write on the MCP stdout | The STEP parser's error line was not JSON-RPC, and it landed on the protocol stream. |
| Opening someone else's project does not create their notes or requirements file | Those files belong to that project. A missing one is not a template to write. |
| `check-wall` accepts `--min-wall`, the same flag as `inspect` | The two commands were spelling one threshold differently. |

## 2026-10-21

| Decision | Rationale |
| --- | --- |
| A design script cannot allow or load a plugin | Plugin consent stays a separate switch the person uses; the sandbox cannot grant it. |
| Design scripts and part scripts run in a Linux child with Landlock and seccomp | The file and network limits have to be real, and this kernel can apply them; if it cannot, the script does not run. |
| Opening a project loads script text and a saved STEP solid | A downloaded session must not execute Python just because it was opened. |
| The import allowlist is a clear error; Landlock is the boundary | build123d can reach past a Python import list, so the operating system is what confines the process. |
| Design-script permission lives outside the project | A script can write the project folder, so it must not be able to allow itself. |
| Network stays off unless the person turns it on | The assistant has no switch for this; the app button or `--script-network` does. |
| Limits are 120 seconds, 60 seconds of CPU, and 4 GiB | A test can lower them. Nothing can raise them. The parent kills the child at the wall-clock limit. |
| Part scripts are sandboxed but not opt-in | Making one solid is the normal path. A multi-step design script is the one the person agrees to, and the one that is logged. |
| MCP stays on stdio for this version | Cursor, Claude Desktop, and a generic stdio client can connect. HTTP and SSE wait. |
| `save_project` rewrites session.json; `open_project` switches folders | Edits are already saved. Opening does not run scripts. The CLI still takes `--workspace` per command. |
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
