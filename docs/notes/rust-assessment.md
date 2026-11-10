# Would Rust make the app faster? An assessment, November 2026

This is a write-up only. Nothing was rewritten. Version 0.9 ships on Python. The numbers come from [the November profile](profiling-2026-11.md), measured on the 38-part finger rig on one Windows desktop.

**Recommendation: stay on Python, and add no native code yet.** The profile shows no loop that Rust would speed up by an amount a user could notice. The largest remaining cost is writing meshes as JSON text, and numpy, which aicad already depends on, removes that cost more completely than a Rust encoder would.

## Where the remaining time is

After the four fixes in the profile, the measured stages of one move add up to about 220 ms from release to a settled page. In the browser the median is 257 ms, because the command's file work is slower in the running app than in a stage test. The split by layer is from the stages:

| Layer | ms | Share | Would Rust change it? |
|---|---|---|---|
| JSON and HTTP: encode, parse, five requests | 105 | 48% | Partly. See below. |
| Python code in aicad | 44 | 20% | Yes, most of it. |
| Browser JavaScript | 45 | 20% | No. |
| Saving the session | 21 | 10% | A little. The file write, 13 ms, stays. |
| OpenCascade and build123d | 6 | 3% | No. It is C++ already. |

Before the fixes, OpenCascade was 64% of a move, 554 of 873 ms. All of that went away without a faster language. The work was not slow. It was not needed: a moved solid was meshed, measured, and described again when only its place had changed. Rust would have run the same unneeded OpenCascade calls at the same speed.

The heavy operations are OpenCascade from end to end. Opening the project cold takes about 19 s to the first frame: 6.6 s to import the geometry libraries, 5.6 s to open the workspace and build its parts, 4.6 s to mesh 38 parts, 1.2 s to measure them, and 1.1 s to write 35 MB of mesh as JSON. Only the last of those is Python work that aicad controls.

## What a Rust rewrite would speed up

**Writing meshes as JSON: 43 ms of a move, 1.1 s of a cold open.** This is the hottest loop left, so it was benchmarked. One million coordinates, rounded to four decimals as the mesh code rounds them, best of 15 runs:

| Way to send one million numbers | ms | Size |
|---|---|---|
| Python `json.dumps` of a list | 146 | 8.3 MB |
| A pure-Python loop over `repr` | 173 | 8.3 MB |
| Rust, standard library formatting, `rustc -O` | 55 | 8.3 MB |
| numpy, 32-bit floats as bytes, from a list | 14 | 4.0 MB |
| numpy, 32-bit floats as bytes, from an array | 1.8 | 4.0 MB |
| Rust, 32-bit floats as bytes | 1.2 | 4.0 MB |

And on the receiving side, in Node, which runs the same engine as the page:

| Way to read them | ms |
|---|---|
| `JSON.parse` | 23 |
| A `Float32Array` view of the bytes | under 0.01 |

Python's JSON encoder is already C. A Rust encoder is 2.7 times faster at the same job. Not doing the job is 80 times faster, and it needs no Rust: the page already turns these numbers into `Float32Array`, so the server can send the bytes that array would hold. For a move, that is 43 ms down to about 2 on the server and 18 ms down to about 0 on the page. A Rust JSON encoder would save 27 of those 61 ms. numpy saves 59.

**Requests: about 40 ms of a move.** A move makes five requests at about 8 ms each. The server speaks HTTP/1.0 with a new connection per request, and each command crosses a pipe to the geometry process as JSON. A Rust server with kept connections would bring this to about 5 ms. So would kept connections in the Python server, or answering a command with the state and scene list it will be asked for next.

**Python code in aicad: 44 ms of a move.** This is the copy of the session kept for undo (17), carrying the mesh with numpy (13), building the state (9), and bookkeeping. Rust would do the copies and bookkeeping five to ten times faster, about 25 ms saved. The numpy part is native already.

**Saving the session: 21 ms.** The JSON text is 5 ms, because indented output uses Python's slow encoder. Rust would save about 4 ms. The file write would stay.

**Starting the app: 6.6 s of imports.** This is the one place a native app would be clearly better. Loading OCP registers thousands of bound classes before any geometry runs. A native app linked to OpenCascade would start in well under a second. It is paid once per session, and the page could be served while the geometry process is still importing.

**OpenCascade and the browser: nothing.** Meshing, booleans, measuring, and drawing would cost what they cost now.

### The estimate

| | Move, ms |
|---|---|
| Today, after the four fixes, by stages | 220 |
| Python with binary mesh buffers and kept connections | about 130 |
| A full Rust server doing the same things | about 105 |

A rewrite would be about 25 ms better per move than Python with two targeted fixes. That is one and a half frames at 60 frames a second. The reasoning: of the 220 ms, the page's 45 and OpenCascade's 6 do not change, the file write's 13 does not change, and the rest shrinks by the factors above.

## Would a targeted extension get most of the gain?

Yes, and the extension is numpy. Binary mesh buffers get about 60 of the roughly 115 ms a rewrite could remove. Kept connections or fewer requests get about 30 more. Neither needs a compiler on a contributor's machine.

A PyO3 module would earn its place only for a hot loop that numpy cannot express. This profile has none: no pure-Python loop over 20 ms is left in any interaction that was measured. If a later profile finds one, a single PyO3 function built as an `abi3` wheel is a contained change, and it is the right second step.

## What a Rust or native app would cost

- **build123d and OCP.** build123d is a Python library, and every design script and catalog part is Python that calls it. OCP exposes nearly all of OpenCascade to Python. The Rust bindings to OpenCascade cover a small part of it. A Rust core would have two choices. It could embed Python to keep build123d, which keeps the slow start and gains nothing where the time is. Or it could replace the modeling layer, which breaks every existing script and is the largest piece of work in the project.
- **The plugin and script sandbox.** Scripts run in restricted child processes, with a separate mechanism on each of Windows, macOS, and Linux, and each guarantee is written down under `docs/security/`. A rewrite would build all three again and prove each guarantee again. This is where a mistake costs the most.
- **Installs on three platforms.** Today the install is a virtual environment and wheels. A native app means building OpenCascade from source for each platform in CI, signing and notarizing the macOS build, and shipping the C++ runtime on Windows. Even one PyO3 module adds a wheel build per platform.
- **Contributor friction.** A contributor today needs Python. The people most likely to contribute are CAD users who already write Python design scripts. A Rust and C++ core asks them for two more languages and a long first build.

## Recommendation and effort

Stay on Python. In order:

1. **Binary mesh buffers with numpy.** About 60 ms off every move, undo, and redo, and about 1 s off a cold open. Two to three days with tests, because the stored mesh, the answer tags, and the page's reader all change together.
2. **Fewer or cheaper requests per action.** About 30 ms. One to two days.
3. **Look at the start.** Serve the page while the geometry process imports. Two to three days. It does not shorten the import. It shortens the wait for a window.

That is about a week and a half, with no new toolchain. Undo and redo now take about 280 ms in the browser. The first two steps would bring them to about 190, which is at their 200 ms target and not safely under it.

A rewrite of the server and core in Rust is roughly 9 to 15 person-months before it matches today's features: the server and session code, OpenCascade bindings for what aicad uses, the sandbox on three platforms, and installers. It would still leave the script compatibility question open. Its measured advantage over the plan above is about 25 ms a move and about 6 s at start.

Look at this again if a profile shows a pure-Python loop over 50 ms that numpy cannot express, or if start time becomes the main complaint. The first calls for one PyO3 function. Neither calls for a rewrite.
