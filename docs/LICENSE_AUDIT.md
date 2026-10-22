# Dependency license audit

Audited 2026-10-21, against aicad `AGPL-3.0-or-later`.

This is a compatibility check of the packages aicad installs or runs, not a legal opinion. There is no lockfile. Versions are the newest PyPI release on that date that satisfies `pyproject.toml`, and the direct dependencies of those releases. "Imported" means aicad loads it in the same Python process. "Separate program" means aicad only launches an executable and exchanges files.

## Result

Nothing in the runtime, optional, or build set is incompatible with AGPL-3.0-or-later in the way aicad uses it.

Two items need care, and neither blocks this license:

- **CalculiX (`ccx`) is GPL-2.0-only.** GPL-2.0-only cannot be linked into an AGPL-3.0 program. aicad does not link it. `src/aicad/fea.py` and `src/aicad/modal.py` run `ccx` as a separate process. Keep it that way.
- **Gmsh and python-solvespace are imported, and they are GPL.** GPL-2.0-or-later and GPL-3.0-or-later can be combined with AGPL-3.0 (GPLv3 section 13). The AGPL network-source term applies to that combined program. A distributor of aicad has to be able to ship the corresponding source of those GPL parts.

PyPI metadata is incomplete for a few permissive packages. Each of those was checked against the upstream `LICENSE` file. Details are in [Flagged](#flagged).

## How a "yes" was decided

- MIT, MIT-CMU, BSD-2-Clause, BSD-3-Clause, ISC, Apache-2.0, PSF-2.0, Zlib, 0BSD, CC0-1.0, and MIT-0 can be included in an AGPL-3.0 work.
- MPL-2.0 can sit inside an AGPL-3.0 work. The MPL still covers the MPL files.
- LGPL-2.1-only with the Open CASCADE exception can be dynamically linked from an AGPL-3.0 program. The LGPL stays on the Open CASCADE library.
- GPL-2.0-or-later and GPL-3.0-or-later can be combined with AGPL-3.0. GPL-2.0-only cannot.
- A separate program that aicad only launches is not combined with aicad. Its license stays on that program.

## Direct dependencies

Declared in `pyproject.toml`.

| Package | Version | SPDX | How aicad uses it | AGPL-3.0 compatible |
| --- | --- | --- | --- | --- |
| build123d | 0.13.0 | Apache-2.0 | Imported (`aicad.kernel`) | Yes |
| bd_warehouse | 0.4.0 | Apache-2.0 | Imported (`aicad.library`) | Yes |
| gmsh | 4.15.2 | GPL-2.0-or-later | Imported (`aicad.fea`, `aicad.modal`) | Yes, combined under GPLv3 §13 |
| mcp | 2.3.0 | MIT | Imported (`aicad.server`) | Yes |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Imported | Yes |
| pillow | 13.0.0 | MIT-CMU | Imported (`aicad.render`) | Yes |
| python-solvespace | 3.0.8 | GPL-3.0-or-later | Imported (`aicad.sketch`) | Yes, combined under GPLv3 §13 |

build123d 0.13 requires `numpy>=2`, which is tighter than aicad's `numpy>=1.26`. The installed NumPy is 2.x.

## Geometry kernel: build123d, OCP, and Open CASCADE

aicad imports `build123d` and `OCP` (see `src/aicad/kernel.py`). The `OCP` module is provided by **cadquery-ocp-novtk**, which build123d depends on. It is not the unrelated PyPI project named `OCP` (0.1.9, whose own metadata says LGPL-2.1-or-later). That project is not installed.

| Package | Version | SPDX | Role | AGPL-3.0 compatible |
| --- | --- | --- | --- | --- |
| cadquery-ocp-novtk | 8.0.1.1.0 | Apache-2.0 for the bindings. The wheel also ships Open CASCADE. | Provides `import OCP` | Yes. See the flag below. |
| cadquery-ocp-proxy | 8.0.1.1.0 | Apache-2.0 | Required by cadquery-ocp-novtk and by ocpsvg | Yes |
| Open CASCADE Technology | bundled in the cadquery-ocp-novtk 8.0.1.1.0 wheel (about 67 MB) | LGPL-2.1-only WITH OCCT-exception-1.0 | Shared libraries inside that wheel | Yes, as a dynamically linked LGPL library |

The Open CASCADE README also offers a commercial license as an alternative for Open CASCADE itself. aicad uses the LGPL grant. aicad does not buy that license and does not offer one.

`cadquery-ocp` (the wheel that includes VTK) is not what build123d 0.13 installs. VTK is BSD and is not a current dependency. Do not switch the dependency onto the PyPI project named `OCP`.

## Other packages pip installs with build123d

| Package | Version | SPDX | Note | AGPL-3.0 compatible |
| --- | --- | --- | --- | --- |
| typing_extensions | 4.16.0 | PSF-2.0 | | Yes |
| svgpathtools | 1.8.0 | MIT | Also pulls svgwrite (MIT) | Yes |
| anytree | 2.13.0 | Apache-2.0 | PyPI license field is empty. Upstream `LICENSE` is Apache-2.0. | Yes |
| ezdxf | 1.4.5 | MIT | | Yes |
| fonttools | 4.67.0 | MIT | | Yes |
| ipython | 9.17.1 | BSD-3-Clause | A normal build123d dependency, not an extra. Its own dependencies are permissive; see below. | Yes |
| ocpsvg | 0.7.0 | Apache-2.0 | PyPI license field is empty. The sdist `LICENSE` is Apache-2.0. Pulls svgelements. | Yes |
| svgelements | 1.9.6 | MIT | Required by ocpsvg | Yes |
| ocp_gordon | 0.3.1 | Apache-2.0 | | Yes |
| trianglesolver | 1.2 | MIT | | Yes |
| sympy | 1.14.0 | BSD-3-Clause | PyPI classifier says "BSD License" | Yes |
| scipy | 1.18.1 | BSD-3-Clause | | Yes |
| scikit-learn | 1.9.1 | BSD-3-Clause | | Yes |
| webcolors | 24.8.0 | BSD-3-Clause | Pinned by build123d to `~=24.8.0`, so 25.x is not installed | Yes |
| requests | 2.34.2 | Apache-2.0 | Pulls urllib3 (MIT), charset-normalizer (MIT), idna (BSD-3-Clause), certifi (MPL-2.0) | Yes |
| certifi | 2026.10.19 | MPL-2.0 | Second-level, via requests | Yes |
| lib3mf | 2.5.0 | BSD-2-Clause | Installed where the platform is not Linux aarch64. PyPI only says "BSD License". Upstream `LICENSE` is the 2-clause BSD. | Yes |
| py-lib3mf | 2.5.0 | Apache-2.0 | Linux aarch64 only, instead of lib3mf. PyPI license field says Apache-2.0 and has no classifier. | Yes |
| bd_materials | 0.2.4 | Apache-2.0 | PyPI license field is empty. Upstream `LICENSE` is Apache-2.0. | Yes |
| threejs-materials | 1.2.5 | Apache-2.0 | Python material data. The browser app does not load three.js. | Yes |

IPython 9.17.1 also installs these, all permissive:

| Package | Version | SPDX |
| --- | --- | --- |
| jedi | 0.20.1 | MIT |
| parso | 0.8.7 | MIT |
| prompt_toolkit | 3.0.53 | BSD (PyPI does not say 2-clause or 3-clause) |
| wcwidth | 0.9.3 | MIT |
| traitlets | 5.16.1 | BSD-3-Clause |
| pexpect | 4.9.0 | ISC |
| psutil | 7.2.2 | BSD-3-Clause |
| pygments | 2.21.0 | BSD-2-Clause |
| ipython-pygments-lexers | 1.1.1 | BSD (generic PyPI classifier) |
| matplotlib-inline | 0.2.2 | BSD-3-Clause |
| stack_data | 0.6.3 | MIT |
| executing | 2.3.0 | MIT |
| asttokens | 3.0.2 | Apache-2.0 |
| pure_eval | 0.2.4 | MIT |
| colorama | 0.4.6 | BSD (Windows only) |

## MCP SDK

`mcp` 2.3.0 and the packages it installs. `pywin32` is Windows-only.

| Package | Version | SPDX | AGPL-3.0 compatible |
| --- | --- | --- | --- |
| mcp | 2.3.0 | MIT | Yes |
| mcp-types | 2.3.0 | MIT | Yes |
| anyio | 4.15.1 | MIT | Yes |
| httpx2 | 2.16.0 | BSD-3-Clause | Yes |
| jsonschema | 4.26.0 | MIT | Yes |
| attrs | 26.1.0 | MIT | Yes |
| jsonschema-specifications | 2025.9.1 | MIT | Yes |
| referencing | 0.37.0 | MIT | Yes |
| rpds-py | 2026.9.1 | MIT | Yes |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Yes |
| pydantic | 2.14.1 | MIT | Yes |
| pydantic-core | 2.50.1 | MIT | Yes |
| annotated-types | 0.8.0 | MIT | Yes |
| typing-inspection | 0.4.4 | MIT | Yes |
| PyJWT | 2.15.1 | MIT | Yes |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Yes. Installed because of the `crypto` extra of PyJWT. Its cffi dependency is MIT-0. |
| python-multipart | 0.0.32 | Apache-2.0 | Yes |
| sse-starlette | 3.5.0 | BSD-3-Clause | Yes |
| starlette | 1.9.0 | BSD-3-Clause | Yes |
| uvicorn | 0.54.0 | BSD-3-Clause | Yes |
| pywin32 | 312 | PSF-2.0 | Yes. Windows only. |

## Optional Python extras

| Package | Version | Extra | SPDX | How aicad uses it | AGPL-3.0 compatible |
| --- | --- | --- | --- | --- | --- |
| pytest | 9.1.1 | dev | MIT | Tests only | Yes |
| pytest-timeout | 2.4.0 | dev | MIT | Stops a test that runs longer than its limit | Yes |
| iniconfig | 2.3.1 | via pytest | MIT | | Yes |
| packaging | 26.3 | via pytest | Apache-2.0 OR BSD-2-Clause | | Yes |
| pluggy | 1.7.0 | via pytest | MIT | | Yes |
| pygments | 2.21.0 | via pytest | BSD-2-Clause | | Yes |
| mujoco | 3.16.0 | robot | Apache-2.0 | Imported only when the extra is installed (`aicad.robot`) | Yes |
| absl-py | 2.5.1 | via mujoco | Apache-2.0 | | Yes |
| etils | 1.14.0 | via mujoco | Apache-2.0 | | Yes |
| glfw | 2.11.0 | via mujoco | MIT | | Yes |
| PyOpenGL | 3.1.10 | via mujoco | BSD | | Yes |
| websockets | 17.3 | via mujoco | BSD-3-Clause | | Yes |

## Build

| Package | Version | SPDX | AGPL-3.0 compatible |
| --- | --- | --- | --- |
| setuptools | 84.2.1 | MIT | Yes. Build backend. Not shipped in the installed package. |

## Programs aicad runs but does not ship

| Program | SPDX | How aicad uses it | AGPL-3.0 compatible |
| --- | --- | --- | --- |
| CalculiX `ccx` | GPL-2.0-only | Separate process. Ubuntu `calculix-ccx` 2.21 copyright file: upstream files are "GPL-2", the notice names version 2 and does not say "or later". Some files linked into `ccx` itself (a LAPACK routine) are BSD-3-Clause. | Yes, only while it stays a separate program. Do not import or link it. |
| PrusaSlicer | AGPL-3.0 | Separate process, and only if the binary is already on `PATH` (`aicad.printprep`) | Yes. aicad does not ship it. |
| OrcaSlicer | AGPL-3.0 | Same as PrusaSlicer. Upstream README: "GNU Affero General Public License, version 3." | Yes. aicad does not ship it. |

## Web app

The workspace UI is `src/aicad/app_static/index.html`, `app.js`, and `app.css`. `index.html` loads only `/static/app.js`. There is no `package.json`, no npm package, and no third-party script or stylesheet. Those files are part of aicad and are AGPL-3.0-or-later.

## Flagged

### Incompatible

None.

### Unclear metadata, resolved

These PyPI records do not state a precise SPDX identifier. The upstream license text does. All of them are permissive and compatible.

- anytree 2.13.0, bd_materials 0.2.4, ocpsvg 0.7.0: empty PyPI license field. Upstream `LICENSE` is Apache-2.0.
- lib3mf 2.5.0: PyPI says "BSD License". Upstream `LICENSE` is BSD-2-Clause.
- py-lib3mf 2.5.0: PyPI license field says Apache-2.0 and there is no classifier. Treated as Apache-2.0. Compatible either way if the wheel also contains the BSD-2-Clause lib3mf library.
- prompt_toolkit 3.0.53 and ipython-pygments-lexers 1.1.1: PyPI says "BSD License" without a clause count. Compatible under either the 2-clause or the 3-clause BSD.
- cadquery-ocp-novtk: PyPI says Apache-2.0. That covers the binding code. The wheel bundles Open CASCADE, which is LGPL-2.1-only with the OCCT exception. Anyone who redistributes that wheel has to keep the LGPL and the exception on the Open CASCADE libraries and has to leave those libraries replaceable. That is compatible with AGPL-3.0. The PyPI label alone is not the whole license.

### Usage constraint

CalculiX is the one GPL-2.0-only program in the stack. It is compatible because aicad launches it. Linking CalculiX into the aicad process, or shipping a modified `ccx` inside an aicad install without treating it as a separate GPL-2.0-only program, would not be compatible with AGPL-3.0.

Gmsh and python-solvespace are compatible, and they are part of the combined program. A build that strips their license notices, or that fails to offer their source, would break that.
