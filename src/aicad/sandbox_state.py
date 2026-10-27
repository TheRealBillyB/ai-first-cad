"""Whether this process is a confined script.

This is not an environment variable. A script can change ``AICAD_SANDBOX_CHILD``.
It cannot clear the flag below, and the plugin door does not read that variable.
The account home is remembered before a script's home directory is replaced.
"""

from __future__ import annotations

from pathlib import Path


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
