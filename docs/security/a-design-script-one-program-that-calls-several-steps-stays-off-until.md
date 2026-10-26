---
order: 130
---

A design script, one program that calls several steps, stays off until you allow it with `aicad allow-scripts` or the button in the app. The app shows a warning before you do. Each of those runs is appended to `.aicad/script_log.jsonl` in the project. That log sits in the project folder, so a later script can edit it. It is a record you can read, not a lock. A script that only builds one solid (`part = ...`) uses the same confined process. It does not need the allow step. A design script can call only the public document methods. The check report lists only those calls, and only when the script made them. The process that ran the calls writes that report. A result written by the script is not the report. Earlier calls stay in the report.
