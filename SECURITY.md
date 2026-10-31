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

A script the assistant sends runs in a separate process. That process can read and write the project folder. It cannot use the network unless you turn that on yourself (`aicad app --script-network`, or **Allow network on the next script** in the app). It stops if it runs too long or uses too much memory. If this computer cannot apply those limits, the script is not run.

- **Linux.** Landlock limits files and blocks TCP. When the kernel can scope signals, Landlock also blocks a signal to any process outside the script. The Landlock version is the one this kernel reports. A project file, a configuration file, an environment variable, a plugin, or a tool argument cannot ask for an older one. A symlink, named pipe, socket node, or device node cannot be created outside the project. A small filter blocks every other kind of socket, including UDP, and blocks starting a new session. The same filter refuses a signal call unless it names the script process that installed it, and it refuses a signal sent through a pid file descriptor. It also refuses a resource limit aimed at another process, and a file-owner or terminal notice that can deliver a signal. A limit on this process still works. A child the script forks inherits that filter, so it can signal that script process, which is inside the sandbox, and no other pid. That holds on the kernels that already run a script, including those whose Landlock cannot scope signals yet. It accepts only the native instruction set, so a 32-bit call cannot open a socket the filter already refused. It also refuses io_uring, ptrace, cross-process memory, and taking a file from another process. CPU and memory use `setrlimit`. The process cap counts only the script's own threads. When this user can create a cgroup without root, the cap is `pids.max` on a cgroup made for that script. If the script cannot be moved into that cgroup, it is not run. A cgroup directory that is still busy after the script stops is removed on a later stop. Otherwise the parent counts the threads in the script's process tree and stops the group above the same number (256). The user-wide process limit is not used, because it also counts other programs and was stopping geometry when a browser was already open. A classic seccomp filter cannot count `clone` or `fork`, so it does not limit how fast those calls happen. The process filter is installed on every thread that already exists. If another thread is already there, the script is not run. The script process adopts children that lose their parent. When the tool returns, the script's process group is killed, so a child it started does not keep running. `/proc` stays readable so Python can start. Another process's command line under `/proc` can still be read. Its environment and open files cannot.
- **Mac.** `sandbox-exec` applies a Seatbelt profile, and `setrlimit` sets CPU time. Apple has deprecated `sandbox-exec`. It is still the practical profile that does not need a container. The address-space cap is not applied. macOS maps a system library cache larger than 4 GiB into every process, and setting the cap stops the script before it runs. The parent instead checks the script's resident memory about four times a second and stops the whole process group when that passes 4 GiB. The wall clock still stops a script that runs too long. The profile allows Mach service lookup so Python can start. That is a gap: a system service could do something the script is not allowed to do itself. It also allows reading the `/` directory itself. Without that, the process aborts instead of starting. Files in other directories stay closed. `os.kill` of another process is refused in the script. A child that leaves the script's process group is killed, and the group is killed when the tool returns.
- **Windows.** An AppContainer plus a Job Object. The stricter container is used when Python can still start that way. A normal AppContainer can still read files Windows has opened to every AppContainer, mostly under `C:\Windows`. User files outside the project are blocked either way. The drawing-font list is built once in the Python install, which the container can read. Matplotlib and other library caches, which would otherwise sit in the user profile, are kept in `.aicad/cache` inside the project. The profile itself is not redirected. After the script exits, aicad grants the current user access to files the container created, so the result can be read. The job caps memory, CPU time, and how many processes the script can start, and it does not let a child leave the job. User time at the CPU cap stops the script, including when the exit does not name a signal. The job is terminated when the tool returns, so a child the script started does not keep running. `os.kill` of another process is refused in the script. A job cannot stop the script from calling the Windows `OpenProcess` function on a process outside the job. An AppContainer can open a process only when that process grants the container. This aicad process does not, so `OpenProcess` and `TerminateProcess` on aicad fail. aicad does not rewrite that access list, because doing so stopped later scripts from finishing. Another program can still be opened when it already allows every AppContainer. That is a gap. A traverse grant that does not finish does not change that folder after the wait ends. aicad has to mark the Python install so the container may read it. That works for the usual per-user install from python.org. If Windows refuses that change, the script does not run. When the network is off, firewall rules and a filter block that container's inbound and outbound traffic for every protocol, with no port limit. The script runs only after a TCP connection and both a connected and an unconnected UDP send are refused. A successful send is not a refusal, and a timeout is not a refusal. Turning the network on removes the block. It still does not by itself allow localhost; Windows requires a separate loopback exemption for that.

