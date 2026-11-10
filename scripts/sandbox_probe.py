"""A research probe into a restricted-token sandbox -- not a sandbox Foundry
uses (D-003, D-165).

It runs a command under a write-restricted token, the way codex's unelevated
Windows sandbox level does, and reports what the command could and could not
do. A write-restricted token keeps the user's identity for reading: the
command reads whatever the user can. Every write is checked twice, the second
time against the restricting SIDs alone -- here one SID made up for the run,
which only the folders granted to it allow.

    python scripts/sandbox_probe.py                      # the checks, each configuration
    python scripts/sandbox_probe.py -- git status        # a command, in a granted folder
    python scripts/sandbox_probe.py --mode "low integrity" -- python -m pytest

Everything it changes is under a temporary folder it makes and then removes,
plus a registry key and a file named for the run, which it removes too.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":  # pragma: no cover - the probe is Windows'
    raise SystemExit("the probe runs on Windows only")

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Handles and pointers are 64 bits wide; ctypes' default int would cut them.
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD]
advapi32.CreateRestrictedToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
advapi32.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(ctypes.c_void_p)]
advapi32.SetEntriesInAclW.restype = wintypes.DWORD
advapi32.GetNamedSecurityInfoW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
advapi32.SetNamedSecurityInfoW.argtypes = [
    wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p]
advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
advapi32.GetSecurityDescriptorSacl.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.BOOL)]
advapi32.CreateProcessAsUserW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
    ctypes.c_void_p]

# -- the Windows API it needs ---------------------------------------------------

TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_ADJUST_DEFAULT = 0x0080
DISABLE_MAX_PRIVILEGE = 0x1
LUA_TOKEN = 0x4
WRITE_RESTRICTED = 0x8
TOKEN_USER_CLASS = 1
TOKEN_DEFAULT_DACL_CLASS = 6
TOKEN_LOGON_SID_CLASS = 28
TOKEN_INTEGRITY_LEVEL_CLASS = 25
SE_GROUP_INTEGRITY = 0x20
LABEL_SECURITY_INFORMATION = 0x10
LOW_INTEGRITY = "S-1-16-4096"
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x4
GRANT_ACCESS = 1
SET_ACCESS = 2
SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
NO_INHERITANCE = 0
TRUSTEE_IS_SID = 0
GENERIC_ALL = 0x10000000
FILE_ALL_ACCESS = 0x1F01FF
CREATE_SUSPENDED = 0x4
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESTDHANDLES = 0x100
WAIT_TIMEOUT = 0x102


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TRUSTEE_W(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int),
                ("ptstrName", ctypes.c_void_p)]


class EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wintypes.DWORD), ("grfAccessMode", ctypes.c_int),
                ("grfInheritance", wintypes.DWORD), ("Trustee", TRUSTEE_W)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _check(ok, what: str):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)
    return ok


def _sid(text: str) -> ctypes.c_void_p:
    sid = ctypes.c_void_p()
    _check(advapi32.ConvertStringSidToSidW(wintypes.LPCWSTR(text), ctypes.byref(sid)),
           f"ConvertStringSidToSidW({text})")
    return sid


def _token_info(token, cls: int) -> ctypes.Array:
    size = wintypes.DWORD()
    advapi32.GetTokenInformation(token, cls, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    _check(advapi32.GetTokenInformation(token, cls, buffer, size, ctypes.byref(size)),
           "GetTokenInformation")
    return buffer


def _logon_sid(token, keep: list) -> ctypes.c_void_p:
    """The session's logon SID; ``keep`` holds the buffer it points into."""
    buffer = _token_info(token, TOKEN_LOGON_SID_CLASS)
    keep.append(buffer)
    # TOKEN_GROUPS: a DWORD count, then the array, aligned for a pointer.
    first = SID_AND_ATTRIBUTES.from_buffer(buffer, ctypes.sizeof(ctypes.c_void_p))
    return ctypes.c_void_p(first.Sid)


