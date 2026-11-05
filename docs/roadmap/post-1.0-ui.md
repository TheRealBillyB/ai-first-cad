# Workspace app interface work after v1.0

This is the list of interface ideas to start on after v1.0, in order. None of it is promised for a date. Each item records the estimate, the pull request split, what makes it harder than it looks, and the decisions already made.

One day of estimate means about what one mid-size pull request takes, including CI on all three systems and one review round. The estimates are rough. The whole list is about 8 to 10 days, one pull request at a time.

The dark theme, grid, and origin axes are not on this list. They are in [#418](https://github.com/TheRealBillyB/ai-first-cad/pull/418), before v1.0.

## Rules for all of it

- Each pull request is focused, with merge commits only, no force-push, unsigned commits, and a DCO sign-off. Fix at the root. Do not skip a test or raise a timeout to get a pass.
- Every new action in the interface has an MCP equivalent, because an assistant has to be able to use the app as well as a person can.
- The command palette is exempt from that rule. It only starts commands that already exist.
- With no app open, a view command, such as hide, says that no viewer is attached. It does not appear to succeed.
- CI has no browser. A change to how the app looks or behaves on screen is checked by hand in the running app, and the pull request says what was checked.

## Order

| # | Item | Estimate | Pull requests |
|---|---|---|---|
| 1 | Viewport groundwork | 1 to 1.5 days | 2: the drawing change, then the binary mesh format |
| 2 | Hide, isolate, and selection outline | 1 day | 1 |
| 3 | Realistic materials and lighting | 2 days | 2: the appearance data, then the shader and smooth normals |
| 4 | Exploded view slider | 0.5 to 1 day | 1 |
| 5 | Motion playback bar | 1.5 to 2 days | 1 |
| 6 | The rest of the layout | 1 to 1.5 days | 1 |
| 7 | Command palette | 0.5 day | 1 |
| 8 | A clear notice when the app stops | 2 to 4 hours | 1 |

## 1. Viewport groundwork

**The idea.** The 3D view keeps a separate GPU buffer and a transform for each part, draws only when something changed, and loads meshes in a binary format. Nothing looks different. Items 2 to 5 depend on it.

**Harder than it looks.**

- The view is one merged mesh today, and every redraw sends all of it to the GPU again.
- On a 10-part test project, 130,756 triangles were 33.6 MB of JSON, which took 13.5 s to load the first time. Each redraw took 14 to 41 ms on a fast desktop graphics card, which is too slow for smooth playback.
- Picking and vertex snapping read the merged mesh. They have to follow each part's transform.

## 2. Hide, isolate, and selection outline

**The idea.** Hide a selected part from the viewport, an eye toggle for every part in the tree, show all, isolate the selection, and a crisp outline on the selection. An assistant can hide and show parts over MCP.

**Decisions.**

- Do not use plain Space if it can hit the wrong target. Hiding acts only on an explicit selection in the viewport.
- A permanent "N hidden" count stays on screen.
- Hidden parts stay in checks, the bill of materials, and exports.

**Harder than it looks.**

- The tree lists parts only, so an assembly needs a new list of its instances. Two instances of one part hide separately.
- Space already activates a focused row or button.
- A part is always current in the app, even when nobody clicked it. That is why the current part cannot count as a selection.
- Commands wait behind a running command in the worker. The page has to hide the part at once, without waiting for a solve to finish.
- An assistant could hide a part from the person. The permanent count is there for that reason.

## 3. Realistic materials and lighting

**The idea.** Physically based shading in the 3D view, for finishes such as brushed steel, chrome, aluminum, brass, and plastics, with environment reflections and soft lighting. A plain mode stays for engineering work, and the view stays fast on modest hardware.

**Decisions.**

- Appearance is a separate field from the engineering material. Picking chrome never changes a mass or a strength result.

**Harder than it looks.**

- Mesh normals are flat per triangle, so chrome on a cylinder would show every facet. Smooth normals have to come first.
- Parts have no assigned material today. Only assembly instances do, and that value feeds the mass figures.
- The handbook material list has no brass or chrome.
- The likely approach is a hand-written shader with a computed studio environment. That adds no 3D library and no image files.
- The pictures an assistant gets over MCP come from the software renderer. The assistant would see a plainer image than the person unless that gets its own work.

## 4. Exploded view slider

**The idea.** A distance slider that spreads an assembly along its joints, with a one-click collapse, using the logic the drawings already have and available over MCP.

**Decisions.**

- The slider can go past 100%.
- Measuring is off while the view is exploded.
- The view is labeled display only.

**Harder than it looks.**

- Each part's offset can be multiplied by the slider value in the page, so dragging needs no server call. That needs the per-part transforms from item 1.
- Picking has to stay correct, or stay off, while parts are away from their real positions.

## 5. Motion playback bar

**The idea.** A floating bar at the bottom of the viewport for an assembly with motion: play and pause, a time readout, a scrubbable timeline, and a loop toggle, controllable over MCP.

**Harder than it looks.**

- The server works out the poses once, and the page interpolates between them. Scrubbing cannot send a command for every frame.
- Collision results do not update live during playback, and the bar must not suggest that they do.

## 6. The rest of the layout

**The idea.** The viewport takes nearly the whole screen, with the part tree on the left. One right panel changes with the context, between the inspector (mass, volume, surface area, material, and bounding box in millimeters), the exploded controls, and motion. Camera controls for orbit, pan, zoom, and fit float over the view and are semi-transparent.

**Decisions.**

- The assistant chat stays docked on the right, as a collapsible panel.

**Harder than it looks.**

- The viewport gets only about 35% of the window at 1440 by 900 today.
- The 14 right-hand tabs need new homes.
- This is the item most likely to cause regressions.
- It comes after items 4 and 5, so their controls will be moved when it lands. Build them so that they are easy to move.

## 7. Command palette

**The idea.** Ctrl or Cmd+K searches commands, parts, and projects.

**Harder than it looks.**

- The list of commands already exists, so search is the easy part.
- Many commands need arguments. The palette should open the right panel for those, not run them.

## 8. A clear notice when the app stops

**The idea.** When the server stops, the page says plainly that the app stopped and needs a restart, and stops sending requests.

**Decisions.**

- This replaces the idea of a "new version available, reload" notice, which is dropped. A new version arrives only when the local app restarts, so that notice suits a hosted app more than this one.

**Harder than it looks.**

- Today the page shows a raw "network error" twice. One stale tab logged more than 1,000 failed requests.
- The page has to tell a stopped server from a single failed request.