A design script, one program that calls several steps, stays off until you allow it with `aicad allow-scripts` or the button in the app. The app shows a warning before you do. Each of those runs is appended to `.aicad/script_log.jsonl` in the project. That log sits in the project folder, so a later script can edit it. It is a record you can read, not a lock. A script that only builds one solid (`part = ...`) uses the same confined process. It does not need the allow step. A design script can call only the public document methods. The check report lists only those calls, and only when the script made them. The process that ran the calls writes that report. A result written by the script is not the report. Earlier calls stay in the report.

A shared STEP file larger than 64 MiB is not read, and a project file served to the workspace stops at 32 MiB. The assistant's command client stops reading a response past 32 MiB. A mesh stops at one million triangles. A mesh finer than 0.4 mm, or one whose box would need more than two million elements, is refused before meshing. Tessellation of a large solid uses a coarser deflection so it stays within that cap. A tool result and an error returned to the assistant are marked as data. A script's file-size cap stays at 512 MiB, and one run cannot write more than 512 MiB in total. A setting cannot raise either cap. On Windows the parent enforces both, because that system has no per-file limit. Opening a project does not run Python stored in it. A script part shows its text and, when you have built it before, the solid from that build. STEP files, operation lists, and sketches are data. Importing a STEP file reads that file and does not open another path named inside it. A STEP file in the project that has another name is not opened. A script result that is a link is not read. A geometry file or a picture named by that result is opened only when it is a regular file inside the project. A font configuration file shipped in the project is not read when the geometry libraries are imported. Those libraries use a configuration file packaged with aicad. The process working directory is not changed. An entry title and rationale are one line, with no line break and no comment, so a generated master stays one section.

A reserved device name such as NUL, CON, PRN, AUX, COM1 through COM9, or LPT1 through LPT9 is refused as an output file or folder name, including a superscript digit. On the command line, an absolute export path is the location the person named. A drawing, bill of materials, or exploded view from the workspace stays inside the project. DXF text escapes a backslash and a line break, the same way PDF text escapes characters that would end its string, so a title cannot start another entity. A bill of materials keeps a route name and a material as text. A strength-check picture from a position-error budget stays inside the project. A project path with a colon, an 8.3 short name, a trailing dot or space, or a name that differs only by case from an existing file is refused.

A URDF or MJCF export escapes a part or joint name. A quote in that name stays inside its attribute, and the file parses as one document. The mesh folder is created in that export folder. A mesh folder that is a link is not followed.

ffmpeg, CalculiX, and a slicer are started from PATH outside the project. A copy in the project is not started, and the current directory is not searched.

The sandbox process uses the project folder as its working directory. That folder is not placed on Python's import path. The child does not inherit the parent's PYTHONPATH. It receives only this install's import roots. A module or package shipped in the project cannot run in place of the sandbox. The same rule applies when the project is built again. The limits are applied before the script runs. A child does not inherit loader variables, interpreter startup variables, or the parent's PATH. CalculiX, ffmpeg, and a slicer are started with that same environment. The script network block is installed with Windows PowerShell from the system directory, so that step does not search PATH. A child still receives the system directory, the Windows command interpreter, and a temporary directory. A failure while the sandbox is starting is reported without a traceback.

Files in the project, including `.aicad` and `.env`, do not turn the network on or raise those limits. A link that points outside the project is not written. Saving a project copy does not follow a link, including one that stays inside the project, and does not copy a file that has another name. A session, a note, a requirements file, a blueprint, and a STEP file are read from the project directory without following a link. That open uses the directory handle on Windows, macOS, and Linux, and the size is taken from the opened file. A session larger than 32 MiB, or nested more than 48 levels, is not read. Saving a project as a new folder does not copy a session past that cap. A STEP file larger than 64 MiB is not read, and the text scan of that file uses the same cap. If a file in the project has more than one name, the script is not run, because writing that file would change every name. The script log is not read when that file has another name. A log larger than 1 MiB is not read in full; a new row keeps the newest part of it. The log, a script error, and the design-script status do not repeat the project's absolute path. A plan file is opened from the project directory without following a link. It is not opened when it is a link or has another name, a plan folder that is a link is not listed, and a plan larger than 2,000,000 bytes is not read. A plan file, a plugin manifest, and a file named on the command line are read from a checked directory, and a link at that name is not followed.

