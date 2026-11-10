# Profile of the app, November 2026

This note records where the time goes in each app interaction, from the click to the settled page. It was measured before any fix, after four fixes, after two more, and after a seventh. Each fix has its own pull request.

## What was measured

- **Project:** the 38-part finger rig. Its model view is 257 thousand triangles, 35.8 MB as JSON. Its `session.json` is 329 KB.
- **Machine:** one Windows 10 desktop with 24 logical cores and a 240 Hz display. Every number here is from that machine.
- **Baseline:** main at `86c2a20`, which includes pull requests #481 to #490.
- **The moved part:** `proximal`, the largest link. Its mesh is 3.08 MB as JSON.

Three tools were used:

- **Server stages.** Each stage function was wrapped with a `time.perf_counter` pair and run in process, seven runs each. The tables give medians. Stage times are inclusive, so a stage that calls another includes it.
- **cProfile,** to find which functions to wrap. Its times are not used here. It made pure-Python work look two to four times slower than it is: it showed the session save at 50 ms where the wall clock says 21 to 29 ms.
- **The page.** The page's own functions and `fetch` were wrapped with `performance.now()`, and the Performance panel confirmed the frame times. "Shown" is the end of the first draw that has the server's answer in it. "Settled" is when the side panel and its numbers are also done.

## The original complaint

Moving one part used to hold Python at about 90% of a core for about 30 seconds. On the baseline, one move costs 0.75 CPU-seconds in the worker and 0.80 to 0.95 s on the wall clock. The busiest second is 74% of one core. The 30-second case was fixed by #481 to #490. What was left is the 0.8 s below.

## Before: main at `86c2a20`

Times are in milliseconds. A range is the spread over runs in the browser.

| Interaction | Total | Top costs |
|---|---|---|
| Move a part (settled) | 802–945 | mesh the moved solid again 392, three bounding boxes of the moved solid 108, write its mesh as JSON 91, solve and draw every sketch 56, describe its faces again 40 |
| Undo a move (settled) | 432–449 | place the part again 71 (bounding box 36, volume 14), solve and draw every sketch 55, write its mesh as JSON 44, bounding box for the part list 35, read the stored mesh back from disk 29 |
| Redo a move (settled) | 428–460 | the same five as undo: 70, 56, 43, 35, 31 |
| Orbit, one frame | 0.2 (worst 2.0) | no frame of 240 took longer than 16.7 ms |
| Pan, one frame | 0.2 (worst 1.5) | no frame of 240 took longer than 16.7 ms |
| Drag a part, one frame | 5.4 (worst 9.7) | shift the part's corners 4.4, upload them 1.0 |
| Open the project, cold, to the first frame | about 19,000 | import the geometry libraries 6,553, open the workspace 5,621, mesh 38 parts 4,557, measure 38 parts 1,160, write 35 MB of mesh as JSON 1,144 |
| Open the project, page side, server warm | 234–271 | fetch and parse 42 mesh answers 190, read the state 38, draw 23, join the parts into one scene 16 |
| Add a catalog part (M3 screw) | 181–272 | the insert command 54–92, read the state 69–88 (sketches 56), draw 22–26, join the scene 15–20, list the scene 6–28 |
| Release a joint slider | 186–194 | read the state 66–83 (sketches 56), `set_joint` 55–68, pose the pieces 20–21, draw 12–19, fetch the pose 5–7 |
| Switch to Assembly, first time | 292 | fetch the pieces 213 (6.5 MB), parse them 51, pose them 23, draw 18, fetch the pose 10 |
| Switch to Assembly, again | 47 | pose the pieces 24, draw 15, two answers of "not changed" 8 |
| Switch to Model | 39 | join the scene 17, draw 16, one answer of "not changed" 6 |

### One move on main, step by step

| Step | ms | Where the time is |
|---|---|---|
| `place_part` command | 155 | bounding box asked twice 72, volume 14, session save 29, copy for undo 18, the move itself under 10 |
| Read the state | 65 | solve and draw every sketch 56 |
| List the scene | 509 | mesh the moved solid 392, write its mesh as JSON 91, describe its faces 40, bounding box 36 |
| Fetch the moved part | 9–19 | 3.08 MB over the loopback |
| Parse it | 17 | `JSON.parse` |
| Join the scene | 17 | copy 38 parts into one set of arrays |
| Draw | 19 | upload the arrays and draw |
| Read the part's numbers | 9 | `list_parameters` |

### The same move by layer

| Layer | ms | Share |
|---|---|---|
| OpenCascade and build123d: mesh, describe faces, bounding boxes, volume | 554 | 64% |
| JSON and HTTP: encode, parse, five requests | 150 | 17% |
| Python code in aicad: sketches, undo copy, bookkeeping | 85 | 10% |
| Browser JavaScript: join the scene, draw, side panel | 55 | 6% |
| Saving the session | 29 | 3% |

