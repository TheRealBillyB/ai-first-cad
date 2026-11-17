---
date: 2026-11-16
order: 1
---

# A video can use the optional imageio-ffmpeg build when ffmpeg is not on PATH

ffmpeg on PATH still comes first. The imageio-ffmpeg build is read from that package's own folder, not from the variable that could name another program, and a copy inside a project is not started. It is an opt-in extra (`python install.py --with-ffmpeg`) because the build is GPL and runs as a separate program. With neither, the GIF is still written and the message gives the install commands for this system.
