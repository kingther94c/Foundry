"""Windows primitives via ctypes: no third-party wheel needed for any of them.

Two mechanisms live here because their stdlib/pip alternatives are worse, not
merely heavier:

* Job Objects kill a whole process tree in one call *and* release the pipe
  handles a grandchild inherited -- ``taskkill /T`` and psutil both walk a
  parent-PID snapshot and lose branches whose middle process already exited,
  leaving ``communicate()`` blocked on a pipe nobody will close.
* DPAPI encrypts an arbitrary-size blob for the current user. The Credential
  Manager backend behind ``keyring`` caps a secret at 2560 bytes, which a token
  set with a JWT can exceed, and costs six wheels in the offline wheelhouse.

Everything degrades honestly on non-Windows so the pure-logic tests run anywhere.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

# subprocess creation flags
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ProcessJob:
    """Owns a job object; every assigned process and its descendants die together.

    Known limitation: ``subprocess.Popen`` returns after the child has started,
    so there is a millisecond window in which a very fast child could spawn a
    grandchild outside the job. The stdlib gives no way to close it (no
    CREATE_SUSPENDED, no PROC_THREAD_ATTRIBUTE_JOB_LIST), so it is documented
    rather than papered over.
    """

    def __init__(self) -> None:
        self.handle = None
        if not IS_WINDOWS:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            return
        self.handle = handle
        self._kernel32 = kernel32

    @property
    def active(self) -> bool:
        return self.handle is not None

    def assign(self, pid_handle: int) -> bool:
        if not self.active:
            return False
        return bool(self._kernel32.AssignProcessToJobObject(self.handle, pid_handle))

    def terminate(self, exit_code: int = 1) -> bool:
        if not self.active:
            return False
        return bool(self._kernel32.TerminateJobObject(self.handle, exit_code))

    def close(self) -> None:
        if self.active:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> ProcessJob:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def taskkill_tree(pid: int) -> None:
    """Last resort when the job object could not be created."""
    if not IS_WINDOWS:
        return
    import subprocess

    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, check=False)


# --- DPAPI ---------------------------------------------------------------


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CredentialProtectionUnavailable(RuntimeError):
    """DPAPI is not available (non-Windows, or the API refused)."""


def _blob(data: bytes) -> _DATA_BLOB:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _extract(blob: _DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.WinDLL("kernel32").LocalFree(blob.pbData)


def dpapi_protect(data: bytes) -> bytes:
    if not IS_WINDOWS:
        raise CredentialProtectionUnavailable("DPAPI is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(ctypes.byref(_blob(data)), None, None, None, None,
                                  _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise CredentialProtectionUnavailable(
            f"CryptProtectData failed: {ctypes.get_last_error()}"
        )
    return _extract(out)


def dpapi_unprotect(data: bytes) -> bytes:
    if not IS_WINDOWS:
        raise CredentialProtectionUnavailable("DPAPI is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    out = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(ctypes.byref(_blob(data)), None, None, None, None,
                                    _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        # A forced local-account password reset invalidates the master key. The
        # caller treats this as "logged out", never as a corrupt store.
        raise CredentialProtectionUnavailable(
            f"CryptUnprotectData failed: {ctypes.get_last_error()}"
        )
    return _extract(out)


# --- console output decoding ---------------------------------------------


@dataclass(frozen=True, slots=True)
class DecodedOutput:
    text: str
    encoding: str
    replacements: int


def decode_output(data: bytes) -> DecodedOutput:
    """Decode child-process bytes on a system whose console is not UTF-8.

    Modern tools (git, ripgrep, Python with PYTHONUTF8) write UTF-8 into a pipe;
    cmd built-ins and legacy tools write the OEM code page -- on a zh-CN machine
    that is cp936.

    The decision is "is this UTF-8 at all", not "which decode looks tidier". The
    earlier version kept whichever produced fewer U+FFFD, but the fallbacks are
    single-byte code pages that map all 256 values and can never produce one --
    so any UTF-8 stream with a single damaged byte lost that comparison and the
    whole output was handed to the model as mojibake. ``_drain`` cuts at the
    capture cap mid-character by construction, so that was not a rare case: it
    was every large non-ASCII output.
    """
    if not data:
        return DecodedOutput("", "utf-8", 0)

    try:
        return DecodedOutput(data.decode("utf-8"), "utf-8", 0)
    except UnicodeDecodeError:
        pass

    # A cut at the byte cap leaves a partial sequence at the very end. Decoding
    # the whole prefix and dropping those few bytes preserves the text; treating
    # it as evidence of a different encoding destroys it.
    body, tail = _split_incomplete_tail(data)
    if tail and not body:
        return DecodedOutput("", "utf-8", 0)
    if tail:
        try:
            return DecodedOutput(body.decode("utf-8"), "utf-8", 0)
        except UnicodeDecodeError:
            pass

    primary = data.decode("utf-8", errors="replace")
    primary_bad = primary.count("�")

    # Genuine legacy output fails on essentially every high byte; damaged UTF-8
    # fails on a handful. Only the first case justifies changing codec. A short
    # legacy run whose bytes happen to form valid UTF-8 pairs still reads as
    # UTF-8 -- unavoidable, and the cheaper of the two mistakes.
    high_bytes = sum(1 for byte in data if byte > 0x7F)
    if high_bytes and primary_bad * 2 >= high_bytes:
        for codec in ("oem", "cp936", "mbcs"):
            try:
                alternate = data.decode(codec, errors="replace")
            except LookupError:
                continue
            return DecodedOutput(alternate, codec, alternate.count("�"))

    return DecodedOutput(primary, "utf-8", primary_bad)


def _split_incomplete_tail(data: bytes) -> tuple[bytes, bytes]:
    """Split off a trailing UTF-8 sequence that was cut short, if that is all
    that is wrong. Returns ``(body, tail)``; an empty tail means no such cut."""
    for back in range(1, min(4, len(data)) + 1):
        lead = data[-back]
        if lead < 0x80:
            return data, b""
        if lead < 0xC0:                       # continuation byte, keep walking
            continue
        expected = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
        return (data[:-back], data[-back:]) if back < expected else (data, b"")
    return data, b""


def child_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment for child processes.

    Approving one ``pytest`` should not hand the repository's own test code every
    API key in the developer's shell. The allowlist keeps what a build needs; the
    denylist catches credential-shaped names in anything explicitly passed through.
    """
    source = base if base is not None else os.environ
    keep_exact = {
        "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
        "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "APPDATA",
        "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LANG", "LC_ALL",
        "USERNAME", "COMPUTERNAME", "USERDOMAIN",
        # How build tooling reaches the network on the machine this targets.
        # Dropping these is what makes pip, npm, dotnet and docker fail or hang
        # behind a corporate proxy -- exactly the environment Foundry is for.
        # The credential-shaped denylist below still applies to all of them.
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "no_proxy", "all_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO",
        # Toolchain locations a build needs to find its own runtime.
        "VIRTUAL_ENV", "CONDA_PREFIX", "JAVA_HOME", "GRADLE_HOME", "MAVEN_HOME",
        "GOPATH", "GOROOT", "CARGO_HOME", "RUSTUP_HOME", "NVM_DIR",
    }
    keep_prefix = ("PYTHON", "PIP_", "NPM_CONFIG_", "NUGET_", "DOTNET_", "MSBUILD")
    deny_fragment = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
                     "AWS_", "AZURE_", "OPENAI", "ANTHROPIC")

    env: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if any(fragment in upper for fragment in deny_fragment):
            continue
        if upper in keep_exact or upper.startswith(keep_prefix):
            env[name] = value

    # Make Python children speak UTF-8 so their output needs no guessing.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
