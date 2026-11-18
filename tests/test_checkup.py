"""`foundry checkup` (D-204, D-205, D-206): the first run on a machine whose
results leave it only by word of mouth.

Each case gives one line with a short code; the feedback code holds every
level's mark and the codes of what was not OK, and no host and no secret;
the report stays on the machine. Every failure the network level names is
made here on loopback -- a closed port, a name that does not resolve, a
certificate nobody trusts, a proxy that wants a sign-in, a tunnel to the
wrong name, a web page where the model list should be -- with a TLS server
whose certificate is made for the test, and a proxy that answers CONNECT.
The protocol level asks a gateway on loopback that speaks the three
protocols, with each fault it can be given (tests/fakegateway.py); the task
level runs a real session against that gateway, which answers as a model
doing the task would, well or badly. Elsewhere the task level stands in: a
session takes seconds.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import socketserver
import ssl
import sys
import threading

import pytest

from foundry import __version__
from foundry.cli import app, checkup
from foundry.core import netcheck, protocheck
from foundry.core.auth import CredentialVault
from foundry.core.checkup import FAIL, OK, SKIP, WARN, Case, feedback_code, report
from foundry.core.conversation import ModelTurn, StopReason, ToolUseBlock
from foundry.core.errors import FatalError, FoundryError, ProtocolError, StreamDropped
from foundry.core.httpc import HttpClient
from foundry.core.httptap import Exchange
from fakegateway import ALL, MODEL, MODELS, Gateway
from selfsigned import certificate

SECRET = "sk-checkup-test-0123456789abcdef"
# The checks the fixture stands in for, as the module defines them.
REAL_POWERSHELL = checkup.powershell_case
REAL_LONG_PATHS = checkup.long_paths_case
REAL_TASK = checkup.task


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tls")
    cert, key = certificate(names=["localhost", "api.example.test"])
    (folder / "cert.pem").write_text(cert, encoding="ascii")
    (folder / "key.pem").write_text(key, encoding="ascii")
    return folder / "cert.pem", folder / "key.pem"


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    """A home, a machine policy folder, no credential, no proxy; the checks
    that start a process or read the machine's settings stand in (their own
    tests are below)."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FOUNDRY_GATEWAY_TOKEN",
                 "FOUNDRY_CA_BUNDLE", "SSL_CERT_FILE", "HTTP_PROXY", "HTTPS_PROXY",
                 "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FOUNDRY_HOME", str(home))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    monkeypatch.setattr(netcheck, "pac_url", lambda: None)
    monkeypatch.setattr(checkup, "powershell_case",
                        lambda: Case("E", "powershell", OK, "stood in"))
    monkeypatch.setattr(checkup, "long_paths_case",
                        lambda: Case("E", "longpaths", OK, "stood in"))
    # It adds no case: a code shows a T only where the level was skipped.
    monkeypatch.setattr(checkup, "task", lambda *args: None)
    return home


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def endpoint(tls_files):
    servers = []

    def start(answer=(200, "application/json", MODELS), tls=False, faults=(), speaks=ALL):
        """A gateway answering its model list with ``answer`` and each turn as
        ``faults`` say; over TLS with the test's certificate when ``tls`` is
        true, or with the (cert, key) it is."""
        server = Gateway(answer=answer, faults=faults, speaks=speaks)
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*(tls_files if tls is True else tls))
            server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        head = b""
        while b"\r\n\r\n" not in head:
            piece = self.request.recv(4096)
            if not piece:
                return
            head += piece
        self.server.seen.append(head.split(b"\r\n\r\n")[0].decode("latin-1").split("\r\n"))
        if self.server.mode == "407":
            self.request.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                 b"Proxy-Authenticate: Negotiate\r\n"
                                 b"Proxy-Authenticate: NTLM\r\nContent-Length: 0\r\n\r\n")
            return
        upstream = socket.create_connection(self.server.upstream)
        self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

        def pump(source, target):
            try:
                while data := source.recv(65536):
                    target.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    target.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        back = threading.Thread(target=pump, args=(upstream, self.request), daemon=True)
        back.start()
        pump(self.request, upstream)
        back.join(5)
        upstream.close()


class _Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, mode, upstream=None):
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.mode, self.upstream, self.seen = mode, upstream, []


@pytest.fixture()
def proxy():
    proxies = []

    def start(mode, upstream=None):
        server = _Proxy(mode, upstream)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        proxies.append(server)
        return server

    yield start
    for server in proxies:
        server.shutdown()
        server.server_close()


def _config(home, base_url, extra=""):
    (home / "config.toml").write_text(
        f'[backend]\nbase_url = "{base_url}"\nmodel = "{MODEL}"\n{extra}', encoding="utf-8")


def _checkup(capsys, work, *more):
    code = app.main(["checkup", "-C", str(work), *more])
    out = capsys.readouterr()
    shown = out.out + out.err
    lines = [line.strip() for line in shown.splitlines()]
    return code, " ".join(shown.split()), lines[lines.index("Feedback code, to read out:") + 1]


def _report(home):
    (path,) = (home / "checkups").glob("checkup-*.md")
    return path.read_text(encoding="utf-8")


def _unresolved(monkeypatch, *hosts):
    real = socket.getaddrinfo

    def lookup(host, *args, **kwargs):
        if host in hosts:
            raise socket.gaierror(11001, "getaddrinfo failed")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(netcheck.socket, "getaddrinfo", lookup)


# --- the feedback code ----------------------------------------------------


