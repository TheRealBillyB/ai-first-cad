"""Which shard runs a test file.

The Windows suite runs sandboxed tests one at a time, because they share one
container profile. A runner of its own has its own profile, so the files
are split across runners. A file stays whole, so its fixtures stay together.

windows_test_seconds.json holds each file's share of a Windows job, measured on a
runner. A sandboxed file counts in full. Any other file counts a quarter of its
time, because it shares four workers. A stale number only makes the split less even.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

_SECONDS = Path(__file__).with_name("windows_test_seconds.json")


def measured_seconds() -> dict[str, float]:
    return json.loads(_SECONDS.read_text(encoding="utf-8"))


def assign(seconds: dict[str, float], count: int) -> dict[str, int]:
    """Longest file first, each to the shard with the least so far. Same input, same split."""
    totals = [0.0] * count
    placed: dict[str, int] = {}
    for name in sorted(seconds, key=lambda item: (-seconds[item], item)):
        shard = min(range(count), key=lambda index: (totals[index], index))
        placed[name] = shard
        totals[shard] += seconds[name]
    return placed


def shard_of(path: str, count: int, placed: dict[str, int]) -> int:
    """The shard of a file. A file not measured yet goes by its name, so it still runs exactly once."""
    if path in placed:
        return placed[path]
    return zlib.crc32(path.encode("utf-8")) % count


def parse(text: str) -> tuple[int, int]:
    """``2/3`` is the second of three shards. Returns (1, 3)."""
    left, _, right = text.partition("/")
    index, count = int(left), int(right)
    if not 1 <= index <= count:
        raise ValueError(f"--shard {text} is not one of the shards")
    return index - 1, count