def _access(sid, permissions: int, inheritance: int, mode: int = GRANT_ACCESS):
    entry = EXPLICIT_ACCESS_W()
    entry.grfAccessPermissions = permissions
    entry.grfAccessMode = mode
    entry.grfInheritance = inheritance
    entry.Trustee.TrusteeForm = TRUSTEE_IS_SID
    entry.Trustee.ptstrName = sid.value if isinstance(sid, ctypes.c_void_p) else sid
    return entry


def _acl(entries, base=None) -> ctypes.c_void_p:
    array = (EXPLICIT_ACCESS_W * len(entries))(*entries)
    acl = ctypes.c_void_p()
    code = advapi32.SetEntriesInAclW(len(entries), array, base, ctypes.byref(acl))
    if code:
        raise ctypes.WinError(code, "SetEntriesInAclW")
    return acl


def grant(folder: Path, sid) -> None:
    """Give ``sid`` full access to ``folder`` and everything under it: the
    ACE the research is about, and what it would leave on a workspace."""
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = advapi32.GetNamedSecurityInfoW(wintypes.LPCWSTR(str(folder)), SE_FILE_OBJECT,
                                          DACL_SECURITY_INFORMATION, None, None,
                                          ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if code:
        raise ctypes.WinError(code, "GetNamedSecurityInfoW")
    try:
        merged = _acl([_access(sid, FILE_ALL_ACCESS, SUB_CONTAINERS_AND_OBJECTS_INHERIT)], dacl)
        try:
            code = advapi32.SetNamedSecurityInfoW(wintypes.LPWSTR(str(folder)), SE_FILE_OBJECT,
                                                  DACL_SECURITY_INFORMATION, None, None,
                                                  merged, None)
            if code:
                raise ctypes.WinError(code, "SetNamedSecurityInfoW")
        finally:
            kernel32.LocalFree(merged)
    finally:
        kernel32.LocalFree(descriptor)


def label_low(folder: Path) -> None:
    """Mark ``folder`` and what is made in it low integrity, so a
    low-integrity command may write there: the other change, besides the
    ACE, such a sandbox leaves on a workspace."""
    descriptor = ctypes.c_void_p()
    _check(advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "S:(ML;OICI;NW;;;LW)", 1, ctypes.byref(descriptor), None),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        sacl = ctypes.c_void_p()
        _check(advapi32.GetSecurityDescriptorSacl(descriptor, ctypes.byref(present),
                                                  ctypes.byref(sacl), ctypes.byref(defaulted)),
               "GetSecurityDescriptorSacl")
        code = advapi32.SetNamedSecurityInfoW(wintypes.LPWSTR(str(folder)), SE_FILE_OBJECT,
                                              LABEL_SECURITY_INFORMATION, None, None, None, sacl)
        if code:
            raise ctypes.WinError(code, "SetNamedSecurityInfoW(label)")
    finally:
        kernel32.LocalFree(descriptor)


class RestrictedToken:
    """The user's token, its privileges dropped; with a ``capability``, its
    writes restricted to it and Everyone (and the logon SID, with
    ``logon``); with ``low``, at low integrity.

    Measured on Windows 11 (D-165): without Everyone among the restricting
    SIDs, ``cmd`` starts and Python does not (STATUS_DLL_INIT_FAILED) --
    neither the user's SID, Users, Authenticated Users, INTERACTIVE nor
    RESTRICTED in its place is enough. So anything whose ACL lets Everyone
    write, the command can write. The default DACL -- what an object the
    command creates without an inheritable ACL gets -- names the restricting
    SIDs too; without it Python does not start either."""

    def __init__(self, capability, *, logon: bool = True, low: bool = False) -> None:
        own = wintypes.HANDLE()
        _check(advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT,
            ctypes.byref(own)), "OpenProcessToken")
        keep: list = []      # the buffers the SIDs below point into
        try:
            keep.append(_token_info(own, TOKEN_USER_CLASS))
            user = ctypes.c_void_p(SID_AND_ATTRIBUTES.from_buffer(keep[0]).Sid)
            restricting = []
            if capability is not None:
                everyone = _sid("S-1-1-0")
                keep.append(_Freed(everyone))
                restricting = [capability, everyone]
                if logon:
                    restricting.append(_logon_sid(own, keep))
            sids = (SID_AND_ATTRIBUTES * max(1, len(restricting)))(
                *[SID_AND_ATTRIBUTES(s.value, 0) for s in restricting])
            flags = DISABLE_MAX_PRIVILEGE | LUA_TOKEN | (WRITE_RESTRICTED if restricting else 0)
            self.handle = wintypes.HANDLE()
            _check(advapi32.CreateRestrictedToken(
                own, flags, 0, None, 0, None, len(restricting), sids if restricting else None,
                ctypes.byref(self.handle)), "CreateRestrictedToken")
            if restricting:
                self._default_dacl(user, restricting)
            if low:
                # Mandatory integrity: a low-integrity process may not write,
                # nor delete, what is labelled above it -- every file not
                # labelled, which is medium by default.
                label = _sid(LOW_INTEGRITY)
                try:
                    holder = SID_AND_ATTRIBUTES(label.value, SE_GROUP_INTEGRITY)
                    _check(advapi32.SetTokenInformation(
                        self.handle, TOKEN_INTEGRITY_LEVEL_CLASS, ctypes.byref(holder),
                        ctypes.sizeof(holder) + advapi32.GetLengthSid(label)),
                        "SetTokenInformation(TokenIntegrityLevel)")
                finally:
                    kernel32.LocalFree(label)
        finally:
            kernel32.CloseHandle(own)

    def _default_dacl(self, user, restricting) -> None:
        system = _sid("S-1-5-18")
        try:
            default = _acl([_access(s, GENERIC_ALL, NO_INHERITANCE, SET_ACCESS)
                            for s in (user, system, *restricting)])
            try:
                holder = ctypes.c_void_p(default.value)
                _check(advapi32.SetTokenInformation(self.handle, TOKEN_DEFAULT_DACL_CLASS,
                                                    ctypes.byref(holder),
                                                    ctypes.sizeof(holder)),
                       "SetTokenInformation(TokenDefaultDacl)")
            finally:
                kernel32.LocalFree(default)
        finally:
            kernel32.LocalFree(system)

    def close(self) -> None:
        kernel32.CloseHandle(self.handle)


