"""scripts/sandbox_probe.py: the findings D-165 rests on, measured again.

The probe is research, not a sandbox Foundry uses (D-003). These tests run it
so that the research stays reproducible, and so that a change in Windows or
Python that would change the conclusion fails here instead of being assumed:

- a write-restricted token lets a command delete the user's files, and breaks
  pytest's ``tmp_path``;
- low integrity alone refuses every write and delete outside the workspace,
  and pytest works;
- in every configuration the command still reads the user's files and
  decrypts what DPAPI protects for the user.

Everything the probe changes is under a temporary folder it removes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the probe is Windows'")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sandbox_probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("sandbox_probe_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measured(probe):
    seen = {}
    for name, (restricted, low) in probe.MODES.items():
        with probe.Probe(restricted=restricted, low=low) as run:
            seen[name] = run.checks()
    return seen


def _allowed(seen, check):
    return seen[check] == "allowed"


def test_every_configuration_writes_its_workspace_and_temp(measured):
    for seen in measured.values():
        assert _allowed(seen, "write the granted folder")
        assert _allowed(seen, "write the granted temp folder")


def test_a_write_restricted_token_lets_a_command_delete_the_users_files(measured):
    seen = measured["write-restricted"]
    assert _allowed(seen, "delete a file of the user's not granted")
    assert not _allowed(seen, "write a folder of the user's not granted")
    assert _allowed(seen, "write a folder whose ACL lets Everyone write")
    assert not _allowed(seen, "write a folder it made private, as tempfile.mkdtemp does")


def test_low_integrity_fences_writes_and_deletes(measured):
    seen = measured["low integrity"]
    for check in ("write a folder of the user's not granted",
                  "write a folder whose ACL lets Everyone write",
                  "write the user's own temp folder",
                  "delete a file of the user's not granted",
                  "rename a file of the user's not granted",
                  "create a key in the user's registry",
                  "a process it starts writes a folder not granted"):
        assert not _allowed(seen, check), check
    assert _allowed(seen, "write a folder it made private, as tempfile.mkdtemp does")


def test_no_configuration_is_a_sandbox(measured):
    for seen in measured.values():
        assert _allowed(seen, "read a file of the user's not granted")
        assert _allowed(seen, "connect to a port on this machine")
        assert _allowed(seen, "decrypt a secret DPAPI protects for the user")


def test_pytest_runs_at_low_integrity_and_not_write_restricted(probe):
    test = "def test_one(tmp_path):\n    (tmp_path / 'f').write_text('x')\n"
    codes = {}
    for name in ("write-restricted", "low integrity"):
        restricted, low = probe.MODES[name]
        with probe.Probe(restricted=restricted, low=low) as run:
            (run.work / "test_probe.py").write_text(test, encoding="utf-8")
            codes[name], _ = run.run([sys.executable, "-m", "pytest", "-q", "-p",
                                      "no:cacheprovider", "test_probe.py"])
    assert codes == {"write-restricted": 1, "low integrity": 0}


def test_the_probe_leaves_nothing_behind(probe):
    with probe.Probe(low=True) as run:
        root = run.root
        run.checks()
    assert not root.exists()
