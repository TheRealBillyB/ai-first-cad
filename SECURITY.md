# Security

Report a vulnerability in private. Please do not open a public GitHub issue for a problem that is not fixed yet.

## How to report

Use GitHub private vulnerability reporting on this repository:

https://github.com/TheRealBillyB/ai-first-cad/security/advisories/new

That form stays off the public issue list. Turn on private vulnerability reporting in the repository settings. Until the form is available, send a private message on GitHub to [TheRealBillyB](https://github.com/TheRealBillyB).

Include what you can:

- What you found, and what an attacker could do with it.
- The version, the commit, and the operating system.
- The steps to reproduce it, including a design file or a script if one is required.
- Whether you have already seen it used against someone.

You will get an acknowledgement. Please give the maintainer time to publish a fix before you write about the problem in public.

## What is in scope

The local tool: the MCP server, the workspace app, the command line, and the Python API. A script the assistant runs, a plugin, and a design file from someone else are in scope. So is anything in a downloaded project that runs code, reads files outside the project, or reaches the network when the person did not ask it to.

## What this version does

On Linux, a script the assistant sends runs in a separate process. That process can read and write the project folder. It cannot use the network unless you turn that on yourself (`aicad app --script-network`, or **Allow network on the next script** in the app). It stops if it runs too long or uses too much memory. If this computer cannot apply those limits, the script is not run.

A design script, one program that calls several steps, stays off until you allow it with `aicad allow-scripts` or the button in the app. The app shows a warning before you do. Each of those runs is appended to `.aicad/script_log.jsonl` in the project. That log sits in the project folder, so a later script can edit it. It is a record you can read, not a lock. A script that only builds one solid (`part = ...`) uses the same confined process. It does not need the allow step.

Opening a project does not run Python stored in it. A script part shows its text and, when you have built it before, the solid from that build. STEP files, operation lists, and sketches are data.

Plugins are separate. Installed packs are listed from entry points without importing them. A third-party pack runs only after you allow the publisher, the version, and the permissions it declared. Opening a design does not allow or load a plugin. A design script cannot allow or load a plugin either. The warning is written by aicad, and the permission list is a declaration, not a sandbox. See [docs/plugins.md](docs/plugins.md).

This sandbox is for one person on their own Linux machine. It is not a boundary between tenants. Windows and macOS refuse to run assistant scripts until they grow their own confinement. The choices are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).