class _Freed:
    """A SID ConvertStringSidToSidW allocated, freed with its holder."""

    def __init__(self, sid) -> None:
        self.sid = sid

    def __del__(self) -> None:
        kernel32.LocalFree(self.sid)


def _environment_block(env: dict[str, str]) -> ctypes.Array:
    text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper()))
    return ctypes.create_unicode_buffer(text + "\0")


def run_restricted(token: RestrictedToken, argv: list[str], *, cwd: Path, env: dict[str, str],
                   out: Path, timeout_s: float = 120) -> int:
    """``argv`` under ``token``, its output to ``out``; its exit code, or
    None if it would not start in time."""
    with open(out, "wb") as sink, open(os.devnull, "rb") as nothing:
        handles = [ctypes.c_void_p(__import__("msvcrt").get_osfhandle(f.fileno()))
                   for f in (nothing, sink)]
        for handle in handles:
            os.set_handle_inheritable(handle.value, True)
        info = STARTUPINFOW()
        info.cb = ctypes.sizeof(info)
        info.dwFlags = STARTF_USESTDHANDLES
        info.hStdInput, info.hStdOutput, info.hStdError = (handles[0].value, handles[1].value,
                                                            handles[1].value)
        process = PROCESS_INFORMATION()
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        _check(advapi32.CreateProcessAsUserW(
            token.handle, None, line, None, None, True,
            CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            _environment_block(env), wintypes.LPCWSTR(str(cwd)), ctypes.byref(info),
            ctypes.byref(process)), "CreateProcessAsUserW")
        try:
            kernel32.ResumeThread(process.hThread)
            if kernel32.WaitForSingleObject(process.hProcess,
                                            int(timeout_s * 1000)) == WAIT_TIMEOUT:
                kernel32.TerminateProcess(process.hProcess, 1)
                kernel32.WaitForSingleObject(process.hProcess, 5000)
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
            return code.value
        finally:
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)