def test_the_code_gives_each_level_its_mark_and_names_what_was_not_ok():
    cases = [Case("E", "python", OK, "x"), Case("E", "longpaths", WARN, "x", "LP0"),
             Case("C", "config", OK, "x"), Case("N", "proxy", OK, "x"),
             Case("N", "tls", FAIL, "x", "TLS1"), Case("N", "models", SKIP, "x")]

    code = feedback_code("abc1234", cases, {"K": "key-env", "M": "-", "R": "chat"})

    assert code == "FB1 abc1234 E~ C+ K:key-env M:- R:chat N!  E~=LP0 N!=TLS1"


def test_a_level_names_its_failures_first_and_at_most_three_codes():
    cases = ([Case("N", f"w{n}", WARN, "x", f"W{n}") for n in range(3)]
             + [Case("N", "a", FAIL, "x", "F1"), Case("N", "b", FAIL, "x", "F2")]
             + [Case("T", "all", SKIP, "x")])

    assert feedback_code("v1", cases, {}) == "FB1 v1 N! T-  N!=F1,F2,W0,+2"


def test_a_case_that_is_not_ok_must_have_a_short_code():
    with pytest.raises(ValueError):
        Case("N", "tls", FAIL, "no code")
    with pytest.raises(ValueError):
        Case("N", "tls", WARN, "a code with a space", "TLS 1")
    with pytest.raises(ValueError):
        Case("Q", "tls", OK, "no such level")


def test_the_report_has_the_code_each_case_and_what_only_the_user_can_say():
    cases = [Case("N", "tls", FAIL, "not trusted", "TLS1", "set FOUNDRY_CA_BUNDLE")]

    text = report(when="2026-11-17 10:00", about=["Foundry abc1234"], cases=cases,
                  code="FB1 abc1234 N!  N!=TLS1")

    assert "Feedback code, to read out: `FB1 abc1234 N!  N!=TLS1`" in text
    assert "- N network: FAIL (TLS1)" in text
    assert "- **FAIL TLS1** tls: not trusted\n  - What to do: set FOUNDRY_CA_BUNDLE" in text
    assert "## To tell me in words" in text and "policy.toml" in text


# --- end to end, against loopback -----------------------------------------


def test_every_level_ok_against_an_endpoint_that_lists_the_model(endpoint, machine, work,
                                                                 monkeypatch, capsys):
    server = endpoint()
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line == f"FB1 {checkup.source_token()[0]} E+ C+ K:key-env M:- R:chat N+ P+"
    assert code == 0
    # The list once, then the eight turns, each with the credential, which is
    # shown nowhere.
    assert [seen["path"] for seen in server.seen] == (["/v1/models"]
                                                      + ["/v1/chat/completions"] * 8)
    assert {seen["authorization"] for seen in server.seen} == {f"Bearer {SECRET}"}
    assert "2 model(s) listed, gpt-test among them" in shown
    assert "spent 8 request(s), 140 token(s) in and 82 out" in shown
    text = _report(machine)
    assert line in text and "## N network" in text and "## P protocol" in text
    assert SECRET not in shown and SECRET not in text


@pytest.mark.parametrize("status, content_type, body, mark, code", [
    (401, "application/json", b'{"error": "no"}', "N!", "A401"),
    (403, "application/json", b'{"error": "no"}', "N!", "A403"),
    (404, "application/json", b'{"error": "none"}', "N~", "M404"),
    (500, "application/json", b'{"error": "boom"}', "N!", "H500"),
    (200, "text/html", b"<html><head><title>Sign in to Corp</title></head></html>", "N~",
     "HTML"),
    (200, "text/html; charset=utf-8", b"Please sign in", "N~", "HTML"),
    (200, "application/json", b'{"object": "list"}', "N~", "MDLJ"),
    (200, "application/json", json.dumps({"data": [{"id": "other"}]}).encode(), "N~", "MDLX"),
])
def test_each_answer_to_the_model_list_has_its_code(endpoint, machine, work, monkeypatch,
                                                   capsys, status, content_type, body, mark,
                                                   code):
    server = endpoint((status, content_type, body))
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    exit_code, shown, line = _checkup(capsys, work)

    # A credential refused is refused for a turn too; anything else, a turn
    # is still asked, and this gateway answers it.
    refused = code in ("A401", "A403")
    assert line.endswith(f" {mark} {'P- T-' if refused else 'P+'}  {mark}={code}")
    assert exit_code == (1 if mark == "N!" else 0)
    if refused:
        assert "P protocol SKIP all not checked: the credential was refused" in shown
    if code == "HTML" and b"title" in body:
        assert "a web page answered, not a model list (Sign in to Corp)" in shown
    if code == "H500":
        assert 'the endpoint answered HTTP 500: {"error": "boom"}' in shown


def test_an_answer_that_quotes_the_credential_is_shown_without_it(endpoint, machine, work,
                                                                  monkeypatch, capsys):
    server = endpoint((500, "application/json", b'{"error": "bad key ' + SECRET.encode() + b'"}'))
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith("N!=H500")
    assert 'HTTP 500: {"error": "bad key [redacted]"}' in shown
    assert SECRET not in shown and SECRET not in _report(machine)


def test_a_closed_port_fails_at_tcp_and_the_endpoint_is_not_asked(machine, work, monkeypatch,
                                                                   capsys):
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N! P- T-  N!=TCP0") and code == 1
    assert f"127.0.0.1 refused port {port}" in shown
    assert "models not asked: the endpoint was not reached" in shown


