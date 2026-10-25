---
order: 110
separator: line
---

- **Mac.** `sandbox-exec` applies a Seatbelt profile, and `setrlimit` sets CPU time. Apple has deprecated `sandbox-exec`. It is still the practical profile that does not need a container. The address-space cap is not applied. macOS maps a system library cache larger than 4 GiB into every process, and setting the cap stops the script before it runs. The parent instead checks the script's resident memory about four times a second and stops the whole process group when that passes 4 GiB. The wall clock still stops a script that runs too long. The profile allows Mach service lookup so Python can start. That is a gap: a system service could do something the script is not allowed to do itself. It also allows reading the `/` directory itself. Without that, the process aborts instead of starting. Files in other directories stay closed. `os.kill` of another process is refused in the script. A child that leaves the script's process group is killed, and the group is killed when the tool returns.
