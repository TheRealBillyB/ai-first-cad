"""Design scripts stay in the project, and opening a project does not run them.

These tests run on Linux, macOS, and Windows. Each machine has to confine the
probe with its own sandbox. A missing file does not count as confined.
"""

import asyncio
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from aicad.document import Document
from aicad.errors import ScriptError
from aicad.sandbox import allow_design_scripts, os_probe, run_design_script, run_part_script, script_log_path
from aicad.server import build_server
from aicad.workspace import COMMANDS, Workspace


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AICAD_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AICAD_SCRIPT_NETWORK", raising=False)
    return tmp_path


def test_windows_traverse_grant_skips_the_volume_root():
    from aicad.sandbox_windows import _is_volume_root_text, _windows_traverse_parents

    parents = _windows_traverse_parents(r"C:\hostedtoolcache\windows\Python\3.11.9\x64")
    assert r"C:\hostedtoolcache\windows\Python\3.11.9" in parents
    assert r"C:\hostedtoolcache" in parents
    assert not any(_is_volume_root_text(parent) for parent in parents)
    workspace = _windows_traverse_parents(r"D:\a\ai-first-cad\ai-first-cad\.venv\Lib\site-packages")
    assert r"D:\a" in workspace
    assert not any(_is_volume_root_text(parent) for parent in workspace)
    assert _windows_traverse_parents(r"\\server\share\folder") == []


def test_grant_traverse_does_not_call_icacls_on_the_volume_root(monkeypatch):
    from aicad import sandbox_windows as win

    calls: list[str] = []
    icacls_calls: list[str] = []
    win._granted_traverse.clear()
    monkeypatch.setattr(win, "_grant_traverse_one", lambda path, sid, package_sid: calls.append(str(path)))
    monkeypatch.setattr(win, "_icacls", lambda path, sid, rights, *, recurse: icacls_calls.append(str(path)))
    win._grant_traverse(Path(r"D:\a\ai-first-cad\ai-first-cad\.venv"), "S-1-2-3", "S-1-15-2-2")
    assert calls
    assert icacls_calls == []
    assert all(not win._is_volume_root_text(path) for path in calls)


def test_a_stuck_icacls_fails_with_a_clear_error(monkeypatch):
    import subprocess

    from aicad import sandbox_windows as win

    def stuck(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "icacls", timeout=kwargs.get("timeout", 15))

    monkeypatch.setattr(win.subprocess, "run", stuck)
    with pytest.raises(win.IcaclsTimeout, match="did not finish") as raised:
        win._icacls(Path(r"C:\hostedtoolcache"), "S-1-2-3", "(RX)", recurse=False)
    message = str(raised.value)
    assert r"C:\hostedtoolcache" in message
    assert "confirmation" in message

    def stuck_grant(path, sid, package_sid):
        raise win.IcaclsTimeout(
            "icacls did not finish within 15 seconds for "
            r"C:\hostedtoolcache. It may be waiting for confirmation."
        )

    win._granted_traverse.clear()
    monkeypatch.setattr(win, "_grant_traverse_one", stuck_grant)
    with pytest.raises(win.IcaclsTimeout, match="confirmation"):
        win._grant_traverse(Path(r"C:\hostedtoolcache\windows\Python"), "S-1-2-3", "S-1-15-2-2")


def test_traverse_is_skipped_only_when_that_container_can_already_walk():
    from aicad import sandbox_windows as win

    allow_all = (0, 0, win._FILE_TRAVERSE, win._ALL_APPLICATION_PACKAGES)
    assert win._dacl_needs_grant([allow_all], "S-1-5-1", win._package_sid(False)) is False
    assert win._dacl_needs_grant([allow_all], "S-1-5-1", win._package_sid(True)) is True
    restricted = (0, 0, win._FILE_TRAVERSE | win._SYNCHRONIZE, win._ALL_RESTRICTED_APPLICATION_PACKAGES)
    assert win._dacl_needs_grant([restricted], "S-1-5-1", win._package_sid(True)) is False
    assert win._dacl_needs_grant([restricted], "S-1-5-1", win._package_sid(False)) is True
    own = (0, 0, win._FILE_TRAVERSE | win._FILE_READ_ATTRIBUTES, "S-1-5-1")
    assert win._dacl_needs_grant([own], "S-1-5-1", win._package_sid(True)) is False
    inherit_only = (0, win._INHERIT_ONLY_ACE, win._FILE_TRAVERSE, "S-1-5-1")
    assert win._dacl_needs_grant([inherit_only], "S-1-5-1", win._package_sid(False)) is True
    deny = (1, 0, win._FILE_TRAVERSE, "S-1-5-1")
    assert win._dacl_needs_grant([deny], "S-1-5-1", win._package_sid(False)) is True
    assert win._package_sid(True) == "S-1-15-2-2"
    assert win._package_sid(False) == "S-1-15-2-1"
    assert win._TRAVERSE_MASK & win._FILE_TRAVERSE
    assert win._TRAVERSE_MASK & win._SYNCHRONIZE
    assert not win._TRAVERSE_MASK & win._FILE_LIST_DIRECTORY


def test_traverse_write_is_skipped_when_the_directory_already_allows_it(monkeypatch):
    from aicad import sandbox_windows as win

    writes: list[str] = []
    monkeypatch.setattr(win, "_parent_allows_traverse", lambda path, sid, package_sid: True)
    monkeypatch.setattr(win, "_write_traverse_ace", lambda path, sid: writes.append(str(path)))
    win._grant_traverse_one(Path(r"C:\hostedtoolcache"), "S-1-2-3", "S-1-15-2-1")
    assert writes == []