# -- the checks the command makes ----------------------------------------------

CHILD = r'''
import ctypes, json, os, socket, subprocess, sys, winreg
from ctypes import wintypes
work, outside, user_temp, port, run_id, blob, open_to_all = sys.argv[1:8]
seen = {}

def attempt(name, action):
    try:
        action()
        seen[name] = "allowed"
    except Exception as exc:
        seen[name] = f"denied: {type(exc).__name__}: {exc}"[:200]

def write(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("probe")

attempt("write the granted folder", lambda: write(os.path.join(work, "inside.txt")))
attempt("write the granted temp folder",
        lambda: write(os.path.join(os.environ["TEMP"], "temp.txt")))

def private_folder():
    import tempfile
    write(os.path.join(tempfile.mkdtemp(), "f.txt"))
attempt("write a folder it made private, as tempfile.mkdtemp does", private_folder)
attempt("write a folder of the user's not granted",
        lambda: write(os.path.join(outside, "written.txt")))
attempt("write a folder whose ACL lets Everyone write",
        lambda: write(os.path.join(open_to_all, "written.txt")))
attempt("write the user's own temp folder",
        lambda: write(os.path.join(user_temp, f"foundry-probe-{run_id}.txt")))
attempt("read a file of the user's not granted",
        lambda: open(os.path.join(outside, "secret.txt"), encoding="utf-8").read())
attempt("delete a file of the user's not granted",
        lambda: os.remove(os.path.join(outside, "secret.txt")))
attempt("rename a file of the user's not granted",
        lambda: os.rename(os.path.join(outside, "keep.txt"), os.path.join(outside, "moved.txt")))
attempt("create a key in the user's registry",
        lambda: winreg.CloseKey(winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                                  rf"Software\FoundrySandboxProbe-{run_id}")))

def connect():
    with socket.create_connection(("127.0.0.1", int(port)), timeout=5) as s:
        s.sendall(b"probe")
attempt("connect to a port on this machine", connect)

def grandchild():
    target = os.path.join(outside, "from_grandchild.txt")
    subprocess.run(["cmd", "/c", f"echo x> {target}"], check=False, timeout=30)
    if not os.path.exists(target):
        raise PermissionError("the grandchild's write did not happen")
attempt("a process it starts writes a folder not granted", grandchild)

class BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

def unprotect():
    data = open(blob, "rb").read()
    buf = ctypes.create_string_buffer(data, len(data))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))), None, None,
            None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    if ctypes.string_at(out.pbData, out.cbData) != b"the user's credential":
        raise ValueError("decrypted something else")
attempt("decrypt a secret DPAPI protects for the user", unprotect)
print(json.dumps(seen))
'''


def _protect(data: bytes) -> bytes:
    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buffer = ctypes.create_string_buffer(data, len(data))
    out = BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _check(crypt32.CryptProtectData(ctypes.byref(BLOB(len(data), ctypes.cast(buffer,
                                                                             ctypes.c_void_p))),
                                    None, None, None, None, 0, ctypes.byref(out)),
           "CryptProtectData")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.c_void_p(out.pbData))


def _listen() -> tuple[socket.socket, list[bytes]]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(60)
    heard: list[bytes] = []

    def accept():
        try:
            conn, _ = server.accept()
            with conn:
                heard.append(conn.recv(64))
        except OSError:
            pass

    threading.Thread(target=accept, daemon=True).start()
    return server, heard


