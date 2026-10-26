---
order: 180
---

The sandbox process uses the project folder as its working directory. That folder is not placed on Python's import path. The child does not inherit the parent's PYTHONPATH. It receives only this install's import roots. A module or package shipped in the project cannot run in place of the sandbox. The same rule applies when the project is built again. The limits are applied before the script runs. A child does not inherit loader variables, interpreter startup variables, or the parent's PATH. CalculiX, ffmpeg, and a slicer are started with that same environment. The script network block is installed with Windows PowerShell from the system directory, so that step does not search PATH. A child still receives the system directory, the Windows command interpreter, and a temporary directory. A failure while the sandbox is starting is reported without a traceback.