def test_a_slow_traverse_grant_fails_fast(monkeypatch):
    import time

    from aicad import sandbox_windows as win

    monkeypatch.setattr(win, "_GRANT_TIMEOUT_SECONDS", 0.3)

    def hang(path, sid, package_sid):
        time.sleep(2)

    win._granted_traverse.clear()
    monkeypatch.setattr(win, "_grant_traverse_one", hang)
    started = time.monotonic()
    with pytest.raises(win.GrantTimeout, match="did not finish") as raised:
        win._grant_traverse(Path(r"C:\hostedtoolcache\windows"), "S-1-2-3", "S-1-15-2-2")
    assert time.monotonic() - started < 3
    message = str(raised.value)
    assert r"C:\hostedtoolcache" in message
    assert "not changed" in message


def test_availability_probe_runs_once_per_process(monkeypatch):
    from aicad import sandbox_windows as win

    calls: list[int] = []

    def once(paths):
        calls.append(1)

    monkeypatch.setattr(win, "_probe_done", False)
    monkeypatch.setattr(win, "_probe_result", None)
    monkeypatch.setattr(win, "ensure_mode", once)
    assert win.availability_error([Path(r"C:\Python")]) is None
    assert win.availability_error([Path(r"C:\Python")]) is None
    assert calls == [1]
    assert win.availability_error(None) == "The sandbox was not given the Python folders it needs to read."


def test_windows_acl_calls_match_their_declared_types():
    """The access-list arguments have to convert on any system, not only Windows."""

    import ctypes
    from ctypes import wintypes

    from aicad import sandbox_windows as win

    def invoke(name: str, *args: object) -> None:
        restype, argtypes = win._security_signatures()[name]
        prototype = ctypes.CFUNCTYPE(restype, *argtypes)

        def stub(*_got):
            return 0

        prototype(stub)(*args)

    info = win._AclSizeInformation()
    dacl = ctypes.c_void_p(1)
    ace = ctypes.c_void_p(2)
    sid = ctypes.c_void_p(3)
    handle = wintypes.HANDLE(4)
    descriptor = ctypes.c_void_p()
    out_dacl = ctypes.c_void_p()
    buffer = ctypes.create_string_buffer(64)
    security = ctypes.create_string_buffer(64)
    invoke("GetAclInformation", *win._acl_information_arguments(dacl, info))
    with pytest.raises(ctypes.ArgumentError):
        invoke(
            "GetAclInformation",
            dacl,
            ctypes.byref(info),
            ctypes.sizeof(info),
            win._AclSizeInformation,
        )
    invoke(
        "GetSecurityInfo",
        handle,
        win._SE_FILE_OBJECT,
        win._DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(out_dacl),
        None,
        ctypes.byref(descriptor),
    )
    invoke("GetAce", dacl, 0, ctypes.byref(ace))
    invoke("GetLengthSid", sid)
    invoke("InitializeAcl", buffer, len(buffer), int(win._ACL_REVISION))
    invoke("AddAce", buffer, int(win._ACL_REVISION), 0xFFFFFFFF, ace, 20)
    invoke("AddAccessAllowedAce", buffer, int(win._ACL_REVISION), int(win._TRAVERSE_MASK), sid)
    invoke("InitializeSecurityDescriptor", security, int(win._SECURITY_DESCRIPTOR_REVISION))
    invoke("SetSecurityDescriptorDacl", security, True, buffer, False)
    invoke("SetKernelObjectSecurity", handle, win._DACL_SECURITY_INFORMATION, security)
    assert int(win._ACL_SIZE_INFORMATION) == 2
    assert set(win._security_signatures()) == {
        "GetSecurityInfo",
        "SetKernelObjectSecurity",
        "GetAclInformation",
        "GetAce",
        "InitializeAcl",
        "AddAce",
        "AddAccessAllowedAce",
        "InitializeSecurityDescriptor",
        "SetSecurityDescriptorDacl",
        "GetLengthSid",
    }


def test_windows_launch_attributes_point_at_the_container_and_the_job():
    """CreateProcess has to receive the container, the job, and the handle list."""

    import ctypes
    from ctypes import wintypes

    from aicad import sandbox_windows as win

    class Adv:
        def ConvertStringSidToSidW(self, text, out):
            assert text == "S-1-15-3-1"
            out._obj.value = 0x99
            return 1

    rows, alive, capability = win._confinement_attributes(
        sid=0x1234,
        job=0x55,
        handles=(1, 2, 3),
        lpac=True,
        network=True,
        advapi32=Adv(),
    )
    assert alive
    assert capability.value == 0x99
    by_id = {attribute: (address, size) for attribute, address, size in rows}
    security_address, security_size = by_id[win._PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES]
    assert security_size == ctypes.sizeof(win._SecurityCapabilities)
    security = ctypes.cast(security_address, ctypes.POINTER(win._SecurityCapabilities)).contents
    assert security.AppContainerSid == 0x1234
    assert security.CapabilityCount == 1
    capability_row = ctypes.cast(security.Capabilities, ctypes.POINTER(win._SidAndAttributes)).contents
    assert capability_row.Sid == 0x99
    assert capability_row.Attributes == 0x4
    job_address, job_size = by_id[win._PROC_THREAD_ATTRIBUTE_JOB_LIST]
    assert job_size == ctypes.sizeof(wintypes.HANDLE)
    assert ctypes.cast(job_address, ctypes.POINTER(ctypes.c_void_p)).contents.value == 0x55
    handle_address, handle_size = by_id[win._PROC_THREAD_ATTRIBUTE_HANDLE_LIST]
    assert handle_size == ctypes.sizeof(wintypes.HANDLE) * 3
    handle_values = ctypes.cast(handle_address, ctypes.POINTER(ctypes.c_void_p * 3)).contents
    assert list(handle_values) == [1, 2, 3]
    policy_address, policy_size = by_id[win._PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY]
    assert policy_size == ctypes.sizeof(wintypes.DWORD)
    assert ctypes.cast(policy_address, ctypes.POINTER(wintypes.DWORD)).contents.value == (
        win._PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT
    )
    plain, _plain_alive, plain_capability = win._confinement_attributes(
        sid=0x1234,
        job=0x55,
        handles=(1, 2, 3),
        lpac=False,
        network=False,
    )
    assert plain_capability is None
    plain_security = ctypes.cast(plain[0][1], ctypes.POINTER(win._SecurityCapabilities)).contents
    assert plain_security.CapabilityCount == 0
    assert not plain_security.Capabilities
    assert win._PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY not in {row[0] for row in plain}

    restype, argtypes = win._update_attribute_signature()
    seen = []

    @ctypes.CFUNCTYPE(restype, *argtypes)
    def stub(attribute_list, flags, attribute, value, size, previous, returned):
        seen.append((attribute, value, size, previous, returned))
        return 1

    attribute_list = ctypes.create_string_buffer(64)
    win._apply_attributes(type("Kernel", (), {"UpdateProcThreadAttribute": staticmethod(stub)})(), attribute_list, rows)
    assert [item[0] for item in seen] == [row[0] for row in rows]
    assert all(item[1] for item in seen)
    assert [item[2] for item in seen] == [row[2] for row in rows]
    assert all(item[3] is None for item in seen)

    startup = win._startup_info(attribute_list, 1, 2, 3)
    assert startup.StartupInfo.cb == ctypes.sizeof(win._StartupInfoEx)
    assert startup.lpAttributeList == ctypes.addressof(attribute_list)
    flags = win._creation_flags(breakaway=True)
    assert flags & win._CREATE_SUSPENDED
    assert flags & win._EXTENDED_STARTUPINFO_PRESENT
    assert flags & win._CREATE_UNICODE_ENVIRONMENT
    assert flags & win._CREATE_BREAKAWAY_FROM_JOB
    plain_flags = win._creation_flags(breakaway=False)
    assert plain_flags & win._CREATE_SUSPENDED
    assert not plain_flags & win._CREATE_BREAKAWAY_FROM_JOB


