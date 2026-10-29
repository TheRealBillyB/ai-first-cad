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


def test_a_traverse_grant_does_not_write_after_its_timeout(tmp_path, monkeypatch):
    import time

    from aicad import sandbox_windows as win

    monkeypatch.setattr(win, "_GRANT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(win, "_parent_allows_traverse", lambda path, sid, package_sid: False)
    marker = tmp_path / "wrote"

    def slow_write(path, sid):
        time.sleep(1.0)
        try:
            stopped = win._grant_was_stopped()
        except AttributeError:
            stopped = False
        if not stopped:
            marker.write_text("wrote", encoding="utf-8")

    win._granted_traverse.clear()
    monkeypatch.setattr(win, "_write_traverse_ace", slow_write)
    started = time.monotonic()
    with pytest.raises(win.GrantTimeout, match="did not finish"):
        win._grant_traverse(Path(r"C:\hostedtoolcache\windows"), "S-1-2-3", "S-1-15-2-2")
    assert time.monotonic() - started < 3
    time.sleep(1.2)
    assert not marker.exists()


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

    class Counting(Api):
        def QueryInformationJobObject(self, job, klass, info, size, returned):
            self.calls.append("QueryInformationJobObject")
            return 0

    counting = Counting()
    assert win._commit_suspended(counting, counting, 0x55, process) is None
    assert counting.calls.index("QueryInformationJobObject") < counting.calls.index("ResumeThread")

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


def test_a_stuck_whoami_does_not_hold_the_parent(monkeypatch):
    import subprocess

    from aicad import sandbox_windows as win

    monkeypatch.setattr(win, "_user_sid", None)
    monkeypatch.setattr(win, "_user_sid_checked", False)
    calls = {"n": 0}

    def stuck(*_args, **_kwargs):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd="whoami", timeout=10)

    monkeypatch.setattr(win.subprocess, "run", stuck)
    assert win._current_user_sid() is None
    assert win._current_user_sid() is None
    assert calls["n"] == 1

    monkeypatch.setattr(win, "_user_sid", None)
    monkeypatch.setattr(win, "_user_sid_checked", False)

    def missing(*_args, **_kwargs):
        raise OSError("whoami missing")

    monkeypatch.setattr(win.subprocess, "run", missing)
    assert win._current_user_sid() is None

    monkeypatch.setattr(win, "_user_sid", None)
    monkeypatch.setattr(win, "_user_sid_checked", False)
    seen: dict = {}

    class Completed:
        returncode = 0
        stdout = '"computer\\user","S-1-5-21-1"\n'

    def ok(command, **kwargs):
        seen["command"] = command
        seen["timeout"] = kwargs.get("timeout")
        seen["stdin"] = kwargs.get("stdin")
        return Completed()

    monkeypatch.setattr(win.subprocess, "run", ok)
    assert win._current_user_sid() == "S-1-5-21-1"
    assert seen["command"][0] == "whoami"
    assert seen["timeout"] == 10
    assert seen["stdin"] is subprocess.DEVNULL


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


