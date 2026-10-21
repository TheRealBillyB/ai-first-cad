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

On the current code, a part script runs in-process with the full Python builtins, and opening a project runs the scripts stored in `session.json`. There is no plugin loader. Treat a project from someone else as code you are about to run.