def test_windows_suspended_process_is_resumed_only_in_the_job_and_container():
    """A suspended process that missed the job or the container is not resumed."""

    import ctypes

    from aicad import sandbox_windows as win

    class Api:
        def __init__(self, *, assign=True, in_job=False, open_token=True, container=1, resume=1):
            self.calls = []
            self.assign = assign
            self.in_job = in_job
            self.open_token = open_token
            self.container = container
            self.resume = resume

        def AssignProcessToJobObject(self, job, process):
            self.calls.append("AssignProcessToJobObject")
            return int(self.assign)

        def IsProcessInJob(self, process, job, flag):
            self.calls.append("IsProcessInJob")
            flag._obj.value = int(self.in_job)
            return 1

        def OpenProcessToken(self, process, access, token):
            self.calls.append("OpenProcessToken")
            if not self.open_token:
                return 0
            token._obj.value = 42
            return 1

        def GetTokenInformation(self, token, info_class, info, length, returned):
            self.calls.append("GetTokenInformation")
            assert info_class == win._TokenIsAppContainer
            info._obj.value = self.container
            returned._obj.value = 4
            return 1

        def CloseHandle(self, handle):
            self.calls.append("CloseHandle")
            return 1

        def TerminateProcess(self, process, code):
            self.calls.append("TerminateProcess")
            return 1

        def ResumeThread(self, thread):
            self.calls.append("ResumeThread")
            return self.resume

    process = win._ProcessInformation()
    process.hProcess = 7
    process.hThread = 8

    ready = Api()
    win._commit_suspended(ready, ready, 0x55, process)
    assert ready.calls.index("AssignProcessToJobObject") < ready.calls.index("ResumeThread")
    assert "TerminateProcess" not in ready.calls
    assert "IsProcessInJob" not in ready.calls

    already = Api(assign=False, in_job=True)
    win._commit_suspended(already, already, 0x55, process)
    assert already.calls.index("IsProcessInJob") < already.calls.index("ResumeThread")
    assert "TerminateProcess" not in already.calls

    missed_job = Api(assign=False, in_job=False)
    with pytest.raises(OSError, match="not in the Job Object"):
        win._commit_suspended(missed_job, missed_job, 0x55, process)
    assert "ResumeThread" not in missed_job.calls
    assert missed_job.calls[-1] == "TerminateProcess"
    assert "OpenProcessToken" not in missed_job.calls

    missed_container = Api(container=0)
    with pytest.raises(OSError, match="not an AppContainer"):
        win._commit_suspended(missed_container, missed_container, 0x55, process)
    assert "ResumeThread" not in missed_container.calls
    assert "TerminateProcess" in missed_container.calls

    unread_token = Api(open_token=False)
    with pytest.raises(OSError, match="not started"):
        win._commit_suspended(unread_token, unread_token, 0x55, process)
    assert "ResumeThread" not in unread_token.calls

    stuck = Api(resume=0xFFFFFFFF)
    with pytest.raises(OSError, match="Could not resume"):
        win._commit_suspended(stuck, stuck, 0x55, process)
    assert stuck.calls.index("ResumeThread") < stuck.calls.index("TerminateProcess")

    class Creator:
        def __init__(self, codes):
            self.codes = list(codes)
            self.flags = []

        def CreateProcessW(self, *args):
            self.flags.append(args[5])
            code = self.codes.pop(0)
            self.last_error = code
            return code == 0

    denied = Creator([win._ERROR_ACCESS_DENIED, 0])
    win._start_suspended(
        denied,
        "python.exe",
        ctypes.create_unicode_buffer("python.exe"),
        ctypes.create_string_buffer(4),
        "C:\\work",
        win._StartupInfoEx(),
        win._ProcessInformation(),
    )
    assert denied.flags[0] & win._CREATE_BREAKAWAY_FROM_JOB
    assert denied.flags[0] & win._CREATE_SUSPENDED
    assert denied.flags[0] & win._EXTENDED_STARTUPINFO_PRESENT
    assert not denied.flags[1] & win._CREATE_BREAKAWAY_FROM_JOB
    assert denied.flags[1] & win._CREATE_SUSPENDED

    other = Creator([87])
    with pytest.raises(OSError, match="Could not start"):
        win._start_suspended(
            other,
            "python.exe",
            ctypes.create_unicode_buffer("python.exe"),
            ctypes.create_string_buffer(4),
            "C:\\work",
            win._StartupInfoEx(),
            win._ProcessInformation(),
        )
    assert len(other.flags) == 1


