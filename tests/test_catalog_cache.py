"""A built catalog solid is kept on disk for this account, so opening a project does not build it again.

A ball bearing took most of a second to build, and every open built each one again.
"""

from __future__ import annotations

import pytest

from aicad import catalog_cache, kernel, library


@pytest.fixture
def cache(tmp_path, monkeypatch):
    folder = tmp_path / "catalog"
    monkeypatch.setenv("AICAD_CATALOG_CACHE", str(folder))
    builds = []
    real = library._build

    def counted(*args, **kwargs):
        builds.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(library, "_build", counted)
    return folder, builds


def _facts(solid) -> tuple:
    measured = kernel.measure(kernel.adopt(solid))
    box = measured.bounding_box
    return (measured.volume_mm3, measured.area_mm2, measured.face_count, measured.edge_count, box.min, box.max)


def test_a_bearing_is_built_once_and_read_back_the_same(cache):
    folder, builds = cache
    first = library.build("ball_bearing", "608")
    second = library.build("ball_bearing", "608")
    assert len(builds) == 1
    assert len(list(folder.glob("*.brep"))) == 1
    assert _facts(second) == _facts(first)


def test_each_request_has_its_own_entry(cache):
    folder, builds = cache
    library.build("socket_head_cap_screw", "M3", 10)
    library.build("socket_head_cap_screw", "M3", 12)
    library.build("socket_head_cap_screw", "M3", 10)
    assert [args[2] for args in builds] == [10, 12]
    assert len(list(folder.glob("*.brep"))) == 2


def test_a_change_to_the_catalog_code_is_a_different_entry(cache, monkeypatch):
    _folder, builds = cache
    library.build("hex_nut", "M4")
    catalog_cache._fingerprint.cache_clear()
    monkeypatch.setattr(catalog_cache, "_fingerprint", lambda: "another catalog")
    library.build("hex_nut", "M4")
    assert len(builds) == 2


def test_a_damaged_entry_is_built_again(cache):
    folder, builds = cache
    first = library.build("ball_bearing", "6001")
    (entry,) = folder.glob("*.brep")
    entry.write_text("not a solid", encoding="utf-8")
    again = library.build("ball_bearing", "6001")
    assert len(builds) == 2
    assert _facts(again) == _facts(first)


def test_a_folder_that_cannot_be_written_still_builds(tmp_path, monkeypatch):
    blocked = tmp_path / "a file, not a folder"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setenv("AICAD_CATALOG_CACHE", str(blocked))
    solid = library.build("plain_washer", "M5")
    assert kernel.measure(kernel.adopt(solid)).volume_mm3 > 0


def test_a_failed_build_is_not_kept(cache):
    folder, _builds = cache
    with pytest.raises(Exception):
        library.build("ball_bearing", "no such bearing")
    assert list(folder.glob("*.brep")) == []
