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

The account runs as a scheduled task, which Windows gives a service window
station. A person's sign-in gives every app package access to its window
station and desktop, and a container cannot load user32 without that. So the
account first gives its own station and desktop those same entries.

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
# A scheduled task gets a service window station, not the interactive WinSta0.
# Windows lets every app package (AC) use WinSta0 and its Default desktop, and
# user32, which _ctypes loads through ole32, cannot start in a container
# without that. Give this account's own station and desktop the same AC
# entries a person's sign-in has. aicad's container is unchanged.
#
# TEST HARNESS ONLY. Do not copy this into aicad. It mirrors what a desktop
# sign-in already grants, so this throwaway account sees what a person sees.
# It changes only the station and desktop this process runs on, opened by
# their own names, on a CI machine that is thrown away. aicad must never
# change a window station's or desktop's access list. With no signed-in
# desktop (Task Scheduler, a service, SSH), aicad's scripts do not start; that
# is a known 1.0 limit.
from ctypes import wintypes
user32 = ctypes.WinDLL("user32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
user32.GetProcessWindowStation.restype = wintypes.HANDLE
user32.GetThreadDesktop.restype = wintypes.HANDLE
user32.OpenWindowStationW.restype = wintypes.HANDLE
user32.OpenDesktopW.restype = wintypes.HANDLE
advapi32.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
advapi32.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
advapi32.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
def object_name(handle):
    name = ctypes.create_unicode_buffer(256)
    user32.GetUserObjectInformationW(handle, 2, name, 512, None)
    return name.value
def access_list(handle):
    dacl, sd, text = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.LPWSTR()
    error = advapi32.GetSecurityInfo(handle, 7, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if error:
        raise OSError(error, "GetSecurityInfo")
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 4, ctypes.byref(text), None)
    return text.value
def add_entries(handle, entries):
    before = access_list(handle)
    if entries in before:
        return before
    sd, present, dacl, defaulted = ctypes.c_void_p(), wintypes.BOOL(), ctypes.c_void_p(), wintypes.BOOL()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(before + entries, 1, ctypes.byref(sd), None):
        raise ctypes.WinError(ctypes.get_last_error())
    advapi32.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted))
    error = advapi32.SetSecurityInfo(handle, 7, 4, None, None, dacl, None)
    if error:
        raise OSError(error, "SetSecurityInfo")
    return access_list(handle)
READ_CONTROL_AND_WRITE_DAC = 0x00020000 | 0x00040000
station_name = object_name(user32.GetProcessWindowStation())
desktop_name = object_name(user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId()))
say(f"window station {station_name!r}, desktop {desktop_name!r}")
station = user32.OpenWindowStationW(station_name, False, READ_CONTROL_AND_WRITE_DAC)
desktop = user32.OpenDesktopW(desktop_name, 0, False, READ_CONTROL_AND_WRITE_DAC)
if not station or not desktop:
    raise ctypes.WinError(ctypes.get_last_error())
say("window station before: " + access_list(station))
say("window station after: " + add_entries(station, "(A;OICIIO;GXGR;;;AC)(A;NP;0x20327;;;AC)"))
say("desktop after: " + add_entries(desktop, "(A;;CCDCLCSWRPWPDTLOSDRCWDWO;;;AC)"))
report["station"] = station_name
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