def test_windows_script_starts_from_the_base_interpreter(monkeypatch):
    from aicad import sandbox_windows as win

    venv = r"D:\a\ai-first-cad\.venv\Scripts\python.exe"
    base = r"C:\hostedtoolcache\windows\Python\3.11.9\x64\python.exe"
    monkeypatch.setattr(sys, "executable", venv)
    monkeypatch.setattr(sys, "_base_executable", base, raising=False)
    command, env = win._command_for_container([venv, "-P", "-c", "print(1)"], {"PATH": "x"})
    assert command == [base, "-P", "-c", "print(1)"]
    assert env["__PYVENV_LAUNCHER__"] == venv
    assert env["PATH"] == "x"
    same, same_env = win._command_for_container([base, "-c", "print(1)"], {})
    assert same == [base, "-c", "print(1)"]
    assert "__PYVENV_LAUNCHER__" not in same_env
    other, other_env = win._command_for_container([r"C:\Tools\other.exe"], {})
    assert other == [r"C:\Tools\other.exe"]
    assert "__PYVENV_LAUNCHER__" not in other_env


def test_windows_probe_requires_the_confinement_check():
    from aicad import sandbox_windows as win

    source = win._probe_source()
    assert "identity_failures" in source
    assert "sandbox-ok" in source
    assert "if failed" in source
    assert str(win._PROBE_MEMORY_BYTES) in source


def _socket_error(errno=None, winerror=None, *, timeout=False, dns=False):
    import socket

    if dns:
        error = socket.gaierror(errno if errno is not None else 11001, "dns")
    elif timeout:
        error = TimeoutError(errno if errno is not None else 10060, "timed out")
    else:
        error = OSError(errno if errno is not None else 0, "probe")
    if winerror is not None:
        error.winerror = winerror
    if errno is not None:
        error.errno = errno
    return error


def test_windows_network_refusal_is_not_a_timeout_or_a_dns_failure():
    """The script runs only when both probes were refused and the token has no network capability."""

    import inspect

    from aicad.sandbox import (
        _NETWORK_CAPABILITY_SIDS,
        _WINDOWS_TCP_PROBE,
        _WINDOWS_UDP_PROBE,
        _public_probe_address,
        apply_sandbox,
        confined_errno,
        network_attempt_label,
        windows_network_failure_text,
        windows_network_is_off,
    )

    assert _WINDOWS_TCP_PROBE == ("1.1.1.1", 443)
    assert _WINDOWS_UDP_PROBE == ("1.1.1.1", 53)
    assert _public_probe_address("1.1.1.1")
    assert not _public_probe_address("127.0.0.1")
    assert not _public_probe_address("localhost")
    assert not _public_probe_address("::1")
    assert _NETWORK_CAPABILITY_SIDS == frozenset({"S-1-15-3-1", "S-1-15-3-2", "S-1-15-3-3"})

    for winerror, errno in ((5, 5), (10013, 13), (10051, 10051), (None, 1), (None, 13), (None, 101)):
        error = _socket_error(errno, winerror)
        assert confined_errno(error)
        assert network_attempt_label(error, success="connected").startswith("denied ")

    timeout = _socket_error(10060, 10060, timeout=True)
    host = _socket_error(10065, 10065)
    dns = _socket_error(11001, 11001, dns=True)
    assert not confined_errno(host)
    assert network_attempt_label(timeout, success="connected").startswith("timeout ")
    assert "10060" in network_attempt_label(timeout, success="connected")
    assert network_attempt_label(host, success="connected").startswith("error ")
    assert "10065" in network_attempt_label(host, success="sent")
    assert network_attempt_label(dns, success="connected").startswith("dns ")
    assert network_attempt_label(None, success="connected") == "connected"
    assert network_attempt_label(None, success="sent") == "sent"
    # A timeout that also carries a denial errno is still a timeout.
    mixed = _socket_error(13, 10060, timeout=True)
    assert network_attempt_label(mixed, success="connected").startswith("timeout ")

    denied = network_attempt_label(_socket_error(13, 10013), success="connected")
    denied_udp = network_attempt_label(_socket_error(13, 10013), success="sent")
    package = "S-1-15-2-11-22-33-44-55-66-77"
    assert windows_network_is_off(tcp=denied, udp=denied_udp, package=package, capabilities=())
    assert not windows_network_is_off(
        tcp=network_attempt_label(timeout, success="connected"),
        udp=denied_udp,
        package=package,
        capabilities=(),
    )
    assert not windows_network_is_off(
        tcp=denied,
        udp="sent",
        package=package,
        capabilities=(),
    )
    assert not windows_network_is_off(tcp="loopback", udp=denied_udp, package=package, capabilities=())
    assert not windows_network_is_off(tcp=denied, udp=denied_udp, package=package, capabilities=None)
    assert not windows_network_is_off(tcp=denied, udp=denied_udp, package="unread", capabilities=())
    for capability in ("S-1-15-3-1", "s-1-15-3-2", "S-1-15-3-3"):
        assert not windows_network_is_off(
            tcp=denied,
            udp=denied_udp,
            package=package,
            capabilities=(capability,),
        )
    text = windows_network_failure_text(
        tcp=network_attempt_label(timeout, success="connected"),
        udp="sent",
        package=package,
        capabilities=("S-1-15-3-1",),
    )
    assert text.startswith("Windows did not turn the network off, so the script was not run.")
    assert "probe=1.1.1.1:443,1.1.1.1:53" in text
    assert "tcp=timeout " in text
    assert "winerror=10060" in text
    assert "udp=sent" in text
    assert "package=" + package in text
    assert "capabilities=S-1-15-3-1" in text
    assert "capabilities=none" in windows_network_failure_text(
        tcp=denied, udp=denied_udp, package=package, capabilities=()
    )
    assert "capabilities=unread" in windows_network_failure_text(
        tcp=denied, udp=denied_udp, package=package, capabilities=None
    )

    source = inspect.getsource(apply_sandbox)
    windows = source.split('sys.platform == "win32"', 1)[1].split('sys.platform == "darwin"', 1)[0]
    assert windows.index("identity_failures") < windows.index("windows_network_is_off")
    assert "_network_blocked" not in windows