Plugins are separate. Installed packs are listed from entry points without importing them. A third-party pack runs only after you allow the publisher, the version, and the permissions it declared. A pack ships with aicad only when its metadata directory is the one next to this copy. The package name is not used for that, so another package with the same name still needs an allow. An entry-point file that cannot be read skips that package and lists a warning; the other packages are still listed. Opening a design does not allow or load a plugin. A design script cannot allow or load a plugin either. That refusal is a flag in the running script process, not an environment variable the script can clear. The consent path it reports is the home config, not a cache folder inside the project. The warning is written by aicad, and the permission list is a declaration, not a sandbox. A plugin export is written inside the project. A path outside that folder is refused. See [docs/plugins.md](docs/plugins.md).

A one-shot network grant is used up only when the design script finishes without an error. If the script fails, the next run still has the grant.

## Security tests

These checks use a marker file outside the project. A project file is data. The tests do not run it as a program.

- **R.** Text in a note, a part name, a blueprint, or a session does not start a script, open a socket, or write outside the project. A design script stays off until it is allowed.
- **S.** The workspace server rejects a foreign host and a foreign origin. A command with no origin is refused. Responses do not offer cross-origin access. A WebSocket upgrade is not accepted. The session token is not written to the server log.
- **T.** A setting cannot raise a script's time or memory cap. A blueprint past 20 MB is not opened. A PNG header with a huge dimension is not opened. A zip or 3MF in the project is not extracted on open. A script past the character cap is not run.
- **U.** A session, a note, a requirements file, or a blueprint that is a link to a file outside the project is not read.
- **V.** A STEP, STL, URDF, or MJCF export, and a session record, do not contain an environment value, a token, or the account name unless that text is part of the model.
- **W.** Opening a project and exporting a part do not run git, a hook, a filter, or `core.fsmonitor`.
- **X.** A project `.env`, pip or uv configuration, `pyproject.toml`, or `requirements.txt` does not change the process environment. A module in the project does not replace the standard library, and `sitecustomize` there does not run.
- **Y.** A parent loader, startup, or import-path variable does not reach a script or CalculiX.
- **Z.** An alternate data stream name, an 8.3 short name, a trailing dot or space, an overlong project path, and a case-only name are refused.
- **AA.** A plan, a plugin manifest, and a command-line file that is a link is not opened.
- **AB.** A STEP file that is exported and imported again stays geometry. A session replay does not run the recorded commands, and opening a project does not run a script stored in the session.
- **AC.** A bidi control, a zero-width character, or a Latin lookalike does not change the title, file name, or note that is stored and shown.
- **AD.** Solver text and errors shown to the assistant are marked as data. Text that already starts with the marker is still wrapped, and a tool result keeps this program's marker sentence. Tessellation and meshing stay inside the triangle budget.

- **AE.** A resource or session handed to the assistant is marked as data. Text that already starts with the marker is still wrapped. A bidi control or a Latin lookalike in a sheet, a research note, or receipt text is not shown as a different string.

Master documents are generated from the entry files in the repository. A symlink or junction on that path is not followed, and a file with another name is not read or written. Generating the masters does not run an entry as code.

- **AF.** Gmsh, OpenCascade, and font libraries do not read a project config file or a parent variable that names one.

This sandbox is for one person on their own machine. It is not a boundary between tenants. The choices are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).

- **AG.** Tessellation and STL export stay inside the triangle budget before native meshing starts, including a large face and a tiny tolerance.

The workspace app listens on this computer only. A bind address that is not loopback is refused. It answers `localhost` and `127.0.0.1` on the port it opened, and it refuses a connection whose peer is not loopback, and a request whose `Origin` is some other site. Responses refuse to be framed, and they ask the browser not to send the address of a request onward. A picture response, whose address carries the session, is not stored. A command that does not name this app as its origin is refused too. A read from the page often sends no origin; that read still needs the session token. Each launch picks a new session token, puts that token in the page it serves, and accepts an API call only when the page sends the token back. The picture requests are the one exception: an image tag cannot set a header, so those carry the same token in the query. Names, notes, and other text from the project are shown as text. A plan title and a note drop hidden format characters. A title or file name that uses a letter which looks like a different letter is refused. The workspace page drops format characters before it shows text. The workspace file view does not open a file that is a link or that has another name. That file is read from the project directory without following a link, and the response tells the browser not to run it. A call from the page is the person. A call from the attached assistant is the assistant. The command body cannot choose which of those it is. Allowing design scripts, granting the network for one script, saving settings, and starting a chat require the page token. The assistant token cannot do those. An MCP resource returns a project error as that error, without a traceback.

