"""macOS confinement for an untrusted design script.

``sandbox-exec`` applies a Seatbelt profile: the script may read the system
locations Python needs, and it may read and write only the project folder.
The network is denied. CPU time and address space come from ``setrlimit``.
Apple has deprecated ``sandbox-exec``. It is still the practical per-process
profile that does not need a container or an extra install. If it is missing,
or the limits cannot be set, the script is not run.
"""

from __future__ import annotations

import os
import resource
import shutil
from collections.abc import Callable, Sequence

_SYSTEM_ROOTS = (
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library",
    "/opt",
    "/opt/homebrew",
    "/usr/local",
    "/dev",
)


def availability_error() -> str | None:
    if shutil.which("sandbox-exec") is None:
        return (
            "This Mac does not have sandbox-exec, so aicad cannot limit the script "
            "to the project folder. The script was not run."
        )
    return None


def _escape(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_profile(read_paths: Sequence[str], write_paths: Sequence[str], *, network: bool) -> str:
    """Return a Seatbelt profile for one run."""

    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        # Python and the dynamic linker look up a long list of system services.
        # The profile still denies the network below. Mach lookup is a gap: a
        # system service could do work the script is not allowed to do itself.
        "(allow mach-lookup)",
        "(allow ipc-posix-shm*)",
        "(allow system-socket)",
        "(allow file-read-metadata (subpath \"/\"))",
        "(allow file-ioctl (literal \"/dev/null\") (literal \"/dev/zero\") "
        "(literal \"/dev/random\") (literal \"/dev/urandom\") (literal \"/dev/dtracehelper\"))",
    ]
    roots = [path for path in _SYSTEM_ROOTS if os.path.isdir(path)]
    if roots:
        joined = " ".join(f"(subpath {_escape(path)})" for path in roots)
        lines.append(f"(allow file-read* file-map-executable file-ioctl {joined})")
    for path in read_paths:
        lines.append(
            "(allow file-read* file-map-executable file-ioctl "
            f"(subpath {_escape(path)}))"
        )
    for path in write_paths:
        lines.append(
            "(allow file-read* file-write* file-map-executable file-ioctl "
            f"(subpath {_escape(path)}))"
        )
    if network:
        lines.append("(allow network*)")
    else:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def apply_limits(*, cpu_seconds: int, memory_bytes: int) -> None:
    """Set CPU and memory limits. On failure the process exits before the script."""

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (OSError, ValueError) as exc:
        os.write(2, f"Could not set CPU or memory limits: {exc}\n".encode())
        os._exit(127)
    file_cap = 8 * 1024 * 1024 * 1024
    for resource_limit, value in (
        (resource.RLIMIT_DATA, memory_bytes),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, file_cap),
    ):
        try:
            resource.setrlimit(resource_limit, (value, value))
        except (OSError, ValueError):
            pass


def make_preexec(*, cpu_seconds: int, memory_bytes: int) -> Callable[[], None]:
    def preexec() -> None:
        apply_limits(cpu_seconds=cpu_seconds, memory_bytes=memory_bytes)

    return preexec


def command_prefix(profile_path: str) -> list[str]:
    sandbox_exec = shutil.which("sandbox-exec") or "sandbox-exec"
    return [sandbox_exec, "-f", profile_path]


def identity_failures(cpu_seconds: int, memory_bytes: int) -> list[str]:
    failures = []
    _cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
    if cpu_hard == resource.RLIM_INFINITY or cpu_hard > cpu_seconds:
        failures.append("the CPU time limit is not in effect")
    _as_soft, as_hard = resource.getrlimit(resource.RLIMIT_AS)
    if as_hard == resource.RLIM_INFINITY or as_hard > memory_bytes:
        failures.append("the memory limit is not in effect")
    return failures


def network_error_is_confined(error: OSError) -> bool:
    return error.errno in {getattr(os, "EPERM", 1), getattr(os, "EACCES", 13)}
