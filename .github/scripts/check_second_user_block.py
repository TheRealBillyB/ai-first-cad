"""Check the permanent network block for accounts that did not run the setup.

CI only. The runner's administrator has already run `aicad sandbox-setup`. This
makes normal accounts and starts a real container under each one. All three
sends have to be refused, and the account has to be able to read every filter.
The account must also see the setup as complete, the way `aicad sandbox-setup
--status`, the installer, and a run see it, because this runner's own checks run
as an administrator.

Each account uses its own copy of Python, as a per-user install does, because
a normal account cannot grant a container access to folders it does not own.
Only the container code runs, so the copy does not need the geometry libraries.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(r"C:\aicad-second-user")
SOURCE = Path(__file__).resolve().parents[2] / "src" / "aicad"
ACCOUNTS = ("aicadnormal1", "aicadnormal2")
TASK_SECONDS = 240

SENDS = """import json, socket
found = {}
def attempt(name, send):
    try:
        send()
        found[name] = "sent"
    except OSError as exc:
        found[name] = str(exc.winerror)
def tcp():
    socket.create_connection(("1.1.1.1", 53), 6).close()
def udp_connected():
    packet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    packet.connect(("1.1.1.1", 53))
    packet.send(b"aicad-second-user")
def udp_unconnected():
    packet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    packet.bind(("0.0.0.0", 0))
    packet.sendto(b"aicad-second-user", ("1.1.1.1", 53))
attempt("tcp", tcp)
attempt("udp_connected", udp_connected)
attempt("udp_unconnected", udp_unconnected)
print(json.dumps(found))
"""

AS_THE_ACCOUNT = r"""import ctypes, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aicad import sandbox_windows as win
from aicad.sandbox_setup_windows import filters_present, is_installed, status_report

