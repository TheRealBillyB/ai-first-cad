"""One-time Windows setup for the design-script network block.

An administrator approves this once per computer. It creates a permanent block
for one fixed script container: two firewall rules and persistent WFP filters.

The elevated process runs this file with ``python -I -S``, so it imports only
the standard library. Nothing from aicad, ``.venv``, or a project is imported
while elevated. It takes no path and no id from its caller. Every value it
writes is a constant in this file or is derived from one.

A script run installs nothing. It reads the filters by key and checks from
inside the container that the network is refused.
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
EXIT_FILTERS = 5
EXIT_RULES = 6
EXIT_NOT_CONFIRMED = 7
EXIT_REMOVE_FILTERS = 8
EXIT_REMOVE_RULES = 9
# A Windows prompt cannot pass text back, and an elevated process does not write
# a file a normal account could redirect. The exit code carries the reason.
_FAILURE_REASONS = {
    EXIT_USAGE: "The setup step was started the wrong way.",
    EXIT_NOT_ADMIN: "The setup step did not get administrator rights.",
    EXIT_FAILED: "The setup step stopped on an unexpected Windows error.",
    EXIT_FILTERS: "Windows refused the network filters. The Base Filtering Engine service may be stopped.",
    EXIT_RULES: "Windows refused the firewall rules. A policy or another firewall product may be managing them.",
    EXIT_NOT_CONFIRMED: "The block was created, but Windows could not read it back.",
    EXIT_REMOVE_FILTERS: "Windows refused to remove the network filters.",
    EXIT_REMOVE_RULES: "Windows refused to remove the firewall rules.",
}
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


class _StepFailed(OSError):
    """One step of the elevated process failed. ``code`` is its exit code."""

    def __init__(self, code: int, detail: object = "") -> None:
        super().__init__(str(detail) or _FAILURE_REASONS[code])
        self.code = code


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


def firewall_read_script(sid: str, rules: tuple = FIREWALL_RULES) -> str:
    """Exit 0 when these rules block every protocol for this container. Otherwise exit 2."""
    if _PACKAGE_SID.fullmatch(sid) is None:
        raise ValueError("Not a package SID.")
    package = sid.upper()
    checks = "".join(
        f"  if (-not (Test-AicadBlock '{name}' '{direction}' '{package}')) {{ exit 2 }}\n"
        for name, _display, direction in rules
    )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Test-AicadBlock([string]$Name, [string]$Direction, [string]$Package) {\n"
        "  $rules = @(Get-NetFirewallRule -Name $Name -ErrorAction Ignore)\n"
        "  if ($rules.Count -ne 1) { return $false }\n"
        "  $rule = $rules[0]\n"
        "  if ([string]$rule.Enabled -ne 'True') { return $false }\n"
        "  if ([string]$rule.Action -ne 'Block') { return $false }\n"
        "  if ([string]$rule.Direction -ne $Direction) { return $false }\n"
        "  $ports = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule)\n"
        "  if ($ports.Count -ne 1) { return $false }\n"
        "  $protocol = [string]$ports[0].Protocol\n"
        "  if ($protocol -ne 'Any' -and $protocol -ne '256') { return $false }\n"
        "  foreach ($value in @($ports[0].LocalPort) + @($ports[0].RemotePort)) {\n"
        "    foreach ($item in @($value)) {\n"
        "      $text = [string]$item\n"
        "      if ($text -and $text -ne 'Any') { return $false }\n"
        "    }\n"
        "  }\n"
        "  $apps = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule)\n"
        "  if ($apps.Count -ne 1) { return $false }\n"
        "  if ([string]$apps[0].Package.Trim().ToUpperInvariant() -ne $Package) { return $false }\n"
        "  return $true\n"
        "}\n"
        "try {\n"
        f"{checks}"
        "  exit 0\n"
        "} catch {\n"
        "  exit 2\n"
        "}\n"
    )


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


def _free_local(pointer: ctypes.c_void_p) -> None:
    if pointer:
        ctypes.windll.kernel32.LocalFree(pointer)


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
    try:
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
    finally:
        _free_local(descriptor)
        _free_local(sid_pointer)


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


def readable_filters() -> list[str]:
    """The layers whose block filter this account can read. It needs no administrator rights."""
    client = _wfp()
    engine = _open_engine(client)
    found = []
    try:
        for name, _layer in BLOCK_LAYERS:
            key = _guid(filter_key(name))
            out = ctypes.c_void_p()
            if client.FwpmFilterGetByKey0(engine, ctypes.byref(key), ctypes.byref(out)) == 0 and out.value:
                client.FwpmFreeMemory0(ctypes.byref(out))
                found.append(name)
    finally:
        client.FwpmEngineClose0(engine)
    return found


def filters_present() -> int:
    """How many block filters this account can read."""
    return len(readable_filters())


def rules_present() -> bool:
    return _run_powershell(firewall_read_script(package_sid())).returncode == 0


def is_installed() -> bool:
    return filters_present() == len(BLOCK_LAYERS) and rules_present()


def _powershell_failure(script: str) -> str | None:
    """What went wrong running this script, or None when it finished cleanly."""
    try:
        done = _run_powershell(script)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if not done.returncode:
        return None
    return (done.stdout + done.stderr).strip() or "PowerShell failed."


def _install() -> None:
    try:
        sid = package_sid()
        _install_wfp(sid)
    except OSError as exc:
        raise _StepFailed(EXIT_FILTERS, exc) from exc
    # Half a block looks like a block, so a failed install leaves none of it.
    failure = _powershell_failure(firewall_install_script(sid))
    if failure is not None:
        _remove_after_failure()
        raise _StepFailed(EXIT_RULES, f"Adding the firewall rules failed. {failure}")
    try:
        installed = is_installed()
    except (OSError, subprocess.SubprocessError):
        installed = False
    if not installed:
        _remove_after_failure()
        raise _StepFailed(EXIT_NOT_CONFIRMED)


def _remove_after_failure() -> None:
    try:
        _remove()
    except OSError as exc:
        print(f"The partial block could not be removed. {exc}", file=sys.stderr)


def _remove() -> None:
    try:
        _remove_wfp()
    except OSError as exc:
        raise _StepFailed(EXIT_REMOVE_FILTERS, exc) from exc
    failure = _powershell_failure(firewall_remove_script())
    if failure is not None:
        raise _StepFailed(EXIT_REMOVE_RULES, f"Removing the firewall rules failed. {failure}")


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
    except _StepFailed as exc:
        print(exc, file=sys.stderr)
        return exc.code
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


def status_report() -> dict:
    """What is in place and what is missing. This reads and changes nothing."""
    report: dict = {
        "ready": False,
        "container": PROFILE_NAME,
        "package_sid": None,
        "filters_found": [],
        "filters_missing": [name for name, _layer in BLOCK_LAYERS],
        "rules_found": [],
        "rules_missing": [rule[0] for rule in FIREWALL_RULES],
        "errors": [],
    }
    try:
        sid = package_sid()
    except OSError as exc:
        report["errors"].append(str(exc))
        return report
    report["package_sid"] = sid
    try:
        found = readable_filters()
    except OSError as exc:
        report["errors"].append(str(exc))
    else:
        report["filters_found"] = found
        report["filters_missing"] = [name for name, _layer in BLOCK_LAYERS if name not in found]
    rules_found = []
    for rule in FIREWALL_RULES:
        try:
            if _run_powershell(firewall_read_script(sid, (rule,))).returncode == 0:
                rules_found.append(rule[0])
        except (OSError, subprocess.SubprocessError) as exc:
            report["errors"].append(str(exc))
    report["rules_found"] = rules_found
    report["rules_missing"] = [rule[0] for rule in FIREWALL_RULES if rule[0] not in rules_found]
    report["ready"] = not (report["filters_missing"] or report["rules_missing"] or report["errors"])
    return report


def run_status() -> int:
    """``aicad sandbox-setup --status``. Exit 0 when ready, 1 when not. It changes nothing."""
    if sys.platform != "win32":
        print("Only Windows needs this step. There is nothing to set up on this computer.")
        return 0
    report = status_report()
    print("The Windows sandbox is set up." if report["ready"] else "The Windows sandbox is not set up.")
    print(f"Blocked script container: {report['container']} ({report['package_sid'] or 'could not be read'})")
    print(f"Network filters: {len(report['filters_found'])} of {len(BLOCK_LAYERS)}")
    for name in report["filters_missing"]:
        print(f"    missing: {name}")
    print(f"Firewall rules: {len(report['rules_found'])} of {len(FIREWALL_RULES)}")
    for name in report["rules_missing"]:
        print(f"    missing: {name}")
    for error in report["errors"]:
        print(f"Could not read this: {error}")
    if report["ready"]:
        return 0
    print("To set it up, run `aicad sandbox-setup` and approve the Windows prompt.")
    return 1


def failure_reason(code: int) -> str:
    """What an exit code from the elevated step means."""
    return _FAILURE_REASONS.get(code) or f"The setup step exited with code {code & 0xFFFFFFFF:#x}."


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
        print(failure_reason(code))
        print("The block was left as it was or removed. From an administrator prompt, this command also prints the Windows message.")
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