def test_windows_network_block_names_only_that_container():
    """The firewall block is the package SID, and it stays until the last network-off child exits."""

    import inspect

    from aicad import sandbox_windows as win

    package = "S-1-15-2-11-22-33-44-55-66-77"
    script = win._firewall_script(package, install=True)
    assert script.count("New-NetFirewallRule") == 2
    assert script.count("-Action Block") == 2
    assert "-Direction Outbound" in script
    assert "-Direction Inbound" in script
    assert f"-Package '{package}'" in script
    assert "S-1-15-3-1" not in script
    assert "Remove-NetFirewallRule" in script
    removal = win._firewall_script(install=False)
    assert "New-NetFirewallRule" not in removal
    assert "Remove-NetFirewallRule" in removal
    for bad in ("S-1-15-3-1", "S-1-15-2-1'; Remove-Item", "S-1-5-18", ""):
        with pytest.raises(OSError, match="not a package SID"):
            win._firewall_script(bad, install=True)

    calls = []

    def record(text):
        calls.append(text)

    win._network_off_holders = 0
    win._network_off_sid = None
    original = win._run_firewall_script
    win._run_firewall_script = record
    try:
        win._hold_network_block(package.lower())
        win._hold_network_block(package)
        assert len(calls) == 1
        assert win._network_off_holders == 2
        with pytest.raises(OSError, match="still has the network blocked"):
            win._require_script_network_open()
        assert len(calls) == 1
        with pytest.raises(OSError, match="still has the network blocked"):
            win._hold_network_block("S-1-15-2-9-9-9-9-9-9-9")
        win._release_network_block()
        assert len(calls) == 1
        win._release_network_block()
        assert len(calls) == 2
        assert "New-NetFirewallRule" not in calls[1]
        assert win._network_off_holders == 0
        win._require_script_network_open()
        assert len(calls) == 3
    finally:
        win._run_firewall_script = original
        win._network_off_holders = 0
        win._network_off_sid = None

    spawn = inspect.getsource(win.spawn)
    assert spawn.index("_hold_network_block") < spawn.index("_launch")
    assert spawn.index("TerminateJobObject") < spawn.index("_release_network_block")
    probe = inspect.getsource(win._probe_mode)
    assert probe.index("_hold_network_block") < probe.index("_launch")


def test_windows_token_capability_list_keeps_network_sids():
    """The capability reader returns the SIDs the token actually carries."""

    from aicad import sandbox_windows as win

    def pack(pointers):
        raw = len(pointers).to_bytes(4, "little") + b"\x00" * 4
        for pointer in pointers:
            raw += int(pointer).to_bytes(8, "little") + b"\x00" * 8
        return raw

    names = {10: "S-1-15-3-1", 11: "S-1-15-3-2", 12: "S-1-15-3-9"}
    found = win._token_group_sids(pack([10, 0, 11, 12]), lambda pointer: names[pointer])
    assert found == ["S-1-15-3-1", "S-1-15-3-2", "S-1-15-3-9"]
    assert win._token_group_sids(pack([]), lambda pointer: pointer) == []
    with pytest.raises(OSError, match="cut off"):
        win._token_group_sids(pack([10])[:16], lambda pointer: "S-1-15-3-1")


def test_windows_script_keeps_library_caches_in_the_project(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from aicad import sandbox as sandbox_mod

    font_cache = tmp_path / "font-cache"
    font_file = font_cache / "ezdxf" / "font_manager_cache.json"
    font_file.parent.mkdir(parents=True)
    font_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sandbox_mod, "_windows_font_cache_home", lambda: font_cache)
    env = sandbox_mod._child_env(tmp_path, tmp_path / "job", False)
    cache = str(tmp_path / ".aicad" / "cache")
    assert env["XDG_CACHE_HOME"] == str(font_cache)
    assert env["XDG_CONFIG_HOME"] == cache
    assert env["XDG_DATA_HOME"] == cache
    assert env["MPLCONFIGDIR"] == cache
    assert env.get("USERPROFILE") != cache
    assert env.get("APPDATA") != cache
    assert env.get("LOCALAPPDATA") != cache
    assert env.get("HOME") != cache


def test_opening_a_session_does_not_run_its_script(tmp_path, config_home):
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "evil",
                "source": "script",
                "script": "open('marker.txt', 'w').write('ran')\npart = Box(1, 1, 1)\n",
            }
        ],
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    document = Document(tmp_path)
    assert not (tmp_path / "marker.txt").exists()
    info = document.list_parts()[0]
    assert info.name == "evil"
    assert info.built is False
    source = document.get_source("evil")
    assert source["script_pending"] is True
    assert "Box" in source["script"]
    with pytest.raises(Exception, match="has not been run"):
        document.measure("evil")


def test_script_geometry_reopens_without_running_again(tmp_path, config_home):
    document = Document(tmp_path)
    script = 'block = feature.add("base", Box(4, 4, 4))\npart = block\n'
    document.create_part_from_script("block", script)
    assert document.feature_report("block").tracked is True
    assert document.feature_report("block").steps[0].name == "base"
    reopened = Document(tmp_path)
    assert not (tmp_path / "marker.txt").exists()
    report = reopened.feature_report("block")
    assert report.tracked is True
    assert report.steps[0].name == "base"
    assert reopened.measure("block").volume_mm3 == pytest.approx(64)
    assert reopened.get_source("block")["script_pending"] is False