def test_a_name_that_does_not_resolve_fails_at_dns_and_a_proxy_script_is_named(
        machine, work, monkeypatch, capsys):
    _unresolved(monkeypatch, "api.example.test")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(netcheck, "pac_url", lambda: "http://wpad.example.test/proxy.pac")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "https://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N! P- T-  N!=DNS0,PAC") and code == 1
    assert "Windows uses the proxy script http://wpad.example.test/proxy.pac" in shown
    assert "tcp not tried: the name did not resolve" in shown
    assert "tls not tried: the name did not resolve" in shown


def test_a_certificate_nobody_trusts_is_named_by_its_issuer_until_the_bundle_trusts_it(
        endpoint, tls_files, machine, work, monkeypatch, capsys):
    server = endpoint(tls=True)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"https://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N! P- T-  N!=TLS1") and code == 1
    assert ("the certificate (issued by CN=Foundry checkup test CA, O=Foundry tests) is not "
            "trusted by the Windows certificate store") in shown
    assert "set FOUNDRY_CA_BUNDLE to it" in shown
    assert all(seen["authorization"] is None for seen in server.seen)

    monkeypatch.setenv("FOUNDRY_CA_BUNDLE", str(tls_files[0]))
    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N+ P+") and code == 0
    assert f"trusted by the CA bundle {tls_files[0]}" in shown
    assert server.seen[-1]["authorization"] == f"Bearer {SECRET}"


def test_an_expired_certificate_is_said_to_be(endpoint, machine, work, tmp_path, monkeypatch,
                                              capsys):
    cert, key = certificate(days=(-3, -1))
    (tmp_path / "old.pem").write_text(cert, encoding="ascii")
    (tmp_path / "old.key").write_text(key, encoding="ascii")
    server = endpoint(tls=(tmp_path / "old.pem", tmp_path / "old.key"))
    monkeypatch.setenv("FOUNDRY_CA_BUNDLE", str(tmp_path / "old.pem"))
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"https://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N! P- T-  N!=TLS5") and code == 1
    assert "the certificate (issued by CN=Foundry checkup test CA, O=Foundry tests) has expired" in shown


def test_a_proxy_that_wants_a_sign_in_says_what_it_asks_for(proxy, machine, work, monkeypatch,
                                                            capsys):
    asking = proxy("407")
    monkeypatch.setenv("HTTPS_PROXY", f"http://alice:p%40ss@127.0.0.1:{asking.server_address[1]}")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "https://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N! P- T-  N!=PX407") and code == 1
    assert "the proxy wants a sign-in (407; it asks for Negotiate, NTLM)" in shown
    assert "Foundry can answer only Basic" in shown
    assert (f"proxy http://127.0.0.1:{asking.server_address[1]} for api.example.test, from the "
            "variable HTTPS_PROXY (its user name and password not shown)") in shown
    # The user name and password went to the proxy, decoded, and nowhere else.
    sent = [line for line in asking.seen[0] if line.startswith("Proxy-Authorization: Basic ")]
    assert base64.b64decode(sent[0].split()[-1]) == b"alice:p@ss"
    text = _report(machine)
    for said in (shown, text):
        assert "p%40ss" not in said and "p@ss" not in said and "alice" not in said


def test_a_tunnel_through_the_proxy_reaches_the_endpoint_and_lists_its_models(
        endpoint, proxy, tls_files, machine, work, monkeypatch, capsys):
    server = endpoint(tls=True)
    tunnel = proxy("tunnel", ("127.0.0.1", server.server_port))
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{tunnel.server_address[1]}")
    monkeypatch.setenv("FOUNDRY_CA_BUNDLE", str(tls_files[0]))
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "https://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N+ P+") and code == 0
    assert "tunnel the proxy opens a tunnel to api.example.test:443" in shown
    assert "dns 127.0.0.1 is 127.0.0.1 (the proxy looks up api.example.test)" in shown
    # A tunnel for the check, and one for each request of the client a
    # session uses: the model list and the eight turns.
    assert [seen[0] for seen in tunnel.seen] == ["CONNECT api.example.test:443 HTTP/1.1"] * 10
    assert server.seen[-1]["authorization"] == f"Bearer {SECRET}"


def test_a_certificate_for_another_name_is_said_to_be(endpoint, proxy, tls_files, machine, work,
                                                      monkeypatch, capsys):
    server = endpoint(tls=True)
    tunnel = proxy("tunnel", ("127.0.0.1", server.server_port))
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{tunnel.server_address[1]}")
    monkeypatch.setenv("FOUNDRY_CA_BUNDLE", str(tls_files[0]))
    _config(machine, "https://other.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith("N! P- T-  C!=KEY0 N!=TLS4") and code == 1
    assert "is not for other.example.test" in shown


def test_a_proxy_declared_https_is_refused_before_anything_is_tried(machine, work, monkeypatch,
                                                                    capsys):
    monkeypatch.setenv("HTTPS_PROXY", "https://127.0.0.1:9")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "https://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N! P- T-  N!=PRXS") and code == 1
    assert " dns " not in shown


# --- the configuration ----------------------------------------------------


def test_a_utf16_configuration_has_its_own_code_and_the_network_is_not_checked(
        machine, work, capsys):
    (machine / "config.toml").write_text('[backend]\nmodel = "x"\n', encoding="utf-16")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" C! K:? M:? R:? N- P- T-  C!=CFG16") and code == 1
    assert "N network SKIP all not checked: the configuration could not be read" in shown


def test_a_configuration_in_the_system_code_page_has_its_own_code(machine, work, capsys):
    (machine / "config.toml").write_bytes(b'[backend]\nmodel = "caf\xe9"\n')

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" C! K:? M:? R:? N- P- T-  C!=CFGCP") and code == 1


