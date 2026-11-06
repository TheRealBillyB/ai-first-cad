"""A container that will not start says why. The script still does not start."""

import pytest

from aicad import sandbox_windows


def test_a_container_that_will_not_start_is_named_and_the_script_is_not_run(monkeypatch):
    monkeypatch.setattr(sandbox_windows, "_mode", None)
    monkeypatch.setattr(sandbox_windows, "_mode_error", None)
    monkeypatch.setattr(sandbox_windows, "_probe_mode", lambda mode, paths: "the probe stopped: access denied")
    with pytest.raises(OSError) as caught:
        sandbox_windows.ensure_mode([])
    assert "Windows could not start a confined Python (the probe stopped: access denied)." in str(caught.value)
    assert "The script was not run." in str(caught.value)
    assert sandbox_windows._mode is None
