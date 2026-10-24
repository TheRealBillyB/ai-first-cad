# Principles

These are the rules for choosing what to build. A feature that needs to break one of them gets a new file in [docs/decisions/](decisions/) first. The [decision log](DECISIONS.md) is generated from those files.

A person and an assistant share one command layer. The rules below are how that stays true once the program can do more than make a single part.

## One command layer

People and assistants share every action. Sketching, extruding, selecting a face, changing a dimension, placing a screw, running a check, and exporting a file are all commands.

The Python library, the command-line tool, the MCP server, and the future application are different doors into that same set. (MCP, the Model Context Protocol, is how an assistant such as Cursor calls a local program.) If a person can do it, the assistant has a command for it. If the assistant can do it, the person has the same command.

## The assistant has to see the result

An assistant that only writes a script is guessing. After a change it needs something to look at and something to measure.

- **Pictures.** The usual views of the part, drawn with no screen and no graphics card, so a server can make them. The pictures come back to the assistant as images.
- **Cuts.** A section through the part, so a rib hidden inside a boss shows up.
- **Numbers.** Size, volume, and (once a material is set) mass. Wall thickness, gaps, and overlap.
- **Checks.** A valid solid, a closed surface, clearance, and later strength, motion, and manufacturing.

## Say what was assumed

Every analysis states its assumptions in plain language, in the result, every time. The result also names whether it is a verified check of the model or a rough estimate.

A wall-thickness number says that it came from samples, and which samples. A stress result says which material, which loads, which supports, and what was left out. A first strength check, for example, is a linear static one: it estimates stress under a steady load, and it does not cover buckling, fatigue, or parts pushing on each other. A pass under unstated assumptions is not a pass.

## Explain why

Feedback says what happened and why, in words a designer can act on. A bare pass or fail is not enough.

The first motor-mount bracket is the example. Two ribs sat almost entirely inside the round boss, so they added little material, and a cut through the middle of the boss did not show them. A useful report says that: how much of the rib was already buried, and which cut makes the leftover tip visible. A later strength check should say which region is past the safety factor and which load put it there.

A design-review helper should turn a pile of checks into that kind of explanation, and point at the next change.

## A person can design the whole part here

The assistant is a partner. The person can still sketch, extrude, pick faces, edit dimensions, and write the requirements themselves, inside this application.

Looking over an assistant's work is one way to use the program. Designing a part here, without handing the model to some other program, is the ordinary way.

## Requirements stay with the design

Goals are written down where the design lives. Hold this load. Fit this printer bed. Stay under this mass. Each new version of the design is checked against those goals, and the result is kept next to the decisions that led there.

## Reuse engines that already work

Solving a sketch, building a mesh, estimating stress, slicing a print, and laying out a circuit are problems with maintained open-source tools. aicad connects those tools, puts their answers on the command layer, and explains them.

New code belongs in the gaps between those engines, and in the shared application.

## Related

- [Roadmap](ROADMAP.md)
- [Decisions](DECISIONS.md)
