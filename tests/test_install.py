"""Install hints for CalculiX. The solver stays a separate program."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from aicad.prereqs import ccx_hint, ccx_status

ROOT = Path(__file__).resolve().parents[1]


def test_windows_hint_names_a_windows_install():
    text = ccx_hint("win32")
    assert "ccx" in text
    assert "conda" in text
    assert "CalculiX-Windows" in text
    assert "calculix-ccx" not in text


def test_mac_hint_names_homebrew():
    text = ccx_hint("darwin")
    assert "brew install costerwi/homebrew-calculix/calculix-ccx" in text
    assert "conda" in text


def test_linux_hint_names_apt():
    text = ccx_hint("linux")
    assert "calculix-ccx" in text
    assert "apt-get" in text
    assert "conda" in text


def test_missing_ccx_message_is_the_hint_for_this_machine(monkeypatch):
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: None)
    status = ccx_status()
    assert status.found is False
    assert status.path is None
    assert "ccx" in status.message
    assert ccx_hint() == status.message


def test_found_ccx_message_includes_the_path(monkeypatch):
    monkeypatch.setattr("aicad.prereqs.find_program", lambda _name, project=None: "/usr/bin/ccx")
    status = ccx_status()
    assert status.found is True
    assert status.path == "/usr/bin/ccx"
    assert "/usr/bin/ccx" in status.message


def _load_install():
    spec = importlib.util.spec_from_file_location("aicad_install_under_test", ROOT / "install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_old_virtualenv_is_not_reused(tmp_path, monkeypatch):
    module = _load_install()
    name = "python.exe" if sys.platform == "win32" else "python"
    fake = tmp_path / name
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "_venv_python", lambda: fake)

    def too_old(_command, **_kwargs):
        class Done:
            returncode = 1

        return Done()

    monkeypatch.setattr(module.subprocess, "run", too_old)
    with pytest.raises(SystemExit, match="older than Python 3.11"):
        module._ensure_venv()


def test_a_current_virtualenv_is_reused(tmp_path, monkeypatch):
    module = _load_install()
    name = "python.exe" if sys.platform == "win32" else "python"
    fake = tmp_path / name
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "_venv_python", lambda: fake)
    calls: list[list[str]] = []

    def current(command, **_kwargs):
        calls.append(list(command))

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(module.subprocess, "run", current)
    assert module._ensure_venv() == fake
    assert calls == [
        [
            str(fake),
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
        ]
    ]


def test_ccx_status_runs_when_the_package_is_not_imported():
    """install.py loads this file by path, then asks whether ccx is on PATH."""

    script = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "class Block:\n"
        "    def find_spec(self, fullname, path, target=None):\n"
        "        if fullname == 'aicad' or fullname.startswith('aicad.'):\n"
        "            raise ModuleNotFoundError(fullname)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "path = root / 'src' / 'aicad' / 'prereqs.py'\n"
        "spec = importlib.util.spec_from_file_location('aicad_prereqs', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "assert spec.loader is not None\n"
        "spec.loader.exec_module(module)\n"
        "status = module.ccx_status()\n"
        "if not status.message or 'ccx' not in status.message:\n"
        "    raise SystemExit(status.message)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_package_commands_stop_if_they_do_not_finish(monkeypatch):
    """A hung apt or pip command has to end. The installer used to wait forever."""

    module = _load_install()
    timeouts: list[float | None] = []

    def record(command, **kwargs):
        timeouts.append(kwargs.get("timeout"))

        class Done:
            returncode = 0
            stdout = "aicad 0.9.0\n"
            stderr = ""

        return Done()

    monkeypatch.setattr(module.subprocess, "run", record)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module, "_passwordless_sudo", lambda: True)
    python = module._venv_python()
    module._pip_install(python)
    module._install_linux_packages()
    module._check(module._hints(), require_ccx=False)
    assert timeouts
    assert all(timeout == module._INSTALL_TIMEOUT_SECONDS for timeout in timeouts)


def test_install_script_help_runs_without_installing():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "install.py"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "virtual environment" in completed.stdout
    assert "CalculiX" in completed.stdout
