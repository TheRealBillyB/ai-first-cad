"""Every test file runs on exactly one shard."""

import subprocess
import sys
from pathlib import Path

import pytest

from shards import assign, measured_seconds, parse, shard_of

ROOT = Path(__file__).resolve().parents[1]


def _test_files() -> list[str]:
    return sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").rglob("test_*.py"))


@pytest.mark.parametrize("count", [2, 3, 4])
def test_every_file_lands_on_exactly_one_shard(count):
    placed = assign(measured_seconds(), count)
    shards = [shard_of(path, count, placed) for path in _test_files()]
    assert all(0 <= shard < count for shard in shards)
    assert shards == [shard_of(path, count, placed) for path in _test_files()]


def test_the_measured_files_are_real_files():
    assert set(measured_seconds()) <= set(_test_files())


def test_the_split_is_balanced_by_measured_time():
    seconds = {"a": 100.0, "b": 60.0, "c": 50.0, "d": 40.0, "e": 30.0, "f": 20.0}
    placed = assign(seconds, 3)
    totals = [sum(value for name, value in seconds.items() if placed[name] == index) for index in range(3)]
    assert max(totals) <= 110
    assert assign(seconds, 3) == placed


def test_a_new_file_still_has_a_shard():
    assert 0 <= shard_of("tests/test_not_measured_yet.py", 3, {}) < 3


@pytest.mark.parametrize("text", ["0/3", "4/3", "x/3", "3"])
def test_a_bad_shard_is_refused(text):
    with pytest.raises(ValueError):
        parse(text)


def _collected(*extra: str) -> set[str]:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", *extra, "tests/test_shards.py", "tests/test_install.py", "tests/test_sketch_arc_tool.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {line for line in done.stdout.splitlines() if "::" in line}


def test_the_option_splits_a_real_collection_with_nothing_lost():
    everything = _collected()
    parts = [_collected("--shard", f"{index}/3") for index in (1, 2, 3)]
    assert everything
    assert set().union(*parts) == everything
    assert sum(len(part) for part in parts) == len(everything)
