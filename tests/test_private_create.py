"""A private file is created with its final access, before its contents are written."""

from __future__ import annotations

import os
from pathlib import Path

from aicad.paths import file_is_user_private, publish_file, publish_text


def test_a_private_file_is_owner_only_before_it_has_contents(tmp_path):
    path = tmp_path / "secret.txt"

    def write(temporary: Path) -> None:
        assert temporary.stat().st_size == 0
        assert file_is_user_private(temporary)
        temporary.write_text("secret", encoding="utf-8")

    publish_file(path, write, root=tmp_path, private=True)
    assert path.read_text(encoding="utf-8") == "secret"
    assert file_is_user_private(path)


def test_a_normal_publish_stays_group_readable(tmp_path):
    path = tmp_path / "plain.txt"
    publish_text(path, "plain", root=tmp_path)
    assert path.read_text(encoding="utf-8") == "plain"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o644
        assert not file_is_user_private(path)


def test_a_token_is_private_before_the_later_tighten(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path))
    from aicad import paths
    from aicad.app import write_agent_token

    real = paths.restrict_private_file
    seen: list[bool] = []

    def spy(path: Path) -> None:
        size = path.stat().st_size
        if size == 0:
            real(path)
            return
        seen.append(file_is_user_private(path))
        real(path)

    monkeypatch.setattr(paths, "restrict_private_file", spy)
    write_agent_token(9, "secret-token")
    stored = tmp_path / "9.token"
    assert stored.read_text(encoding="utf-8") == "secret-token"
    assert file_is_user_private(stored)
    assert seen == [True]


def test_an_opt_in_file_is_private_before_chmod(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path))
    from aicad.sandbox import allow_design_scripts

    real = os.chmod
    seen: list[bool] = []

    def spy(path, mode, follow_symlinks=True):
        file = Path(path)
        if file.name == "design_scripts.json":
            seen.append(file_is_user_private(file))
        return real(path, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "chmod", spy)
    allow_design_scripts(tmp_path / "project")
    assert seen == [True]
    assert file_is_user_private(tmp_path / "design_scripts.json")
