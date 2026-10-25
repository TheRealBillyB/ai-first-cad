---
order: 190
---

Files in the project, including `.aicad` and `.env`, do not turn the network on or raise those limits. A link that points outside the project is not written. A session, a note, a requirements file, a blueprint, and a STEP file are read from the project directory without following a link. A session larger than 32 MiB, or nested more than 48 levels, is not read. Saving a project as a new folder does not copy a session past that cap. A STEP file larger than 64 MiB is not read, and the text scan of that file uses the same cap. If a file in the project has more than one name, the script is not run, because writing that file would change every name. The script log is not read when that file has another name. A plan file is not opened when it is a link or has another name, and a plan folder that is a link is not listed.
