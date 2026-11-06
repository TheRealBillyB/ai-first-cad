"""Run real design scripts as a standard account, with a current Python and aicad's real start.

CI only. The runner's administrator has already run `aicad sandbox-setup` with
the Python this script runs under. That Python is a current release, the kind
a person installs today, with aicad installed into it. A new standard account
gets its own copy of it, which it owns as a person owns a per-user install,
and runs two design scripts through the real sandbox. aicad makes a private temporary folder as it
loads in the container. The second run reuses the project, as a person's
second click does.

Python 3.11.10 and later make ``os.mkdir(path, 0o700)`` an owner-only folder
on Windows. The script container cannot create one, and every run once spun
until its CPU limit. The other account check imports only the container code,
and the test jobs use Python 3.11.9, so neither could see that.

Every step prints as it starts, with the time. While the account works, a
line every minute shows the newest thing it printed. A step that does not
finish prints everything the account wrote, including a stack dump the account
writes shortly before its own limit.
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("check_second_user_block", HERE / "check_second_user_block.py")
accounts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(accounts)

ACCOUNT = "aicadscripts1"
COPY_SECONDS = 420
SCRIPTS_SECONDS = 840
# The account dumps its stacks this long before the wait above gives up.
STACK_DUMP_MARGIN = 90
RUN_SECONDS_LIMIT = 300
STARTED = time.monotonic()

AS_THE_ACCOUNT = r"""import faulthandler, sys, time
began = time.monotonic()
def say(text):
    print(f"[{time.monotonic() - began:6.1f}s] {text}", flush=True)
