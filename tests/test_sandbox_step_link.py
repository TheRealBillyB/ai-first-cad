"""A STEP file in the project that has another name is not imported.

The marker is CANARY_HIT outside the project. Import must not copy it into
the project and must not change the outside file.
"""

from __future__ import annotations

import os
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