Nearly two thirds of a move was OpenCascade doing work whose answer was already known. A move changes where a solid is. It does not change its triangles, its faces, its volume, or the size of its box.

### Where a dragged part is first seen

While a part is dragged, the page draws it shifted on every frame, at 5.4 ms a frame. At release, the last drag frame is still on the canvas, so the part is seen in its new place at once. The baseline had two gaps. Any redraw before the server answered, such as an orbit, drew the part back where it started. And a position typed into the panel was not drawn at all until the server's mesh came back, about 0.8 s later.

## The fixes

Each fix is one pull request with its own regression tests. The server numbers are stage medians in process.

| Fix | What it stops | Before | After |
|---|---|---|---|
| A moved part keeps its mesh | meshing and describing a solid that only moved | list the scene after a move: 509 | 62 |
| A move asks for no box and no volume | three OpenCascade questions with known answers | `place_part` 155, undo 143, redo 152 | 50, 81, 74 |
| The state keeps solved sketches | solving and drawing every sketch after every change | read the state after a move: 65 | 9 |
| A move is drawn at once | waiting for the server before a typed position shows, and snapping back on a redraw | typed position shown after about 800 | 12–19, measured in the browser: one draw in the same task as the commit |
| A part's mesh is sent as bytes | writing a mesh as JSON text and parsing it on the page | write the largest part's answer 45, read it on the page 8.3 | 7.6, 0.5 |
| Undo writes back only what changed | writing every note and the requirements file on each undo, and checking the notes folder once per note | undo 162, redo 158, move 155 | 121, 119, 138 |

The last two fixes came after the tables that follow, which are of the first four. The section "After all six fixes" has the numbers with all of them.

### After the four fixes together, server side

| Step | Move | Undo | Redo | Turn |
|---|---|---|---|---|
| The command | 46 | 74 | 76 | 80 |
| Read the state | 9 | 9 | 9 | 8 |
| List the scene and encode the moved part | 62 | 61 | 61 | 104 |
| Server total | 117 | 144 | 146 | 192 |
| Server total on main | 730 | 339 | 351 | 705 |

What is left in a move, largest first:

| Cost | ms |
|---|---|
| Write the moved part's mesh as JSON (server) | 43 |
| Save the session, of which writing the file is 13 | 21 |
| Parse the moved part's mesh (page) | 18 |
| Join the 38 parts into one scene (page) | 18 |
| Copy the session for undo | 17 |
| Draw (page) | 17 |
| Carry the mesh to its new place with numpy | 13 |
| Read the state | 9 |
| Read the part's numbers | 9 |
| The move itself in OpenCascade | 6 |

A turn costs more than a shift: it asks OpenCascade for one new bounding box (28) and describes again the faces whose axes the turn changed (38).

### The commands in the running app

The stage medians above are from calls made back to back. In the running app the three commands take longer, and the other steps do not:

| Command | Back to back | With 20 ms pauses between calls | In the app, over HTTP |
|---|---|---|---|
| `place_part` | 44 | 57 | 58–67 |
| Undo | 75 | 94 | 99 |
| Redo | 68 | 96 | 89–93 |

The difference is not HTTP and not the pipe to the geometry process. Through the pipe alone a move is 41 ms and an undo 65 to 71. It is the pause: on this machine the first work after an idle moment is slower, and in the app a command always follows one. "The first work after a pause" below has the check. A command also reads and writes more files than the session:

| File work in one command, with pauses | Move | Undo |
|---|---|---|
| Save the session | 23 | 23 |
| List and read the two notes for the undo copy | 17 | 21 |
| Write the notes, the requirements, and the plans back, changed or not | none | 31 |

A move changes none of the notes, requirements, or plans. Both this and the mesh encoding were fixed next.

### The move by layer, after

| Layer | ms | Share |
|---|---|---|
| JSON and HTTP: encode, parse, five requests | 105 | 48% |
| Python code in aicad | 44 | 20% |
| Browser JavaScript | 45 | 20% |
| Saving the session | 21 | 10% |
| OpenCascade and build123d | 6 | 3% |

These stage medians add up to about 220 ms. The measured time is longer.

### After the four fixes together, in the browser

Measured on a local build with the four fixes merged, from the release or the click to the settled page:

| Interaction | Runs | Range | Median |
|---|---|---|---|
| Move a part by dragging | 13 | 217–325 | 257 |
| Move a part by typing a position | 4 | 234–369 | 249 |
| Undo a move | 10 | 259–332 | 278 |
| Redo a move | 10 | 258–341 | 287 |

The steps of those runs, as the page saw them:

| Step | Move | Undo |
|---|---|---|
| The command | 55–105 | 100–137 |
| Read the state | 12–29 | 11–18 |
| List the scene, which encodes the moved part | 62–82 | 64–73 |
| Fetch the moved part, 3.08 MB | 6–25 | 9–18 |
| Parse it | 15–30 | 15–23 |
| Join the scene | 14–18 | 14–19 |
| Draw | 14–19 | 15–19 |
| Read the part's numbers | 5–13 | 5–8 |

The browser medians are 35 to 40 ms above the sum of the stage medians. Most of that is the command, as the table of commands in the running app shows. The rest is time between the steps on the page.

## After all six fixes

These numbers are from a local build with the six fixes merged together.

### Server side

Stage medians in process, 11 runs, with a 20 ms pause before each step:

| Step | Move | Undo | Redo |
|---|---|---|---|
| The command | 48 | 57 | 55 |
| Read the state | 11 | 10 | 9 |
| List the scene and pack the moved part | 25 | 25 | 24 |
| Server total | 84 | 92 | 88 |
| Server total on main | 730 | 339 | 351 |

An undo's command is two things: copying the session for redo, 22, and putting the older copy back, 36, of which saving the session is 21. Listing the scene is mostly carrying the mesh to its new place with numpy, 14.

### In the browser

Measured with the page on screen, from the release or the click to the settled page:

| Interaction | Runs | Range | Median | On main |
|---|---|---|---|---|
| Move a part by dragging | 12 | 134–280 | 173 | 802–945 |
| Undo a move | 10 | 175–221 | 201 | 432–449 |
| Redo a move | 10 | 163–267 | 193 | 428–460 |

A dragged part is in its new place 0.1 to 1.9 ms after release. The steps of those runs, as the page saw them, range and median:

| Step | Move | Undo | Redo |
|---|---|---|---|
| The command | 43–88, 64 | 69–107, 91 | 58–104, 89 |
| Read the state | 12–31, 23 | 11–23, 15 | 11–24, 13 |
| List the scene | 23–31, 25 | 28–39, 31 | 23–30, 26 |
| Fetch the moved part, 1.86 MB | 4–22, 4 | 5–24, 8 | 4–11, 9 |
| Read the answers | 4–23, 5 | 4–11, 5 | 4–6, 4 |
| Join the scene | 14–21, 15 | 12–20, 18 | 14–17, 15 |
| Draw | 14–17, 16 | 13–21, 15 | 14–25, 17 |
| Read the part's numbers | 5–95, 9 | 5–12, 7 | 5–96, 10 |

### The move by layer, with all six

This split of the 173 ms is an estimate. It takes the browser medians, subtracts the stage medians, and puts what is left under requests and the pause.

| Layer | ms | Share |
|---|---|---|
| Browser JavaScript: read, join, draw, and the time between steps | 45 | 26% |
| Python code in aicad: the undo copy, the state, the scene list, bookkeeping | 44 | 25% |
| Five requests, the pipe, and the first work after a pause | 35 | 20% |
| numpy: carrying the mesh and packing it as bytes | 22 | 13% |
| Saving the session, of which writing the file is 13 | 21 | 12% |
| OpenCascade and build123d | 6 | 3% |

### The other interactions

| Interaction | On main | With all six | What changed |
|---|---|---|---|
| Open the project, page side, server warm | 234–271 | about 160, one run, from the first request to the last answer | 43 mesh answers, 21.8 MB, arrive in 44 ms. It was 190 ms for 35 MB. |
| Add a catalog part (M3 screw) | 181–272 | 162–297, 4 runs | Reading the state is 20–26 where it was 69–88. The insert command itself ran from 42 to 178, so the total is no better. |
| Release a joint slider | 186–194 | 105–154, median 143, 5 runs | Reading the state is 12–23 where it was 66–83. |
| Switch to Assembly, first time on the page | 292 | 228, one run | none of the fixes touch it |
| Switch to Assembly, again | 47 | 59–68, 4 runs | none of the fixes touch it |
| Switch to Model | 39 | 47–61 in 2 of 5 runs | none of the fixes touch it |
| Orbit and pan, one frame | 0.2 | not measured again | none of the fixes touch it |

Two rows need a note. The two repeat switches are slower than on main though their code did not change. Their runs were half a second apart, and their two requests took 10 to 20 ms where the same requests back to back take 2: see the next section. And in three of the five switches to Model, the side panel read the selected part's numbers again and finished 0.24 to 0.31 s after the click. The scene was drawn in the same time in all five. The single run on main did not show that, and it was not traced.

### The first work after a pause

The page's numbers are higher than the stage medians, and the earlier guess was file work. A check showed that all work is slower after a pause on this machine, with no files involved:

| The same work | Back to back | After a pause of 0.5 s |
|---|---|---|
| A Python loop of 200 thousand additions, no server | 2.6 | 14.3 |
| The scene list when nothing changed, asked from the page | 1.4–2.7 | 7–20 |
| `place_part` over HTTP | 49 | 88 |

The cause was not traced. The processor has 8 fast cores and 16 slow ones, the power plan is Balanced, and Windows 10 is older than that kind of processor, so a thread that wakes on a slow core at a low clock is the likely one. No code in aicad causes it, and none was changed for it.

It matters for reading the numbers. A click always follows a pause, so the browser numbers carry this cost and the stage medians do not. The targets are judged on the browser numbers.

## A seventh fix: fewer requests per action

After an undo, a redo, a move, or a joint change, the page asked for the state and then the scene list, each in a request of its own, right after the command answered. The command now carries both, read by the server once the command has run, so each of those actions makes three requests instead of five. A carried state older than the one on screen is not used, and a carried list that a newer change overtook is read again.

Measured in the browser on the six-fix build, without and with this fix, on the same project one after the other: 24 runs of each, a 0.6 s pause before each action, and the time from the click to the settled page. These runs time a typed move, not a drag, and include reading the part's numbers at the end, so they are not the same measure as the tables above.

| | Without | With |
|---|---|---|
| Requests per action | 5 | 3 |
| Undo, median and range | 206, 162–304 | 188, 152–292 |
| Undo, runs under 200 | 11 of 24 | 18 of 24 |
| Redo, median and range | 203, 135–384 | 189, 129–301 |
| Redo, runs under 200 | 11 of 24 | 16 of 24 |
| Typed move, median | 207 | 190 |

### Opening the page at `localhost`

The page answers at both `http://127.0.0.1:PORT/` and `http://localhost:PORT/`, but the server listens on IPv4 only. At `localhost`, the browser tries IPv6 first and falls back to IPv4 about 300 ms later, and the server closes each connection after one answer, so every request pays it. On this machine, after an undo, fetching the moved part took 342 ms at `localhost` and 6 ms at `127.0.0.1`, and reading its numbers 336 ms and 9 ms. An undo took 1.16 s instead of 0.19. The app opens the browser at `127.0.0.1` and prints that address, so this costs only someone who types `localhost`. All the numbers in this note are at `127.0.0.1`.

## Ideas that were checked

- **Show a move at once with a transform.** Done for a drag and for a typed position. A typed turn, a mounted part, and parts mounted on the moved part are not previewed: the page waits for the model for those.
- **Never mesh or measure again for a move alone.** Done. A shift now asks OpenCascade for nothing but the move.
- **Skip work for parts that did not change.** The server already sent only the changed part. The state now keeps its sketches. Undo and redo now write back only the notes and the requirement list that differ. The page still joins all 38 parts into one scene (15 ms) and uploads all of it (16 ms) when one part changes.
- **Binary buffers instead of JSON number arrays.** Done for a part's mesh, which was the largest single cost after the first four fixes. Writing the largest part's answer went from 45 ms to 7.6, reading it on the page from 8.3 to 0.5, and its size from 3.08 MB to 1.85. The 38 parts sent when a project opens went from 35.3 MB to 21.5. The scene list, the assembly pieces, and the pose are still JSON. The pieces are 6.5 MB, fetched once per page, and are the next candidate.
- **Stop saving the full session on every action.** Checked and left alone. The save is 21 ms, and the file write is 13 of that. Notes and requirements that did not change are no longer written at all. Saving later would put a crash between the user's action and the disk for a gain of about 15 ms.

## Targets

| Target | Before | After the fixes |
|---|---|---|
| A moved part shows in under 100 ms | at release for a drag, with gaps. About 800 for a typed position. | Met. 0.1 to 1.9 after release for a drag, and it stays. 12–19 for a typed position, measured with the first four fixes. |
| A move settles in under 300 ms | 802–945 | Met. 134–280, median 173, and 12 of 12 runs under 300. |
| Undo or redo in under 200 ms | 428–460 | Met at the median, not on every run. With six fixes, undo was 175–221, median 201, and redo 163–267, median 193. With the seventh, in 24 runs each, undo is 152–292, median 188, with 18 under 200, and redo is 129–301, median 189, with 16 under 200. |
| Orbit and pan at 60 frames a second | met: 0.2 ms a frame | Met. Unchanged. |

Undo and redo are now under 200 ms at the median, and a quarter to a third of the runs are still over it. The slow runs are spread across the steps, and the first work after a pause is part of each. Two things would take more off, and neither is done: taking one copy of the session for an undo where it now takes one and builds one, about 12 ms; and joining and uploading only the part that changed on the page, about 20 ms.