# Shortly before the parent stops waiting, write where every thread is.
faulthandler.dump_traceback_later(float(sys.argv[1]), repeat=True, file=sys.stderr)
say(f"python {sys.version.split()[0]} at {sys.prefix}")
import ctypes, json, os, tempfile
from pathlib import Path
home = Path(tempfile.mkdtemp(prefix="aicad-account-", dir=Path.home()))
os.environ["AICAD_CONFIG"] = str(home / "config")
os.environ["AICAD_RECENT"] = str(home / "recent.json")
os.environ["AICAD_SERVER_TOKENS"] = str(home / "tokens")
report = {"is_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()), "python": list(sys.version_info[:3]), "runs": []}
say("importing aicad")
from aicad.sandbox import allow_design_scripts
from aicad.workspace import Workspace
project = home / "project"
say(f"opening the project {project}")
workspace = Workspace(project)
allow_design_scripts(project)
script = '''print("private folders work")
'''
for attempt in (1, 2):
    say(f"design script run {attempt} started")
    start = time.monotonic()
    try:
        result = workspace.execute("run_design_script", {"script": script}, "ui")
        report["runs"].append({"ok": True, "seconds": round(time.monotonic() - start, 1), "stdout": str(result.get("stdout", ""))[:200]})
    except Exception as exc:
        report["runs"].append({"ok": False, "seconds": round(time.monotonic() - start, 1), "error": f"{type(exc).__name__}: {exc}"[:6000]})
    say(f"design script run {attempt} finished: {report['runs'][-1]}")
faulthandler.cancel_dump_traceback_later()
print(json.dumps(report), flush=True)
"""


def step(text: str) -> None:
    print(f"[{time.monotonic() - STARTED:6.1f}s] {text}", flush=True)


def tail(path: Path, size: int) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[-size:] if path.exists() else "(nothing yet)"


def as_account(account: str, password: str, line: str, label: str, seconds: int) -> tuple[str, str, bool]:
    """Run one command line as the account. Returns stdout, stderr, and whether it finished in time."""
    out, err, finished = (accounts.WORK / "out" / f"{label}.{suffix}" for suffix in ("txt", "err", "done"))
    batch = accounts.WORK / "out" / f"{label}.bat"
    batch.write_text(f'@echo off\r\n{line} > "{out}" 2> "{err}"\r\necho x > "{finished}"\r\n', encoding="ascii")
    task = f"aicad-{label}"
    accounts.run(
        "schtasks", "/create", "/tn", task, "/tr", f'cmd /c "{batch}"', "/sc", "once", "/st", "23:59",
        "/f", "/ru", account, "/rp", password, "/rl", "LIMITED",
    )
    accounts.run("schtasks", "/run", "/tn", task)
    started = time.monotonic()
    heartbeat = started
    while not finished.exists() and time.monotonic() - started < seconds:
        time.sleep(1)
        if time.monotonic() - heartbeat >= 60:
            heartbeat = time.monotonic()
            newest = [line for line in tail(out, 4000).splitlines() if line.strip()]
            step(f"{label}: still running after {heartbeat - started:.0f} s. Newest line: {newest[-1][:200] if newest else '(none)'}")
    done = finished.exists()
    if not done:
        query = subprocess.run(["schtasks", "/query", "/tn", task, "/v", "/fo", "list"], capture_output=True, text=True)
        status = [line.strip() for line in query.stdout.splitlines() if line.strip().startswith(("Status", "Last Result"))]
        step(f"{label}: did not finish in {seconds} s. Task {status}")
        accounts.run("schtasks", "/end", "/tn", task, check=False)
    accounts.run("schtasks", "/delete", "/tn", task, "/f", check=False)
    return tail(out, 6000), tail(err, 6000), done


def copy_python(copied: str) -> str | None:
    """Copy the real install folder for the account and make it the owner. None when it worked.

    The account could not read the packages uv installed, so this runner's
    administrator copies them. The account then owns its copy, as a person owns
    a per-user install, which lets it grant its own script container. The copy
    is outside the account's profile: a profile folder made before the first
    logon would send Windows to another profile name.
    """
    # uv names its Python through a junction, so this copies the real folder.
    source = Path(sys.base_prefix).resolve()
    # A file that cannot be read is tried twice, a second apart, then reported.
    done = subprocess.run(
        ["robocopy", str(source), copied, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NP"],
        capture_output=True, text=True, timeout=COPY_SECONDS,
    )
    print(done.stdout[-1500:], flush=True)
    # robocopy uses 0 to 7 for a copy that worked, and 8 or more when a file failed.
    if done.returncode >= 8 or not Path(copied, "python.exe").exists():
        return f"the copy failed (robocopy exit {done.returncode}). {done.stdout[-1500:]} {done.stderr[-500:]}"
    step(f"making {ACCOUNT} the owner of its copy")
    accounts.run("icacls", copied, "/setowner", ACCOUNT, "/T", "/C", "/Q")
    accounts.run("icacls", copied, "/grant", f"{ACCOUNT}:(OI)(CI)F", "/T", "/C", "/Q")
    return None


def check(password: str) -> list[str]:
    copied = r"C:\aicad-account-python"
    step(f"copying {Path(sys.base_prefix).resolve()} to {copied} for {ACCOUNT}")
    problem = copy_python(copied)
    if problem:
        return [problem]
    step("running two design scripts as the account")
    line = f'"{copied}\\python.exe" -I -u "{accounts.WORK / "run_scripts.py"}" {SCRIPTS_SECONDS - STACK_DUMP_MARGIN}'
    text, stderr, done = as_account(ACCOUNT, password, line, f"{ACCOUNT}-scripts", SCRIPTS_SECONDS)
    print(text, flush=True)
    if not done:
        return [f"the scripts did not finish in {SCRIPTS_SECONDS} s. What the account printed is above. stderr, with its stack dump:\n{stderr}"]
    try:
        report = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"the account gave no report. stderr: {stderr}"]
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
    return problems


def main() -> int:
    work = accounts.WORK
    (work / "out").mkdir(parents=True, exist_ok=True)
    (work / "run_scripts.py").write_text(AS_THE_ACCOUNT, encoding="utf-8")
    step(f"Python {sys.version.split()[0]} at {sys.base_prefix}, which is {Path(sys.base_prefix).resolve()}")
    step("letting standard accounts read the work folder")
    accounts.run("icacls", str(work), "/grant", "Users:(OI)(CI)RX", "/T", "/Q")
    accounts.run("icacls", str(work / "out"), "/grant", "Users:(OI)(CI)M", "/T", "/Q")
    step(f"creating the standard account {ACCOUNT}")
    password = "Aa1!" + secrets.token_urlsafe(18)
    accounts.run("net", "user", ACCOUNT, password, "/add", "/y")
    # The batch logon right only. This group is not an administrator.
    accounts.run("net", "localgroup", "Performance Log Users", ACCOUNT, "/add")
    problems = check(password)
    step("done")
    for problem in problems:
        print(f"::error::{accounts.annotation(problem)}", flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        code = main()
    except (Exception, SystemExit) as exc:
        print(f"::error title=Account script check::{accounts.annotation(repr(exc))}", flush=True)
        code = 1
    raise SystemExit(code)
