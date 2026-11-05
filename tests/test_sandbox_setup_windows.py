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


def test_a_failed_step_returns_the_exit_code_that_names_it(monkeypatch, capsys):
    def fails():
        raise setup._StepFailed(setup.EXIT_RULES, "Adding the firewall rules failed. Access is denied.")

    monkeypatch.setattr(setup, "is_admin", lambda: True)
    monkeypatch.setattr(setup, "_install", fails)
    assert setup._elevated_main(["install"]) == setup.EXIT_RULES
    assert "Access is denied" in capsys.readouterr().err


def test_the_block_names_the_profile_network_off_runs_use():
    assert setup.PROFILE_NAME == sandbox_windows._PROFILE_NAME
    assert setup.PROFILE_NAME != sandbox_windows._NETWORK_PROFILE_NAME
    assert [rule[0] for rule in setup.FIREWALL_RULES] == ["aicad-script-network-off-out", "aicad-script-network-off-in"]
    assert setup._STALE_FIREWALL_RULES == ("aicad-script-network-off-udp",)
    assert len(setup.BLOCK_LAYERS) == 8


def test_the_filter_layout_matches_the_windows_structure():
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
    for name in [rule[0] for rule in setup.FIREWALL_RULES] + list(setup._STALE_FIREWALL_RULES):
        assert name in script


def test_the_read_back_checks_the_rule_belongs_to_this_container():
    script = setup.firewall_read_script(SID)
    assert SID.upper() in script
    for _name, display, direction in setup.FIREWALL_RULES:
        assert f"'{display}' {setup._COM_DIRECTIONS[direction]} " in script
    assert "New-NetFirewallRule" not in script
    assert "Remove-NetFirewallRule" not in script
    with pytest.raises(ValueError):
        setup.firewall_read_script("S-1-15-2-1'; Remove-Item C:\\ #")


def test_the_read_back_needs_no_administrator_rights():
    """A standard account gets "Access is denied" from every NetSecurity cmdlet, so it was told the rules were missing."""
    script = setup.firewall_read_script(SID)
    assert "HNetCfg.FwPolicy2" in script
    assert "Get-NetFirewall" not in script


# What the firewall's COM interface reports for the rules the setup adds,
# read on Windows 10 from an account without administrator rights.
_REAL_RULE = {
    "Name": "aicad script network off outbound",
    "Enabled": "$true",
    "Action": "0",
    "Direction": "2",
    "Protocol": "256",
    "LocalPorts": "''",
    "RemotePorts": "''",
    "LocalAddresses": "'*'",
    "RemoteAddresses": "'*'",
    "Profiles": "2147483647",
    "InterfaceTypes": "'All'",
    "Interfaces": "$null",
    "ApplicationName": "''",
    "serviceName": "''",
    "LocalAppPackageId": f"'{SID}'",
}


def _judged(changes: dict) -> bool:
    """Run the script's own check on one stand-in rule. The real policy is not read."""
    script = setup.firewall_read_script(SID, (setup.FIREWALL_RULES[0],))
    functions = script[: script.index("try {")]
    rule = {**_REAL_RULE, **changes}
    fields = "; ".join(
        f"{key} = '{value}'" if key == "Name" else f"{key} = {value}" for key, value in rule.items()
    )
    probe = (
        functions
        + f"$rule = [pscustomobject]@{{ {fields} }}\n"
        + f"if (Test-AicadBlock @($rule) 'aicad script network off outbound' 2 '{SID.upper()}') {{ exit 0 }} else {{ exit 2 }}\n"
    )
    return setup._run_powershell(probe).returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
