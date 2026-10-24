"""Stack size for threads that run geometry on macOS.

OpenCascade recurses through a boolean or a mesh on the calling thread once
its own pool is that caller. A thread created with ``pthread_create`` and
the default attributes gets 512 KiB. CPython's own threads on macOS are
about 16 MiB when the size is left unset, which is large enough. Calling
``threading.stack_size`` with no size stores that unset default and returns
the previous explicit size, so a read has to put the explicit size back.
The process main thread keeps the stack it was given when the process started.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# 8 MiB, a multiple of the machine page. This is the main-thread stack this
# work already runs on, and the size Linux gives a thread.
GEOMETRY_THREAD_STACK = 8 * 1024 * 1024
# 16 MiB, the stack CPython gives a macOS thread when no size is set.
# A multiple of the 16 KiB page.
_PYTHON_THREAD_STACK = 16 * 1024 * 1024
# pthread_get_stacksize_np can report a few pages under the reserved stack.
_STACK_REPORT_SLACK = 64 * 1024

SMALL_CALLER_STACK = "The stack on this thread is too small for geometry, so the script was not run."

_lock = threading.Lock()
_chosen: int | None = None


def _measure_default_thread() -> int:
    """Stack of one new thread while the explicit size is still unset."""

    found: dict[str, int] = {}

    def read() -> None:
        size = current_thread_stack_bytes()
        if size is not None:
            found["size"] = size

    thread = threading.Thread(target=read, name="aicad-stack-size")
    thread.start()
    thread.join()
    return found.get("size", 0)


def _choose_stack_size() -> int:
    """Pick an explicit size and remember it.

    ``threading.stack_size(0)`` stores the platform default and returns the
    previous explicit size. A previous size of at least 8 MiB is kept. An
    unset size is measured: the CPython default is kept when that thread is
    already large enough, and 8 MiB is stored when it is not.
    """

    global _chosen
    if _chosen is not None:
        return _chosen
    previous = int(threading.stack_size(0))
    if previous >= GEOMETRY_THREAD_STACK:
        _chosen = previous
        return previous
    if previous:
        threading.stack_size(previous)
    measured = _measure_default_thread() if previous == 0 else 0
    if measured >= GEOMETRY_THREAD_STACK:
        _chosen = _PYTHON_THREAD_STACK
    else:
        _chosen = GEOMETRY_THREAD_STACK
    return _chosen


def prepare_geometry_thread_stack() -> int:
    """Give later Python threads a stack large enough for geometry.

    On macOS, store an explicit size of at least 8 MiB and apply it again
    on every call. Other systems are unchanged. Returns that size, or 0
    where the platform default stays in force.
    """

    if sys.platform != "darwin":
        return 0
    with _lock:
        chosen = _choose_stack_size()
        threading.stack_size(chosen)
        return chosen


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
