"""Install hints for CalculiX. The solver stays a separate program."""

import subprocess
import sys
from pathlib import Path

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