- **AH.** A helper program does not inherit the parent environment. An 8.3 short name, including one in an absolute path, is not a project path.

That stops another website, including one that tries to reach this computer by a DNS name that merely points here. It does not stop a program that already has a shell on this computer. An AI with that access can read the page, copy the session, and do what you can do. If a later approval step treats a click in the app as already approved, that step is a guardrail for an assistant that follows the rules. It is not a security boundary against a malicious program on this computer.

- A design script's standard output and standard error are kept only up to 1 MB. A longer stream is not stored.
- **AI.** A name inside a drawing, a 3MF package, or a session frame does not open a file outside the project.

- **E.** A project file is read from the project directory, so a directory link in the middle of the path is not followed.
- A design script's disk quota counts the bytes it writes, including a file that was removed before the next look. The count is read again when the script exits, before the process is collected, so a run that finishes before the next look is still counted. That reading still happens when the system cannot report the exit without collecting the process. A child that exits before its own counter is opened is counted too: on Linux those writes are added when the script collects the child, and on macOS they are read while the child is still a zombie.
- **AJ.** A copy, an export, a render, and a picture response do not follow a symlink or junction, and do not send bytes from outside the project.

- A design script's disk quota counts writes from every thread in the process, not from one thread alone.
- A file a script removes is still counted at the size it reached, including toward the per-file cap. A file that appears and is removed between timer samples is counted too. macOS counts an unlinked file that is still open. Windows counts the file from the directory change.
- A design script's check record is written to the file the parent opened. A different file at that path is not the record.
- **AK.** A request to the model API or the local workspace app does not use a proxy or a certificate file named by the environment. Those variables stay in the process environment.

- A design script's job file, script file, and result are not opened when that name is a link.
- **AL.** A slicer, ffmpeg, and CalculiX each run in a private folder outside the project and see only file names from that folder. Results are copied back into the project. An output folder that is a symlink or junction is not used.

- A token, a design-script opt-in, a session record, and saved assistant settings are created readable by this user only.
- **AM.** A request to the model API or the workspace app does not follow a redirect, and it does not open a file URL. The workspace app refuses a request line that names another host. A host or origin is accepted only as ``localhost`` or ``127.0.0.1`` with the decimal port it bound.

- **AN.** A path that matches an existing name after Unicode normalization is refused.

- **AO.** An 8.3 short name is refused, including a numeric suffix of more than one digit.

- **AP.** Text that is shown or stored from a name, a note, a plugin warning, or an error does not contain a terminal sequence or a hidden control character.

- **AQ.** A STEP import is read from the bytes already taken from that file. The reader is not given a temporary name to open.

- **AR.** A file that is not a STEP exchange, or a STEP list nested past the limit, is not given to the STEP reader.

- **AS.** A glTF, OBJ, or COLLADA import reads the file through the shared file reader. An external buffer, image, or material library is opened only when it is embedded or stays inside that file's folder. A path that leaves the folder is not opened. The project path is not given to a geometry reader.

- **AW.** A directory created on Windows is one request on the parent directory handle. A link at that name is not followed.

- **AT.** Geometry libraries do not read a configuration file from the account home. A design script's home and config directory are an empty folder, and Matplotlib uses a private config directory. Plugin consent stays on the account home.

- **AX.** A session path and a STEP import do not follow a directory link, including one whose target stays inside the project.

- **AU.** A design script receives an allowlist of environment variables. Ordinary parent variables are not copied. The account home is included so the script can resolve it, and Windows also receives the session variables it needs to start. The sandbox on Linux, macOS, and Windows is unchanged.

- **AY.** When the project folder is a link, a file under it is opened from the directory that link points at. A nested link is not followed.

- **AV.** Opening a plan draws each sheet from the plan data. A picture already on disk is shown only when it is that drawing.

- **AZ.** A junction is removed the same way as a symlink before a slicer writes. A junction in a plan folder is not entered or removed when orphan sheets are cleaned up.

