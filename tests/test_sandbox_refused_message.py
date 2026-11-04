"""A container that will not start says why. The script still does not start."""

import pytest

from aicad import sandbox_windows


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        (["same", "same"], "Both AppContainer modes failed (same)."),
        (["one", "two"], "A normal AppContainer failed (two)."),
    ],
)
def test_neither_mode_starts_and_the_script_is_not_run(monkeypatch, errors, expected):
    monkeypatch.setattr(sandbox_windows, "_mode", None)
    monkeypatch.setattr(sandbox_windows, "_mode_error", None)
    answers = iter(errors)
    monkeypatch.setattr(sandbox_windows, "_probe_mode", lambda mode, paths: next(answers))
    with pytest.raises(OSError) as caught:
        sandbox_windows.ensure_mode([])
    assert expected in str(caught.value)
    assert "The script was not run." in str(caught.value)
    assert sandbox_windows._mode is None
