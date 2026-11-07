"""The threadlocker condition counts as the locking feature it is."""

from __future__ import annotations

from aicad.bolts import check_joint

_BASE = {
    "size": "M8",
    "grade": "a2-70",
    "axial_n": 1000,
    "shear_n": 1000,
    "grip_mm": 20,
    "friction": 0.14,
    "vibration": "intense",
}
_NO_LOCK = "No locking feature is declared. Preload alone is what is holding the nut."


def _recommended(report: dict) -> set[str]:
    return {item["name"] for item in report["loosening"]["countermeasures"] if item["recommended"]}


def test_a_threadlocker_condition_is_not_reported_as_no_locking_feature():
    report = check_joint({**_BASE, "condition": "threadlocker"})
    assert report["loosening"]["rating"] == "moderate"
    assert "threadlocker" in report["locking"]
    assert _NO_LOCK not in report["loosening"]["reasons"]
    # It does not tell the person to add the threadlocker they already chose.
    assert "threadlocker" not in _recommended(report)
    assert "nyloc nut" in _recommended(report)


def test_a_dry_joint_still_says_no_locking_feature():
    report = check_joint({**_BASE, "condition": "dry"})
    assert report["locking"] == []
    assert _NO_LOCK in report["loosening"]["reasons"]
    assert "threadlocker" in _recommended(report)


def test_a_declared_threadlocker_is_not_listed_twice():
    report = check_joint({**_BASE, "condition": "threadlocker", "locking": ["Threadlocker"]})
    assert report["locking"] == ["Threadlocker"]
    assert "threadlocker" not in _recommended(report)


def test_the_threadlocker_torque_is_unchanged():
    report = check_joint({**_BASE, "condition": "threadlocker"})
    assert report["nut_factor"] == 0.18
    assert report["tightening_torque_nm"] == round(0.18 * report["preload_n"] * 8 / 1000.0, 3)
