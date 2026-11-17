"""`foundry checkup` (D-204): the first run on a machine whose results leave
it only by word of mouth.

Each case gives one line with a short code; the feedback code holds every
level's mark and the codes of what was not OK, and no host and no secret;
the report stays on the machine. Every failure the network level names is
made here on loopback -- a closed port, a name that does not resolve, a
certificate nobody trusts, a proxy that wants a sign-in, a tunnel to the
wrong name, a web page where the model list should be -- with a TLS server
whose certificate is made for the test, and a proxy that answers CONNECT.
"""

from __future__ import annotations

import base64
import http.server
import json
import re
import socket
import socketserver
import ssl
import threading

import pytest

from foundry import __version__
from foundry.cli import app, checkup
from foundry.core import netcheck
from foundry.core.auth import CredentialVault
from foundry.core.checkup import FAIL, OK, SKIP, WARN, Case, feedback_code, report
from foundry.core.errors import FatalError
from foundry.core.httpc import HttpClient
from selfsigned import certificate

SECRET = "sk-checkup-test-0123456789abcdef"
MODEL = "gpt-test"
MODELS = json.dumps({"data": [{"id": MODEL}, {"id": "other"}]}).encode()
# The checks the fixture stands in for, as the module defines them.
REAL_POWERSHELL = checkup.powershell_case
REAL_LONG_PATHS = checkup.long_paths_case


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
    return home


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


class _Endpoint(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append({"path": self.path,
                                 "authorization": self.headers.get("Authorization")})
        status, content_type, body = self.server.answer
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass        # a client that refused the certificate


@pytest.fixture()
def endpoint(tls_files):
    servers = []

    def start(answer=(200, "application/json", MODELS), tls=False):
        """A server answering GET with ``answer``; over TLS with the test's
        certificate when ``tls`` is true, or with the (cert, key) it is."""
        server = _Server(("127.0.0.1", 0), _Endpoint)
        server.answer, server.seen = answer, []
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


def _checkup(capsys, work):
    code = app.main(["checkup", "-C", str(work)])
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

    assert line == f"FB1 {checkup.source_token()[0]} E+ C+ K:key-env M:- R:chat N+"
    assert code == 0
    # Asked once, with the credential, which is shown nowhere.
    assert server.seen == [{"path": "/v1/models", "authorization": f"Bearer {SECRET}"}]
    assert "2 model(s) listed, gpt-test among them" in shown
    text = _report(machine)
    assert line in text and "## N network" in text
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

    assert line.endswith(f" {mark}  {mark}={code}")
    assert exit_code == (1 if mark == "N!" else 0)
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

    assert line.endswith(" N!  N!=TCP0") and code == 1
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

    assert line.endswith(" N!  N!=DNS0,PAC") and code == 1
    assert "Windows uses the proxy script http://wpad.example.test/proxy.pac" in shown
    assert "tcp not tried: the name did not resolve" in shown
    assert "tls not tried: the name did not resolve" in shown


def test_a_certificate_nobody_trusts_is_named_by_its_issuer_until_the_bundle_trusts_it(
        endpoint, tls_files, machine, work, monkeypatch, capsys):
    server = endpoint(tls=True)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, f"https://127.0.0.1:{server.server_port}/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N!  N!=TLS1") and code == 1
    assert ("the certificate (issued by CN=Foundry checkup test CA, O=Foundry tests) is not "
            "trusted by the Windows certificate store") in shown
    assert "set FOUNDRY_CA_BUNDLE to it" in shown
    assert all(seen["authorization"] is None for seen in server.seen)

    monkeypatch.setenv("FOUNDRY_CA_BUNDLE", str(tls_files[0]))
    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N+") and code == 0
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

    assert line.endswith(" N!  N!=TLS5") and code == 1
    assert "the certificate (issued by CN=Foundry checkup test CA, O=Foundry tests) has expired" in shown


def test_a_proxy_that_wants_a_sign_in_says_what_it_asks_for(proxy, machine, work, monkeypatch,
                                                            capsys):
    asking = proxy("407")
    monkeypatch.setenv("HTTPS_PROXY", f"http://alice:p%40ss@127.0.0.1:{asking.server_address[1]}")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "https://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N!  N!=PX407") and code == 1
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

    assert line.endswith(" N+") and code == 0
    assert "tunnel the proxy opens a tunnel to api.example.test:443" in shown
    assert "dns 127.0.0.1 is 127.0.0.1 (the proxy looks up api.example.test)" in shown
    # The tunnel was asked for twice: by the check, and by the client a session uses.
    assert [seen[0] for seen in tunnel.seen] == ["CONNECT api.example.test:443 HTTP/1.1"] * 2
    assert server.seen[-1]["authorization"] == f"Bearer {SECRET}"


def test_a_certificate_for_another_name_is_said_to_be(endpoint, proxy, tls_files, machine, work,
                                                      monkeypatch, capsys):
    server = endpoint(tls=True)
    tunnel = proxy("tunnel", ("127.0.0.1", server.server_port))
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{tunnel.server_address[1]}")
    monkeypatch.setenv("FOUNDRY_CA_BUNDLE", str(tls_files[0]))
    _config(machine, "https://other.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith("N!  C!=KEY0 N!=TLS4") and code == 1
    assert "is not for other.example.test" in shown


def test_a_proxy_declared_https_is_refused_before_anything_is_tried(machine, work, monkeypatch,
                                                                    capsys):
    monkeypatch.setenv("HTTPS_PROXY", "https://127.0.0.1:9")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "https://api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N!  N!=PRXS") and code == 1
    assert " dns " not in shown


# --- the configuration ----------------------------------------------------


def test_a_utf16_configuration_has_its_own_code_and_the_network_is_not_checked(
        machine, work, capsys):
    (machine / "config.toml").write_text('[backend]\nmodel = "x"\n', encoding="utf-16")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" C! K:? M:? R:? N-  C!=CFG16") and code == 1
    assert "all not checked: the configuration could not be read" in shown


def test_a_configuration_in_the_system_code_page_has_its_own_code(machine, work, capsys):
    (machine / "config.toml").write_bytes(b'[backend]\nmodel = "caf\xe9"\n')

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" C! K:? M:? R:? N-  C!=CFGCP") and code == 1


def test_a_base_url_that_is_not_an_address_skips_the_network(machine, work, monkeypatch,
                                                             capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    _config(machine, "api.example.test/v1")

    code, shown, line = _checkup(capsys, work)

    assert line.endswith(" N-  C!=URL0") and code == 1
    assert "all not checked: base_url is not an address" in shown


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

    assert " C! K:key-env M:- R:? N+  C!=PRO0" in line and code == 1
    assert "models not asked: the protocol is not one Foundry speaks" in shown
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
    assert line == f"FB1 v{__version__} E! C+ K:key-env M:- R:chat N+  E!=GIT0"
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


# --- what the session's client does with a proxy's password ---------------


def test_the_proxy_s_user_name_and_password_are_sent_as_the_url_means_them(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://alice:p%40ss%3Aw@127.0.0.1:9")

    _, _, headers = HttpClient().connect("http://api.example.test/v1/models")

    assert base64.b64decode(headers["Proxy-Authorization"].split()[-1]) == b"alice:p@ss:w"


def test_an_unreadable_certificate_names_no_issuer():
    assert netcheck.issuer_of(b"\x30\x03\x02\x01") == ""
    assert netcheck.issuer_of(b"") == ""