def test_a_base_url_that_is_not_an_address_skips_the_network(machine, work, monkeypatch,
                                                             capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N- P- T-  C!=URL0") and code == 1
    assert "N network SKIP all not checked: base_url is not an address" in shown


def test_a_key_in_the_variable_backend_env_key_names(endpoint, machine, work, monkeypatch,
                                                     capsys):
    server = endpoint()
    monkeypatch.setenv("CORP_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1", 'env_key = "CORP_KEY"\n')

    code, shown, line = _checkup(capsys, work)

    assert " C+ K:key-named " in line and code == 0
    assert "from the variable CORP_KEY (backend.env_key)" in shown
    assert server.seen[-1]["authorization"] == f"Bearer {SECRET}"


def test_no_configuration_and_no_credential(machine, work, monkeypatch, capsys):
    _unresolved(monkeypatch, "api.openai.com")
    monkeypatch.setenv("NO_PROXY", "*")

    code, shown, line = _checkup(capsys, work)

    assert " C! K:none M:- R:chat N! " in line and line.endswith("C!=KEY0,CFG0 N!=DNS0")
    assert "the defaults point at api.openai.com" in shown
    assert "none: set OPENAI_API_KEY or run foundry login" in shown


def test_an_unknown_protocol_is_named_and_the_endpoint_not_asked(endpoint, machine, work,
                                                                  monkeypatch, capsys):
    server = endpoint()
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1", 'protocol = "anthropic"\n')

    code, shown, line = _checkup(capsys, work)

    assert " C! K:key-env M:- R:? N+ P- T-  C!=PRO0" in line and code == 1
    assert "models not asked: the protocol is not one Foundry speaks" in shown
    assert "P protocol SKIP all not checked: the protocol is not one Foundry speaks" in shown
    assert server.seen == []


def test_a_gateway_token_and_the_machine_policy_are_in_the_code(endpoint, machine, work, tmp_path,
                                                                monkeypatch, capsys):
    server = endpoint()
    monkeypatch.setenv("FOUNDRY_GATEWAY_TOKEN", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1",
            'credential_source = "gateway_token"\n')
    policy = tmp_path / "programdata" / "Foundry" / "policy.toml"
    policy.parent.mkdir(parents=True)
    policy.write_text('[[permissions]]\ntool = "run_command"\npattern = "git push*"\n'
                      'decision = "deny"\n\n[[permissions]]\ntool = "run_command"\n'
                      'pattern = "curl *"\ndecision = "deny"\n', encoding="utf-8")

    code, shown, line = _checkup(capsys, work)

    assert " C+ K:gw-env M:2 R:chat N+" in line and code == 0
    assert f"policy {policy}: 2 permission rule(s) from the machine" in shown
    assert server.seen[-1]["authorization"] == f"Bearer {SECRET}"


def test_a_stored_key_is_a_login(endpoint, machine, work, capsys):
    server = endpoint()
    CredentialVault(machine / "auth.json").save({"api_key": SECRET})
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert " K:key-login " in line and code == 0
    assert SECRET not in shown and SECRET not in _report(machine)


def test_plain_http_to_another_machine_is_a_warning(machine, work, monkeypatch, capsys):
    _unresolved(monkeypatch, "api.example.test")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "http://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert " C~ " in line and "C~=URLHTTP" in line
    assert "the credential would cross the network unencrypted" in shown


# --- this machine ---------------------------------------------------------


def test_git_missing_fails_this_machine(endpoint, machine, work, monkeypatch, capsys):
    def no_git(args, cwd, *rest, **kwargs):
        raise FatalError("git is not on PATH")

    monkeypatch.setattr(checkup, "run_git", no_git)
    server = endpoint()
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    # Without git, the Foundry that ran is named by its version.
    # Nor is a session given the task: it would need git too.
    assert line == f"FB1 v{__version__} E! C+ K:key-env M:- R:chat N+ P+ T-  E!=GIT0"
    assert "T a task, end to end SKIP all not checked: git does not run" in shown
    assert code == 1


def test_the_python_that_runs_is_checked():
    assert checkup.python_case((3, 12, 4)).status == OK
    older = checkup.python_case((3, 11, 9))
    assert (older.status, older.code) == (WARN, "PY311")


@pytest.mark.skipif(not checkup.IS_WINDOWS, reason="Windows PowerShell and its settings")
def test_powershell_is_run_and_its_absence_named(monkeypatch):
    case = REAL_POWERSHELL()
    assert case.status == OK and case.said.startswith("Windows PowerShell 5.")
    monkeypatch.setattr(checkup, "find_program", lambda name: None)
    missing = REAL_POWERSHELL()
    assert (missing.status, missing.code) == (FAIL, "PS0")


@pytest.mark.skipif(not checkup.IS_WINDOWS, reason="the Windows registry")
def test_long_paths_disabled_is_a_warning(monkeypatch):
    import winreg

    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (0, winreg.REG_DWORD))
    case = REAL_LONG_PATHS()
    assert (case.status, case.code) == (WARN, "LP0")
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (1, winreg.REG_DWORD))
    assert REAL_LONG_PATHS().status == OK


def test_an_installed_copy_is_named_by_its_version(tmp_path, monkeypatch):
    # In a virtual environment inside a clone, as .venv often is: git there
    # answers for the clone, not for the copy that runs.
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "a clone"], check=True)
    monkeypatch.setattr(checkup.foundry, "__file__", str(
        tmp_path / ".venv" / "Lib" / "site-packages" / "foundry" / "__init__.py"))

    token, said = checkup.source_token()

    assert token == f"v{__version__}"
    assert said.startswith(f"Foundry {__version__} from ")


