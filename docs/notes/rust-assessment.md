# Would Rust make the app faster? An assessment, November 2026

This is a write-up only. Nothing was rewritten. Version 0.9 ships on Python. The numbers come from [the November profile](profiling-2026-11.md), measured on the 38-part finger rig on one Windows desktop.

**Recommendation: stay on Python, and add no native code yet.** The profile shows no loop that Rust would speed up by an amount a user could notice. The largest cost after the first fixes was writing meshes as JSON text, and numpy, which aicad already depends on, removed that cost more completely than a Rust encoder would have.

## Where the remaining time is

After the six fixes in the profile, one move takes 173 ms at the median in the browser, from release to a settled page. The split by layer is an estimate from the browser medians and the server stage medians:

| Layer | ms | Share | Would Rust change it? |
|---|---|---|---|
| Browser JavaScript | 45 | 26% | No. |
| Python code in aicad | 44 | 25% | Yes, most of it. |
| Five requests, the pipe, and the first work after a pause | 35 | 20% | Partly. See below. |
| numpy: carrying the mesh and packing it as bytes | 22 | 13% | No. It is native already. |
| Saving the session | 21 | 12% | A little. The file write, 13 ms, stays. |
| OpenCascade and build123d | 6 | 3% | No. It is C++ already. |

So about a quarter of a move is Python code that aicad controls. The rest is the browser, native code, the disk, and the cost of five requests.

Before the fixes, OpenCascade was 64% of a move, 554 of 873 ms. All of that went away without a faster language. The work was not slow. It was not needed: a moved solid was meshed, measured, and described again when only its place had changed. Rust would have run the same unneeded OpenCascade calls at the same speed.

The heavy operations are OpenCascade from end to end. Opening the project cold takes about 19 s to the first frame: 6.6 s to import the geometry libraries, 5.6 s to open the workspace and build its parts, 4.6 s to mesh 38 parts, 1.2 s to measure them, and 1.1 s to write 35 MB of mesh as JSON. Only the last of those is Python work that aicad controls.

## What a Rust rewrite would speed up

**Writing meshes as JSON: 43 ms of a move and 1.1 s of a cold open, before it was fixed.** This was the hottest loop after the first four fixes, so it was benchmarked. One million coordinates, rounded to four decimals as the mesh code rounds them, best of 15 runs:

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

That change has since been made, with numpy. In the app, writing the largest part's answer went from 45 ms to 7.6, and reading it on the page from 8.3 ms to 0.5. The 7.6 is above the benchmark's 2 because the mesh is still held as Python lists and turned into arrays when it is sent, which is the 14 ms per million numbers in the table.

**Requests: about 35 ms of a move.** A move makes five requests. The server speaks HTTP/1.0 with a new connection per request, and each command crosses a pipe to the geometry process as JSON. Part of the 35 ms is not the requests themselves: on the test machine the first work after an idle moment runs several times slower, in any language, and a click always follows one. A Rust server with kept connections would save about 15 ms. Answering a command with the state and scene list it will be asked for next would save about 20 in Python, because it removes two requests.

**Python code in aicad: 44 ms of a move.** This is the copy of the session kept for undo (14), building the state (11), listing the scene and its tags (9), and bookkeeping. Rust would do these five to ten times faster, about 35 ms saved.

**Saving the session: 21 ms.** The JSON text is 5 ms, because indented output uses Python's slow encoder. Rust would save about 4 ms. The file write would stay.

**Starting the app: 6.6 s of imports.** This is the one place a native app would be clearly better. Loading OCP registers thousands of bound classes before any geometry runs. A native app linked to OpenCascade would start in well under a second. It is paid once per session, and the page could be served while the geometry process is still importing.

**OpenCascade and the browser: nothing.** Meshing, booleans, measuring, and drawing would cost what they cost now.

### The estimate

| | Move, ms |
|---|---|
| Today, after the six fixes, in the browser | 173 |
| Python with fewer requests per action | about 150 |
| A full Rust server doing the same things | about 120 |

A rewrite would be about 30 ms better per move than Python with one more targeted fix. That is two frames at 60 frames a second. The reasoning: of the 173 ms, the page's 45, numpy's 22, OpenCascade's 6, and the file write's 13 do not change. The Python code's 44 falls to under 10, the session's JSON text saves about 4, and the requests save about 15.

Before any fix a move was 802 to 945 ms. Removing work that was not needed took 700 ms off. A faster language has about 50 left to take.

## Would a targeted extension get most of the gain?

Yes, and the extension is numpy. Binary mesh buffers, now done, took about 60 ms off a move, which was more than half of what a rewrite could have removed at that point. Fewer requests would get about 20 of the 50 that are left. Neither needs a compiler on a contributor's machine.

A PyO3 module would earn its place only for a hot loop that numpy cannot express. This profile has none: no pure-Python loop over 20 ms is left in any interaction that was measured. If a later profile finds one, a single PyO3 function built as an `abi3` wheel is a contained change, and it is the right second step.

## What a Rust or native app would cost

- **build123d and OCP.** build123d is a Python library, and every design script and catalog part is Python that calls it. OCP exposes nearly all of OpenCascade to Python. The Rust bindings to OpenCascade cover a small part of it. A Rust core would have two choices. It could embed Python to keep build123d, which keeps the slow start and gains nothing where the time is. Or it could replace the modeling layer, which breaks every existing script and is the largest piece of work in the project.
- **The plugin and script sandbox.** Scripts run in restricted child processes, with a separate mechanism on each of Windows, macOS, and Linux, and each guarantee is written down under `docs/security/`. A rewrite would build all three again and prove each guarantee again. This is where a mistake costs the most.
- **Installs on three platforms.** Today the install is a virtual environment and wheels. A native app means building OpenCascade from source for each platform in CI, signing and notarizing the macOS build, and shipping the C++ runtime on Windows. Even one PyO3 module adds a wheel build per platform.
- **Contributor friction.** A contributor today needs Python. The people most likely to contribute are CAD users who already write Python design scripts. A Rust and C++ core asks them for two more languages and a long first build.

## Recommendation and effort

Stay on Python. In order:

1. **Fewer requests per action.** Answer a command with the state and the scene list. About 20 ms off every move, undo, and redo. One to two days.
2. **Keep meshes as arrays, and send the assembly pieces as bytes.** A mesh is still held as lists and stored on disk as JSON. Arrays from the mesher to the wire would take about 5 ms off a move and about 25 off an undo, which reads a stored mesh back. The pieces are 6.5 MB of JSON on the first switch to Assembly. Two to three days.
3. **Look at the start.** Serve the page while the geometry process imports. Two to three days. It does not shorten the import. It shortens the wait for a window.

That is about a week and a half, with no new toolchain. Binary mesh buffers for parts were the first step of this list when it was written, and they are done. Undo is now 201 ms at the median in the browser and redo is 193, against a target of 200. The first step would put both under it with room.

A rewrite of the server and core in Rust is roughly 9 to 15 person-months before it matches today's features: the server and session code, OpenCascade bindings for what aicad uses, the sandbox on three platforms, and installers. It would still leave the script compatibility question open. Its estimated advantage over the plan above is about 30 ms a move and about 6 s at start.

Look at this again if a profile shows a pure-Python loop over 50 ms that numpy cannot express, or if start time becomes the main complaint. The first calls for one PyO3 function. Neither calls for a rewrite.