def test_windows_network_refusal_is_not_a_timeout_or_a_dns_failure(monkeypatch):
    """The script runs only when both probes were refused and the token has no network capability."""

    import inspect

    from aicad.sandbox import (
        _NETWORK_CAPABILITY_SIDS,
        _WINDOWS_TCP_PROBE,
        _WINDOWS_UDP_PROBE,
        _public_probe_address,
        _windows_one_attempt,
        apply_sandbox,
        confined_errno,
        network_attempt_label,
        windows_network_failure_text,
        windows_network_is_off,
        windows_receiver_targets,
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
    # WSAEINVAL is not a refusal. A blocked implicit bind can report it, and
    # the datagram was not sent, but the check still does not treat it as off.
    invalid = _socket_error(22, 10022)
    assert not confined_errno(invalid)
    assert network_attempt_label(invalid, success="sent").startswith("error ")
    assert "10022" in network_attempt_label(invalid, success="sent")
    # A timeout that also carries a denial errno is still a timeout.
    mixed = _socket_error(13, 10060, timeout=True)
    assert network_attempt_label(mixed, success="connected").startswith("timeout ")

    denied = network_attempt_label(_socket_error(13, 10013), success="connected")
    denied_udp = network_attempt_label(_socket_error(13, 10013), success="sent")
    package = "S-1-15-2-11-22-33-44-55-66-77"
    refused = (
        ("tcp", denied),
        ("udp-sendto", denied_udp),
        ("udp-connected", denied_udp),
    )
    assert windows_network_is_off(attempts=refused, package=package, capabilities=())
    assert not windows_network_is_off(
        attempts=(
            ("tcp", network_attempt_label(timeout, success="connected")),
            ("udp-sendto", denied_udp),
            ("udp-connected", denied_udp),
        ),
        package=package,
        capabilities=(),
    )
    # A successful send is on, connected or unconnected. An empty receiver
    # does not make that send a refusal.
    assert not windows_network_is_off(
        attempts=(("tcp", denied), ("udp-sendto", "sent"), ("udp-connected", denied_udp)),
        package=package,
        capabilities=(),
    )
    assert not windows_network_is_off(
        attempts=(("tcp", denied), ("udp-sendto", denied_udp), ("udp-connected", "sent")),
        package=package,
        capabilities=(),
    )
    assert not windows_network_is_off(
        attempts=refused + (("udp-sendto@10.0.0.5:9", "sent"),),
        package=package,
        capabilities=(),
    )
    assert windows_network_is_off(
        attempts=refused + (("udp-sendto@10.0.0.5:9", denied_udp),),
        package=package,
        capabilities=(),
    )
    assert not windows_network_is_off(
        attempts=(("tcp", "loopback"), ("udp-sendto", denied_udp), ("udp-connected", denied_udp)),
        package=package,
        capabilities=(),
    )
    assert not windows_network_is_off(attempts=refused, package=package, capabilities=None)
    assert not windows_network_is_off(attempts=refused, package="unread", capabilities=())
    assert not windows_network_is_off(attempts=(("tcp", denied),), package=package, capabilities=())
    for capability in ("S-1-15-3-1", "s-1-15-3-2", "S-1-15-3-3"):
        assert not windows_network_is_off(
            attempts=refused,
            package=package,
            capabilities=(capability,),
        )
    text = windows_network_failure_text(
        attempts=(
            ("tcp", network_attempt_label(timeout, success="connected")),
            ("udp-sendto", "sent"),
            ("udp-connected", "sent"),
        ),
        package=package,
        capabilities=("S-1-15-3-1",),
    )
    assert text.startswith("Windows did not turn the network off, so the script was not run.")
    assert "probe=1.1.1.1:443,1.1.1.1:53" in text
    assert "tcp=timeout " in text
    assert "winerror=10060" in text
    assert "udp-sendto=sent" in text
    assert "udp-connected=sent" in text
    assert "package=" + package in text
    assert "capabilities=S-1-15-3-1" in text
    assert "capabilities=none" in windows_network_failure_text(
        attempts=refused, package=package, capabilities=()
    )
    assert "capabilities=unread" in windows_network_failure_text(
        attempts=refused, package=package, capabilities=None
    )
    monkeypatch.setenv(
        "AICAD_WIN_NET_RECEIVERS",
        "10.1.2.3:40000:40001;127.0.0.1:5:6;example.test:1:2;127.0.0.1:0:1;010.0.0.1:1:2",
    )
    assert windows_receiver_targets() == (("10.1.2.3", 40000, 40001), ("127.0.0.1", 5, 6))

    source = inspect.getsource(apply_sandbox)
    windows = source.split('sys.platform == "win32"', 1)[1].split('sys.platform == "darwin"', 1)[0]
    assert windows.index("identity_failures") < windows.index("windows_network_is_off")
    assert "_network_blocked" not in windows
    probe = inspect.getsource(_windows_one_attempt)
    assert "sendto" in probe
    assert "connect" in probe
    assert ".send(" in probe
    udp = probe.split("SOCK_DGRAM", 1)[1]
    assert "sendto" in udp
    assert "connect" in udp
    assert ".send(" in udp
    assert udp.index("bind(") < udp.index("sock.sendto(")


def test_windows_network_block_names_only_that_container():
    """The firewall block is the package SID, and it stays until the last network-off child exits."""

    import inspect

    from aicad import sandbox_windows as win

    package = "S-1-15-2-11-22-33-44-55-66-77"
    script = win._firewall_script(package, install=True)
    assert script.count("New-NetFirewallRule") == 2
    assert script.index("Count -eq 0") < script.index("New-NetFirewallRule")
    assert "183" not in script
    assert "already exists" not in script
    assert script.count("-Action Block") == 2
    assert script.count("-Protocol Any") == 2
    assert "-Direction Outbound" in script
    assert "-Direction Inbound" in script
    assert "-Protocol UDP" not in script
    assert "-Protocol TCP" not in script
    assert "LocalPort" not in script
    assert "RemotePort" not in script
    assert "aicad-script-network-off-udp" in script
    assert f"-Package '{package}'" in script
    assert "S-1-15-3-1" not in script
    assert "Remove-NetFirewallRule" in script
    assert script.endswith("exit 0\n")
    runner = inspect.getsource(win._run_firewall_script)
    assert '"timeout": 120' in runner
    removal = win._firewall_script(install=False)
    assert "New-NetFirewallRule" not in removal
    assert "Remove-NetFirewallRule" in removal
    assert removal.endswith("exit 0\n")
    for bad in ("S-1-15-3-1", "S-1-15-2-1'; Remove-Item", "S-1-5-18", ""):
        with pytest.raises(OSError, match="not a package SID"):
            win._firewall_script(bad, install=True)

    calls = []
    closed = []
    present_calls = []

    def record(text):
        calls.append(text)

    def present(sid):
        # The first read is a missing block, so the rules are installed.
        # Every later read says the block is already in place, so it is not removed.
        present_calls.append(sid)
        return len(present_calls) != 1

    import shutil
    import tempfile

    win._network_off_holders = 0
    win._network_off_sid = None
    win._network_off_rules_sid = None
    win._wfp_engine = None
    marker_dir = Path(tempfile.mkdtemp())
    original_marker = win._network_off_marker_path
    win._network_off_marker_path = marker_dir / "sid"
    original = win._run_firewall_script
    original_close = win._close_wfp_block
    original_install = win._install_wfp_block
    original_present = win._firewall_block_is_present
    win._run_firewall_script = record
    win._firewall_block_is_present = present

    def fake_close():
        closed.append("closed")
        # Match the real close: the next hold must see no open filter,
        # or it skips the installer the failure case replaces.
        win._wfp_engine = None

    win._close_wfp_block = fake_close
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
        assert closed == []
        win._release_network_block()
        assert len(calls) == 1
        assert closed == ["closed"]
        assert win._network_off_holders == 0
        assert win._network_off_rules_sid == package.upper()
        assert win._recorded_rules_sid() == package.upper()
        win._network_off_rules_sid = None
        win._hold_network_block(package)
        assert len(calls) == 1
        assert present_calls == [package.upper(), package.upper(), package.upper()]
        win._release_network_block()
        win._hold_network_block(package)
        assert len(calls) == 1
        win._release_network_block()
        win._require_script_network_open()
        assert len(calls) == 2
        assert "New-NetFirewallRule" not in calls[-1]
        assert win._network_off_rules_sid is None
        assert win._recorded_rules_sid() is None

        def boom(_sid):
            raise OSError("wfp failed")

        win._install_wfp_block = boom
        with pytest.raises(OSError, match="wfp failed"):
            win._hold_network_block(package)
        assert win._network_off_holders == 0
        assert "New-NetFirewallRule" not in calls[-1]
        assert "Remove-NetFirewallRule" in calls[-1]
    finally:
        win._forget_recorded_rules()
        win._network_off_marker_path = original_marker
        shutil.rmtree(marker_dir, ignore_errors=True)
        win._run_firewall_script = original
        win._close_wfp_block = original_close
        win._install_wfp_block = original_install
        win._firewall_block_is_present = original_present
        win._network_off_holders = 0
        win._network_off_sid = None
        win._network_off_rules_sid = None
        win._wfp_engine = None

    spawn = inspect.getsource(win.spawn)
    assert "_acquire_profile_lock" not in spawn
    assert spawn.index("_appcontainer_sid") < spawn.index("_hold_network_block")
    assert spawn.index("_hold_network_block") < spawn.index("_launch")
    assert spawn.index("TerminateJobObject") < spawn.index("_release_network_block")
    assert spawn.index("_release_network_block") < spawn.index("_release_profile_use")
    probe = inspect.getsource(win._probe_mode)
    assert "_acquire_profile_lock" not in probe
    assert probe.index("_appcontainer_sid") < probe.index("_hold_network_block")
    assert probe.index("_hold_network_block") < probe.index("_launch")
    assert probe.index("_release_network_block") < probe.index("_release_profile_use")
    hold = inspect.getsource(win._hold_network_block)
    assert hold.index("_firewall_block_is_present") < hold.index("_run_firewall_script")
    assert hold.index("_run_firewall_script") < hold.index("_install_wfp_block")
    assert "_close_wfp_block" in inspect.getsource(win._release_network_block)


def test_windows_network_block_reinstalls_when_the_record_has_no_rules():
    """A recorded package id is not the block. Missing rules are installed again.

    If that install fails, the script is not started. A successful send is
    still a network that is on.
    """

    import shutil
    import tempfile

    from aicad import sandbox_windows as win
    from aicad.sandbox import windows_network_is_off

    package = "S-1-15-2-11-22-33-44-55-66-77"
    check = win._firewall_present_script(package)
    assert "Get-NetFirewallRule" in check
    assert "Get-NetFirewallApplicationFilter" in check
    assert "Get-NetFirewallPortFilter" in check
    assert "Block" in check
    assert "'Any'" in check or '"Any"' in check
    assert package in check
    assert "aicad-script-network-off-out" in check
    assert "aicad-script-network-off-in" in check
    assert "New-NetFirewallRule" not in check
    assert "Remove-NetFirewallRule" not in check
    assert "exit 0" in check
    assert "exit 2" in check
    with pytest.raises(OSError, match="not a package SID"):
        win._firewall_present_script("S-1-15-3-1")

    calls = []
    installed = []

    def record(text):
        calls.append(text)
        assert win._network_off_holders == 0

    reads = {"n": 0}

    def missing(_sid):
        reads["n"] += 1
        return reads["n"] > 1

    def fake_install(sid):
        installed.append(sid)
        win._wfp_engine = 1

    def fake_close():
        win._wfp_engine = None

    marker_dir = Path(tempfile.mkdtemp())
    original_marker = win._network_off_marker_path
    original_run = win._run_firewall_script
    original_present = win._firewall_block_is_present
    original_install = win._install_wfp_block
    original_close = win._close_wfp_block
    looked = []

    def lookup_fails(script):
        looked.append(script)
        raise OSError("lookup timed out")

    platform = sys.platform
    try:
        # A timed-out or failed read is not proof the rules exist.
        sys.platform = "win32"
        win._run_firewall_script = lookup_fails
        assert win._firewall_block_is_present(package) is False
    finally:
        sys.platform = platform
        win._run_firewall_script = original_run
    assert looked
    assert "New-NetFirewallRule" not in looked[0]
    assert "Remove-NetFirewallRule" not in looked[0]
    win._network_off_holders = 0
    win._network_off_sid = None
    win._network_off_rules_sid = None
    win._wfp_engine = None
    win._network_off_marker_path = marker_dir / "sid"
    win._record_rules_sid(package)
    win._run_firewall_script = record
    win._firewall_block_is_present = missing
    win._install_wfp_block = fake_install
    win._close_wfp_block = fake_close
    try:
        assert win._recorded_rules_sid() == package
        win._hold_network_block(package)
        assert win._network_off_holders == 1
        assert len(calls) == 1
        script = calls[0]
        assert script.count("New-NetFirewallRule") == 2
        assert script.count("-Protocol Any") == 2
        assert f"-Package '{package}'" in script
        assert "-Protocol UDP" not in script
        assert installed == [package]
        win._release_network_block()
        assert win._network_off_holders == 0

        win._network_off_rules_sid = None
        win._wfp_engine = None
        installed.clear()
        reads["n"] = 0

        def refuse(text):
            calls.append(text)
            raise OSError("firewall rules were not installed")

        win._run_firewall_script = refuse
        with pytest.raises(OSError, match="firewall rules were not installed"):
            win._hold_network_block(package)
        assert win._network_off_holders == 0
        assert win._network_off_sid is None
        assert win._network_off_rules_sid is None
        assert installed == []
        assert "New-NetFirewallRule" in calls[-1]

        def still_missing(_sid):
            return False

        win._run_firewall_script = record
        win._firewall_block_is_present = still_missing
        win._network_off_holders = 0
        win._network_off_sid = None
        win._network_off_rules_sid = None
        win._wfp_engine = None
        installed.clear()
        with pytest.raises(OSError, match="not in place"):
            win._hold_network_block(package)
        assert win._network_off_holders == 0
        assert win._network_off_sid is None
        assert win._network_off_rules_sid is None
        assert installed == []
        assert "New-NetFirewallRule" in calls[-1]
    finally:
        win._forget_recorded_rules()
        win._network_off_marker_path = original_marker
        shutil.rmtree(marker_dir, ignore_errors=True)
        win._run_firewall_script = original_run
        win._firewall_block_is_present = original_present
        win._install_wfp_block = original_install
        win._close_wfp_block = original_close
        win._network_off_holders = 0
        win._network_off_sid = None
        win._network_off_rules_sid = None
        win._wfp_engine = None

    denied = (
        ("tcp", "denied winerror=10013"),
        ("udp-sendto", "denied winerror=10013"),
        ("udp-connected", "denied winerror=10013"),
    )
    assert windows_network_is_off(attempts=denied, package=package, capabilities=())
    assert not windows_network_is_off(
        attempts=(
            ("tcp", "denied winerror=10013"),
            ("udp-sendto", "sent"),
            ("udp-connected", "denied winerror=10013"),
        ),
        package=package,
        capabilities=(),
    )


def test_windows_stale_profile_is_replaced_before_the_script_starts():
    """A profile that is already registered is deleted and created with no capabilities.

    The registered profile is not opened. A create that succeeds the first time
    is kept.
    """

    import ctypes
    import inspect
    import os
    import re

    from aicad import sandbox_windows as win

    created = inspect.getsource(win._create_appcontainer_profile)
    assert re.search(r"None,\s*\n\s*0,", created)
    body = inspect.getsource(win._appcontainer_sid)
    assert "DeriveAppContainerSidFromAppContainerName" not in body
    assert body.index("_profile_is_in_use") < body.index("_delete_appcontainer_profile")
    assert body.count("_create_appcontainer_profile()") == 2
    assert "_delete_appcontainer_profile()" in body
    assert "_open_proven_profile" in body
    proven = inspect.getsource(win._open_proven_profile)
    assert proven.index("_derive_profile_sid") < proven.index("_stored_capabilities_are_empty")
    assert proven.index("_stored_capabilities_are_empty") < proven.index("return")
    derived = inspect.getsource(win._derive_profile_sid)
    assert "DeriveAppContainerSidFromAppContainerName" in derived
    check = inspect.getsource(win._stored_capabilities_are_empty)
    assert "TerminateProcess" in check
    assert "ResumeThread" not in check
    assert "CapabilityCount = 0" in check
    assert "_creation_flags" in check
    assert "_CREATE_SUSPENDED" in inspect.getsource(win._creation_flags)
    alive = inspect.getsource(win._pid_alive)
    windows_alive = alive.split('sys.platform == "win32"', 1)[1].split("os.kill", 1)[0]
    assert "OpenProcess" in windows_alive
    assert "os.kill" not in windows_alive
    assert win._pid_alive(os.getpid()) is True
    assert win._pid_alive(0) is False
    calls = []

    def fail_then_create():
        calls.append("create")
        if calls.count("create") == 1:
            return 0x8000FFFF, ctypes.c_void_p()
        return 0, ctypes.c_void_p()

    def delete():
        calls.append("delete")
        return 0

    def text(_sid):
        calls.append("text")
        return "S-1-15-2-11-22-33-44-55-66-77"

    original_create = win._create_appcontainer_profile
    original_delete = win._delete_appcontainer_profile
    original_text = win._sid_to_string
    original_in_use = win._profile_is_in_use
    original_change = win._change_profile_use
    original_acquire = win._acquire_profile_lock
    original_release = win._release_profile_lock
    original_open = win._open_proven_profile
    win._create_appcontainer_profile = fail_then_create
    win._delete_appcontainer_profile = delete
    win._sid_to_string = text
    win._profile_is_in_use = lambda: False
    win._change_profile_use = lambda _delta: None
    win._acquire_profile_lock = lambda: None
    win._release_profile_lock = lambda: None
    try:
        _sid, got = win._appcontainer_sid()
        assert got == "S-1-15-2-11-22-33-44-55-66-77"
        assert calls == ["create", "delete", "create", "text"]

        calls.clear()

        def create_ok():
            calls.append("create")
            return 0, ctypes.c_void_p()

        win._create_appcontainer_profile = create_ok
        win._appcontainer_sid()
        assert calls == ["create", "text"]

        calls.clear()

        def create_fail():
            calls.append("create")
            return 0x800700B7, ctypes.c_void_p()

        win._create_appcontainer_profile = create_fail
        with pytest.raises(OSError, match="could not create an AppContainer profile"):
            win._appcontainer_sid()
        assert calls == ["create", "delete", "create"]

        calls.clear()
        win._profile_is_in_use = lambda: True

        def proven_profile():
            calls.append("prove")
            return ctypes.c_void_p(), "S-1-15-2-11-22-33-44-55-66-77"

        win._open_proven_profile = proven_profile
        _sid, got = win._appcontainer_sid()
        assert got == "S-1-15-2-11-22-33-44-55-66-77"
        assert calls == ["create", "prove"]

        calls.clear()

        def refuse_profile():
            calls.append("prove")
            raise OSError(
                "The Windows script profile has capabilities, so the script was not started."
            )

        win._open_proven_profile = refuse_profile
        with pytest.raises(OSError, match="has capabilities"):
            win._appcontainer_sid()
        assert calls == ["create", "prove"]
    finally:
        win._create_appcontainer_profile = original_create
        win._delete_appcontainer_profile = original_delete
        win._sid_to_string = original_text
        win._profile_is_in_use = original_in_use
        win._change_profile_use = original_change
        win._acquire_profile_lock = original_acquire
        win._release_profile_lock = original_release
        win._open_proven_profile = original_open


def test_design_model_reuses_a_proved_windows_mode(monkeypatch, tmp_path):
    """The design-script model reuses a mode the parent already proved.

    The untrusted script does not receive that choice. Nested part scripts
    then do not repeat the confinement probe on the design script's clock.
    """

    import inspect

    from aicad import sandbox_windows as win
    from aicad.design_calls import model_main
    from aicad.sandbox import _child_env, _spawn

    monkeypatch.setenv("AICAD_WIN_SANDBOX_MODE", "lpac")
    env = _child_env(tmp_path, tmp_path / "job", False)
    assert "AICAD_WIN_SANDBOX_MODE" not in env
    saved_mode = win._mode
    saved_error = win._mode_error
    try:
        win._mode = None
        win._mode_error = None
        win.adopt_probed_mode("nope")
        assert win._mode is None
        win.adopt_probed_mode("appcontainer")
        assert win._mode == "appcontainer"
        win.adopt_probed_mode("lpac")
        assert win._mode == "appcontainer"
    finally:
        win._mode = saved_mode
        win._mode_error = saved_error
    source = inspect.getsource(_spawn)
    assert source.index("ensure_mode") < source.index("design_record.start")
    assert "adopt_probed_mode" in inspect.getsource(model_main)


def test_windows_stale_firewall_block_is_not_removed():
    """A block that is already installed stays when the record is missing."""

    import shutil
    import tempfile

    from aicad import sandbox_windows as win

    package = "S-1-15-2-11-22-33-44-55-66-77"
    calls = []

    def record(text):
        calls.append(text)

    def present(sid):
        assert sid == package
        return True

    marker_dir = Path(tempfile.mkdtemp())
    original_marker = win._network_off_marker_path
    original_run = win._run_firewall_script
    original_present = win._firewall_block_is_present
    original_install = win._install_wfp_block
    original_close = win._close_wfp_block
    win._network_off_holders = 0
    win._network_off_sid = None
    win._network_off_rules_sid = None
    win._wfp_engine = None
    win._network_off_marker_path = marker_dir / "sid"
    win._run_firewall_script = record
    win._firewall_block_is_present = present

    def fake_install(_sid):
        win._wfp_engine = 1

    def fake_close():
        win._wfp_engine = None

    win._install_wfp_block = fake_install
    win._close_wfp_block = fake_close
    try:
        assert win._recorded_rules_sid() is None
        win._hold_network_block(package)
        assert calls == []
        assert win._network_off_holders == 1
        assert win._recorded_rules_sid() == package
        win._release_network_block()
        assert calls == []
        assert win._network_off_holders == 0
    finally:
        win._forget_recorded_rules()
        win._network_off_marker_path = original_marker
        shutil.rmtree(marker_dir, ignore_errors=True)
        win._run_firewall_script = original_run
        win._firewall_block_is_present = original_present
        win._install_wfp_block = original_install
        win._close_wfp_block = original_close
        win._network_off_holders = 0
        win._network_off_sid = None
        win._network_off_rules_sid = None
        win._wfp_engine = None


def test_windows_network_rule_record_is_outside_the_sandbox(tmp_path, monkeypatch):
    """The package-id record is not in the project and not in a folder the script can write."""

    import inspect

    from aicad import sandbox_windows as win

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    original_marker = win._network_off_marker_path
    known = win._profile_local_app_data_known
    cached = win._profile_local_app_data_path
    win._network_off_marker_path = None
    win._profile_local_app_data_known = False
    win._profile_local_app_data_path = None
    try:
        marker = win._network_off_marker()
        if marker is not None:
            resolved = marker.resolve()
            writable = tmp_path.resolve()
            assert writable != resolved
            assert writable not in resolved.parents
            assert Path.cwd().resolve() not in resolved.parents
        lookup = inspect.getsource(win._lookup_profile_local_app_data)
        assert "os.environ" not in lookup
        assert "LOCALAPPDATA" not in lookup
        assert "tempfile" not in lookup
        assert "F1B32785-6FBA-4FCF-9D55-7B8E7F157091" in inspect.getsource(win)
    finally:
        win._network_off_marker_path = original_marker
        win._profile_local_app_data_known = known
        win._profile_local_app_data_path = cached


def test_windows_wfp_block_is_every_protocol_for_that_container():
    """The filter matches the package SID only. It names no protocol and no port."""

    import ctypes

    from aicad import sandbox_windows as win

    assert ctypes.sizeof(win._WfpFilter) == 200
    assert win._WfpFilter.flags.offset == 32
    assert win._WfpFilter.providerKey.offset == 40
    assert win._WfpFilter.layerKey.offset == 64
    assert win._WfpFilter.weight.offset == 96
    assert win._WfpFilter.numFilterConditions.offset == 112
    assert win._WfpFilter.filterCondition.offset == 120
    assert win._WfpFilter.action.offset == 128
    assert win._WfpFilter.context.offset == 152
    assert win._WfpFilter.effectiveWeight.offset == 184
    assert ctypes.sizeof(win._WfpSession) == 72
    assert win._WfpSession.sid.offset == 48
    assert ctypes.sizeof(win._WfpSubLayer) == 72
    assert win._WfpSubLayer.weight.offset == 64
    assert ctypes.sizeof(win._WfpCondition) == 40
    assert ctypes.sizeof(win._WfpValue) == 16
    assert ctypes.sizeof(win._WfpAction) == 20
    assert ctypes.sizeof(win._WfpBlob) == 16

    filters = win._wfp_block_filters(0x1234)
    assert len(filters) == len(win._WFP_BLOCK_LAYERS)
    layers = set()
    package = bytes(ctypes.string_at(ctypes.addressof(win._guid(win._WFP_ALE_PACKAGE_ID)), 16))
    for filt, (_name, layer) in zip(filters, win._WFP_BLOCK_LAYERS, strict=True):
        assert int(filt.numFilterConditions) == 1
        assert int(filt.action.type) == win._FWP_ACTION_BLOCK
        assert int(filt.flags) == win._FWPM_FILTER_FLAG_CLEAR_ACTION_RIGHT
        assert not int(filt.flags) & win._FWPM_FILTER_FLAG_PERSISTENT
        condition = filt.filterCondition.contents
        field = bytes(ctypes.string_at(ctypes.addressof(condition.fieldKey), 16))
        assert field == package
        assert int(condition.matchType) == win._FWP_MATCH_EQUAL
        assert int(condition.value.type) == win._FWP_SID
        assert int(condition.value.data.sid) == 0x1234
        assert int(filt.weight.type) == win._FWP_UINT64
        assert int(filt.weight.data.uint64.contents.value) == 0xFFFFFFFFFFFFFFFF
        layer_bytes = bytes(ctypes.string_at(ctypes.addressof(filt.layerKey), 16))
        expected = bytes(ctypes.string_at(ctypes.addressof(win._guid(layer)), 16))
        assert layer_bytes == expected
        layers.add(layer)
    for required in (
        "c38d57d1-05a7-4c33-904f-7fbceee60e82",
        "4a72393b-319f-44bc-84c3-ba54dcb3b6b4",
        "e1cd9fe7-f4b5-4273-96c0-592e487b8650",
        "a3b42c97-9f04-4672-b87e-cee9c483257f",
    ):
        assert required in layers
    with pytest.raises(OSError, match="container id was empty"):
        win._wfp_block_filter(win._WFP_BLOCK_LAYERS[0][1], 0, win._guid(str(__import__("uuid").uuid4())))


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


def test_a_script_does_not_open_geometry_from_another_file(tmp_path, config_home):
    import hashlib

    from aicad.kernel import export_step_file, make_from_ops

    script = "part = Box(1, 1, 1)\n"
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    decoy = tmp_path / "imports" / "other.step"
    decoy.parent.mkdir(parents=True)
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [10, 10, 10]}]), decoy)
    payload = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "small",
                "source": "script",
                "script": script,
                "script_sha256": digest,
                "geometry_file": "imports/other.step",
            }
        ],
    }
    (tmp_path / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    document = Document(tmp_path)
    info = document.list_parts()[0]
    assert info.built is False
    assert info.volume_mm3 == 0
    assert document.get_source("small")["script_pending"] is True
    assert decoy.is_file()


def test_script_geometry_at_its_own_path_reopens(tmp_path, config_home):
    import hashlib

    from aicad.kernel import export_step_file, make_from_ops

    script = "part = Box(2, 2, 2)\n"
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    relative = f".aicad/geometry/block-{digest[:16]}.step"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    export_step_file(make_from_ops([{"op": "add", "shape": "box", "size": [2, 2, 2]}]), path)
    payload = {
        "units": "mm",
        "sketches": [],
        "assemblies": [],
        "parts": [
            {
                "name": "block",
                "source": "script",
                "script": script,
                "script_sha256": digest,
                "geometry_file": relative,
            }
        ],
    }
    (tmp_path / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    document = Document(tmp_path)
    assert document.get_source("block")["script_pending"] is False
    assert document.list_parts()[0].built is True
    assert document.measure("block").volume_mm3 == pytest.approx(8)


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
    if sys.platform == "win32":
        # A leading slash joins onto the drive, so the guard names that path.
        assert "passwd" in text
        assert "aicad-sandbox-outside" in text
    else:
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
    _assert_confined(probe["udp_connected"])
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


def _recv_tcp(sock) -> bytes:
    import socket

    sock.settimeout(0.3)
    try:
        conn, _addr = sock.accept()
    except (TimeoutError, socket.timeout):
        return b""
    except OSError:
        return b""
    data = b""
    try:
        conn.settimeout(0.3)
        while True:
            try:
                chunk = conn.recv(65535)
            except (TimeoutError, socket.timeout):
                break
            if not chunk:
                break
            data += chunk
    finally:
        conn.close()
    return data


def _recv_udp(sock) -> bytes:
    import socket

    sock.settimeout(0.3)
    data = b""
    while True:
        try:
            chunk, _addr = sock.recvfrom(65535)
        except (TimeoutError, socket.timeout):
            return data
        except OSError:
            return data
        data += chunk


@pytest.mark.sandbox_windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer sandbox")
def test_windows_sandbox_delivers_nothing(tmp_path, config_home, monkeypatch):
    """A sandboxed send, connected or not, is refused and the parent receives nothing.

    The receivers are outside the sandbox. One pair is a non-loopback address
    of this machine. The other pair is loopback. The script runs only when
    every attempt was refused. An empty receiver does not excuse a successful send.
    """

    import socket
    import traceback

    from aicad.sandbox import os_probe

    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route.connect(("1.1.1.1", 80))
        external = route.getsockname()[0]
    finally:
        route.close()
    assert external and not external.startswith("127."), external

    def bind_pair(address):
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((address, 0))
        tcp.listen(8)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind((address, 0))
        return tcp, udp

    opened = []
    try:
        ext_tcp, ext_udp = bind_pair(external)
        lo_tcp, lo_udp = bind_pair("127.0.0.1")
        opened = [ext_tcp, ext_udp, lo_tcp, lo_udp]
        spec = ";".join(
            (
                f"{external}:{ext_tcp.getsockname()[1]}:{ext_udp.getsockname()[1]}",
                f"127.0.0.1:{lo_tcp.getsockname()[1]}:{lo_udp.getsockname()[1]}",
            )
        )
        monkeypatch.setenv("AICAD_WIN_NET_RECEIVERS", spec)
        error = None
        probe = None
        try:
            probe = os_probe(tmp_path)
        except Exception as exc:
            error = f"{exc}\n{traceback.format_exc()}"
        received = {
            "external-tcp": _recv_tcp(ext_tcp),
            "external-udp": _recv_udp(ext_udp),
            "loopback-tcp": _recv_tcp(lo_tcp),
            "loopback-udp": _recv_udp(lo_udp),
        }
    finally:
        for sock in opened:
            sock.close()
    detail = probe.get("network") if isinstance(probe, dict) else error
    shown = {key: bytes(value) for key, value in received.items()}
    report = f"windows-net-probe {detail}; receivers={shown}"
    print(report, file=sys.__stderr__, flush=True)
    assert error is None, report
    assert all(value == b"" for value in received.values()), report
    network = str(probe.get("network") or "")
    assert external in network, report
    assert "127.0.0.1" in network, report
    for part in network.split(";"):
        item = part.strip()
        if "=" not in item or item.startswith(("probe=", "package=", "capabilities=")):
            continue
        _name, value = item.split("=", 1)
        assert value.startswith("denied "), report
    _assert_confined(probe["tcp"])
    _assert_confined(probe["udp"])
    _assert_confined(probe["udp_connected"])


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
        "import aicad.sandbox as sandbox\n"
        "kernel = sandbox.ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "kernel.OpenProcess.argtypes = [\n"
        "    sandbox.ctypes.c_uint32, sandbox.ctypes.c_int, sandbox.ctypes.c_uint32,\n"
        "]\n"
        "kernel.OpenProcess.restype = sandbox.ctypes.c_void_p\n"
        "kernel.CloseHandle.argtypes = [sandbox.ctypes.c_void_p]\n"
        "rights = 0x0001 | 0x1000\n"
        "handle = kernel.OpenProcess(rights, 0, sandbox.os.getppid())\n"
        "if handle:\n"
        "    kernel.CloseHandle(handle)\n"
        "    open('open.txt', 'w', encoding='utf-8').write('opened')\n"
        "else:\n"
        "    open('open.txt', 'w', encoding='utf-8').write(\n"
        "        'denied:%s' % sandbox.ctypes.get_last_error()\n"
        "    )\n"
        "part = Box(1, 1, 1)\n"
    )
    run_part_script(script, tmp_path)
    assert (tmp_path / "open.txt").read_text(encoding="utf-8").startswith("denied")


def test_join_pids_cgroup_refuses_a_failed_move(monkeypatch):
    """A prepared cgroup that the process cannot enter does not run the script."""
    from aicad import sandbox as sandbox_mod

    monkeypatch.setenv("AICAD_PIDS_CGROUP", "/tmp/not-a-cgroup")
    monkeypatch.setattr(sandbox_mod, "_move_into_pids_cgroup", lambda path, pid: False)
    with pytest.raises(ScriptError, match="process limit could not be applied"):
        sandbox_mod._join_pids_cgroup()


def test_join_pids_cgroup_does_nothing_without_a_cgroup(monkeypatch):
    from aicad import sandbox as sandbox_mod

    monkeypatch.delenv("AICAD_PIDS_CGROUP", raising=False)
    called = []
    monkeypatch.setattr(
        sandbox_mod,
        "_move_into_pids_cgroup",
        lambda path, pid: called.append(pid) or False,
    )
    sandbox_mod._join_pids_cgroup()
    assert called == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux cgroup pids controller")
def test_a_plain_directory_is_not_a_process_cgroup(tmp_path):
    """Writing the pid into an ordinary file does not put the process in that cgroup."""
    import os

    from aicad.sandbox import _move_into_pids_cgroup

    assert _move_into_pids_cgroup(tmp_path, os.getpid()) is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux cgroup pids controller")
def test_script_is_not_run_when_it_cannot_enter_the_process_cgroup(tmp_path, monkeypatch):
    from aicad import sandbox as sandbox_mod

    supported, reason = sandbox_mod.sandbox_supported()
    if not supported:
        pytest.skip(reason)
    fake = tmp_path / "not-a-cgroup"
    fake.mkdir()
    monkeypatch.setattr(sandbox_mod, "_prepare_pids_cgroup", lambda: fake)
    monkeypatch.setattr(sandbox_mod, "_release_pids_cgroup", lambda path: None)
    with pytest.raises(ScriptError, match="process limit could not be applied"):
        run_part_script("open('ran.txt', 'w').write('yes')\npart = Box(1, 1, 1)\n", tmp_path)
    assert not (tmp_path / "ran.txt").exists()


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


def test_a_child_that_survives_the_kill_does_not_hold_the_parent(monkeypatch):
    import subprocess
    import threading
    import time

    from aicad import sandbox

    class Stuck:
        def __init__(self):
            self.pid = 424242
            self.stdout = None
            self.stderr = None
            self.returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="child", timeout=timeout or 0)

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="child", timeout=timeout or 0)

    killed = []
    monkeypatch.setattr(sandbox, "_kill_process_tree", lambda leader, seen, lock: killed.append(leader))
    started = time.monotonic()
    sandbox._stop_child(Stuck(), set(), threading.Lock(), reap_seconds=0.05)
    assert killed == [424242, 424242]
    assert time.monotonic() - started < 2