def test_a_clone_is_named_by_its_commit():
    token, said = checkup.source_token()

    assert re.fullmatch(r"[0-9a-f]{7}\*?", token)
    assert said.startswith(f"Foundry {token} from ")


@pytest.mark.parametrize("changed, token", [("", "abc1234"), (" M src/foundry/x.py\n",
                                                               "abc1234*")])
def test_a_clone_with_changes_is_marked(monkeypatch, changed, token):
    answers = {"rev-parse": (0, "abc1234\n", ""), "status": (0, changed, "")}
    monkeypatch.setattr(checkup, "run_git", lambda args, cwd: answers[args[0]])

    found, said = checkup.source_token()

    assert found == token
    assert said.endswith(" (with local changes)") == bool(changed)


# --- the protocol level ---------------------------------------------------

PROTOCOLS = [("openai_compat", "chat", "/v1/chat/completions", "OPENAI_API_KEY"),
             ("responses", "resp", "/v1/responses", "OPENAI_API_KEY"),
             ("anthropic_messages", "anth", "/v1/messages", "ANTHROPIC_API_KEY")]


@pytest.fixture()
def gateway():
    servers = []

    def start(protocol, faults=()):
        """A backend of ``protocol`` on a gateway with ``faults``, its client
        a tap; and the gateway."""
        server = Gateway(faults=faults)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(server)
        config = checkup.Config()
        config.backend.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        config.backend.protocol = protocol
        config.backend.model = MODEL
        backend = checkup.make_backend(config, SECRET)
        tap = checkup.TapClient(read_timeout=10)
        backend.client = tap
        return backend, tap, server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("protocol, short, path, variable", PROTOCOLS)
