---
order: 180
---

The sandbox process uses the project folder as its working directory. That folder is not placed on Python's import path. An import path entry that is the project, or a directory inside it, is removed before the process starts. A relative entry is made absolute from the program that launched the script, so it is not read from the project. A module or package shipped in the project cannot run in place of the sandbox. The same rule applies when the project is built again. The limits are applied before the script runs. A failure while the sandbox is starting is reported without a traceback.