def test_cpu_or_memory_limit_stops_the_script(tmp_path, config_home, monkeypatch):
    monkeypatch.setenv("AICAD_SCRIPT_CPU_SECONDS", "1")
    monkeypatch.setenv("AICAD_SCRIPT_WALL_SECONDS", "30")
    with pytest.raises(ScriptError, match="memory or CPU"):
        run_part_script("while True:\n    pass\n", tmp_path)


def test_windows_cpu_stop_is_user_time_at_the_cap():
    """User time at the cap is a limit stop. A short crash and a timeout are not."""

    import ctypes

    from aicad import sandbox_windows as win

    assert ctypes.sizeof(win._JobBasicAccounting) == 48
    limit = 10_000_000
    assert win._user_time_reached_limit(limit, limit) is True
    assert win._user_time_reached_limit(limit - 1_000_000, limit) is True
    assert win._user_time_reached_limit(limit - 1_000_001, limit) is False
    assert win._user_time_reached_limit(0, limit) is False
    assert win._user_time_reached_limit(limit, 0) is False
    short_cap = 500_000
    assert win._user_time_reached_limit(short_cap, short_cap) is True
    assert win._user_time_reached_limit(short_cap - 1, short_cap) is False
    assert win._job_stop_was_a_limit(
        timed_out=False, violated=False, cpu_reached=True, exit_code=0, memory_peak=False
    )
    assert not win._job_stop_was_a_limit(
        timed_out=True, violated=False, cpu_reached=True, exit_code=0, memory_peak=False
    )
    assert not win._job_stop_was_a_limit(
        timed_out=False, violated=False, cpu_reached=False, exit_code=0, memory_peak=True
    )
    assert win._job_stop_was_a_limit(
        timed_out=False, violated=False, cpu_reached=False, exit_code=1, memory_peak=True
    )
    assert not win._job_stop_was_a_limit(
        timed_out=False, violated=False, cpu_reached=False, exit_code=-7, memory_peak=False
    )