class Probe:
    """A temporary root: ``work`` (granted) and ``temp`` (granted, the
    command's TEMP), ``outside`` (the user's, not granted), and
    ``everyone`` (not granted, but its ACL lets Everyone write)."""

    def __init__(self, *, restricted: bool = True, low: bool = False,
                 logon: bool = True) -> None:
        self.run_id = secrets.token_hex(4)
        # Not mkdtemp: since Python 3.12.4 its folder's ACL names only SYSTEM,
        # Administrators and the owner -- and in an elevated session the owner
        # is Administrators, which the LUA token holds deny-only, so the
        # command could not even read it. An ordinary folder inherits the
        # temp folder's ACL, as a workspace inherits its drive's.
        self.root = Path(tempfile.gettempdir()) / f"foundry-sandbox-probe-{self.run_id}"
        self.root.mkdir()
        self.work, self.temp, self.outside, self.open_to_all = (
            self.root / n for n in ("work", "temp", "outside", "everyone"))
        for folder in (self.work, self.temp, self.outside, self.open_to_all):
            folder.mkdir()
        everyone = _sid("S-1-1-0")
        try:
            grant(self.open_to_all, everyone)
        finally:
            kernel32.LocalFree(everyone)
        # A SID no account has, in the resource-manager authority (S-1-9):
        # made up for this run, as codex makes one per sandbox.
        words = [str(int.from_bytes(secrets.token_bytes(4), "little")) for _ in range(4)]
        self.capability = _sid("S-1-9-" + "-".join(words))
        if restricted:
            grant(self.work, self.capability)
            grant(self.temp, self.capability)
        if low:
            label_low(self.work)
            label_low(self.temp)
        self.token = RestrictedToken(self.capability if restricted else None, logon=logon,
                                     low=low)

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(TEMP=str(self.temp), TMP=str(self.temp))
        return env

    def run(self, argv: list[str], *, cwd: Path | None = None,
            timeout_s: float = 120) -> tuple[int, str]:
        out = self.root / f"out-{secrets.token_hex(3)}.txt"
        code = run_restricted(self.token, argv, cwd=cwd or self.work, env=self.environment(),
                              out=out, timeout_s=timeout_s)
        return code, out.read_text(encoding="utf-8", errors="replace")

    def checks(self) -> dict[str, str]:
        (self.outside / "secret.txt").write_text("the user's", encoding="utf-8")
        (self.outside / "keep.txt").write_text("kept", encoding="utf-8")
        blob = self.outside / "blob.bin"
        blob.write_bytes(_protect(b"the user's credential"))
        server, heard = _listen()
        user_temp = Path(tempfile.gettempdir())
        try:
            code, out = self.run([sys.executable, "-I", "-c", CHILD, str(self.work),
                                  str(self.outside), str(user_temp),
                                  str(server.getsockname()[1]), self.run_id, str(blob),
                                  str(self.open_to_all)])
        finally:
            server.close()
        try:
            seen = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"the probe itself": f"exit {code}: {out[-500:]}"}
        if heard != [b"probe"] and seen.get("connect to a port on this machine") == "allowed":
            seen["connect to a port on this machine"] = "connected, but nothing arrived"
        return seen

    def close(self) -> None:
        self.token.close()
        kernel32.LocalFree(self.capability)
        (Path(tempfile.gettempdir()) / f"foundry-probe-{self.run_id}.txt").unlink(missing_ok=True)
        try:
            import winreg

            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\FoundrySandboxProbe-{self.run_id}")
        except OSError:
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> Probe:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# The configurations measured: (write-restricted, low integrity).
MODES = {
    "write-restricted": (True, False),
    "write-restricted, low integrity": (True, True),
    "low integrity": (False, True),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-logon-sid", action="store_true",
                        help="restrict writes to the run's SID and Everyone, not the logon SID")
    parser.add_argument("--mode", choices=list(MODES), default="write-restricted",
                        help="the configuration a command runs under")
    parser.add_argument("command", nargs="*",
                        help="a command to run in the granted folder instead of the checks")
    args = parser.parse_args(argv)
    logon = not args.no_logon_sid
    if args.command:
        restricted, low = MODES[args.mode]
        with Probe(restricted=restricted, low=low, logon=logon) as probe:
            code, out = probe.run(args.command)
            print(out, end="")
            print(f"[exit {code}]")
        return 0
    results = {}
    for name, (restricted, low) in MODES.items():
        with Probe(restricted=restricted, low=low, logon=logon) as probe:
            results[name] = probe.checks()
    checks = list(dict.fromkeys(c for seen in results.values() for c in seen))
    print(json.dumps({c: {name: seen.get(c, "-") for name, seen in results.items()}
                      for c in checks}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