status = status_report()
report = {
    "is_admin": bool(ctypes.windll.shell32.IsUserAnAdmin()),
    "filters": filters_present(),
    "status": {key: status[key] for key in ("ready", "rules_found", "rules_missing", "filters_missing", "errors")},
    "is_installed": is_installed(),
    "run_gate": win.network_block_error(),
}
win._bind()
# The stricter container does not start Python on these runners. The run logs that.
win._mode = "appcontainer"
root = Path(tempfile.mkdtemp(prefix="probe", dir=Path.home()))
script = root / "sends.py"
script.write_text(Path(sys.argv[2]).read_text(encoding="utf-8"), encoding="utf-8")
code, out, err, timed_out, _memory, mechanism = win.spawn(
    [sys.executable, "-I", "-S", "-B", "-u", str(script)],
    cwd=str(root),
    env=win._probe_env(root),
    read_paths=[Path(sys.base_prefix)],
    workspace=root,
    cpu_seconds=30,
    memory_bytes=1 << 31,
    network=False,
    timeout_seconds=60,
    stdout_path=root / "out.txt",
    stderr_path=root / "err.txt",
    child_checks_network=True,
)
report.update(exit=code, timed_out=timed_out, mechanism=mechanism, stderr=err[-300:])
report["sends"] = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
print(json.dumps(report))
"""


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(command, capture_output=True, text=True)
    if check and done.returncode:
        raise SystemExit(f"{command[:2]} failed: {done.stdout} {done.stderr}")
    return done


def as_account(account: str, password: str, line: str, label: str) -> tuple[str, str]:
    """Run one command line as this account through a scheduled task. Returns stdout and stderr."""
    out, err, finished = (WORK / "out" / f"{label}.{suffix}" for suffix in ("txt", "err", "done"))
    batch = WORK / "out" / f"{label}.bat"
    batch.write_text(f'@echo off\r\n{line} > "{out}" 2> "{err}"\r\necho x > "{finished}"\r\n', encoding="ascii")
    task = f"aicad-{label}"
    run(
        "schtasks", "/create", "/tn", task, "/tr", f'cmd /c "{batch}"', "/sc", "once", "/st", "23:59",
        "/f", "/ru", account, "/rp", password, "/rl", "LIMITED",
    )
    run("schtasks", "/run", "/tn", task)
    started = time.time()
    while not finished.exists() and time.time() - started < TASK_SECONDS:
        time.sleep(1)
    run("schtasks", "/delete", "/tn", task, "/f", check=False)
    if not finished.exists():
        raise SystemExit(f"{label} did not finish in {TASK_SECONDS} seconds.")
    text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    return text, err.read_text(encoding="utf-8", errors="replace")[-700:] if err.exists() else ""


def annotation(text: str) -> str:
    """Workflow commands take one line. An annotation can be read without opening the log."""
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def check_account(account: str) -> list[str]:
    # Never printed, and gone with the runner. A failed command reports only its first two words.
    password = "Aa1!" + secrets.token_urlsafe(18)
    run("net", "user", account, password, "/add", "/y")
    # The batch logon right only. This group is not an administrator.
    run("net", "localgroup", "Performance Log Users", account, "/add")
    copied = rf"C:\Users\{account}\py"
    as_account(account, password, f'robocopy "{sys.base_prefix}" "{copied}" /E /NFL /NDL /NJH /NJS /NP', f"{account}-copy")
    line = f'"{copied}\\python.exe" "{WORK / "account.py"}" "{WORK / "source"}" "{WORK / "sends.py"}"'
    text, stderr = as_account(account, password, line, f"{account}-probe")
    print(f"::notice title={account}::" + annotation(text.strip()))
    try:
        report = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"{account}: the probe gave no report. stdout: {text[-300:]} stderr: {stderr}"]
    problems = []
    if report["is_admin"]:
        problems.append("the account is an administrator")
    if report["filters"] != 8:
        problems.append(f"it read {report['filters']} of 8 filters")
    status = report["status"]
    if not status["ready"] or status["rules_missing"] or status["errors"]:
        problems.append(f"--status says the setup is not complete: {status}")
    if not report["is_installed"]:
        problems.append("the check after setup says the block is not in place")
    if report["run_gate"] is not None:
        problems.append(f"a run would be refused: {report['run_gate']}")
    if report["exit"] != 0 or report["timed_out"]:
        problems.append(f"the container exited {report['exit']}: {report['stderr']}")
    sends = report["sends"] or {}
    if set(sends) != {"tcp", "udp_connected", "udp_unconnected"}:
        problems.append(f"the container did not report all three sends: {sends}")
    problems += [f"{name} was not refused: {value}" for name, value in sends.items() if value not in ("5", "10013", "10051")]
    return [f"{account}: {problem}" for problem in problems]


def main() -> int:
    (WORK / "out").mkdir(parents=True)
    (WORK / "source").mkdir()
    shutil.copytree(SOURCE, WORK / "source" / "aicad", ignore=shutil.ignore_patterns("__pycache__"))
    # The real package start imports numpy and the geometry libraries. The container code needs neither.
    (WORK / "source" / "aicad" / "__init__.py").write_text("", encoding="utf-8")
    (WORK / "account.py").write_text(AS_THE_ACCOUNT, encoding="utf-8")
    (WORK / "sends.py").write_text(SENDS, encoding="utf-8")
    run("icacls", str(WORK), "/grant", "Users:(OI)(CI)RX", "/T", "/Q")
    run("icacls", str(WORK / "out"), "/grant", "Users:(OI)(CI)M", "/T", "/Q")
    started = time.time()
    problems: list[str] = []
    for account in ACCOUNTS:
        problems += check_account(account)
    print(f"::notice title=Second account check::Checked {len(ACCOUNTS)} accounts in {time.time() - started:.0f} seconds.")
    for problem in problems:
        print(f"::error::{annotation(problem)}")
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        code = main()
    except (Exception, SystemExit) as exc:
        print(f"::error title=Second account check::{annotation(repr(exc))}")
        code = 1
    raise SystemExit(code)
