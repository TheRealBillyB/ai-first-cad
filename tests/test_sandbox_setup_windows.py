"""The one-time Windows setup: what it may import, what it writes, and what it says.

Tests here do not touch this computer's firewall. The real install and removal
run in tests/test_sandbox_setup_real.py, on a disposable CI runner.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from aicad import sandbox_setup_windows as setup
from aicad import sandbox_windows
from aicad.cli import build_parser
from aicad.parity import CLI_EXEMPT
from aicad.server import build_server

SID = "S-1-15-2-2927816343-110413436-3480298780-3592348200-2632021430-1141120307-2823852601"
SOURCE = Path(setup.__file__)
STANDARD_LIBRARY = {"__future__", "ctypes", "re", "subprocess", "sys", "uuid", "pathlib"}


def test_the_elevated_file_imports_only_the_standard_library():
    imported = set()
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add((node.module or "").split(".")[0])
    assert imported <= STANDARD_LIBRARY


def test_the_file_starts_isolated_and_refuses_anything_but_the_two_actions():
    # No argument stops before the administrator check, so nothing is changed on any computer.
    done = subprocess.run([sys.executable, "-I", "-S", str(SOURCE)], capture_output=True, text=True, check=False)
    assert done.returncode == setup.EXIT_USAGE, done.stderr
    assert "install or remove" in done.stderr


@pytest.mark.parametrize("argv", [[], ["other"], ["install", "extra"], ["install", "--sid=S-1-1-0"], ["C:\\project"]])
def test_the_elevated_step_takes_no_path_and_no_id(argv, capsys):
    assert setup._elevated_main(argv) == setup.EXIT_USAGE
    assert "install or remove" in capsys.readouterr().err


def test_the_elevated_step_stops_without_administrator_rights(monkeypatch, capsys):
    monkeypatch.setattr(setup, "is_admin", lambda: False)
    monkeypatch.setattr(setup, "_install", lambda: pytest.fail("the block was changed"))
    assert setup._elevated_main(["install"]) == setup.EXIT_NOT_ADMIN
    assert "administrator" in capsys.readouterr().err


def test_a_failure_in_the_elevated_step_is_its_own_exit_code(monkeypatch, capsys):
    def fails():
        raise OSError("Adding the sublayer failed.")

    monkeypatch.setattr(setup, "is_admin", lambda: True)
    monkeypatch.setattr(setup, "_install", fails)
    assert setup._elevated_main(["install"]) == setup.EXIT_FAILED
    assert "sublayer" in capsys.readouterr().err


def test_the_block_names_match_what_a_run_uses_today():
    assert setup.PROFILE_NAME == sandbox_windows._PROFILE_NAME
    assert tuple(rule[0] for rule in setup.FIREWALL_RULES) == sandbox_windows._NETWORK_OFF_RULES
    assert setup._STALE_FIREWALL_RULES == sandbox_windows._NETWORK_OFF_STALE_RULES
    assert setup.BLOCK_LAYERS == sandbox_windows._WFP_BLOCK_LAYERS
    assert setup._ALE_PACKAGE_ID == sandbox_windows._WFP_ALE_PACKAGE_ID


def test_the_filter_layout_matches_the_windows_structure():
    assert ctypes_size(setup._Filter) == ctypes_size(sandbox_windows._WfpFilter)
    if sys.maxsize > 2**32:
        assert ctypes_size(setup._Filter) == 200


def ctypes_size(kind) -> int:
    import ctypes

    return ctypes.sizeof(kind)


def test_every_key_is_fixed_and_distinct():
    keys = [setup.SUBLAYER_KEY] + [setup.filter_key(name) for name, _layer in setup.BLOCK_LAYERS]
    assert len(set(keys)) == len(setup.BLOCK_LAYERS) + 1
    assert keys == [setup.SUBLAYER_KEY] + [setup.filter_key(name) for name, _layer in setup.BLOCK_LAYERS]


def test_the_rules_block_every_protocol_both_ways_for_one_package():
    script = setup.firewall_install_script(SID)
    assert script.count("New-NetFirewallRule") == 2
    assert script.count("-Action Block -Protocol Any") == 2
    assert script.count(f"-Package '{SID.upper()}'") == 2
    assert "-Direction Outbound" in script
    assert "-Direction Inbound" in script
    assert "Port" not in script


@pytest.mark.parametrize("sid", ["S-1-1-0", "S-1-15-2-1'; Remove-Item C:\\ #", "", "S-1-15-3-1"])
def test_only_a_package_sid_reaches_a_rule(sid):
    with pytest.raises(ValueError):
        setup.firewall_install_script(sid)


def test_a_signed_in_user_may_read_a_filter_and_nothing_more():
    assert setup.FILTER_DESCRIPTOR.endswith("(A;;0xC0;;;AU)")


def test_removal_clears_the_old_rule_name_too():
    script = setup.firewall_remove_script()
    assert "New-NetFirewallRule" not in script
    for name in sandbox_windows._NETWORK_OFF_RULES + sandbox_windows._NETWORK_OFF_STALE_RULES:
        assert name in script


def test_the_assistant_has_no_tool_for_this(tmp_path):
    assert "sandbox-setup" in CLI_EXEMPT
    names = {tool.name for tool in asyncio.run(build_server(tmp_path).list_tools())}
    assert not {name for name in names if "setup" in name or "elevat" in name}


def test_the_command_parses_with_and_without_remove():
    parser = build_parser()
    assert parser.parse_args(["sandbox-setup"]).remove is False
    assert parser.parse_args(["sandbox-setup", "--remove"]).remove is True


def test_another_system_has_nothing_to_set_up(monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "platform", "linux")
    assert setup.run_setup() == 0
    assert "Only Windows" in capsys.readouterr().out


def _windows(monkeypatch, *, admin):
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup, "is_admin", lambda: admin)
    monkeypatch.setattr(setup, "_system_directory", lambda: Path("."))


def test_an_account_without_rights_goes_through_the_prompt(monkeypatch):
    _windows(monkeypatch, admin=False)
    seen = []
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda action: seen.append(action) or 0)
    monkeypatch.setattr(setup, "is_installed", lambda: True)
    assert setup.run_setup() == 0
    assert seen == ["install"]


def test_an_administrator_runs_it_isolated_with_no_prompt(monkeypatch):
    _windows(monkeypatch, admin=True)
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda action: pytest.fail("prompted"))
    monkeypatch.setattr(setup, "filters_present", lambda: 0)
    assert setup.run_setup(remove=True) == 0
    assert calls[0][1:3] == ["-I", "-S"]
    assert calls[0][-1] == "remove"


def test_declining_the_prompt_changes_nothing_and_says_so(monkeypatch, capsys):
    _windows(monkeypatch, admin=False)

    def declined(_action):
        error = OSError("cancelled")
        error.winerror = setup._ERROR_CANCELLED
        raise error

    monkeypatch.setattr(setup, "_run_with_uac_prompt", declined)
    assert setup.run_setup() == 1
    assert "nothing changed" in capsys.readouterr().out


def test_a_success_this_account_cannot_confirm_is_not_a_success(monkeypatch, capsys):
    _windows(monkeypatch, admin=False)
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda _action: 0)
    monkeypatch.setattr(setup, "is_installed", lambda: False)
    assert setup.run_setup() == 1
    assert "cannot confirm" in capsys.readouterr().out


def test_a_failed_elevated_step_is_a_failed_command(monkeypatch, capsys):
    _windows(monkeypatch, admin=False)
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda _action: setup.EXIT_FAILED)
    assert setup.run_setup() == 1
    assert "failed" in capsys.readouterr().out


def test_a_removal_that_left_filters_is_reported(monkeypatch, capsys):
    _windows(monkeypatch, admin=False)
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda _action: 0)
    monkeypatch.setattr(setup, "filters_present", lambda: 3)
    assert setup.run_setup(remove=True) == 1
    assert "still in place" in capsys.readouterr().out


def test_a_failed_firewall_step_leaves_no_half_block(monkeypatch):
    removed = []
    monkeypatch.setattr(setup, "package_sid", lambda: SID)
    monkeypatch.setattr(setup, "_install_wfp", lambda _sid: None)
    monkeypatch.setattr(setup, "_remove_wfp", lambda: removed.append(True))
    monkeypatch.setattr(
        setup,
        "_run_powershell",
        lambda _script: subprocess.CompletedProcess([], 1, stdout="Access is denied.", stderr=""),
    )
    with pytest.raises(OSError, match="firewall rules"):
        setup._install()
    assert removed == [True]