def test_each_protocol_is_asked_every_case_at_its_own_address(endpoint, machine, work, monkeypatch,
                                                             capsys, protocol, short, path,
                                                             variable):
    server = endpoint()
    monkeypatch.setenv(variable, SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1", f'protocol = "{protocol}"\n')

    code, shown, line = _checkup(capsys, work)

    assert f" R:{short} N+ P+" in line and code == 0
    turns = server.seen[1:]
    assert [seen["path"] for seen in turns] == [path] * 8
    sent = (f"Bearer {SECRET}" if short != "anth" else SECRET)
    assert {seen["authorization"] for seen in turns} == {sent}
    for name in ("text", "text-once", "stream", "tool", "tool-once", "continue", "cut",
                 "badmodel"):
        assert f"OK {name} " in shown
    # Two asked a stream, two once, as a session would; the ceiling, and the
    # model that does not exist, as said.
    assert [seen["body"]["stream"] for seen in turns[:5]] == [True, False, True, True, False]
    assert turns[-1]["body"]["model"] == protocheck.NO_SUCH_MODEL
    assert "cut at the ceiling of 16 tokens, and said so (16 out)" in shown
    # The gateway says what the tool's result it was sent says.
    assert "went on with the result: 'The tool says 5.'" in shown


# What each fault comes to, whatever the protocol: (the cases not OK and
# their codes).
FAULTS = {
    "drop_tools": {"tool": "TCS0", "tool-once": "TCO0", "continue": ""},
    "buffer": {"stream": "BUF"},
    "no_sse": {"text": "NOSSE", "stream": ""},
    "ignore_ceiling": {"cut": "CUTI"},
    "hide_cut": {"cut": "CUTU"},
    "mangle_args": {"tool": "TCSJ", "tool-once": "TCOJ", "continue": ""},
    "reject_tool_result": {"continue": "CON400"},
    "any_model": {"badmodel": "BAD0"},
    "post401": {"text": "TXS401", "text-once": "", "stream": "", "tool": "", "tool-once": "",
                "continue": "", "cut": "", "badmodel": ""},
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("protocol", [protocol for protocol, *_ in PROTOCOLS])
def test_each_fault_of_a_gateway_has_its_code_in_each_protocol(gateway, protocol, fault):
    backend, tap, server = gateway(protocol, [fault])

    cases, spent = protocheck.protocol_cases(backend, tap, MODEL)

    assert {case.name: case.code for case in cases if case.status != OK} == FAULTS[fault]
    # A request each case, and one more for each stream asked of a gateway
    # that does not stream.
    posts = [seen for seen in server.seen if "body" in seen]
    assert spent.requests == len(posts) == len(tap.exchanges)
    assert spent.requests == {"post401": 1, "no_sse": 13,
                              "drop_tools": 7, "mangle_args": 7}.get(fault, 8)
    names = ["text-stream", "text-once", "stream", "tool", "tool-once", "continue", "cut",
             "badmodel"]
    skipped = {"post401": names[1:], "drop_tools": ["continue"],
               "mangle_args": ["continue"]}.get(fault, [])
    assert [name for name, _ in spent.captures] == [name for name in names
                                                    if name not in skipped]


def test_a_refusal_and_an_empty_answer_are_failures():
    def asked(**turn):
        return protocheck.Asked(ModelTurn(**turn), None, None, 0.1)

    refused = protocheck.text_case("text", "TXS", asked(
        text="I cannot help", stop_reason=StopReason.REFUSAL), stream=False)
    empty = protocheck.text_case("text", "TXS", asked(text=" "), stream=False)

    assert (refused.status, refused.code) == (FAIL, "TXSR")
    assert (empty.status, empty.code) == (FAIL, "TXSE")


def test_a_call_of_another_tool_or_with_other_numbers():
    def called(name, arguments):
        call = ToolUseBlock(call_id="c1", name=name, arguments=arguments)
        return protocheck.Asked(ModelTurn(text="", tool_calls=(call,)), None,
                                None, 0.1)

    other = protocheck.tool_case("tool", "TCS", called("sum", '{"a": 2, "b": 3}'))
    numbers = protocheck.tool_case("tool", "TCS", called("add", '{"a": 3, "b": 2}'))
    cut = protocheck.tool_case("tool", "TCS", called("add", '{"a": 2, "b"'))

    assert (other.status, other.code) == (FAIL, "TCSN")
    assert (numbers.status, numbers.code) == (WARN, "TCSA")
    assert (cut.status, cut.code) == (FAIL, "TCSJ")


def test_a_short_stream_is_too_short_to_tell_its_pace():
    exchange = Exchange(headers={"content-type": "text/event-stream"})
    for piece in (b"data: 1\n", b"data: 2\n", b"\n"):
        exchange.took(piece)

    case = protocheck.pace_case(protocheck.Asked(None, None, exchange, 0.1))

    assert case.status == OK and case.said == "2 event(s): too few to tell its pace"


def test_a_continuation_without_the_result_is_a_warning():
    asked = protocheck.Asked(ModelTurn(text="Done."), None, None, 0.1)

    case = protocheck.continue_case(asked)

    assert (case.status, case.code) == (WARN, "CONR")


@pytest.mark.parametrize("status, error, code, words", [
    (401, FoundryError("x"), "TXS401", "does not accept the credential"),
    (403, FoundryError("x"), "TXS403", "refuses the credential here"),
    (407, FoundryError("x"), "TXS407", "the proxy wants a sign-in"),
    (404, FoundryError("x"), "TXS404", "has no such address"),
    (429, FoundryError("x"), "TXS429", "rate limited"),
    (400, FoundryError("x"), "TXS400", "refused the request"),
    # A 5xx is the endpoint's own failure, not a refusal of what was sent.
    (502, FoundryError("x"), "TXS502", "the endpoint failed"),
    (200, ValueError("bad"), "TXSX", "Foundry failed reading the answer"),
    (200, ProtocolError("bad"), "TXSP", "the answer could not be read"),
    (None, StreamDropped("cut"), "TXSD", "the stream was cut"),
    (None, FoundryError("the read timed out"), "TXST", "no answer in time"),
    (None, FoundryError("the stream stalled"), "TXST", "no answer in time"),
    (None, FoundryError("connection refused"), "TXSN", "the request failed"),
])
def test_each_failed_request_has_its_code_and_says_what_it_was(status, error, code, words):
    exchange = None if status is None else Exchange(status=status)

    found, said, fix = protocheck.failure("TXS", protocheck.Asked(None, error, exchange, 0.1))

    assert found == code and words in said and fix


def test_a_ceiling_refused_and_a_failure_of_a_wrong_model_are_warnings():
    refused = protocheck.Asked(None, FoundryError("x"), Exchange(status=400), 0.1)
    dropped = protocheck.Asked(None, StreamDropped("cut"), None, 0.1)

    cut = protocheck.cut_case(refused)
    failed = protocheck.cut_case(protocheck.Asked(None, FoundryError("x"), Exchange(status=503),
                                                  0.1))
    wrong = protocheck.wrong_model_case(dropped)

    assert (cut.status, cut.code) == (WARN, "CUT400") and "refuses one of 16 tokens" in cut.fix
    assert failed.code == "CUT503" and "the endpoint's side" in failed.fix
    assert (wrong.status, wrong.code) == (WARN, "BADD")


def test_a_failing_endpoint_is_asked_once_a_case(gateway):
    # A session asks again after a 5xx; the checkup says what came, once.
    backend, tap, server = gateway("openai_compat", ["post500"])

    cases, spent = protocheck.protocol_cases(backend, tap, MODEL)

    assert [case.code for case in cases] == ["TXS500", "TXO500", "STR500", "TCS500", "TCO500",
                                             "", "CUT500", "BAD500"]
    assert spent.requests == len(server.seen) == 7


def test_what_was_sent_is_kept_redacted_too(endpoint, machine, work, monkeypatch, capsys):
    # Nothing a session sends in a body is a credential today; a value the
    # redactor holds is scrubbed from it all the same.
    redactor = checkup.default_redactor().__class__()
    redactor.register(protocheck.SAY_READY)
    monkeypatch.setattr(checkup, "default_redactor", lambda: redactor)
    server = endpoint()
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    _checkup(capsys, work)

    (folder,) = [path for path in (machine / "checkups").iterdir() if path.is_dir()]
    sent = (folder / "chat-text-once.request.json").read_text(encoding="utf-8")
    assert "[redacted]" in sent and protocheck.SAY_READY not in sent


def test_turns_refused_while_the_list_is_given_stop_after_the_first(endpoint, machine, work,
                                                                    monkeypatch, capsys):
    server = endpoint(faults=["post401"])
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N+ P! T-  P!=TXS401") and code == 1
    assert "T a task, end to end SKIP all not checked: the protocol level failed" in shown
    assert len(server.seen) == 2
    assert "text-once not tried: the first request failed" in shown
    assert "spent 1 request(s), 0 token(s) in and 0 out" in shown


def test_a_warning_of_the_protocol_level_leaves_the_exit_code_alone(endpoint, machine, work,
                                                                     monkeypatch, capsys):
    server = endpoint(faults=["any_model"])
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N+ P~  P~=BAD0") and code == 0


def test_all_protocols_says_which_others_the_gateway_speaks_at_the_address(
        endpoint, machine, work, monkeypatch, capsys):
    server = endpoint(speaks=("chat", "anth"))
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1", 'effort = "minimal"\n')

    code, shown, line = _checkup(capsys, work, "--all-protocols")

    assert line.endswith(" N+ P~  P~=XRESP") and code == 0
    assert "also resp responses at the same address: TXS404" in shown
    assert "also anth anthropic_messages at the same address works too" in shown
    assert "spent 17 request(s)" in shown
    # The effort this configuration sets went to its own protocol only; the
    # other was asked with the same credential, as its own header.
    bodies = {seen["path"]: seen["body"] for seen in server.seen if "body" in seen}
    assert bodies["/v1/chat/completions"]["reasoning_effort"] == "minimal"
    assert "output_config" not in bodies["/v1/messages"]
    assert {seen["authorization"] for seen in server.seen
            if seen["path"] == "/v1/messages"} == {SECRET}


def test_all_protocols_says_what_another_protocol_warned_of(
        endpoint, machine, work, monkeypatch, capsys):
    """A gateway that ignores the output ceiling ignores it in each protocol
    (OpenClaw's, 2026-11-18): "works too" alone would hide that."""
    server = endpoint(speaks=("chat", "anth"), faults=["ignore_ceiling"])
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work, "--all-protocols")

    assert line.endswith(" N+ P~  P~=CUTI,XRESP") and code == 0
    assert ("also anth anthropic_messages at the same address works too, warning CUTI there"
            in shown)


def test_what_the_protocol_level_received_is_kept_beside_the_report_redacted(
        endpoint, machine, work, monkeypatch, capsys):
    token = "corp-gateway-token-7f3a9c2e5b81"
    server = endpoint(faults=["echo_auth"])
    monkeypatch.setenv("FOUNDRY_GATEWAY_TOKEN", token)
    _config(machine, f"http://127.0.0.1:{server.server_port}/v1",
            'credential_source = "gateway_token"\n')

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N+ P+") and code == 0
    (folder,) = [path for path in (machine / "checkups").iterdir() if path.is_dir()]
    names = sorted(path.name for path in folder.iterdir())
    assert len(names) == 16 and "chat-text-stream.raw" in names
    assert "chat-badmodel.request.json" in names
    raw = (folder / "chat-text-stream.raw").read_bytes()
    assert b"ready" in raw and b"[redacted]" in raw
    sent = json.loads((folder / "chat-tool.request.json").read_bytes())
    assert sent["tools"][0]["function"]["name"] == "add"
    for path in folder.iterdir():
        assert token.encode() not in path.read_bytes()
    assert token not in shown
    text = _report(machine)
    assert f"What the protocol level received, as it came and redacted: {folder}" in text
    assert token not in text


# --- the task level --------------------------------------------------------


@pytest.fixture()
def tasked(endpoint, machine, monkeypatch):
    """The real task level, against a gateway with ``faults``."""
    monkeypatch.setattr(checkup, "task", REAL_TASK)

    def start(faults=(), *, stored=False):
        server = endpoint(faults=faults)
        if stored:
            CredentialVault(machine / "auth.json").save({"api_key": SECRET})
        else:
            monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        _config(machine, f"http://127.0.0.1:{server.server_port}/v1")
        return server

    return start


def _sessions(server):
    """The requests a session sent: those that offered finish."""
    return [seen for seen in server.seen if "body" in seen
            and "finish" in json.dumps(seen["body"].get("tools") or [])]


@pytest.mark.parametrize("faults, mark, code", [
    ((), "T+", ""),
    (("solver_wrong",), "T!", "TSK0"),
    (("solver_tests",), "T!", "TSKE"),
    (("solver_quiet",), "T~", "TSKP"),
])
def test_a_session_given_the_sample_task_is_judged_by_its_tests(tasked, machine, work, capsys,
                                                                faults, mark, code):
    server = tasked(faults)

    exit_code, shown, line = _checkup(capsys, work)

    assert line.endswith(f" N+ P+ {mark}" + (f"  {mark}={code}" if code else ""))
    assert exit_code == (1 if mark == "T!" else 0)
    assert "a session is fixing a sample module, in at most 8 rounds" in shown
    # It ran as exec runs a task, with the credential, in rounds the gateway saw.
    sessions = _sessions(server)
    assert sessions and {seen["authorization"] for seen in sessions} == {f"Bearer {SECRET}"}
    assert "test_interval.py fail" in json.dumps(sessions[0]["body"])
    # Its own Foundry folder: nothing of it is left in the user's.
    assert sorted(path.name for path in machine.iterdir()) == ["checkups", "config.toml"]
    (folder,) = [path for path in (machine / "checkups").iterdir() if path.is_dir()]
    events = (folder / "task.jsonl").read_text(encoding="utf-8")
    assert '"kind": "termination"' in events and SECRET not in events
    assert f"The task session's events, redacted: {folder / 'task.jsonl'}" in _report(machine)


def test_the_task_reports_its_rounds_and_tokens(tasked, work, capsys):
    tasked()

    _, shown, _ = _checkup(capsys, work)

    found = re.search(r"OK task fixed, and said so: (\d+) round\(s\), (\d+) tool call\(s\), "
                      r"(\d+) token\(s\) in and (\d+) out", shown)
    # A read, a patch, the tests, finish; the gateway counts 20 in, 10 out each.
    assert found and [int(n) for n in found.groups()] == [4, 4, 80, 40]
    # Nothing it did was refused: the rule for the tests reached it.
    assert "refused:" not in shown


def test_the_session_is_run_as_a_script_runs_a_task(tmp_path):
    argv = checkup.taskcheck.exec_argv(tmp_path)

    assert argv[:3] == [sys.executable, "-m", "foundry"]
    assert argv[3:] == ["exec", "--json", "--ephemeral", "-C", str(tmp_path),
                        "-c", "runtime.max_tool_rounds=8", checkup.taskcheck.TASK]


def test_the_session_s_events_are_kept_redacted(tasked, machine, work, monkeypatch, capsys):
    # What the session said is scrubbed as everything kept is.
    redactor = checkup.default_redactor().__class__()
    redactor.register("Fixed the count in overlap.")
    monkeypatch.setattr(checkup, "default_redactor", lambda: redactor)
    tasked()

    _checkup(capsys, work)

    (folder,) = [path for path in (machine / "checkups").iterdir() if path.is_dir()]
    events = (folder / "task.jsonl").read_text(encoding="utf-8")
    assert "[redacted]" in events and "Fixed the count" not in events


def test_a_stored_key_reaches_the_session_in_its_variable(tasked, machine, work, capsys):
    # The session's Foundry folder has no auth.json: the key the checkup
    # read goes to it in OPENAI_API_KEY, in memory.
    server = tasked(stored=True)

    code, shown, line = _checkup(capsys, work)

    assert " K:key-login " in line and line.endswith(" P+ T+") and code == 0
    assert {seen["authorization"] for seen in _sessions(server)} == {f"Bearer {SECRET}"}


def test_the_session_runs_the_tests_with_this_python(tasked, work, tmp_path, monkeypatch,
                                                     capsys):
    # A `python` that comes first on PATH is not the one the session uses.
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "python.bat").write_text("@echo not this python & exit /b 1\n", encoding="ascii")
    monkeypatch.setenv("PATH", f"{decoy}{os.pathsep}{os.environ['PATH']}")
    tasked()

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" P+ T+") and code == 0