- **BA.** The design-record log is created from its parent directory handle. A link at that name is not followed.

- A private directory used for Gmsh, CalculiX, a design-call job, a plugin part copy, or a robot export is opened by its handle. Replacing that directory's name does not change the files read or written there.

- **BB.** Fontconfig reads the file shipped with aicad. That file names system font folders. A configuration file in the account home is not read. Plugin consent stays on the account home.

- **BC.** Gmsh meshes a part from a private folder outside the project. The process temporary directory is not used when that directory is inside a project.

- CalculiX output is read as UTF-8. A byte that is not UTF-8 does not stop the error report.

- A session picture's width and height are read from its header before the picture is decoded. A picture past the pixel limit is refused.

- **BD.** The OpenCascade glTF reader does not open a file. A glTF import reads the file through the shared file reader.

- A file name that the Windows upcase table stores as an existing name is refused, including U+0131 LATIN SMALL LETTER DOTLESS I.

- **BE.** A glTF, OBJ, or COLLADA import may read a file in a subfolder of the mesh file's folder. Each name in that path stays inside the folder. A link is not followed. The file is read through the shared file reader.

- **BF.** Opening a session record draws each step from its stored mesh. A picture that already matches that drawing is left as it is. A step with no mesh does not return its stored picture. A path outside the project is not opened.

- **BH.** Slicer output is read as UTF-8. A byte that is not UTF-8 does not stop the report, and a terminal sequence in that output is not shown.

- **BI.** The directory where a slicer or ffmpeg runs is opened by its handle. Replacing that directory's name does not change the files read or written there.

- **BJ.** Matplotlib's private config directory is opened by its handle. Replacing that directory's name does not change the directory that is used.

- **BK.** A part name in a PDF drawing stays inside one text string. Parentheses and a backslash in that name do not start another PDF command.

- **BL.** A part name in a DXF drawing stays in one group. A newline in that name does not start another entity.

- **BG.** A bill-of-materials cell that begins with =, +, -, or @ is stored as text.

- **BM.** A design script's check record and its macOS confinement profile are written outside the project. The process temporary directory is not used when that directory is inside a project.

- **BN.** A design script cannot read or write a file outside the project. An export and a STEP import stay inside that folder.

- **BO.** A STEP import from the assistant stays inside the project. A path outside that folder is not read.

- **BP.** A directory link is unlinked when a project folder is removed, and the directory it points at is left in place.
- **BQ.** A picture is read from the project before it is shown, and a robot mesh is read from the project before a simulator opens it.
- **BR.** A new project file or folder is created from a directory handle. When the project folder is a link, that handle is the directory the link points at. A link in the rest of the path is not followed.

- **BS.** The design-record stop file is created from its parent directory, and the record is read from its parent directory. A link at either name is not followed.

- **BT.** A file created for the design record is readable and writable by its owner only, on Windows and on other systems.

- **BU.** A picture under a project folder that is a link is read from the directory that link points at. A nested link is not followed.

- **BV.** A project folder that is a link is opened from the directory it points at, including when another directory link sits further out. A nested link is not followed.

- **BW.** When confinement stops, the script result is opened from its parent directory. A link at that name is not followed, and another name for the file is not changed.

- **BX.** A Windows script profile that is already registered is deleted and created again with no capabilities, unless a script is already using it. A profile a script is using is opened only when it has no capabilities. The network block is read back before the script starts.

- A script remove opens that name only to delete it. It does not read or write the file, and a link is removed without opening the file it names. The quota still counts a removed file the watch is holding.
- **BY.** Only the person at the workspace can open a project folder or save a copy and switch to it. The assistant cannot. A session file cannot.

- **BZ.** An assembly name, a part name, an instance name, a joint name, or a material name stays on one line. A line separator, a paragraph separator, or another control character in that name is refused. A drawing, a bill of materials, a report, and a log do not gain a line from that character.

- **CA.** A CalculiX input line is one card. A part name, a note, or a material name is not written into that file. A line break in a card is refused, so it cannot add another card.

- **CB.** The model process does not read a Matplotlib config from the project directory. A design script cannot write the library configuration directory and cannot point that directory somewhere else.

- **CC.** A helper process started by a confined script cannot read a file outside the project. A file in the project can still be read.

- **CD.** The assistant lists folders only inside the open project. A person can list a folder they chose. A folder outside the project is not listed for the assistant.
