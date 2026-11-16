"""`foundry doctor` says what is so (A6).

It said "auth present" when auth.json existed, whatever the backend reads,
and "not signed in" beside a key in the very variable a session would use;
it printed HTTPS_PROXY as it was, a password in it included; and outside a
Git repository it warned and still exited 0, though no session can start
there. Now the credential line names where a session's credential would
come from -- the variable's name or the vault's field, never the value --
and a missing one fails the check; the proxy is the one Foundry's own HTTP
client would use for the endpoint, without its user name and password; the
machine's policy file is reported with its rule count; the folder checked
is the one given with -C, whose repository settings are a layer.
"""

from __future__ import annotations

import subprocess

import pytest

from foundry.cli import app
from foundry.core.auth import CredentialVault

SECRET = "sk-doctor-test-0123456789abcdef"


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    """A home, a machine policy folder and no credentials but the test's."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FOUNDRY_GATEWAY_TOKEN", "CORP_KEY",
                 "FOUNDRY_CA_BUNDLE", "SSL_CERT_FILE"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    return home


@pytest.fixture()
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    return folder


def _config(home, text):
    (home / "config.toml").write_text(text, encoding="utf-8")


def _doctor(capsys, *args):
    code = app.main(["doctor", *args])
    out = capsys.readouterr()
    return code, " ".join((out.out + out.err).split())


def test_a_key_in_the_variable_is_found_and_never_shown(machine, repo, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, '[backend]\nbase_url = "http://127.0.0.1:9/v1"\n')

    code, shown = _doctor(capsys, "-C", str(repo))

    assert "credential from the variable OPENAI_API_KEY" in shown
    assert SECRET not in shown and "auth " not in shown
    assert code == 0


def test_a_stored_key_is_named_by_its_field(machine, repo, capsys):
    CredentialVault(machine / "auth.json").save({"api_key": SECRET})

    code, shown = _doctor(capsys, "-C", str(repo))
    assert f"credential stored by foundry login (api_key, in {machine / 'auth.json'})" in shown
    assert SECRET not in shown

    # The OpenAI key is not Anthropic's: that protocol has its own.
    _config(machine, '[backend]\nprotocol = "anthropic_messages"\n')
    code, shown = _doctor(capsys, "-C", str(repo))
    assert "credential none: set ANTHROPIC_API_KEY or run foundry login" in shown
    assert code == 1


def test_a_backend_that_names_its_variable_reads_only_that(machine, repo, monkeypatch, capsys):
    CredentialVault(machine / "auth.json").save({"api_key": SECRET})
    _config(machine, '[backend]\nenv_key = "CORP_KEY"\nbase_url = "http://127.0.0.1:9/v1"\n')

    code, shown = _doctor(capsys, "-C", str(repo))
    assert ("credential none: CORP_KEY, which backend.env_key names, is not set; "
            "foundry login stores nothing for this backend") in shown
    assert code == 1

    monkeypatch.setenv("CORP_KEY", "corp-" + SECRET)
    code, shown = _doctor(capsys, "-C", str(repo))
    assert "credential from the variable CORP_KEY (backend.env_key)" in shown
    assert SECRET not in shown and code == 0


def test_a_gateway_token(machine, repo, monkeypatch, capsys):
    _config(machine, '[backend]\ncredential_source = "gateway_token"\n'
                     'base_url = "http://127.0.0.1:9/v1"\n')
    code, shown = _doctor(capsys, "-C", str(repo))
    assert "credential none: set FOUNDRY_GATEWAY_TOKEN or run foundry login" in shown

    monkeypatch.setenv("FOUNDRY_GATEWAY_TOKEN", SECRET)
    code, shown = _doctor(capsys, "-C", str(repo))
    assert "credential from the variable FOUNDRY_GATEWAY_TOKEN" in shown and code == 0


def test_outside_a_repository_the_check_fails(machine, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    bare = tmp_path / "bare"
    bare.mkdir()

    code, shown = _doctor(capsys, "-C", str(bare))

    assert f"repo {bare} is not a Git repository; a session here would refuse to start" in shown
    assert code == 1


def test_the_folder_given_is_the_one_checked_with_its_settings(machine, repo, monkeypatch,
                                                               capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    (repo / ".foundry").mkdir()
    (repo / ".foundry" / "config.toml").write_text("[runtime]\nmax_tool_rounds = \n",
                                                   encoding="utf-8")

    code, shown = _doctor(capsys, "--workspace", str(repo))

    assert f"repo {repo}" in shown
    assert "cannot read" in shown and "config.toml" in shown
    assert code == 1


def test_the_proxy_is_shown_without_its_password(machine, repo, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:pa55word@proxy.example:8080")

    code, shown = _doctor(capsys, "-C", str(repo))

    assert ("proxy http://proxy.example:8080 for api.openai.com "
            "(its user name and password not shown)") in shown
    assert "pa55word" not in shown and "alice" not in shown

    # The one Foundry would use: none for a gateway on this machine.
    _config(machine, '[backend]\nbase_url = "http://127.0.0.1:9/v1"\n')
    code, shown = _doctor(capsys, "-C", str(repo))
    assert "proxy none for 127.0.0.1" in shown

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    _config(machine, "")
    code, shown = _doctor(capsys, "-C", str(repo))
    assert "proxy http://proxy.example:3128 for api.openai.com" in shown
    assert "not shown" not in shown


def test_the_machine_policy_and_its_rules(machine, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    policy = tmp_path / "programdata" / "Foundry" / "policy.toml"

    code, shown = _doctor(capsys, "-C", str(repo))
    assert f"policy none on this machine ({policy})" in shown

    # The user's own rule is not the machine's.
    _config(machine, '[[permissions]]\ntool = "run_command"\npattern = "npm test"\n'
                     'decision = "allow"\n')
    policy.parent.mkdir(parents=True)
    policy.write_text('[[permissions]]\ntool = "run_command"\npattern = "git push*"\n'
                      'decision = "deny"\n\n[[permissions]]\ntool = "run_command"\n'
                      'pattern = "curl *"\ndecision = "deny"\n', encoding="utf-8")
    code, shown = _doctor(capsys, "-C", str(repo))
    assert f"policy {policy}: 2 permission rule(s) from the machine" in shown
