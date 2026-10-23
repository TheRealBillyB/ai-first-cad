"""Supported starts keep a project folder off the import path.

The canary is SANDBOX_CANARY.txt containing CANARY_HIT, written outside the
project. A folder named aicad in that project must not run when the program
is started with the aicad command, with python -P, with the MCP server, or
from a sandbox child. Do not run python -m aicad from a project folder.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_CANARY_TEXT = "CANARY_HIT\n"


def _outside(tmp_path: Path) -> Path:
    return tmp_path / "SANDBOX_CANARY.txt"


def _canary_source(outside: Path) -> str:
    return (
        "from pathlib import Path\n"
        f"Path({str(outside)!r}).write_text({_CANARY_TEXT!r}, encoding='utf-8')\n"
    )


def _project_with_package(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "session.json").write_text('{"units": "mm", "parts": []}\n', encoding="utf-8")
    outside = _outside(tmp_path)
    package = project / "aicad"
    package.mkdir()
    (package / "__init__.py").write_text(_canary_source(outside), encoding="utf-8")
    return project, outside


def _assert_canary_absent(outside: Path) -> None:
    assert not outside.exists()
    assert not outside.with_name("SHADOW_RUNNER.txt").exists()


def _console_script() -> Path:
    script = Path(sys.executable).with_name("aicad")
    if script.is_file() and os.access(script, os.X_OK):
        return script
    found = shutil.which("aicad")
    if found:
        return Path(found)
    pytest.skip("aicad console script is not installed")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONSAFEPATH", None)
    env.pop("PYTHONPATH", None)
    return env


def _load_install():
    spec = importlib.util.spec_from_file_location("aicad_install_script", ROOT / "install.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not read install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_console_command_does_not_import_a_project_package(tmp_path):
    project, outside = _project_with_package(tmp_path)
    completed = subprocess.run(
        [str(_console_script()), "--help"],
        cwd=project,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    _assert_canary_absent(outside)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage: aicad")
    assert "CANARY_HIT" not in completed.stdout
    assert "CANARY_HIT" not in completed.stderr


def test_safe_path_module_mode_does_not_import_a_project_package(tmp_path):
    project, outside = _project_with_package(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-P", "-m", "aicad", "--help"],
        cwd=project,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    _assert_canary_absent(outside)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage: aicad")


def test_mcp_server_command_does_not_import_a_project_package(tmp_path):
    import asyncio

    project, outside = _project_with_package(tmp_path)
    asyncio.run(_mcp_initialize(str(_console_script()), ["serve", "--workspace", str(project)], project))
    _assert_canary_absent(outside)


def test_safe_path_mcp_server_does_not_import_a_project_package(tmp_path):
    import asyncio

    project, outside = _project_with_package(tmp_path)
    asyncio.run(
        _mcp_initialize(
            sys.executable,
            ["-P", "-m", "aicad", "serve", "--workspace", str(project)],
            project,
        )
    )
    _assert_canary_absent(outside)


async def _mcp_initialize(command: str, args: list[str], project: Path) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=command, args=args, cwd=str(project), env=_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert "inspect_part" in names


def test_sandbox_child_command_names_the_installed_runner(tmp_path):
    from aicad.sandbox import _child_command, _sandbox_runner

    job = tmp_path / "job"
    command = _child_command(job)
    runner = _sandbox_runner()
    assert command[0] == sys.executable
    assert "-P" in command
    assert "-m" not in command
    assert str(runner) in command
    assert str(job) in command
    assert runner.name == "sandbox_run.py"
    assert runner.is_file()


def test_sandbox_run_does_not_import_a_project_package(tmp_path, monkeypatch):
    from aicad.kernel import measure
    from aicad.sandbox import run_part_script, sandbox_supported

    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    project, outside = _project_with_package(tmp_path)
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    shape = run_part_script("part = Box(1, 1, 1)\n", project)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    _assert_canary_absent(outside)
    assert not (project / "SHADOW_RUNNER.txt").exists()


def test_windows_child_boot_loads_the_runner_file(tmp_path):
    from aicad.sandbox import _WINDOWS_CHILD_BOOT, _child_env, _sandbox_runner, sandbox_supported

    supported, reason = sandbox_supported()
    if not supported:
        pytest.skip(reason)
    project, outside = _project_with_package(tmp_path)
    job = project / ".aicad" / "jobs" / "boot"
    job.mkdir(parents=True)
    (project / ".aicad" / "tmp").mkdir(parents=True, exist_ok=True)
    (job / "job.json").write_text(json.dumps({"kind": "part", "network": False}) + "\n", encoding="utf-8")
    (job / "script.py").write_text("part = Box(1, 1, 1)\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-P", "-B", "-u", "-c", _WINDOWS_CHILD_BOOT, str(job), str(_sandbox_runner())],
        cwd=project,
        env=_child_env(project, job, False),
        capture_output=True,
        text=True,
        check=False,
    )
    _assert_canary_absent(outside)
    payload = json.loads((job / "result.json").read_text(encoding="utf-8"))
    assert payload.get("ok") is True, completed.stderr or payload


def test_windows_probe_does_not_import_a_project_package(tmp_path):
    from aicad.sandbox_windows import _probe_command

    project = tmp_path / "project"
    project.mkdir()
    outside = _outside(tmp_path)
    command = _probe_command(project)
    package = project / "aicad"
    package.mkdir()
    (package / "__init__.py").write_text(_canary_source(outside), encoding="utf-8")
    assert command[0] == sys.executable
    assert command[1] == "-P"
    script = Path(command[-1])
    assert script.is_absolute()
    assert script.is_file()
    source = script.read_text(encoding="utf-8")
    assert "import aicad" in source
    assert "identity_failures" in source
    completed = subprocess.run(
        command,
        cwd=project,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    _assert_canary_absent(outside)
    # This process is not in the container. The probe must not treat that as success.
    detail = completed.stdout + completed.stderr
    assert "sandbox-ok" not in completed.stdout
    assert completed.returncode != 0
    assert "confinement:" in detail or "identity_failures" in detail


def test_install_check_imports_the_environment_copy(tmp_path, monkeypatch):
    project, outside = _project_with_package(tmp_path)
    install = _load_install()
    hints = install._hints()
    monkeypatch.setattr(install, "ROOT", project)
    monkeypatch.setattr(install, "_venv_python", lambda: Path(sys.executable))
    monkeypatch.setenv("PYTHONPATH", str(project))
    code = install._check(hints, require_ccx=False)
    _assert_canary_absent(outside)
    assert code == 0


def test_install_message_names_the_console_command():
    install = _load_install()
    text = install._ready_message(Path("/tmp/example/.venv/bin/python"))
    assert "`aicad --help`" in text
    assert "python -P -m aicad" in text
    assert "Do not run `python -m aicad` from a project folder." in text
    if sys.platform == "win32":
        assert text.count("aicad.exe") >= 1
    else:
        assert "/tmp/example/.venv/bin/aicad" in text
