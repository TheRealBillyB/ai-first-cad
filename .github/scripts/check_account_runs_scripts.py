"""Run real design scripts as standard accounts, with a current Python and aicad's real start.

CI only. The runner's administrator has already run `aicad sandbox-setup` with
the Python this script runs under. That Python is a current release, the kind
a person installs today, with aicad installed into it. Each account gets its
own copy of it, as a per-user install has, and runs two design scripts through
the real sandbox. aicad makes a private temporary folder as it loads in the
container. The second run reuses the project, as a person's second click does.

Python 3.11.10 and later make ``os.mkdir(path, 0o700)`` an owner-only folder
on Windows. The script container cannot create one, and every run once spun
until its CPU limit. The other account check imports only the container code,
and the test jobs use Python 3.11.9, so neither could see that.
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("check_second_user_block", HERE / "check_second_user_block.py")
accounts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(accounts)

ACCOUNTS = ("aicadscripts1", "aicadscripts2")
# The first run grants the container read access to the whole Python copy.
accounts.TASK_SECONDS = 900
RUN_SECONDS_LIMIT = 300

AS_THE_ACCOUNT = r"""import ctypes, json, os, sys, tempfile, time
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="aicad-account-", dir=Path.home()))
os.environ["AICAD_CONFIG"] = str(home / "config")
os.environ["AICAD_RECENT"] = str(home / "recent.json")
os.environ["AICAD_SERVER_TOKENS"] = str(home / "tokens")
report = {"is_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()), "python": list(sys.version_info[:3]), "runs": []}
from aicad.sandbox import allow_design_scripts
from aicad.workspace import Workspace

project = home / "project"
workspace = Workspace(project)
allow_design_scripts(project)
script = '''print("private folders work")
'''
for _attempt in (1, 2):
    began = time.perf_counter()
    try:
        result = workspace.execute("run_design_script", {"script": script}, "ui")
        report["runs"].append({"ok": True, "seconds": round(time.perf_counter() - began, 1), "stdout": str(result.get("stdout", ""))[:200]})
    except Exception as exc:
        report["runs"].append({"ok": False, "seconds": round(time.perf_counter() - began, 1), "error": f"{type(exc).__name__}: {exc}"[:600]})
print(json.dumps(report))
"""


def check_account(account: str) -> list[str]:
    password = "Aa1!" + secrets.token_urlsafe(18)
    accounts.run("net", "user", account, password, "/add", "/y")
    accounts.run("net", "localgroup", "Performance Log Users", account, "/add")
    copied = rf"C:\Users\{account}\py"
    accounts.as_account(account, password, f'robocopy "{sys.base_prefix}" "{copied}" /E /NFL /NDL /NJH /NJS /NP', f"{account}-copy")
    line = f'"{copied}\\python.exe" -I "{accounts.WORK / "run_scripts.py"}"'
    text, stderr = accounts.as_account(account, password, line, f"{account}-scripts")
    print(f"::notice title={account}::" + accounts.annotation(text.strip()))
    try:
        report = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"{account}: the account gave no report. stdout: {text[-400:]} stderr: {stderr}"]
    problems = []
    if report["is_admin"]:
        problems.append("the account is an administrator")
    if tuple(report["python"]) < (3, 11, 10):
        problems.append(f"Python {report['python']} does not make owner-only folders, so this check proves nothing")
    if len(report["runs"]) != 2:
        problems.append(f"expected two runs: {report['runs']}")
    for index, run in enumerate(report["runs"], start=1):
        if not run["ok"]:
            problems.append(f"run {index} failed after {run['seconds']} s: {run['error']}")
        elif "private folders work" not in run["stdout"]:
            problems.append(f"run {index} printed {run['stdout']!r}")
        elif run["seconds"] > RUN_SECONDS_LIMIT:
            problems.append(f"run {index} took {run['seconds']} s")
    return [f"{account}: {problem}" for problem in problems]


def main() -> int:
    work = accounts.WORK
    (work / "out").mkdir(parents=True, exist_ok=True)
    (work / "run_scripts.py").write_text(AS_THE_ACCOUNT, encoding="utf-8")
    accounts.run("icacls", str(work), "/grant", "Users:(OI)(CI)RX", "/T", "/Q")
    accounts.run("icacls", str(work / "out"), "/grant", "Users:(OI)(CI)M", "/T", "/Q")
    # Each account copies this install, as the existing check copies its own Python.
    accounts.run("icacls", sys.base_prefix, "/grant", "Users:(OI)(CI)RX", "/T", "/Q")
    print(f"::notice title=Python::{sys.version.split()[0]} at {sys.base_prefix}")
    started = time.time()
    problems: list[str] = []
    for account in ACCOUNTS:
        problems += check_account(account)
    print(f"::notice title=Account script check::Checked {len(ACCOUNTS)} accounts in {time.time() - started:.0f} seconds.")
    for problem in problems:
        print(f"::error::{accounts.annotation(problem)}")
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        code = main()
    except (Exception, SystemExit) as exc:
        print(f"::error title=Account script check::{accounts.annotation(repr(exc))}")
        code = 1
    raise SystemExit(code)
