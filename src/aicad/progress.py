"""How far a long command has got, for whoever is waiting on it.

A long command (a motion study, a strength solve, a wall check, an animation)
calls :func:`report` as it goes. Whoever runs the command listens with
:func:`listening`. The workspace does, and passes what it hears to the app's
busy note and, from an agent's server, to stderr. Nobody listening costs
nothing. A new stage ("meshing", then "solving") always reaches the listener.
Repeats of one stage, such as "step 3 of 10" then "step 4 of 10", reach it at
most once a second.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# A listener hears at most one update in this many seconds.
_EVERY_SECONDS = 1.0

_local = threading.local()


@contextmanager
def listening(callback: Callable[[dict], None]) -> Iterator[None]:
    """Hear the updates :func:`report` makes on this thread while the block runs."""
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = _local.stack = []
    entry = {"callback": callback, "last": None, "stage": None}
    stack.append(entry)
    try:
        yield
    finally:
        stack.remove(entry)


def report(text: str, done: int | None = None, total: int | None = None) -> None:
    """Say how far the command on this thread has got, such as ``"step 3 of 10"``."""
    stack = getattr(_local, "stack", None)
    if not stack:
        return
    entry = stack[-1]
    now = time.monotonic()
    stage = _stage(str(text), done, total)
    # A new stage always goes out: dropping it left "meshing" on screen for a whole solve.
    if stage == entry["stage"] and entry["last"] is not None and now - entry["last"] < _EVERY_SECONDS:
        return
    entry["last"] = now
    entry["stage"] = stage
    update: dict = {"text": str(text)}
    if done is not None:
        update["done"] = int(done)
    if total is not None:
        update["total"] = int(total)
    try:
        entry["callback"](update)
    except Exception:  # A listener's failure must not stop the command it watches.
        pass


def _stage(text: str, done: int | None, total: int | None) -> tuple:
    """What an update is about, without its count: "step 3 of 10" and "step 4 of 10" are one stage."""
    if done is None:
        return ("text", text)
    return ("count", re.sub(r"\d+", "#", text), total)