def test_open_project_loads_script_text_and_does_not_run_it(tmp_path, config_home):
    other = tmp_path / "other"
    other.mkdir()
    session = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "evil",
                "source": "script",
                "script": "open('marker.txt', 'w').write('ran')\npart = Box(1, 1, 1)\n",
            }
        ],
    }
    (other / "session.json").write_text(json.dumps(session), encoding="utf-8")
    workspace = Workspace(tmp_path / "here")
    opened = workspace.execute("open_project", {"path": str(other)})
    assert opened["parts"] == ["evil"]
    assert workspace.document.list_parts()[0].built is False
    assert not (other / "marker.txt").exists()
    saved = workspace.execute("save_project", {})
    saved_path = Path(saved["path"])
    session_file = saved_path if saved_path.name == "session.json" else saved_path / "session.json"
    assert session_file.is_file()
    assert workspace.execute("design_script_status", {})["enabled"] is False


def test_design_script_cannot_allow_a_plugin(tmp_path, config_home, monkeypatch):
    consent = tmp_path / "consent" / "plugins.json"
    monkeypatch.setenv("AICAD_PLUGIN_CONSENT", str(consent))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    allow_design_scripts(tmp_path)
    script = """
import aicad.plugins as plugins
print("consent_path", plugins.consent_file())
try:
    plugins.allow("anything", accept=True)
    print("allow ran")
except Exception as exc:
    print("allow", type(exc).__name__)
    print(exc)
try:
    plugins.load("anything")
    print("load ran")
except Exception as exc:
    print("load", type(exc).__name__)
    print(exc)
try:
    plugins.revoke("anything")
    print("revoke ran")
except Exception as exc:
    print("revoke", type(exc).__name__)
    print(exc)
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload.get("error")
    text = payload["stdout"]
    assert "consent_path" in text
    assert str(tmp_path) not in text.split("consent_path", 1)[1].splitlines()[0]
    for name in ("allow", "load", "revoke"):
        assert f"{name} PluginConsentError" in text
    assert "cannot" in text
    assert not consent.exists()
    assert not (tmp_path / "xdg" / "aicad" / "plugins.json").exists()


def test_design_script_requires_opt_in(tmp_path, config_home):
    with pytest.raises(ScriptError, match="allow-scripts"):
        run_design_script(tmp_path, "print('no')\n")
    assert not script_log_path(tmp_path).exists()


def test_import_and_paths_are_refused(tmp_path, config_home):
    allow_design_scripts(tmp_path)
    script = """
errors = []
try:
    import os
except Exception as exc:
    errors.append(str(exc))
try:
    open("/etc/passwd", encoding="utf-8")
except Exception as exc:
    errors.append(str(exc))
try:
    open("/tmp/aicad-sandbox-outside.txt", "w", encoding="utf-8")
except Exception as exc:
    errors.append(str(exc))
open("inside.txt", "w", encoding="utf-8").write("ok")
print(" | ".join(errors))
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload
    text = payload["stdout"]
    assert "not allowed" in text
    assert "outside the project" in text
    assert "/etc/passwd" in text
    assert "/tmp/aicad-sandbox-outside.txt" in text
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8") == "ok"
    assert not Path("/tmp/aicad-sandbox-outside.txt").exists()
    log = script_log_path(tmp_path).read_text(encoding="utf-8")
    assert "import os" in log
    assert '"ok": true' in log


# EPERM, EACCES, ENETUNREACH, and the Windows access-denied socket codes.
# ENOENT (2) is a missing path, not a sandbox.
_CONFINED_ERRNOS = {1, 13, 101, 10013, 10051}


def _assert_confined(label: str) -> None:
    assert label.startswith("denied:"), label
    code = int(label.rsplit(":", 1)[1])
    assert code in _CONFINED_ERRNOS, label


@pytest.mark.sandbox
def test_os_probe_blocks_network_and_outside_files(tmp_path, config_home):
    probe = os_probe(tmp_path)
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["write_outside"])
    assert probe["write_inside"] == "ok"
    _assert_confined(probe["tcp"])
    _assert_confined(probe["udp"])
    if sys.platform.startswith("linux"):
        assert probe["mechanism"] == "landlock+seccomp"
    elif sys.platform == "darwin":
        assert probe["mechanism"] == "seatbelt"
    elif sys.platform == "win32":
        assert probe["mechanism"] == "appcontainer+job"


@pytest.mark.sandbox_linux
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux Landlock sandbox")
def test_linux_sandbox_is_landlock_and_seccomp(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["mechanism"] == "landlock+seccomp"
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["udp"])


@pytest.mark.sandbox_macos
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt sandbox")
def test_macos_sandbox_is_seatbelt(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["mechanism"] == "seatbelt"
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["tcp"])


@pytest.mark.sandbox_windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer sandbox")
def test_windows_sandbox_is_an_appcontainer(tmp_path, config_home):
    probe = os_probe(tmp_path)
    assert probe["mechanism"] == "appcontainer+job"
    _assert_confined(probe["read_outside"])
    _assert_confined(probe["tcp"])


