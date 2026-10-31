"""The limitations section matches the workspace that ships."""

from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_limitations_match_the_workspace():
    text = README.read_text(encoding="utf-8")
    assert "They are not buttons in the Workspace app yet" not in text
    assert "Windows and macOS do not run those scripts" not in text
    assert "There is no click-to-measure" not in text
    assert "Drawing, Bill of materials, and Exploded view" in text
    assert "AppContainer and a Job Object" in text
    assert "Seatbelt profile" in text
    assert "The solid view measures a click" in text
