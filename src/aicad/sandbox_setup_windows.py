"""One-time Windows setup for the design-script network block.

An administrator approves this once per computer. It creates a permanent block
for one fixed script container: two firewall rules and persistent WFP filters.

The elevated process runs this file with ``python -I -S``, so it imports only
the standard library. Nothing from aicad, ``.venv``, or a project is imported
while elevated. It takes no path and no id from its caller. Every value it
writes is a constant in this file or is derived from one.

Scripts do not use this block yet. Each run still installs its own.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

PROFILE_NAME = "aicadscript"
FIREWALL_RULES = (
    ("aicad-script-network-off-out", "aicad script network off outbound", "Outbound"),
    ("aicad-script-network-off-in", "aicad script network off inbound", "Inbound"),
)
_STALE_FIREWALL_RULES = ("aicad-script-network-off-udp",)
BLOCK_LAYERS = (
    ("ALE_AUTH_CONNECT_V4", "c38d57d1-05a7-4c33-904f-7fbceee60e82"),
    ("ALE_AUTH_CONNECT_V6", "4a72393b-319f-44bc-84c3-ba54dcb3b6b4"),
    ("ALE_AUTH_RECV_ACCEPT_V4", "e1cd9fe7-f4b5-4273-96c0-592e487b8650"),
    ("ALE_AUTH_RECV_ACCEPT_V6", "a3b42c97-9f04-4672-b87e-cee9c483257f"),
    ("ALE_AUTH_LISTEN_V4", "88bb5dad-76d7-4227-9c71-df0a3ed7be7e"),
    ("ALE_AUTH_LISTEN_V6", "7ac9de24-17dd-4814-b4bd-a9fbc95a321b"),
    ("ALE_RESOURCE_ASSIGNMENT_V4", "1247d66d-0b60-4a15-8d44-7155d0f53a0c"),
    ("ALE_RESOURCE_ASSIGNMENT_V6", "55a650e1-5f0a-4eca-a653-88f53b26aa8c"),
)
# A later setup that has to change these objects changes this, so its keys differ.
SETUP_VERSION = 1
_KEYS = uuid.UUID("7f3b3d2e-5a1c-4d0e-9d55-0b1f6a2c9e11")
SUBLAYER_KEY = str(uuid.uuid5(_KEYS, f"v{SETUP_VERSION}/sublayer"))
# Administrators and the system have full control. A signed-in user may only
# read a filter (FWPM_ACTRL_OPEN | FWPM_ACTRL_READ), so a run can confirm it.
FILTER_DESCRIPTOR = "O:BAG:BAD:(A;;GA;;;BA)(A;;GA;;;SY)(A;;0xC0;;;AU)"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_ADMIN = 3
EXIT_FAILED = 4
_ERROR_CANCELLED = 1223
_FWP_E_FILTER_NOT_FOUND = 0x80320003
_FWP_E_SUBLAYER_NOT_FOUND = 0x80320007
_FWP_E_ALREADY_EXISTS = 0x80320009

_FWP_ACTION_BLOCK = 0x00001001
_FWP_UINT64 = 4
_FWP_SID = 13
_FWP_MATCH_EQUAL = 0
_FWPM_FILTER_FLAG_PERSISTENT = 0x00000001
_FWPM_FILTER_FLAG_CLEAR_ACTION_RIGHT = 0x00000008
_FWPM_SUBLAYER_FLAG_PERSISTENT = 0x00000001
_RPC_C_AUTHN_WINNT = 10
_ALE_PACKAGE_ID = "71bc78fa-f17c-4997-a602-6abb261f351c"
_PACKAGE_SID = re.compile(r"^S-1-15-2(?:-\d+)+$", re.IGNORECASE)
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_INFINITE = 0xFFFFFFFF
_CREATE_NO_WINDOW = 0x08000000


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _Display(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("description", ctypes.c_wchar_p)]


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class _ValueData(ctypes.Union):
    _fields_ = [
        ("uint8", ctypes.c_uint8),
        ("uint64", ctypes.POINTER(ctypes.c_uint64)),
        ("sid", ctypes.c_void_p),
    ]


class _Value(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("data", _ValueData)]


class _Condition(ctypes.Structure):
    _fields_ = [("fieldKey", _Guid), ("matchType", ctypes.c_uint32), ("value", _Value)]


class _ActionData(ctypes.Union):
    _fields_ = [("filterType", _Guid), ("calloutKey", _Guid)]


class _Action(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("data", _ActionData)]


class _Context(ctypes.Union):
    _fields_ = [("rawContext", ctypes.c_uint64), ("providerContextKey", _Guid)]


class _Filter(ctypes.Structure):
    """FWPM_FILTER0. The layout is the 64-bit one: 200 bytes."""

    _fields_ = [
        ("filterKey", _Guid),
        ("displayData", _Display),
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),
        ("providerData", _Blob),
        ("layerKey", _Guid),
        ("subLayerKey", _Guid),
        ("weight", _Value),
        ("numFilterConditions", ctypes.c_uint32),
        ("filterCondition", ctypes.POINTER(_Condition)),
        ("action", _Action),
        ("context", _Context),
        ("reserved", ctypes.c_void_p),
        ("filterId", ctypes.c_uint64),
        ("effectiveWeight", _Value),
    ]


class _Session(ctypes.Structure):
    _fields_ = [
        ("sessionKey", _Guid),
        ("displayData", _Display),
        ("flags", ctypes.c_uint32),
        ("txnWaitTimeoutInMSec", ctypes.c_uint32),
        ("processId", ctypes.c_uint32),
        ("sid", ctypes.c_void_p),
        ("username", ctypes.c_wchar_p),
        ("kernelMode", ctypes.c_int),
    ]


class _SubLayer(ctypes.Structure):
    _fields_ = [
        ("subLayerKey", _Guid),
        ("displayData", _Display),
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),
        ("providerData", _Blob),
        ("weight", ctypes.c_uint16),
    ]


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _guid(text: str) -> _Guid:
    return _Guid.from_buffer_copy(uuid.UUID(text).bytes_le)


def filter_key(layer_name: str) -> str:
    return str(uuid.uuid5(_KEYS, f"v{SETUP_VERSION}/filter/{layer_name}"))


def _system_directory() -> Path:
    """System32 from Windows itself, not from an environment variable."""
    buffer = ctypes.create_unicode_buffer(260)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer, 260):
        raise OSError("Could not find the Windows system folder.")
    return Path(buffer.value)


def package_sid() -> str:
    """The package SID of the script container. It is derived from the name alone."""
    userenv = ctypes.WinDLL("userenv")
    advapi32 = ctypes.WinDLL("advapi32")
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    sid = ctypes.c_void_p()
    code = userenv.DeriveAppContainerSidFromAppContainerName(PROFILE_NAME, ctypes.byref(sid))
    if code:
        raise OSError(f"Could not derive the script container id ({code & 0xFFFFFFFF:#010x}).")
    text = ctypes.c_void_p()
    try:
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise OSError("Could not read the script container id.")
        try:
            value = ctypes.cast(text, wintypes.LPWSTR).value or ""
        finally:
            ctypes.windll.kernel32.LocalFree(text)
    finally:
        advapi32.FreeSid(sid)
    if _PACKAGE_SID.fullmatch(value) is None:
        raise OSError("The script container id is not a package SID.")
    return value


def firewall_install_script(sid: str) -> str:
    """PowerShell that replaces the two block rules for this package."""
    if _PACKAGE_SID.fullmatch(sid) is None:
        raise ValueError("Not a package SID.")
    lines = [_firewall_remove_lines()]
    package = sid.upper()
    for name, display, direction in FIREWALL_RULES:
        lines.append(
            f"  New-NetFirewallRule -Name '{name}' -DisplayName '{display}' "
            f"-Direction {direction} -Action Block -Protocol Any "
            f"-Package '{package}' -Profile Any -Enabled True | Out-Null\n"
        )
    return _firewall_script_around("".join(lines))


def firewall_remove_script() -> str:
    return _firewall_script_around(_firewall_remove_lines())


def _firewall_remove_lines() -> str:
    names = ", ".join(repr(rule[0]) for rule in FIREWALL_RULES) + ", " + ", ".join(repr(name) for name in _STALE_FIREWALL_RULES)
    # Remove returns before the firewall service drops the name, so wait for it.
    return (
        f"  foreach ($name in @({names})) {{\n"
        "    foreach ($rule in @(Get-NetFirewallRule -Name $name -ErrorAction Ignore)) {\n"
        "      Remove-NetFirewallRule -InputObject $rule\n"
        "    }\n"
        "    foreach ($attempt in 1..40) {\n"
        "      if (@(Get-NetFirewallRule -Name $name -ErrorAction Ignore).Count -eq 0) { break }\n"
        "      Start-Sleep -Milliseconds 250\n"
        "    }\n"
        "  }\n"
    )


def _firewall_script_around(body: str) -> str:
    return "$ErrorActionPreference = 'Stop'\ntry {\n" + body + "} catch {\n  Write-Output ($_ | Out-String)\n  exit 1\n}\nexit 0\n"


def firewall_read_script() -> str:
    """Exit 0 when both rules exist and are on. Exit 2 when one is missing or off."""
    checks = "".join(
        f"  $rule = @(Get-NetFirewallRule -Name '{name}' -ErrorAction Stop)\n"
        "  if ($rule.Count -ne 1 -or [string]$rule[0].Enabled -ne 'True' -or [string]$rule[0].Action -ne 'Block') { exit 2 }\n"
        for name, _display, _direction in FIREWALL_RULES
    )
    return "$ErrorActionPreference = 'Stop'\ntry {\n" + checks + "} catch {\n  exit 2\n}\nexit 0\n"


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    # A timeout is a failed step. A later firewall call can be slow.
    return subprocess.run(
        [str(_system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )


def _wfp() -> ctypes.WinDLL:
    client = ctypes.WinDLL("fwpuclnt")
    handle = ctypes.c_void_p
    guid = ctypes.POINTER(_Guid)
    client.FwpmEngineOpen0.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(_Session), ctypes.POINTER(handle)]
    client.FwpmEngineClose0.argtypes = [handle]
    client.FwpmTransactionBegin0.argtypes = [handle, wintypes.DWORD]
    client.FwpmTransactionCommit0.argtypes = [handle]
    client.FwpmTransactionAbort0.argtypes = [handle]
    client.FwpmSubLayerAdd0.argtypes = [handle, ctypes.POINTER(_SubLayer), ctypes.c_void_p]
    client.FwpmSubLayerDeleteByKey0.argtypes = [handle, guid]
    client.FwpmFilterAdd0.argtypes = [handle, ctypes.POINTER(_Filter), ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
    client.FwpmFilterDeleteByKey0.argtypes = [handle, guid]
    client.FwpmFilterGetByKey0.argtypes = [handle, guid, ctypes.POINTER(handle)]
    client.FwpmFreeMemory0.argtypes = [ctypes.POINTER(handle)]
    client.FwpmFreeMemory0.restype = None
    for name in (
        "FwpmEngineOpen0",
        "FwpmEngineClose0",
        "FwpmTransactionBegin0",
        "FwpmTransactionCommit0",
        "FwpmTransactionAbort0",
        "FwpmSubLayerAdd0",
        "FwpmSubLayerDeleteByKey0",
        "FwpmFilterAdd0",
        "FwpmFilterDeleteByKey0",
        "FwpmFilterGetByKey0",
    ):
        getattr(client, name).restype = wintypes.DWORD
    return client


def _open_engine(client: ctypes.WinDLL) -> ctypes.c_void_p:
    session = _Session()
    name = ctypes.create_unicode_buffer("aicad sandbox setup")
    session.displayData.name = ctypes.cast(name, ctypes.c_wchar_p)
    engine = ctypes.c_void_p()
    code = client.FwpmEngineOpen0(None, _RPC_C_AUTHN_WINNT, None, ctypes.byref(session), ctypes.byref(engine))
    if code or not engine.value:
        raise OSError(f"Could not open the Windows filtering engine ({code:#010x}).")
    return engine


def _checked(step: str, code: int, *allowed: int) -> None:
    if code and code not in allowed:
        raise OSError(f"{step} failed ({code:#010x}).")


def _delete_wfp_objects(client: ctypes.WinDLL, engine: ctypes.c_void_p) -> None:
    for name, _layer in BLOCK_LAYERS:
        key = _guid(filter_key(name))
        _checked(f"Removing the {name} filter", client.FwpmFilterDeleteByKey0(engine, ctypes.byref(key)), _FWP_E_FILTER_NOT_FOUND)
    key = _guid(SUBLAYER_KEY)
    _checked("Removing the sublayer", client.FwpmSubLayerDeleteByKey0(engine, ctypes.byref(key)), _FWP_E_SUBLAYER_NOT_FOUND)


def _block_filter(layer_name: str, layer: str, sid_pointer: int) -> _Filter:
    """One permanent hard block for this container. The only condition is its package SID."""
    weight = ctypes.c_uint64(0xFFFFFFFFFFFFFFFF)
    condition = _Condition()
    condition.fieldKey = _guid(_ALE_PACKAGE_ID)
    condition.matchType = _FWP_MATCH_EQUAL
    condition.value.type = _FWP_SID
    condition.value.data.sid = sid_pointer
    name = ctypes.create_unicode_buffer("aicad script network block")
    description = ctypes.create_unicode_buffer("Blocks the aicad script container.")
    block = _Filter()
    block.filterKey = _guid(filter_key(layer_name))
    block.displayData.name = ctypes.cast(name, ctypes.c_wchar_p)
    block.displayData.description = ctypes.cast(description, ctypes.c_wchar_p)
    block.flags = _FWPM_FILTER_FLAG_PERSISTENT | _FWPM_FILTER_FLAG_CLEAR_ACTION_RIGHT
    block.layerKey = _guid(layer)
    block.subLayerKey = _guid(SUBLAYER_KEY)
    block.weight.type = _FWP_UINT64
    block.weight.data.uint64 = ctypes.pointer(weight)
    block.numFilterConditions = 1
    block.filterCondition = ctypes.pointer(condition)
    block.action.type = _FWP_ACTION_BLOCK
    block._keepalive = [weight, condition, name, description]
    return block


def _install_wfp(sid: str) -> None:
    """Replace the sublayer and the filters in one transaction, so a failure leaves neither."""
    client = _wfp()
    advapi32 = ctypes.WinDLL("advapi32")
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    sid_pointer = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(sid_pointer)):
        raise OSError("Could not read the script container id.")
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(FILTER_DESCRIPTOR, 1, ctypes.byref(descriptor), None):
        raise OSError("Could not build the filter's access list.")
    engine = _open_engine(client)
    try:
        _checked("Starting the filter transaction", client.FwpmTransactionBegin0(engine, 0))
        try:
            _delete_wfp_objects(client, engine)
            sublayer = _SubLayer()
            sublayer.subLayerKey = _guid(SUBLAYER_KEY)
            name = ctypes.create_unicode_buffer("aicad script network")
            sublayer.displayData.name = ctypes.cast(name, ctypes.c_wchar_p)
            sublayer.flags = _FWPM_SUBLAYER_FLAG_PERSISTENT
            sublayer.weight = 0xFFFF
            _checked("Adding the sublayer", client.FwpmSubLayerAdd0(engine, ctypes.byref(sublayer), None))
            for layer_name, layer in BLOCK_LAYERS:
                block = _block_filter(layer_name, layer, int(sid_pointer.value))
                added = ctypes.c_uint64()
                _checked(f"Adding the {layer_name} filter", client.FwpmFilterAdd0(engine, ctypes.byref(block), descriptor, ctypes.byref(added)))
            _checked("Committing the filters", client.FwpmTransactionCommit0(engine))
        except BaseException:
            client.FwpmTransactionAbort0(engine)
            raise
    finally:
        client.FwpmEngineClose0(engine)
        ctypes.windll.kernel32.LocalFree(descriptor)
        ctypes.windll.kernel32.LocalFree(sid_pointer)


def _remove_wfp() -> None:
    client = _wfp()
    engine = _open_engine(client)
    try:
        _checked("Starting the filter transaction", client.FwpmTransactionBegin0(engine, 0))
        try:
            _delete_wfp_objects(client, engine)
            _checked("Committing the removal", client.FwpmTransactionCommit0(engine))
        except BaseException:
            client.FwpmTransactionAbort0(engine)
            raise
    finally:
        client.FwpmEngineClose0(engine)


def filters_present() -> int:
    """How many block filters this account can read. It needs no administrator rights."""
    client = _wfp()
    engine = _open_engine(client)
    found = 0
    try:
        for name, _layer in BLOCK_LAYERS:
            key = _guid(filter_key(name))
            out = ctypes.c_void_p()
            if client.FwpmFilterGetByKey0(engine, ctypes.byref(key), ctypes.byref(out)) == 0 and out.value:
                client.FwpmFreeMemory0(ctypes.byref(out))
                found += 1
    finally:
        client.FwpmEngineClose0(engine)
    return found


def rules_present() -> bool:
    return _run_powershell(firewall_read_script()).returncode == 0


def is_installed() -> bool:
    return filters_present() == len(BLOCK_LAYERS) and rules_present()


def _install() -> None:
    sid = package_sid()
    _install_wfp(sid)
    done = _run_powershell(firewall_install_script(sid))
    if done.returncode:
        # Half a block looks like a block, so a failed install leaves none of it.
        _remove_wfp()
        raise OSError(f"Adding the firewall rules failed. {(done.stdout + done.stderr).strip()}")
    if not is_installed():
        _remove()
        raise OSError("The block was not in place after setup.")


def _remove() -> None:
    _remove_wfp()
    done = _run_powershell(firewall_remove_script())
    if done.returncode:
        raise OSError(f"Removing the firewall rules failed. {(done.stdout + done.stderr).strip()}")


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _elevated_main(argv: list[str]) -> int:
    """Run in the elevated process. The only input is which of two fixed actions to take."""
    if len(argv) != 1 or argv[0] not in ("install", "remove"):
        print("Use install or remove.", file=sys.stderr)
        return EXIT_USAGE
    if not is_admin():
        print("This step needs administrator rights.", file=sys.stderr)
        return EXIT_NOT_ADMIN
    try:
        _install() if argv[0] == "install" else _remove()
    except (OSError, subprocess.SubprocessError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _interpreter() -> str:
    # The base Python, not the .venv launcher. -I -S keeps .venv and its packages out.
    return getattr(sys, "_base_executable", None) or sys.executable


def _command(action: str) -> list[str]:
    return [_interpreter(), "-I", "-S", str(Path(__file__).resolve()), action]


def _run_with_uac_prompt(action: str) -> int:
    """Ask Windows to elevate. Returns the process exit code, or raises when refused."""
    command = _command(action)
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:])
    # The elevated process does not start in a folder the project could write.
    info.lpDirectory = str(_system_directory())
    info.nShow = _SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        kernel32.WaitForSingleObject(info.hProcess, _INFINITE)
        code = wintypes.DWORD(EXIT_FAILED)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def run_setup(*, remove: bool = False) -> int:
    """``aicad sandbox-setup``. Asks for approval, runs the elevated step, then checks the result."""
    if sys.platform != "win32":
        print("Only Windows needs this step. There is nothing to set up on this computer.")
        return 0
    action = "remove" if remove else "install"
    try:
        if is_admin():
            code = subprocess.run(_command(action), check=False, cwd=str(_system_directory())).returncode
        else:
            print("Windows will ask you to approve this once.")
            code = _run_with_uac_prompt(action)
    except OSError as exc:
        if getattr(exc, "winerror", None) == _ERROR_CANCELLED:
            print("You declined, so nothing changed. Shape, sketch, gear, and catalog parts still work.")
            return 1
        print(f"Windows could not start the setup step: {exc}")
        return 1
    if code:
        print(f"The setup step failed (code {code}), so the block was left as it was or removed.")
        return 1
    if remove:
        if filters_present():
            print("Some of the block is still in place.")
            return 1
        print("The script network block is removed from this computer.")
        return 0
    if not is_installed():
        print("Setup finished, but this account cannot confirm the block is in place.")
        return 1
    print("The script network block is in place for every account on this computer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_elevated_main(sys.argv[1:]))
