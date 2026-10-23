"""Whether this process is a confined script.

This is not an environment variable. A script can change ``AICAD_SANDBOX_CHILD``.
It cannot clear the flag below, and the plugin door does not read that variable.
"""

from __future__ import annotations


def _cells():
    active = False

    def mark() -> None:
        nonlocal active
        active = True

    def flagged() -> bool:
        return active

    return mark, flagged


mark_sandboxed, _flagged = _cells()
del _cells


def in_sandbox() -> bool:
    """True after the confined child starts, even if the script edits the environment."""
    return _flagged()
