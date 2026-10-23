"""Project files and a later rebuild stay inside the project.

A shared project can carry settings, a log, links, and Python files. Opening
it and building a part must not write SANDBOX_CANARY.txt outside the project.
The canary text is CANARY_HIT.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import ScriptError
from aicad.kernel import measure
from aicad.sandbox import (
    MEMORY_BYTES,
    WALL_SECONDS,
    allow_design_scripts,
    memory_bytes,
    run_design_script,
    run_part_script,
    sandbox_supported,
    wall_seconds,
)
from aicad.workspace import Workspace


CANARY_NAME = "SANDBOX_CANARY.txt"
CANARY_BODY = "CANARY_HIT"
_BOX = "part = Box(1, 1, 1)\n"
_ENV_KEYS = (
    "AICAD_SCRIPT_NETWORK",
    "AICAD_SCRIPT_WALL_SECONDS",
    "AICAD_SCRIPT_CPU_SECONDS",
    "AICAD_SCRIPT_MEMORY_MB",
    "XDG_CACHE_HOME",
    "MPLCONFIGDIR",
    "PYTHONPATH",
    "TMPDIR",
)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    monkeypatch.delenv("AICAD_SCRIPT_WALL_SECONDS", raising=False)
    monkeypatch.delenv("AICAD_SCRIPT_CPU_SECONDS", raising=False)
    monkeypatch.delenv("AICAD_SCRIPT_MEMORY_MB", raising=False)
    monkeypatch.delenv("AICAD_SETTINGS", raising=False)
    monkeypatch.delenv("AICAD_PLUGIN_CONSENT", raising=False)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    return tmp_path


def _sandbox_or_skip() -> None:
    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    return project, outside


def _canary(outside: Path) -> Path:
    return outside / CANARY_NAME


def _assert_canary_absent(outside: Path) -> None:
    path = _canary(outside)
    assert not path.exists()


def _assert_outside_empty(outside: Path) -> None:
    assert list(outside.iterdir()) == []
    _assert_canary_absent(outside)


def _symlink(link: Path, target: Path, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are not available: {exc}")


def _plant_text(canary: Path) -> str:
    return (
        "from pathlib import Path\n"
        f"Path({str(canary)!r}).write_text({CANARY_BODY!r}, encoding='utf-8')\n"
        f"Path('module_ran.txt').write_text({CANARY_BODY!r}, encoding='utf-8')\n"
    )


class _Watch:
    """Record names that show up outside the project while a build runs."""

    def __init__(self, outside: Path) -> None:
        self.outside = outside
        self.seen: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> None:
        try:
            for path in self.outside.rglob("*"):
                self.seen.append(path.relative_to(self.outside).as_posix())
        except OSError:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(0.001)

    def __enter__(self) -> "_Watch":
        self._thread.start()
        return self

    def __exit__(self, *args) -> None:
        self._sample()
        self._stop.set()
        self._thread.join(timeout=2)


def _write_attempt(canary: Path) -> str:
    return (
        "from pathlib import Path\n"
        "try:\n"
        f"    Path({str(canary)!r}).write_text({CANARY_BODY!r}, encoding='utf-8')\n"
        "except Exception:\n"
        "    pass\n"
        "part = Box(1, 1, 1)\n"
    )


def test_project_config_cannot_enable_network_or_redirect_writes(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    dot = project / ".aicad"
    dot.mkdir()
    claims = {
        "network": True,
        "AICAD_SCRIPT_NETWORK": "1",
        "AICAD_SCRIPT_WALL_SECONDS": 999999,
        "AICAD_SCRIPT_CPU_SECONDS": 999999,
        "AICAD_SCRIPT_MEMORY_MB": 999999,
        "TMPDIR": str(outside),
        "XDG_CACHE_HOME": str(outside),
        "MPLCONFIGDIR": str(outside),
        "PYTHONPATH": str(project),
        "cache": str(outside),
    }
    (dot / "config.json").write_text(json.dumps(claims) + "\n", encoding="utf-8")
    (dot / "settings.json").write_text(json.dumps(claims) + "\n", encoding="utf-8")
    (dot / "env").write_text("AICAD_SCRIPT_NETWORK=1\nTMPDIR=" + str(outside) + "\n", encoding="utf-8")
    (dot / "environment").write_text("AICAD_SCRIPT_NETWORK=1\n", encoding="utf-8")
    (project / ".env").write_text(
        "AICAD_SCRIPT_NETWORK=1\n"
        f"TMPDIR={outside}\n"
        f"XDG_CACHE_HOME={outside}\n"
        f"MPLCONFIGDIR={outside}\n"
        f"PYTHONPATH={project}\n",
        encoding="utf-8",
    )
    (dot / "script_log.jsonl").write_text(json.dumps(claims) + "\n", encoding="utf-8")
    before = {key: os.environ.get(key) for key in _ENV_KEYS}
    workspace = Workspace(project)
    status = workspace.script_status()
    assert status["enabled"] is False
    assert status["network"] == "off"
    assert wall_seconds() == WALL_SECONDS
    assert memory_bytes() == MEMORY_BYTES
    shape = run_part_script(_write_attempt(canary), project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    assert {key: os.environ.get(key) for key in _ENV_KEYS} == before
    _assert_canary_absent(outside)


def test_script_log_symlink_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    canary.write_text("original\n", encoding="utf-8")
    log = project / ".aicad" / "script_log.jsonl"
    log.parent.mkdir()
    _symlink(log, canary)
    allow_design_scripts(project)
    payload = run_design_script(project, "print('hello')\n")
    assert payload["ok"] is True
    assert canary.read_text(encoding="utf-8") == "original\n"
    assert CANARY_BODY not in canary.read_text(encoding="utf-8")


def test_dangling_script_log_symlink_is_not_created(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    log = project / ".aicad" / "script_log.jsonl"
    log.parent.mkdir()
    _symlink(log, canary)
    allow_design_scripts(project)
    payload = run_design_script(project, "print('hello')\n")
    assert payload["ok"] is True
    _assert_canary_absent(outside)


def test_aicad_directory_symlink_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    _symlink(project / ".aicad", outside, directory=True)
    with _Watch(outside) as watch:
        with pytest.raises(ScriptError, match="outside the project"):
            run_part_script(_write_attempt(_canary(outside)), project)
    assert watch.seen == []
    _assert_outside_empty(outside)


def test_jobs_directory_symlink_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    jobs = project / ".aicad" / "jobs"
    jobs.parent.mkdir()
    _symlink(jobs, outside, directory=True)
    with _Watch(outside) as watch:
        with pytest.raises(ScriptError, match="outside the project"):
            run_part_script(_write_attempt(_canary(outside)), project)
    assert watch.seen == []
    _assert_outside_empty(outside)


def test_tmp_directory_symlink_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    temporary = project / ".aicad" / "tmp"
    temporary.parent.mkdir()
    _symlink(temporary, outside, directory=True)
    with _Watch(outside) as watch:
        with pytest.raises(ScriptError, match="outside the project"):
            run_part_script(_write_attempt(_canary(outside)), project)
    assert watch.seen == []
    _assert_outside_empty(outside)


def test_cache_directory_symlink_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    cache = project / ".aicad" / "cache"
    cache.parent.mkdir()
    _symlink(cache, outside, directory=True)
    script = (
        "from pathlib import Path\n"
        "try:\n"
        f"    Path('.aicad/cache/{CANARY_NAME}').write_text({CANARY_BODY!r}, encoding='utf-8')\n"
        "except Exception:\n"
        "    pass\n"
        "part = Box(1, 1, 1)\n"
    )
    shape = run_part_script(script, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_outside_empty(outside)


def test_geometry_directory_symlink_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    geometry = project / ".aicad" / "geometry"
    geometry.parent.mkdir()
    _symlink(geometry, outside, directory=True)
    document = Document(project)
    with _Watch(outside) as watch:
        with pytest.raises(ScriptError, match="outside the project"):
            document.create_part_from_script("box", _write_attempt(_canary(outside)))
    assert watch.seen == []
    _assert_outside_empty(outside)
    assert list(outside.glob("*.step")) == []


def test_hard_linked_file_is_not_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    inside = project / "linked.txt"
    inside.write_text("original\n", encoding="utf-8")
    canary = _canary(outside)
    try:
        os.link(inside, canary)
    except OSError as exc:
        pytest.skip(f"hard links are not available: {exc}")
    script = (
        "try:\n"
        "    open('linked.txt', 'w', encoding='utf-8').write('CANARY_HIT')\n"
        "except Exception:\n"
        "    pass\n"
        "part = Box(1, 1, 1)\n"
    )
    with pytest.raises(ScriptError, match="more than one name"):
        run_part_script(script, project)
    assert inside.read_text(encoding="utf-8") == "original\n"
    assert canary.read_text(encoding="utf-8") == "original\n"
    assert CANARY_BODY not in canary.read_text(encoding="utf-8")


def test_script_does_not_write_through_a_file_symlink(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    canary.write_text("original\n", encoding="utf-8")
    _symlink(project / "link.txt", canary)
    script = (
        "try:\n"
        "    open('link.txt', 'w', encoding='utf-8').write('CANARY_HIT')\n"
        "except Exception:\n"
        "    pass\n"
        "part = Box(1, 1, 1)\n"
    )
    shape = run_part_script(script, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    assert canary.read_text(encoding="utf-8") == "original\n"


def test_project_modules_are_not_imported(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    body = _plant_text(canary)
    for name in (
        "json.py",
        "pathlib.py",
        "math.py",
        "build123d.py",
        "os.py",
        "sitecustomize.py",
        "usercustomize.py",
    ):
        (project / name).write_text(body, encoding="utf-8")
    package = project / "aicad"
    package.mkdir()
    (package / "__init__.py").write_text(body, encoding="utf-8")
    (package / "sandbox_run.py").write_text(body, encoding="utf-8")
    shape = run_part_script(_BOX, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(outside)
    assert not (project / "module_ran.txt").exists()


def test_second_build_does_not_import_modules_planted_by_the_first(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    body = _plant_text(canary)
    names = (
        "json.py",
        "pathlib.py",
        "math.py",
        "build123d.py",
        "os.py",
        "sitecustomize.py",
        "usercustomize.py",
        "script.py",
        "part.py",
    )
    script = (
        "from pathlib import Path\n"
        f"body = {body!r}\n"
        f"for name in {names!r}:\n"
        "    Path(name).write_text(body, encoding='utf-8')\n"
        "Path('aicad').mkdir(exist_ok=True)\n"
        "Path('aicad/__init__.py').write_text(body, encoding='utf-8')\n"
        "Path('aicad/sandbox_run.py').write_text(body, encoding='utf-8')\n"
        "part = Box(1, 1, 1)\n"
    )
    first = run_part_script(script, project)
    assert measure(first).volume_mm3 == pytest.approx(1)
    assert (project / "json.py").is_file()
    assert (project / "aicad" / "sandbox_run.py").is_file()
    _assert_canary_absent(outside)
    second = run_part_script("part = Box(2, 2, 2)\n", project)
    assert measure(second).volume_mm3 == pytest.approx(8)
    _assert_canary_absent(outside)
    assert not (project / "module_ran.txt").exists()


def test_same_run_import_does_not_load_a_module_just_written(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    body = _plant_text(canary)
    script = (
        "from pathlib import Path\n"
        f"Path('json.py').write_text({body!r}, encoding='utf-8')\n"
        "try:\n"
        "    import json\n"
        "except Exception:\n"
        "    pass\n"
        "part = Box(1, 1, 1)\n"
    )
    shape = run_part_script(script, project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    assert (project / "json.py").is_file()
    _assert_canary_absent(outside)
    assert not (project / "module_ran.txt").exists()


def test_replaced_script_file_is_not_run_on_the_next_build(tmp_path, config_home):
    _sandbox_or_skip()
    project, outside = _project(tmp_path)
    canary = _canary(outside)
    body = _plant_text(canary)
    script = (
        "from pathlib import Path\n"
        f"Path('script.py').write_text({body!r}, encoding='utf-8')\n"
        f"Path('part.py').write_text({body!r}, encoding='utf-8')\n"
        "part = Box(1, 1, 1)\n"
    )
    first = run_part_script(script, project)
    assert measure(first).volume_mm3 == pytest.approx(1)
    second = run_part_script("part = Box(2, 2, 2)\n", project)
    assert measure(second).volume_mm3 == pytest.approx(8)
    _assert_canary_absent(outside)
    assert not (project / "module_ran.txt").exists()
