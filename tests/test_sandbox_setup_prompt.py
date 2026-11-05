"""Nobody on Windows meets the one-time sandbox setup by surprise.

The installer offers it, `--status` reports it, and a blocked run says what to do in
the app and over MCP. Nothing here touches this computer's firewall.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aicad import sandbox, sandbox_setup_windows as setup, sandbox_windows
from aicad.app import make_server
from aicad.cli import build_parser
from aicad.errors import AICADError, SandboxNotReady, ScriptError
from aicad.server import build_server

ROOT = Path(__file__).resolve().parents[1]
SID = "S-1-15-2-2927816343-110413436-3480298780-3592348200-2632021430-1141120307-2823852601"
MESSAGE = sandbox_windows._SETUP_NEEDED


def _load_install():
    spec = importlib.util.spec_from_file_location("aicad_install_sandbox", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def install(monkeypatch, tmp_path):
    module = _load_install()
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(module, "_venv_python", lambda: python)
    monkeypatch.setattr(module, "_ensure_venv", lambda: python)
    monkeypatch.setattr(module, "_pip_install", lambda _python: None)
    monkeypatch.setattr(module, "_install_linux_packages", lambda: True)
    monkeypatch.setattr(module, "_missing_prerequisites", lambda build=True: [])
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    module.ran = []
    module.answers = {"--status": 1, "sandbox-setup": 0}

    def run(command, **_kwargs):
        module.ran.append(list(command))
        key = "--status" if "--status" in command else "sandbox-setup"
        return subprocess.CompletedProcess(command, module.answers[key], stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    return module


class _Keyboard:
    def isatty(self) -> bool:
        return True


def _at_the_keyboard(monkeypatch, module, answer) -> None:
    monkeypatch.setattr(module.sys, "stdin", _Keyboard())

    def typed(_prompt: str) -> str:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", typed)


def _setup_runs(module) -> list:
    return [command for command in module.ran if "--status" not in command]


@pytest.mark.parametrize("typed", ["y", "Y", "yes", " YES "])
def test_yes_runs_the_setup(install, monkeypatch, capsys, typed):
    _at_the_keyboard(monkeypatch, install, typed)
    assert install.main(["--no-system"]) == 0
    assert [command[1:] for command in _setup_runs(install)] == [["sandbox-setup"]]
    out = capsys.readouterr().out
    assert "one administrator approval" in out
    assert "To set it up later" not in out


@pytest.mark.parametrize("typed", ["", "n", "no", "maybe"])
def test_anything_but_yes_is_no_and_names_the_command(install, monkeypatch, capsys, typed):
    _at_the_keyboard(monkeypatch, install, typed)
    assert install.main(["--no-system"]) == 0
    assert install.ran == []
    out = capsys.readouterr().out
    assert "sandbox-setup" in out
    assert "Shape, sketch, gear, and catalog parts still work." in out


@pytest.mark.parametrize("interrupted", [EOFError(), KeyboardInterrupt()])
def test_an_interrupted_prompt_is_no(install, monkeypatch, capsys, interrupted):
    _at_the_keyboard(monkeypatch, install, interrupted)
    assert install.main(["--no-system"]) == 0
    assert install.ran == []
    assert "To set it up later" in capsys.readouterr().out


def test_a_declined_windows_prompt_still_finishes_the_install(install, monkeypatch, capsys):
    # `aicad sandbox-setup` exits 1 when the person declines the UAC prompt.
    install.answers["sandbox-setup"] = 1
    _at_the_keyboard(monkeypatch, install, "y")
    assert install.main(["--no-system"]) == 0
    out = capsys.readouterr().out
    assert "aicad is installed" in out
    assert "To set it up later" in out


def test_a_setup_that_cannot_start_still_finishes_the_install(install, monkeypatch, capsys):
    def missing(command, **_kwargs):
        raise OSError("aicad.exe is missing")

    monkeypatch.setattr(install.subprocess, "run", missing)
    assert install.main(["--no-system", "--sandbox-setup"]) == 0
    out = capsys.readouterr().out
    assert "aicad.exe is missing" in out
    assert "To set it up later" in out


def test_a_slow_status_check_still_finishes_the_install(install, monkeypatch, capsys):
    def slow(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 180)

    monkeypatch.setattr(install.subprocess, "run", slow)
    assert install.main(["--no-system", "--sandbox-setup"]) == 0
    assert "To set it up later" in capsys.readouterr().out


def test_the_flag_runs_it_without_asking(install, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    assert install.main(["--no-system", "--sandbox-setup"]) == 0
    assert len(_setup_runs(install)) == 1


def test_the_no_flag_never_asks_or_runs_it(install, monkeypatch, capsys):
    _at_the_keyboard(monkeypatch, install, "y")
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    assert install.main(["--no-system", "--no-sandbox-setup"]) == 0
    assert install.ran == []
    assert "To set it up later" in capsys.readouterr().out


def test_the_two_flags_cannot_be_combined(install):
    with pytest.raises(SystemExit):
        install.main(["--sandbox-setup", "--no-sandbox-setup"])


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
def test_ci_never_blocks_on_the_prompt(install, monkeypatch, variable):
    _at_the_keyboard(monkeypatch, install, "y")
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    monkeypatch.setenv(variable, "1")
    assert install.main(["--no-system"]) == 0
    assert install.ran == []


def test_no_keyboard_never_blocks_on_the_prompt(install, monkeypatch):
    class Piped:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(install.sys, "stdin", Piped())
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("asked"))
    assert install.main(["--no-system"]) == 0
    assert install.ran == []


def test_a_computer_already_set_up_is_not_asked_to_approve_again(install, capsys):
    install.answers["--status"] = 0
    assert install.main(["--no-system", "--sandbox-setup"]) == 0
    assert _setup_runs(install) == []
    assert "already set up" in capsys.readouterr().out


def test_other_systems_are_not_offered_this(install, monkeypatch, capsys):
    monkeypatch.setattr(install.sys, "platform", "darwin")
    assert install.main(["--no-system", "--sandbox-setup"]) == 0
    assert install.ran == []
    assert "administrator approval" not in capsys.readouterr().out


def test_install_bat_passes_the_flags_along():
    assert 'install.py" %*' in (ROOT / "install.bat").read_text(encoding="utf-8")


def _status_on_windows(monkeypatch, *, filters, rules):
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup, "package_sid", lambda: SID)
    monkeypatch.setattr(setup, "readable_filters", lambda: filters)

    # The read-back names a rule by its display name, which is how the firewall's COM interface lists it.
    displays = [display for name, display, _direction in setup.FIREWALL_RULES if name in rules]

    def read(script):
        passed = any(f"'{display}'" in script for display in displays)
        return subprocess.CompletedProcess([], 0 if passed else 2, stdout="", stderr="")

    monkeypatch.setattr(setup, "_run_powershell", read)


ALL_FILTERS = [name for name, _layer in setup.BLOCK_LAYERS]
ALL_RULES = [rule[0] for rule in setup.FIREWALL_RULES]


def test_status_when_ready(monkeypatch, capsys):
    _status_on_windows(monkeypatch, filters=ALL_FILTERS, rules=ALL_RULES)
    assert setup.run_status() == 0
    out = capsys.readouterr().out
    assert "is set up" in out
    assert "aicadscript" in out
    assert "8 of 8" in out
    assert "2 of 2" in out
    assert "missing:" not in out


def test_status_when_nothing_is_there(monkeypatch, capsys):
    _status_on_windows(monkeypatch, filters=[], rules=[])
    assert setup.run_status() == 1
    out = capsys.readouterr().out
    assert "is not set up" in out
    assert "0 of 8" in out
    assert "0 of 2" in out
    assert "aicad sandbox-setup" in out
    for name in ALL_FILTERS + ALL_RULES:
        assert f"missing: {name}" in out


def test_status_names_only_what_is_missing(monkeypatch, capsys):
    _status_on_windows(monkeypatch, filters=ALL_FILTERS[:-1], rules=ALL_RULES[:1])
    assert setup.run_status() == 1
    out = capsys.readouterr().out
    assert f"missing: {ALL_FILTERS[-1]}" in out
    assert f"missing: {ALL_FILTERS[0]}" not in out
    assert f"missing: {ALL_RULES[1]}" in out
    assert f"missing: {ALL_RULES[0]}" not in out


def test_a_read_that_fails_is_not_ready_and_says_so(monkeypatch, capsys):
    def refused():
        raise OSError("The filtering engine did not open.")

    _status_on_windows(monkeypatch, filters=[], rules=ALL_RULES)
    monkeypatch.setattr(setup, "readable_filters", refused)
    assert setup.run_status() == 1
    assert "The filtering engine did not open." in capsys.readouterr().out


def test_status_changes_nothing(monkeypatch):
    _status_on_windows(monkeypatch, filters=[], rules=[])
    scripts = []
    real = setup._run_powershell

    def watched(script):
        scripts.append(script)
        return real(script)

    monkeypatch.setattr(setup, "_run_powershell", watched)
    monkeypatch.setattr(setup, "_install", lambda: pytest.fail("installed"))
    monkeypatch.setattr(setup, "_remove", lambda: pytest.fail("removed"))
    monkeypatch.setattr(setup, "_run_with_uac_prompt", lambda _action: pytest.fail("asked to elevate"))
    setup.run_status()
    assert scripts
    for script in scripts:
        assert "New-NetFirewallRule" not in script
        assert "Remove-NetFirewallRule" not in script


def test_status_needs_nothing_on_other_systems(monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "platform", "linux")
    assert setup.run_status() == 0
    assert "Only Windows" in capsys.readouterr().out


def test_status_and_remove_cannot_be_combined():
    parser = build_parser()
    assert parser.parse_args(["sandbox-setup", "--status"]).status is True
    with pytest.raises(SystemExit):
        parser.parse_args(["sandbox-setup", "--status", "--remove"])


def test_the_message_says_what_to_do_and_why():
    assert "`aicad sandbox-setup`" in MESSAGE
    assert "administrator" in MESSAGE
    assert "once" in MESSAGE
    assert "off the network" in MESSAGE
    assert "solves" in MESSAGE
    assert "Shape, sketch, gear, and catalog parts still work." in MESSAGE


def test_a_blocked_run_is_an_error_both_kinds_of_caller_handle():
    error = SandboxNotReady(MESSAGE)
    assert isinstance(error, ScriptError)
    assert isinstance(error, AICADError)
    # A caller that falls back when a helper cannot start still falls back.
    assert isinstance(error, OSError)
    assert str(error) == MESSAGE


def test_a_blocked_solve_reaches_the_person_with_the_message(tmp_path, monkeypatch):
    def refused(*_args, **_kwargs):
        raise SandboxNotReady(MESSAGE)

    monkeypatch.setattr("aicad.sandbox_windows.spawn", refused)
    with pytest.raises(SandboxNotReady) as caught:
        sandbox._run_confined_helper_windows(["ccx"], env={}, timeout=1, cwd=tmp_path, reads=[])
    assert str(caught.value) == MESSAGE


@pytest.fixture
def blocked(monkeypatch):
    monkeypatch.setattr(sandbox, "sandbox_supported", lambda: (False, MESSAGE))
    monkeypatch.setattr(sandbox, "windows_setup_needed", lambda: MESSAGE)


def _server(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AICAD_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("AICAD_SERVER_TOKENS", str(tmp_path / "tokens"))
    return make_server(tmp_path / "project", "127.0.0.1", 0)


def _request(server, method, path, body=None):
    import threading

    threading.Thread(target=server.handle_request, daemon=True).start()
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=30)
    headers = {"X-Aicad-Token": server.page_token, "Origin": f"http://127.0.0.1:{port}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def test_the_page_is_told_before_anyone_runs_anything(tmp_path, monkeypatch, blocked):
    server = _server(tmp_path, monkeypatch)
    try:
        status, payload = _request(server, "GET", "/api/state")
    finally:
        server.server_close()
    assert status == 200
    assert payload["result"]["design_scripts"]["setup_needed"] == MESSAGE


def test_the_page_shows_that_message_with_no_button():
    script = (ROOT / "src" / "aicad" / "app_static" / "app.js").read_text(encoding="utf-8")
    branch = script.split("if (scripts.setup_needed) {", 1)[1].split("if (!scripts.enabled)", 1)[0]
    assert "text.textContent = scripts.setup_needed" in branch
    assert "button" not in branch.replace("no button", "")
    assert "return;" in branch


def test_a_run_from_the_page_gets_the_whole_message(tmp_path, monkeypatch, blocked):
    server = _server(tmp_path, monkeypatch)
    try:
        sandbox.allow_design_scripts(server.workspace.root)
        status, payload = _request(
            server,
            "POST",
            "/api/command",
            {"name": "run_design_script", "arguments": {"script": "print('no')\n"}},
        )
    finally:
        server.server_close()
    assert status == 400
    assert MESSAGE in payload["error"]


def _call_tool(project, name, arguments):
    async def call():
        return await build_server(project).call_tool(name, arguments)

    return asyncio.run(call())


def test_an_assistant_gets_the_message_word_for_word(tmp_path, monkeypatch, blocked):
    from aicad.server import ToolError

    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    sandbox.allow_design_scripts(project)
    with pytest.raises(ToolError) as caught:
        _call_tool(project, "run_design_script", {"script": "print('no')\n"})
    assert MESSAGE in str(caught.value)


def test_an_assistant_can_see_it_before_trying(tmp_path, monkeypatch, blocked):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    project = tmp_path / "project"
    project.mkdir()
    result = _call_tool(project, "design_script_status", {})
    text = json.dumps(result if isinstance(result, (dict, list)) else str(result), default=str)
    # JSON escapes the backticks' neighbours differently by client, so check the plain words.
    assert "cannot_run" in text
    assert "aicad sandbox-setup" in text
    assert "setup_needed" in text


def test_other_systems_report_no_setup_needed(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    assert sandbox.windows_setup_needed() is None


def test_scripted_ci_install_uses_the_flag():
    workflow = (ROOT / ".github" / "workflows" / "pytest-suite.yml").read_text(encoding="utf-8")
    assert "python install.py --no-sandbox-setup" in workflow


def test_the_docs_cover_what_a_person_needs():
    for name in ("README.md", "START_HERE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        section = text.split("### Windows sandbox setup", 1)[1].split("\n### ", 1)[0]
        assert "aicad sandbox-setup" in section
        assert "--status" in section
        assert "--remove" in section
        assert "--no-sandbox-setup" in section
        assert "If you skip it" in section