@pytest.mark.sandbox_windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows process rights")
def test_windows_script_cannot_open_its_parent(tmp_path, config_home):
    """Direct OpenProcess / TerminateProcess on this aicad process is denied."""
    script = (
        "import ctypes\n"
        "from ctypes import wintypes\n"
        "import aicad.sandbox as sandbox\n"
        "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]\n"
        "kernel.OpenProcess.restype = wintypes.HANDLE\n"
        "kernel.CloseHandle.argtypes = [wintypes.HANDLE]\n"
        "rights = 0x0001 | 0x1000\n"
        "handle = kernel.OpenProcess(rights, False, sandbox.os.getppid())\n"
        "if handle:\n"
        "    kernel.CloseHandle(handle)\n"
        "    open('open.txt', 'w', encoding='utf-8').write('opened')\n"
        "else:\n"
        "    open('open.txt', 'w', encoding='utf-8').write('denied:%s' % ctypes.get_last_error())\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(script, tmp_path)
    assert (tmp_path / "open.txt").read_text(encoding="utf-8").startswith("denied")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux cgroup pids controller")
def test_linux_process_cap_is_a_cgroup_when_this_user_can_make_one():
    """The cap has to be a sibling cgroup. The user-wide limit counts other programs."""
    from aicad.sandbox import _PROCESS_CAP, _prepare_pids_cgroup, _release_pids_cgroup

    path = _prepare_pids_cgroup()
    if path is None:
        pytest.skip("this user cannot create a cgroup pids.max without root")
    try:
        assert (path / "pids.max").read_text(encoding="utf-8").strip() == str(_PROCESS_CAP)
        assert path.name not in Path("/proc/self/cgroup").read_text(encoding="utf-8")
    finally:
        _release_pids_cgroup(path)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux counts every user thread toward RLIMIT_NPROC")
def test_script_finishes_while_the_user_already_has_many_threads(tmp_path):
    """A desktop with a browser open is already past 256 threads. Geometry still has to finish."""
    from aicad.kernel import measure

    stop = threading.Event()
    threads: list[threading.Thread] = []

    def park() -> None:
        stop.wait()

    try:
        for _ in range(300):
            thread = threading.Thread(target=park, daemon=True)
            try:
                thread.start()
            except RuntimeError:
                pytest.skip("this user cannot start 300 extra threads")
            threads.append(thread)
        shape = run_part_script(
            "text = open('/proc/self/limits', encoding='utf-8').read()\n"
            "open('limits.txt', 'w', encoding='utf-8').write(text)\n"
            "part = Box(1, 1, 1)\n",
            tmp_path,
        )
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=1)
    assert measure(shape).volume_mm3 == pytest.approx(1)
    soft = None
    for line in (tmp_path / "limits.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("Max processes"):
            soft = line.split()[2]
            break
    assert soft is not None
    assert soft == "unlimited" or int(soft) > 256


def test_wall_clock_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "2")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    with pytest.raises(ScriptError, match="longer than 2 seconds"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_cpu_or_memory_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "1")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "30")
    with pytest.raises(ScriptError, match="memory or CPU"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_memory_limit_stops_a_huge_allocation(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_MEMORY_MB", "256")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "60")
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "30")
    with pytest.raises(ScriptError, match="MemoryError|memory or CPU"):
        run_part_script("blob = bytearray(800 * 1024 * 1024)\npart = Box(1, 1, 1)\n", tmp_path)


def test_design_script_reports_the_change_and_the_check(tmp_path, config_home):
    allow_design_scripts(tmp_path)
    script = """
document.create_part_from_script("plate", "part = Box(20, 10, 1)\\n")
wall = document.check_wall_thickness("plate", 2)
print(wall.passed)
document.update_part_script("plate", "part = Box(20, 10, 4)\\n")
wall = document.check_wall_thickness("plate", 2)
print(wall.passed)
"""
    payload = run_design_script(tmp_path, script)
    assert payload["ok"] is True, payload.get("error")
    assert payload["changed_parts"]
    assert payload["changed_parts"][0]["name"] == "plate"
    assert payload["changed_parts"][0]["volume_mm3"] == pytest.approx(800)
    methods = [item["method"] for item in payload["checks"]]
    assert methods.count("check_wall_thickness") == 2
    assert "False" in payload["stdout"]
    assert "True" in payload["stdout"]
    assert payload["views"]
    assert Path(payload["views"][0]["path"]).is_file()
    reopened = Document(tmp_path)
    assert reopened.measure("plate").volume_mm3 == pytest.approx(800)


def test_network_switch_is_not_an_assistant_command(tmp_path, config_home, monkeypatch):
    workspace = Workspace(tmp_path)
    assert workspace.script_status()["network"] == "off"
    workspace.allow_script_network_once()
    assert workspace.script_status()["network"] == "next run"
    assert workspace.consume_script_network() is True
    assert workspace.consume_script_network() is False
    monkeypatch.setenv("AICAD_SCRIPT_NETWORK", "1")
    always = Workspace(tmp_path)
    assert always.script_status()["network"] == "on"
    assert "allow_script_network_once" not in COMMANDS


def test_unexpected_errors_become_tool_errors(tmp_path, config_home):
    from mcp.server.mcpserver.exceptions import ToolError

    saved = COMMANDS["list_parts"]
    COMMANDS["list_parts"] = replace(saved, handler=lambda _workspace, _arguments: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        server = build_server(tmp_path)
        with pytest.raises(ToolError, match="RuntimeError: boom"):
            asyncio.run(server.call_tool("list_parts", {}))
    finally:
        COMMANDS["list_parts"] = saved


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux Landlock sandbox")
def test_script_cannot_create_special_files_outside_the_project(tmp_path):
    """A symlink, named pipe, or socket node outside the project is a write escape.

    Landlock only blocks the file actions it is told to handle. Leaving out
    the special-file actions makes mkfifo, symlink, and a socket node succeed
    in a directory the script can write, such as /tmp.
    """
    import os
    import uuid

    tag = uuid.uuid4().hex
    fifo = f"/tmp/aicad-outside-fifo-{tag}"
    link = f"/tmp/aicad-outside-link-{tag}"
    sock = f"/tmp/aicad-outside-sock-{tag}"
    script = f"""
import aicad.sandbox as sandbox
notes = []
def attempt(label, action):
    try:
        action()
        notes.append(label + ":created")
    except OSError as exc:
        notes.append(label + ":denied:%s" % exc.errno)
attempt("fifo", lambda: sandbox.os.mkfifo({fifo!r}))
attempt("symlink", lambda: sandbox.os.symlink("/etc/passwd", {link!r}))
attempt("socket", lambda: sandbox.os.mknod({sock!r}, 0o140666))
sandbox.os.mkfifo("inside.fifo")
notes.append("inside:" + ("created" if sandbox.os.path.exists("inside.fifo") else "missing"))
open("nodes.txt", "w", encoding="utf-8").write("\\n".join(notes))
part = Box(1, 1, 1)
"""
    try:
        run_part_script(script, tmp_path)
        text = (tmp_path / "nodes.txt").read_text(encoding="utf-8")
        assert "fifo:denied:" in text
        assert "symlink:denied:" in text
        assert "socket:denied:" in text
        assert "inside:created" in text
        assert ":created" not in text.replace("inside:created", "")
        assert not os.path.exists(fifo)
        assert not os.path.lexists(link)
        assert not os.path.exists(sock)
    finally:
        for path in (fifo, link, sock):
            try:
                os.remove(path)
            except OSError:
                pass


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux seccomp sandbox")
def test_script_cannot_send_udp_through_io_uring(tmp_path):
    """io_uring can socket() and sendto() without those syscalls.

    Landlock blocks TCP only. A UDP packet sent through io_uring leaves the
    machine when the socket filter does not cover io_uring.
    """
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    script = f"""
import aicad.sandbox as sandbox
ctypes = sandbox.ctypes
os = sandbox.os
mmap = sandbox._REAL_IMPORT("mmap")
socket = sandbox._REAL_IMPORT("socket")
struct = sandbox._REAL_IMPORT("struct")
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long

class SqOff(ctypes.Structure):
    _fields_ = [("head", ctypes.c_uint32), ("tail", ctypes.c_uint32), ("ring_mask", ctypes.c_uint32),
                ("ring_entries", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("dropped", ctypes.c_uint32),
                ("array", ctypes.c_uint32), ("resv1", ctypes.c_uint32), ("user_addr", ctypes.c_uint64)]

class CqOff(ctypes.Structure):
    _fields_ = [("head", ctypes.c_uint32), ("tail", ctypes.c_uint32), ("ring_mask", ctypes.c_uint32),
                ("ring_entries", ctypes.c_uint32), ("overflow", ctypes.c_uint32), ("cqes", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("resv1", ctypes.c_uint32), ("user_addr", ctypes.c_uint64)]

class Params(ctypes.Structure):
    _fields_ = [("sq_entries", ctypes.c_uint32), ("cq_entries", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("sq_thread_cpu", ctypes.c_uint32), ("sq_thread_idle", ctypes.c_uint32), ("features", ctypes.c_uint32),
                ("wq_fd", ctypes.c_uint32), ("resv", ctypes.c_uint32 * 3), ("sq_off", SqOff), ("cq_off", CqOff)]

def attempt():
    params = Params()
    ring = libc.syscall(425, 8, ctypes.byref(params))
    if ring < 0:
        return "setup-denied:%s" % ctypes.get_errno()
    try:
        sq_sz = params.sq_off.array + params.sq_entries * 4
        cq_sz = params.cq_off.cqes + params.cq_entries * 16
        sqe_sz = params.sq_entries * 64
        sq = mmap.mmap(ring, sq_sz, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0)
        cq = mmap.mmap(ring, cq_sz, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x8000000)
        sqes = mmap.mmap(ring, sqe_sz, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x10000000)
        def submit(opcode, fd, off, addr, length):
            mask = struct.unpack_from("I", sq, params.sq_off.ring_mask)[0]
            tail = struct.unpack_from("I", sq, params.sq_off.tail)[0]
            index = tail & mask
            slot = index * 64
            raw = bytearray(sqe_sz)
            struct.pack_into("<BBHI", raw, slot, opcode, 0, 0, fd & 0xFFFFFFFF)
            struct.pack_into("<Q", raw, slot + 8, off & 0xFFFFFFFFFFFFFFFF)
            struct.pack_into("<Q", raw, slot + 16, addr & 0xFFFFFFFFFFFFFFFF)
            struct.pack_into("<I", raw, slot + 24, length & 0xFFFFFFFF)
            struct.pack_into("<Q", raw, slot + 32, 1)
            sqes.seek(0)
            sqes.write(raw)
            struct.pack_into("I", sq, params.sq_off.array + index * 4, index)
            struct.pack_into("I", sq, params.sq_off.tail, (tail + 1) & 0xFFFFFFFF)
            entered = libc.syscall(426, ring, 1, 1, 0, 0)
            if entered < 0:
                return "enter-denied:%s" % ctypes.get_errno()
            head = struct.unpack_from("I", cq, params.cq_off.head)[0]
            cmask = struct.unpack_from("I", cq, params.cq_off.ring_mask)[0]
            cqe_off = params.cq_off.cqes + (head & cmask) * 16
            _user, res, _flags = struct.unpack_from("<QiI", cq, cqe_off)
            struct.pack_into("I", cq, params.cq_off.head, head + 1)
            return res
        sock = submit(45, socket.AF_INET, socket.SOCK_DGRAM, 0, 0)
        if isinstance(sock, str) or sock < 0:
            return "socket:%s" % sock
        addr = struct.pack("=HH4s8s", socket.AF_INET, socket.htons({port}), socket.inet_aton("127.0.0.1"), b"\\0" * 8)
        buf = ctypes.create_string_buffer(addr)
        conn = submit(16, int(sock), len(addr), ctypes.addressof(buf), 0)
        if isinstance(conn, str) or (isinstance(conn, int) and conn < 0):
            return "connect:%s" % conn
        payload = ctypes.create_string_buffer(b"iouring-udp")
        sent = submit(26, int(sock), 0, ctypes.addressof(payload), len(b"iouring-udp"))
        return "sent:%s" % sent
    finally:
        os.close(ring)

open("iouring.txt", "w", encoding="utf-8").write(attempt())
part = Box(1, 1, 1)
"""
    try:
        run_part_script(script, tmp_path)
        note = (tmp_path / "iouring.txt").read_text(encoding="utf-8")
        assert not note.startswith("sent"), note
        listener.settimeout(0.2)
        with pytest.raises(TimeoutError):
            listener.recv(64)
    finally:
        listener.close()
