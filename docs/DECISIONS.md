# Decisions

Choices that later work should keep, unless a new entry overturns them.

Add a new date as a section at the top. Add a row at the top of that day's table. Keep the rationale to one line.

## 2026-10-23

| Decision | Rationale |
| --- | --- |
| The syscall filter rejects other architectures and io_uring | A 32-bit socket call and io_uring both opened a socket while the network was off. |
| A pose matrix is the exact rigid transform | Rounding the rotation shears the solid, and the boolean then reports no volume for a real overlap. |
| A motion study is a timeline of drivers and gear couplings, posed by the joint solver | Later checks read one record of poses, collisions, and limit hits. |
| Interference starts with the axis-aligned boxes and only then intersects the solids | A pair whose boxes are farther apart than the clearance cannot touch, so the boolean runs on the pairs that can. |
| Penetration depth is the separating-axis translation, and the volume is the boolean | The depth is exact for convex solids. A non-convex solid reports the convex-hull depth, and the volume stays the real intersection. |
| A joint command past its stop is held at the stop and recorded | The pose stays on the limit, and the study says which step reached it. |

## 2026-10-22

| Decision | Rationale |
| --- | --- |
| The Linux process cap is the script's own threads, not every thread of the user | `RLIMIT_NPROC` is per user, so a 256 cap stalled geometry when a browser was open; a cgroup `pids.max` or the parent counting the script's threads does not, and classic seccomp cannot count `clone` or `fork`. |
| Windows and macOS tests are manual, weekly, or the `cross-platform` label | Windows and macOS run on manual dispatch, a weekly schedule, or the cross-platform label to keep CI fast. Ubuntu still runs on every push and pull request. |
| CI stops one test after 5 minutes, and the Windows job after 40 | A hung script was using the whole hour, so the log never reached the later tests. |
| Windows builds the drawing-font list once, in the Python install | The container cannot read the user-profile copy, and building that list inside every script is slow. |
| A Windows script keeps matplotlib's cache in the project | The AppContainer cannot read the user-profile cache, and that import was stopping every script. |
| macOS must be allowed to read the `/` directory | Without that read, a sandboxed script aborts instead of starting. The files inside other directories stay denied. |
| Windows does not rewrite this process's access list | An AppContainer cannot open a process that does not grant it, and adding a deny entry stopped later scripts from finishing. |
| The macOS profile does not name pipe or socket vnode types | `sandbox-exec` treats those names as unset variables and then refuses to start the script. |
| The script cannot open this aicad process on Windows | A job cannot block OpenProcess; the AppContainer is denied that right on this process. |
| macOS stops a script whose resident memory passes 4 GiB | The parent checks that a few times a second, because the address-space cap was stopping every script. |
| A failed design script keeps the one-shot network grant | The grant is spent only when that script finishes without an error. |
| Linux does not hide another process's `/proc` command line | Remounting `/proc` from the sandbox is refused, and Python still needs `/proc` for itself. |
| macOS does not set the 4 GiB address cap | The system library cache is already larger, and the cap was stopping every script. |
| The script's process tree is killed when the tool returns | Linux blocks a new session and kills the group; macOS kills a child that leaves the group; Windows terminates the job. |
| A script cannot signal its parent | Linux Landlock scope blocks signals leaving the sandbox; macOS and Windows refuse `os.kill` of another process in the child. |
| The sandbox flag is not an environment variable | A script can clear `AICAD_SANDBOX_CHILD`; plugin allow, load, and revoke stay refused anyway. |
| CI uses Python 3.12 on Ubuntu and 3.11 on Windows and macOS | The sketch solver publishes Windows and Mac wheels for 3.11 and not for 3.12, so 3.12 on those systems has to be compiled. |
| Windows confinement is an AppContainer plus a Job Object | A restricted token cannot read Python without changing system file permissions; the container blocks the user profile and the network, and the job caps memory, CPU time, and child processes. |
| macOS confinement is `sandbox-exec` plus `setrlimit` | Apple has deprecated `sandbox-exec`, and it is still the practical file and network profile that does not need a container. |
| One install command is `python install.py` | It creates the virtual environment and installs aicad on Windows, Mac, and Linux. |
| CalculiX stays a separate `ccx` program; the installer detects it and prints the command | aicad must not link GPL-2.0-only CalculiX, and the Python install does not download it. |
| An approval is a receipt on this computer, not the word approved in the project file | A downloaded or hand-edited plan can say approved; modeling stays off until approve-plan on this machine records that revision. |
| Plans are JSON and PNG in the project `plans/` folder, never scripts in the session | Opening a project must not run planning notes; a design file stays data until someone asks to run code. |
| A revision is cut only by revise, and it records what changed | Earlier revisions stay, and each one says what changed. |
| Approve, answer, and mark exist on every door, and the agent door refuses them | The assistant writes the plan; the person who has to live with it is the one who accepts it. |
| Modeling and simulation from a plan go through `start_model_from_plan` and `start_simulation_from_plan` | Those two commands are the gate: they refuse until this revision is approved, then they hand over its dimensions and assumptions. |
| A plan sheet uses the blueprint block style | A plan sheet is blue paper, gold dimensions, a grid, and a scale bar. It is not a shop drawing. |
| The workspace server does not trust a source field | The browser session is the person, and MCP is the assistant. A program on this computer can still act as the person. |
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
| A parts-list picture uses a system font that contains the names | A Latin font draws a name such as 供水 as empty boxes. The list looks for an installed font that has the character, and it does not add a font package. |
| An automatic explode gap stops at 1000000 mm | A huge model would otherwise ask for a gap the command rejects. |
| Output files avoid Windows reserved device names | con.pdf is a device on Windows, so the file is named con_file and the report says so. |
| Picture text is looked up in Linux, Windows, and macOS fonts | A missing font was falling back to a bitmap the assistant could not read. |
| A sheet ratio can be smaller than 1:1000 | The usual list stops there, and a larger model still has to land on the page. The sheet names the ratio it used. |
| A size below 1 mm is written out, and a still smaller size is labeled <0.001 | Drawing 0.04 mm as 0 was a false dimension. |
| A bill of materials is a count, and a mass on it is a handbook estimate | Quantities come from the instances. Volume times a handbook density is not a weighed mass, and the list has no prices. |
| An exploded view follows the joints | A slide moves on its axis, a hinge comes off its pin, and a catalog fastener lifts out. The gap is not a dimension. |
| A drawing uses OpenCascade hidden-line removal | The edges come from the kernel already in use, so the sheet does not add a solver. |
| A dimensioned sheet is a layout drawing, not a shop drawing | Overall sizes are the kernel's axis-aligned box of the posed solids. GD&T and tolerances stay off the sheet. |
| Save As copies the saved script solid | The solid lives in `.aicad/geometry`. The copy has to open without running the script. |
| A size or a placement stays within a million millimeters | The viewport stores coordinates in 32-bit floats, and a huge box next to a normal part makes the normal part disappear. |
| A new project folder uses a Windows folder name | Save As rejects a trailing space or dot, a reserved name such as CON, and characters Windows forbids, so the copy can be created there. |
| Live mode follows the assistant, not the person at the screen | A recording should frame the part an assistant just changed; the person's own drag should leave the camera where they put it. |
| Measure is two clicks, and a third click adds the angle | Distance is the line between the first two points, and the angle is at the second point, so the same command works from a click or from coordinates. |
| A size slider edits the number the solid was built from | An operation size, a sketch dimension, an extrude height, or a catalog length rebuilds the solid; a script or a STEP file has a placement and no such number. |
| Open, Save, and Save As are folders, and undo stays the history | A project is the folder on disk, and named versions would be a second history beside the undo stack. |
| `#plan-mount` and `#plan-body` are filled by `plan.js` | `plan.js` sends the workspace session token. |

## 2026-10-21

| Decision | Rationale |
| --- | --- |
| A design script cannot allow or load a plugin | Plugin consent stays a separate switch the person uses; the sandbox cannot grant it. |
| Design scripts and part scripts run in a Linux child with Landlock and seccomp | The file and network limits have to be real, and this kernel can apply them; if it cannot, the script does not run. Superseded by the Windows and macOS sandbox rows. |
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
