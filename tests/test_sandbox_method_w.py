"""Opening a project does not run git configuration that came with it.

The marker outside the project is SANDBOX_CANARY.txt. A hook, a filter, and
core.fsmonitor would write it if git ran them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.workspace import Workspace

_CANARY_TEXT = "CANARY_HIT\n"
_BOX = [{"op": "add", "shape": "box", "size": [4, 4, 4]}]


def test_project_git_configuration_is_not_run(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "SANDBOX_CANARY.txt"
    project = tmp_path / "project"
    project.mkdir()
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = str(empty)
    env["GIT_CONFIG_SYSTEM"] = str(empty)
    subprocess.run([git, "init", "-q"], cwd=project, env=env, check=True)
    hook = project / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        "#!/bin/sh\nprintf '%s' '" + _CANARY_TEXT + "' > '" + str(canary) + "'\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    monitor = project / ".git" / "hooks" / "fsmonitor"
    monitor.write_text(hook.read_text(encoding="utf-8"), encoding="utf-8")
    monitor.chmod(0o755)
    (project / ".git" / "config").write_text(
        "[core]\n"
        f"\tfsmonitor = {monitor}\n"
        "\thooksPath = .git/hooks\n"
        "[filter \"canary\"]\n"
        f"\tsmudge = {hook}\n"
        f"\tclean = {hook}\n"
        "[includeIf \"gitdir:.\"]\n"
        f"\tpath = {outside / 'extra.gitconfig'}\n",
        encoding="utf-8",
    )
    (project / ".gitattributes").write_text("* filter=canary\n", encoding="utf-8")
    (outside / "extra.gitconfig").write_text(
        "[core]\n"
        f"\tfsmonitor = {monitor}\n",
        encoding="utf-8",
    )
    Workspace(project)
    document = Document(project)
    document.create_part_from_ops("base", _BOX)
    document.export("base", "exports/base.step", "step")
    assert not canary.exists()
