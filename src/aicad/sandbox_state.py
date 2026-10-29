"""Whether this process is a confined script.

This is not an environment variable. A script can change ``AICAD_SANDBOX_CHILD``.
It cannot clear the flag below, and the plugin door does not read that variable.
The account home is remembered before a script's home directory is replaced.
A design-script call runs in another process. That process marks the call with
a context flag, not an environment variable, so the script cannot clear it.
"""

from __future__ import annotations

import contextlib
import contextvars
from pathlib import Path

_design_call: contextvars.ContextVar[bool] = contextvars.ContextVar("aicad_design_call", default=False)


def _cells():
    active = False
    home: Path | None = None

    def mark() -> None:
        nonlocal active
        active = True

    def flagged() -> bool:
        return active

    def remember(path: Path) -> None:
        nonlocal home
        if home is None:
            home = Path(path)

    def remembered() -> Path | None:
        return home

    return mark, flagged, remember, remembered


mark_sandboxed, _flagged, remember_account_home, account_home = _cells()
del _cells


def in_sandbox() -> bool:
    """True after the confined child starts, even if the script edits the environment."""
    return _flagged()


def in_design_call() -> bool:
    """True while this thread is running a call a design script asked for."""
    return bool(_design_call.get())


@contextlib.contextmanager
def design_call():
    """Mark one document call that a design script asked for."""
    token = _design_call.set(True)
    try:
        yield
    finally:
        _design_call.reset(token)
