"""A STEP file in the project that has another name is not imported.

The marker is CANARY_HIT outside the project. Import must not copy it into
the project and must not change the outside file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import DocumentError

_CANARY = "CANARY_HIT\n"


def test_a_step_file_with_another_name_is_not_imported(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.step"
    secret.write_text(_CANARY, encoding="utf-8")
    project = tmp_path / "project"
    alias = project / "alias.step"
    document = Document(project)
    try:
        os.link(secret, alias)
    except OSError:
        pytest.skip("this machine cannot create a hard link")
    with pytest.raises(DocumentError, match="another name"):
        document.import_step("box", "alias.step")
    assert not (project / "imports" / "box.step").exists()
    assert secret.read_text(encoding="utf-8") == _CANARY
    assert alias.is_file()


def _directory_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("this machine cannot create a junction")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a directory symlink")


def test_a_step_import_does_not_follow_a_directory_link_inside_the_project(tmp_path):
    project = tmp_path / "project"
    document = Document(project)
    document.create_part_from_ops(
        "seed",
        [{"op": "add", "shape": "box", "size": [4, 4, 4], "center": [0, 0, 0]}],
    )
    kept = project / "kept"
    kept.mkdir()
    source = kept / "seed.step"
    document.export("seed", source, "step")
    before = source.read_bytes()
    _directory_link(project / "alias", kept)
    with pytest.raises(DocumentError, match="link and was not followed"):
        document.import_step("copy", "alias/seed.step")
    assert not (project / "imports" / "copy.step").exists()
    assert source.read_bytes() == before
    assert "copy" not in {part.name for part in document.list_parts()}


def test_a_relative_step_file_is_still_imported(tmp_path):
    project = tmp_path / "project"
    document = Document(project)
    document.create_part_from_ops(
        "seed",
        [{"op": "add", "shape": "box", "size": [4, 4, 4], "center": [0, 0, 0]}],
    )
    destination = project / "seed.step"
    document.export("seed", destination, "step")
    imported = document.import_step("copy", "seed.step")
    assert imported.name == "copy"
    assert (project / "imports" / "copy.step").is_file()