def test_a_bad_result_removes_the_job_directory(tmp_path):
    from aicad.sandbox import _payload_from_result

    job = tmp_path / "job"
    job.mkdir()
    (job / "script.py").write_text("part = Box(1, 1, 1)\n", encoding="utf-8")
    (job / "result.json").write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ScriptError, match="could not be read"):
        _payload_from_result(job, 0, "", "", memory_killed=False)
    assert not job.exists()

    other = tmp_path / "list-job"
    other.mkdir()
    (other / "result.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ScriptError, match="not a report"):
        _payload_from_result(other, 0, "", "", memory_killed=False)
    assert not other.exists()


def test_a_failed_spawn_removes_the_job_directory(tmp_path, monkeypatch):
    from aicad import sandbox as sandbox_mod

    supported, reason = sandbox_mod.sandbox_supported()
    if not supported:
        pytest.skip(reason)

    def boom(*_args, **_kwargs):
        raise OSError("spawn failed")

    if sys.platform == "win32":
        monkeypatch.setattr("aicad.sandbox_windows.spawn", boom)
    else:
        monkeypatch.setattr(sandbox_mod.subprocess, "Popen", boom)
    with pytest.raises(OSError, match="spawn failed"):
        sandbox_mod._spawn(tmp_path, "part", "part = Box(1, 1, 1)\n", False)
    jobs = tmp_path / ".aicad" / "jobs"
    assert jobs.is_dir()
    assert list(jobs.iterdir()) == []


def test_a_crash_without_a_result_does_not_need_sigkill(tmp_path):
    import signal

    from aicad.sandbox import _died_from_limit_signal, _payload_from_result

    assert _died_from_limit_signal(0) is False
    assert _died_from_limit_signal(1) is False
    assert _died_from_limit_signal(-7) is False
    killed = getattr(signal, "SIGKILL", None)
    if killed is not None:
        assert _died_from_limit_signal(-killed) is True
    with pytest.raises(ScriptError, match="stopped before it could report"):
        _payload_from_result(tmp_path / "crash", -7, "", "boom", memory_killed=False)
    with pytest.raises(ScriptError, match="memory or CPU"):
        _payload_from_result(tmp_path / "limit", -7, "", "", memory_killed=True)


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
