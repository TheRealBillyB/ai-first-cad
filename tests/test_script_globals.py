"""A part script can read its own names through globals()."""

import pytest

from aicad.sandbox import run_part_script, sandbox_supported
from aicad.errors import ScriptError


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


def test_globals_returns_the_scripts_own_names(tmp_path):
    _sandbox_or_skip()
    script = (
        "for index, size in enumerate((2, 3, 4)):\n"
        "    globals()[f'side_{index}'] = size\n"
        "part = Box(globals()['side_0'], side_1, globals().get('side_2', 1))\n"
    )
    shape = run_part_script(script, tmp_path)
    box = shape.impl.bounding_box()
    assert (round(box.size.X, 6), round(box.size.Y, 6), round(box.size.Z, 6)) == (2, 3, 4)


@pytest.mark.parametrize("name", ["eval", "exec", "compile", "vars"])
def test_the_names_that_run_or_open_code_are_still_missing(tmp_path, name):
    _sandbox_or_skip()
    with pytest.raises(ScriptError, match=name):
        run_part_script(f"{name}\npart = Box(1, 1, 1)\n", tmp_path)