def test_no_task_leaves_the_task_level_out(tasked, work, capsys):
    server = tasked()

    code, shown, line = _checkup(capsys, work, "--no-task")

    assert line.endswith(" P+ T-") and code == 0
    assert "T a task, end to end SKIP all not checked: --no-task" in shown
    assert _sessions(server) == []


def test_a_protocol_level_that_failed_leaves_the_task_out(tasked, work, capsys):
    server = tasked(["drop_tools"])

    code, shown, line = _checkup(capsys, work)

    assert " P! T-  P!=TCS0,TCO0" in line and code == 1
    assert _sessions(server) == []


def test_a_session_that_ends_without_an_outcome(tasked, work, monkeypatch, capsys):
    tasked()
    monkeypatch.setattr(checkup.taskcheck, "exec_argv", lambda repo: [
        sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"])

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" T!  T!=TSKX") and code == 1
    assert "the session ended without an outcome, exit 3: boom" in shown


def test_a_session_that_does_not_end_is_stopped(tasked, work, monkeypatch, capsys):
    tasked()
    monkeypatch.setattr(checkup.taskcheck, "TIMEOUT_S", 1)
    monkeypatch.setattr(checkup.taskcheck, "exec_argv", lambda repo: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" T!  T!=TSKT") and code == 1
    assert "no end after 1 s" in shown


def test_a_sample_that_cannot_be_made(tasked, work, monkeypatch, capsys):
    tasked()

    def broken(folder):
        raise OSError("no room")

    monkeypatch.setattr(checkup.taskcheck, "make_sample", broken)

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" T!  T!=TSKS") and code == 1
    assert "the sample repository could not be made: no room" in shown


