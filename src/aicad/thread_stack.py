"""Stack size for threads that run geometry on macOS.

OpenCascade recurses through a boolean or a mesh on the calling thread once
its own pool is that caller. A thread Python starts on macOS gets 512 KiB
unless a size was set. CPython 3.11 leaves ``THREAD_STACK_SIZE`` at 0, so
that 512 KiB default is what a workspace request, an MCP tool, or a design
script's call thread would use. The process main thread keeps the stack it
was given when the process started.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# 8 MiB, a multiple of the machine page. This is the main-thread stack this
# work already runs on, and the size Linux gives a thread.
GEOMETRY_THREAD_STACK = 8 * 1024 * 1024
# pthread_get_stacksize_np can report a few pages under the reserved stack.
_STACK_REPORT_SLACK = 64 * 1024

SMALL_CALLER_STACK = "The stack on this thread is too small for geometry, so the script was not run."


def prepare_geometry_thread_stack() -> int:
    """Give later Python threads a stack large enough for geometry.

    On macOS, set 8 MiB when the current size is unset or smaller. A larger
    size already chosen by the process is left in place. Other systems are
    unchanged. Returns the size Python will use for a new thread, or 0 when
    the platform default stays in force.
    """

    if sys.platform != "darwin":
        return threading.stack_size()
    current = threading.stack_size()
    if current >= GEOMETRY_THREAD_STACK:
        return current
    threading.stack_size(GEOMETRY_THREAD_STACK)
    return GEOMETRY_THREAD_STACK


def current_thread_stack_bytes() -> int | None:
    """Stack of this thread in bytes. None when it cannot be read."""

    if sys.platform != "darwin":
        return None
    import ctypes

    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    libc.pthread_self.restype = ctypes.c_void_p
    libc.pthread_get_stacksize_np.restype = ctypes.c_size_t
    libc.pthread_get_stacksize_np.argtypes = [ctypes.c_void_p]
    size = int(libc.pthread_get_stacksize_np(libc.pthread_self()))
    if size <= 0:
        return None
    return size


def current_thread_can_run_geometry() -> bool:
    """This thread's stack is large enough for a boolean or a mesh.

    Other systems, and a macOS thread whose size cannot be read, are treated
    as large enough. A measured stack under 8 MiB is not.
    """

    size = current_thread_stack_bytes()
    if size is None:
        return True
    return size + _STACK_REPORT_SLACK >= GEOMETRY_THREAD_STACK


@contextmanager
def child_inherits_geometry_stack() -> Iterator[None]:
    """Let a new process inherit a main-thread stack of at least 8 MiB.

    The child reads the stack limit when it starts. This does not grow a
    thread that already exists. The previous limit is restored after the
    child has been created. Other systems are unchanged.
    """

    if sys.platform != "darwin":
        yield
        return
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    unlimited = hard == resource.RLIM_INFINITY
    if soft >= GEOMETRY_THREAD_STACK or (not unlimited and hard < GEOMETRY_THREAD_STACK):
        yield
        return
    try:
        resource.setrlimit(resource.RLIMIT_STACK, (GEOMETRY_THREAD_STACK, hard))
    except (OSError, ValueError):
        yield
        return
    try:
        yield
    finally:
        try:
            resource.setrlimit(resource.RLIMIT_STACK, (soft, hard))
        except (OSError, ValueError):
            pass
