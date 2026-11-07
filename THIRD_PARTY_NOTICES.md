# Third-party notices

aicad is licensed under AGPL-3.0-or-later (see [LICENSE](LICENSE)). It uses the outside software listed here. Each of these keeps its own license, and nothing in aicad's license changes that. The full license text of each Python package ships inside that package's installed files. The links below lead to each project.

The list matches an install made on 2026-11-06. A newer install can pull newer versions, and the compatibility check behind this list is [docs/LICENSE_AUDIT.md](docs/LICENSE_AUDIT.md). This file is a record of what aicad uses, not legal advice.

## Open CASCADE Technology

aicad's solid modeling runs on **Open CASCADE Technology (OCCT)**, the open-source geometry kernel from Open Cascade SAS. Every part, sketch solid, fillet, boolean, section, and STEP file in aicad is built by OCCT, through [build123d](https://github.com/gumyr/build123d) and the OCP bindings in [cadquery-ocp-novtk](https://pypi.org/project/cadquery-ocp-novtk/), whose wheel bundles the OCCT libraries.

- License: **LGPL-2.1-only WITH OCCT-exception-1.0** (the GNU Lesser General Public License version 2.1, with the Open CASCADE exception).
- Project: [dev.opencascade.org](https://dev.opencascade.org/) and [github.com/Open-Cascade-SAS/OCCT](https://github.com/Open-Cascade-SAS/OCCT).
- aicad loads the OCCT shared libraries without modifying them, and they stay replaceable. The LGPL and the exception stay on those libraries.

Thank you to Open Cascade SAS and the OCCT contributors.

## Copyleft components that keep their own licenses

These are GPL. aicad does not relicense them, and they stay under their own terms in every copy of aicad.

| Component | License | How aicad uses it | Link |
| --- | --- | --- | --- |
| Gmsh | GPL-2.0-or-later, with the Gmsh exception that allows combining it with Netgen, METIS, Open CASCADE and ParaView under their own licenses | Loaded in the aicad process to mesh parts for the strength and vibration checks | [gmsh.info](https://gmsh.info/), [PyPI](https://pypi.org/project/gmsh/) |
| python-solvespace (the SolveSpace solver) | GPL-3.0-or-later | Loaded in the aicad process to solve sketches | [github.com/KmolYuan/solvespace](https://github.com/KmolYuan/solvespace), [PyPI](https://pypi.org/project/python-solvespace/) |
| CalculiX (`ccx`) | Treated as GPL-2.0-only (see [the audit](docs/LICENSE_AUDIT.md#programs-aicad-runs-but-does-not-ship)) | A separate program that aicad starts and exchanges files with. It is never loaded into the aicad process. On Windows and macOS, `install.py --with-calculix` downloads the pinned conda-forge build. On Linux it comes from the system packages. | [dhondt.de](https://www.dhondt.de/), [conda-forge](https://anaconda.org/conda-forge/calculix) |

Because Gmsh and python-solvespace run in the same process, a copy of aicad that includes them is a combined program, and their GPL terms apply to it alongside the AGPL. Anyone who passes on aicad with them has to keep their license notices and make their source available.

## Python packages installed with aicad

aicad's direct dependencies are build123d, bd_warehouse, gmsh, mcp, numpy, pillow, and python-solvespace. The rest are installed by those packages.

| Package | License | Link |
| --- | --- | --- |
| annotated-types | MIT | [PyPI](https://pypi.org/project/annotated-types/) |
| anyio | MIT | [PyPI](https://pypi.org/project/anyio/) |
| anytree | Apache-2.0 | [PyPI](https://pypi.org/project/anytree/) |
| asttokens | Apache-2.0 | [PyPI](https://pypi.org/project/asttokens/) |
| attrs | MIT | [PyPI](https://pypi.org/project/attrs/) |
| bd_materials | Apache-2.0 | [PyPI](https://pypi.org/project/bd_materials/) |
| bd_warehouse | Apache-2.0 | [PyPI](https://pypi.org/project/bd_warehouse/) |
| build123d | Apache-2.0 | [PyPI](https://pypi.org/project/build123d/) |
| cadquery-ocp-novtk | Apache-2.0 for the bindings; the bundled OCCT is LGPL-2.1-only WITH OCCT-exception-1.0 | [PyPI](https://pypi.org/project/cadquery-ocp-novtk/) |
| cadquery-ocp-proxy | Apache-2.0 | [PyPI](https://pypi.org/project/cadquery-ocp-proxy/) |
| certifi | MPL-2.0 | [PyPI](https://pypi.org/project/certifi/) |
| cffi | MIT-0 | [PyPI](https://pypi.org/project/cffi/) |
| charset-normalizer | MIT | [PyPI](https://pypi.org/project/charset-normalizer/) |
| click | BSD-3-Clause | [PyPI](https://pypi.org/project/click/) |
| cloudpickle | BSD-3-Clause | [PyPI](https://pypi.org/project/cloudpickle/) |
| colorama (Windows) | BSD-3-Clause | [PyPI](https://pypi.org/project/colorama/) |
| cryptography | Apache-2.0 OR BSD-3-Clause | [PyPI](https://pypi.org/project/cryptography/) |
| dataclasses-json | MIT | [PyPI](https://pypi.org/project/dataclasses-json/) |
| Deprecated | MIT | [PyPI](https://pypi.org/project/Deprecated/) |
| executing | MIT | [PyPI](https://pypi.org/project/executing/) |
| ezdxf | MIT | [PyPI](https://pypi.org/project/ezdxf/) |
| fonttools | MIT | [PyPI](https://pypi.org/project/fonttools/) |
| gmsh | GPL-2.0-or-later with the Gmsh exception (see above) | [PyPI](https://pypi.org/project/gmsh/) |
| h11 | MIT | [PyPI](https://pypi.org/project/h11/) |
| httpcore2 | BSD-3-Clause | [PyPI](https://pypi.org/project/httpcore2/) |
| httpx2 | BSD-3-Clause | [PyPI](https://pypi.org/project/httpx2/) |
| idna | BSD-3-Clause | [PyPI](https://pypi.org/project/idna/) |
| ipython | BSD-3-Clause | [PyPI](https://pypi.org/project/ipython/) |
| ipython-pygments-lexers | BSD-3-Clause | [PyPI](https://pypi.org/project/ipython-pygments-lexers/) |
| jedi | MIT | [PyPI](https://pypi.org/project/jedi/) |
| joblib | BSD-3-Clause | [PyPI](https://pypi.org/project/joblib/) |
| jsonschema | MIT | [PyPI](https://pypi.org/project/jsonschema/) |
| jsonschema-specifications | MIT | [PyPI](https://pypi.org/project/jsonschema-specifications/) |
| lib3mf | BSD-3-Clause | [PyPI](https://pypi.org/project/lib3mf/) |
| py-lib3mf (Linux aarch64, instead of lib3mf) | Apache-2.0 | [PyPI](https://pypi.org/project/py-lib3mf/) |
| marshmallow | MIT | [PyPI](https://pypi.org/project/marshmallow/) |
| matplotlib-inline | BSD-3-Clause | [PyPI](https://pypi.org/project/matplotlib-inline/) |
| mcp | MIT | [PyPI](https://pypi.org/project/mcp/) |
| mcp-types | MIT | [PyPI](https://pypi.org/project/mcp-types/) |
| mpmath | BSD-3-Clause | [PyPI](https://pypi.org/project/mpmath/) |
| mypy_extensions | MIT | [PyPI](https://pypi.org/project/mypy-extensions/) |
| narwhals | MIT | [PyPI](https://pypi.org/project/narwhals/) |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [PyPI](https://pypi.org/project/numpy/) |
| ocp_gordon | Apache-2.0 | [PyPI](https://pypi.org/project/ocp-gordon/) |
| ocpsvg | Apache-2.0 | [PyPI](https://pypi.org/project/ocpsvg/) |
| opentelemetry-api | Apache-2.0 | [PyPI](https://pypi.org/project/opentelemetry-api/) |
| packaging | Apache-2.0 OR BSD-2-Clause | [PyPI](https://pypi.org/project/packaging/) |
| parso | MIT | [PyPI](https://pypi.org/project/parso/) |
| pillow | MIT-CMU | [PyPI](https://pypi.org/project/pillow/) |
| platformdirs | MIT | [PyPI](https://pypi.org/project/platformdirs/) |
| prompt_toolkit | BSD-3-Clause | [PyPI](https://pypi.org/project/prompt-toolkit/) |
| psutil | BSD-3-Clause | [PyPI](https://pypi.org/project/psutil/) |
| pure_eval | MIT | [PyPI](https://pypi.org/project/pure-eval/) |
| pycparser | BSD-3-Clause | [PyPI](https://pypi.org/project/pycparser/) |
| pydantic | MIT | [PyPI](https://pypi.org/project/pydantic/) |
| pydantic_core | MIT | [PyPI](https://pypi.org/project/pydantic-core/) |
| pygltflib | MIT | [PyPI](https://pypi.org/project/pygltflib/) |
| Pygments | BSD-2-Clause | [PyPI](https://pypi.org/project/Pygments/) |
| PyJWT | MIT | [PyPI](https://pypi.org/project/PyJWT/) |
| pyparsing | MIT | [PyPI](https://pypi.org/project/pyparsing/) |
| python-multipart | Apache-2.0 | [PyPI](https://pypi.org/project/python-multipart/) |
| python-solvespace | GPL-3.0-or-later (see above) | [PyPI](https://pypi.org/project/python-solvespace/) |
| pywin32 (Windows) | PSF-2.0 | [PyPI](https://pypi.org/project/pywin32/) |
| referencing | MIT | [PyPI](https://pypi.org/project/referencing/) |
| requests | Apache-2.0 | [PyPI](https://pypi.org/project/requests/) |
| rpds-py | MIT | [PyPI](https://pypi.org/project/rpds-py/) |
| scikit-learn | BSD-3-Clause | [PyPI](https://pypi.org/project/scikit-learn/) |
| scipy | BSD-3-Clause; the wheel also carries the notices of code it bundles | [PyPI](https://pypi.org/project/scipy/) |
| sse-starlette | BSD-3-Clause | [PyPI](https://pypi.org/project/sse-starlette/) |
| stack-data | MIT | [PyPI](https://pypi.org/project/stack-data/) |
| starlette | BSD-3-Clause | [PyPI](https://pypi.org/project/starlette/) |
| svgelements | MIT | [PyPI](https://pypi.org/project/svgelements/) |
| svgpathtools | MIT | [PyPI](https://pypi.org/project/svgpathtools/) |
| svgwrite | MIT | [PyPI](https://pypi.org/project/svgwrite/) |
| sympy | BSD-3-Clause | [PyPI](https://pypi.org/project/sympy/) |
| threadpoolctl | BSD-3-Clause | [PyPI](https://pypi.org/project/threadpoolctl/) |
| threejs-materials | Apache-2.0 | [PyPI](https://pypi.org/project/threejs-materials/) |
| traitlets | BSD-3-Clause | [PyPI](https://pypi.org/project/traitlets/) |
| trianglesolver | MIT | [PyPI](https://pypi.org/project/trianglesolver/) |
| truststore | MIT | [PyPI](https://pypi.org/project/truststore/) |
| typing_extensions | PSF-2.0 | [PyPI](https://pypi.org/project/typing-extensions/) |
| typing-inspect | MIT | [PyPI](https://pypi.org/project/typing-inspect/) |
| typing-inspection | MIT | [PyPI](https://pypi.org/project/typing-inspection/) |
| urllib3 | MIT | [PyPI](https://pypi.org/project/urllib3/) |
| uvicorn | BSD-3-Clause | [PyPI](https://pypi.org/project/uvicorn/) |
| wcwidth | MIT | [PyPI](https://pypi.org/project/wcwidth/) |
| webcolors | BSD-3-Clause | [PyPI](https://pypi.org/project/webcolors/) |
| wrapt | BSD-2-Clause | [PyPI](https://pypi.org/project/wrapt/) |

## The `robot` extra

Installed only with the `robot` extra, for example `pip install -e ".[robot]"`. Licenses as recorded in [the audit](docs/LICENSE_AUDIT.md#optional-python-extras).

| Package | License | Link |
| --- | --- | --- |
| mujoco | Apache-2.0 | [PyPI](https://pypi.org/project/mujoco/) |
| absl-py | Apache-2.0 | [PyPI](https://pypi.org/project/absl-py/) |
| etils | Apache-2.0 | [PyPI](https://pypi.org/project/etils/) |
| glfw | MIT | [PyPI](https://pypi.org/project/glfw/) |
| PyOpenGL | BSD | [PyPI](https://pypi.org/project/PyOpenGL/) |
| websockets | BSD-3-Clause | [PyPI](https://pypi.org/project/websockets/) |

## What `install.py --with-calculix` downloads

On Windows and macOS, this flag downloads micromamba and the pinned conda-forge packages in `.github/calculix.lock.json`, checks each sha256, and puts them in the project's `.venv`. They make up the separate `ccx` program and its runtime libraries. The licenses are those conda-forge publishes for each package.

| Package | License | Link |
| --- | --- | --- |
| calculix 2.23 | Treated as GPL-2.0-only (see above) | [conda-forge](https://anaconda.org/conda-forge/calculix) |
| arpack | BSD-3-Clause | [conda-forge](https://anaconda.org/conda-forge/arpack) |
| libopenblas | BSD-3-Clause | [conda-forge](https://anaconda.org/conda-forge/libopenblas) |
| libblas, liblapack | BSD-3-Clause | [conda-forge](https://anaconda.org/conda-forge/liblapack) |
| libgcc, libgfortran, libgfortran5, libgomp, libstdcxx | GPL-3.0-only WITH GCC-exception-3.1 | [conda-forge](https://anaconda.org/conda-forge/libgcc) |
| _openmp_mutex | BSD-3-Clause | [conda-forge](https://anaconda.org/conda-forge/_openmp_mutex) |
| llvm-openmp, libcxx (macOS) | Apache-2.0 WITH LLVM-exception | [conda-forge](https://anaconda.org/conda-forge/llvm-openmp) |
| libwinpthread (Windows) | MIT | [conda-forge](https://anaconda.org/conda-forge/libwinpthread) |
| vc (Windows) | BSD-3-Clause | [conda-forge](https://anaconda.org/conda-forge/vc) |
| vc14_runtime, vcomp14 (Windows) | Microsoft Visual C++ 2015-2022 Runtime license | [conda-forge](https://anaconda.org/conda-forge/vc14_runtime) |
| ucrt (Windows) | Microsoft Windows SDK 10 license | [conda-forge](https://anaconda.org/conda-forge/ucrt) |
| micromamba (the downloader) | BSD-3-Clause AND MIT AND OpenSSL | [github.com/mamba-org/mamba](https://github.com/mamba-org/mamba) |

## Programs aicad runs only when they are already installed

| Program | License | Link |
| --- | --- | --- |
| PrusaSlicer | AGPL-3.0 | [github.com/prusa3d/PrusaSlicer](https://github.com/prusa3d/PrusaSlicer) |
| OrcaSlicer | AGPL-3.0 | [github.com/SoftFever/OrcaSlicer](https://github.com/SoftFever/OrcaSlicer) |

aicad does not ship either one. It starts them as separate programs when they are on `PATH`.

## The workspace app

The browser app in `src/aicad/app_static/` is part of aicad. It loads no outside script, stylesheet, or font.