def test_the_sample_fails_its_tests_until_fixed(tmp_path):
    repo = checkup.taskcheck.make_sample(tmp_path)

    assert not checkup.taskcheck.tests_pass(repo) and checkup.taskcheck.tests_kept(repo)
    fixed = (repo / "interval.py").read_text(encoding="utf-8").replace(
        "end - start)", "end - start + 1)")
    (repo / "interval.py").write_text(fixed, encoding="utf-8")
    assert checkup.taskcheck.tests_pass(repo)
    (repo / "test_interval.py").write_text("", encoding="utf-8")
    assert not checkup.taskcheck.tests_kept(repo)


def test_events_a_session_printed_are_read_and_the_rest_ignored():
    ran = checkup.taskcheck.Ran()
    checkup.taskcheck.read_events("\n".join([
        "not json", "[1]", json.dumps({"kind": "turn_started"}),
        json.dumps({"kind": "tool_rejected", "tool": "run_command", "display": "rm -r x"}),
        json.dumps({"kind": "token_count", "session_total": {"input_tokens": 9,
                                                              "output_tokens": 4}}),
        json.dumps({"kind": "termination", "status": "partial", "reason": "no finish"})]), ran)

    assert (ran.rounds, ran.tools, ran.input_tokens, ran.output_tokens) == (1, 0, 9, 4)
    assert ran.refused == ["run_command rm -r x"] and ran.status == "partial"
    case = checkup.taskcheck.task_case(ran, False, True)
    assert case.code == "TSK0" and "refused: run_command rm -r x" in case.said


# --- what the session's client does with a proxy's password ---------------


def test_the_proxy_s_user_name_and_password_are_sent_as_the_url_means_them(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://alice:p%40ss%3Aw@127.0.0.1:9")

    _, _, headers = HttpClient().connect("http://api.example.test/v1/models")

    assert base64.b64decode(headers["Proxy-Authorization"].split()[-1]) == b"alice:p@ss:w"


def test_an_unreadable_certificate_names_no_issuer():
    assert netcheck.issuer_of(b"\x30\x03\x02\x01") == ""
    assert netcheck.issuer_of(b"") == ""
