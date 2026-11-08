---
date: 2026-11-07
---

# The script container is granted only what it reads, never a broad folder

Windows grants to the script container are permanent and inherited, and they followed every sys.path entry and the folder of any helper program or script, so a Python started from a script in Temp gave aicadscript read access to all of Temp. The container now reads Python's prefixes, the interpreter folder, the site-packages folders, and the folder that holds aicad. A helper keeps its own install folder, and its lib or lib64 beside it, unless that folder is a broad one, in which case only the program file is granted. A script passed to Python is granted as a file, not its folder. A drive root, the user folder, Temp, LocalAppData, Desktop, Documents, and Downloads are refused for any read or project grant with a clear message. `aicad sandbox-setup` removes any aicadscript or aicadscriptnet entry set on one of those folders that opens the folder or its children, and prints each removal. It keeps the traverse-only entry (`S,X,RA` on that folder alone) that lets a script walk through to a project inside; removing it stopped the next script in a running aicad from reaching its project.