@pytest.mark.parametrize(
    ("changes", "blocks"),
    [
        ({}, True),
        ({"LocalPorts": "'*'", "RemotePorts": "'*'"}, True),
        ({"LocalAppPackageId": f"'{SID.lower()}'"}, True),
        ({"Enabled": "$false"}, False),
        ({"Action": "1"}, False),
        ({"Direction": "1"}, False),
        ({"Protocol": "6"}, False),
        ({"RemotePorts": "'53'"}, False),
        ({"RemoteAddresses": "'10.0.0.0/8'"}, False),
        ({"Profiles": "4"}, False),
        ({"InterfaceTypes": "'Wireless'"}, False),
        ({"Interfaces": "@('Ethernet')"}, False),
        ({"ApplicationName": "'C:\\\\other.exe'"}, False),
        ({"serviceName": "'dnscache'"}, False),
        ({"LocalAppPackageId": "'S-1-15-2-1-2-3'"}, False),
        ({"Name": "aicad script network off inbound"}, False),
    ],
)
def test_only_a_rule_that_blocks_everything_for_the_container_counts(changes, blocks):
    assert _judged(changes) is blocks


def test_rules_present_reads_for_the_real_container(monkeypatch):
    seen = []
    monkeypatch.setattr(setup, "package_sid", lambda: SID)

    def answer(script):
        seen.append(script)
        return subprocess.CompletedProcess([], 2)

    monkeypatch.setattr(setup, "_run_powershell", answer)
    assert setup.rules_present() is False
    assert SID.upper() in seen[0]


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
    assert "unexpected Windows error" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (5, "network filters"),
        (6, "firewall rules"),
        (7, "read it back"),
        (8, "remove the network filters"),
        (9, "remove the firewall rules"),
        (0xC0000022, "0xc0000022"),
    ],
)
def test_a_failed_elevated_step_says_why(monkeypatch, capsys, code, expected):
    _windows(monkeypatch, admin=False)
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda _action: code)
    assert setup.run_setup() == 1
    assert expected in capsys.readouterr().out


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
    with pytest.raises(OSError, match="firewall rules") as caught:
        setup._install()
    assert caught.value.code == setup.EXIT_RULES
    assert removed == [True]


def test_a_firewall_step_that_times_out_leaves_no_half_block(monkeypatch):
    removed = []

    def slow(script):
        if "New-NetFirewallRule" in script:
            raise subprocess.TimeoutExpired("powershell", 120)
        removed.append("rules")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(setup, "package_sid", lambda: SID)
    monkeypatch.setattr(setup, "_install_wfp", lambda _sid: None)
    monkeypatch.setattr(setup, "_remove_wfp", lambda: removed.append("filters"))
    monkeypatch.setattr(setup, "_run_powershell", slow)
    with pytest.raises(OSError) as caught:
        setup._install()
    assert caught.value.code == setup.EXIT_RULES
    assert removed == ["filters", "rules"]


def test_a_block_that_cannot_be_read_back_is_removed(monkeypatch):
    removed = []
    monkeypatch.setattr(setup, "package_sid", lambda: SID)
    monkeypatch.setattr(setup, "_install_wfp", lambda _sid: None)
    monkeypatch.setattr(setup, "_remove_wfp", lambda: removed.append(True))
    monkeypatch.setattr(setup, "_run_powershell", lambda _script: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    monkeypatch.setattr(setup, "is_installed", lambda: False)
    with pytest.raises(OSError) as caught:
        setup._install()
    assert caught.value.code == setup.EXIT_NOT_CONFIRMED
    assert removed == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SID conversion")
def test_a_failed_access_list_conversion_frees_the_sid(monkeypatch):
    real = setup.ctypes.WinDLL

    class Function:
        def __init__(self, answer):
            self.answer = answer

        def __call__(self, *args):
            if self.answer == "sid":
                # The real call fills this pointer in, so the test can see it freed.
                args[1]._obj.value = 1234
                return 1
            return 0

    class Advapi:
        ConvertStringSidToSidW = Function("sid")
        ConvertStringSecurityDescriptorToSecurityDescriptorW = Function("fail")

    freed = []
    monkeypatch.setattr(setup.ctypes, "WinDLL", lambda name, **kw: Advapi() if name == "advapi32" else real(name, **kw))
    monkeypatch.setattr(setup, "_free_local", lambda pointer: freed.append(pointer.value))
    with pytest.raises(OSError, match="access list"):
        setup._install_wfp(SID)
    assert 1234 in freed
