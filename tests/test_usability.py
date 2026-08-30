"""Whether a developer can actually use this.

A review round measured the cost of five rounds of hardening: 66/100 everyday
commands auto-allowed, chained commands 7/15 and unimprovable by any number of
rules, idiomatic PowerShell 0/12, and the corporate proxy stripped from every
build. A tool nobody can stand to use protects nothing, so these are pinned the
same way the security properties are.
"""

from __future__ import annotations

import pytest

from foundry.core.policy.engine import Layer, PolicyEngine, Rule, Verdict
from foundry.core.policy.segmenter import segment_command
from foundry.core.tools.base import Operation, ToolKind
from foundry.core.winapi import child_environment


def cmd_op(command: str) -> Operation:
    return Operation(tool="run_command", kind=ToolKind.MUTATOR,
                     args={"command": command}, display=command, target=command)


def engine_with(*patterns: str) -> PolicyEngine:
    engine = PolicyEngine()
    for n, pattern in enumerate(patterns):
        engine.add_rule(Rule(tool="run_command", pattern=pattern, verdict=Verdict.ALLOW,
                             layer=Layer.USER, rule_id=f"user.{n}"))
    return engine


# A plausible allowlist a developer would actually write.
ALLOWLIST = ("python *", "git status*", "git diff*", "git log*", "npm *",
             "dotnet *", "cargo *", "ruff *")


# --- allow rules compose across a chain ----------------------------------


@pytest.mark.parametrize("command", [
    "git status; python -m pytest -q",
    "npm run build; python -m pytest -q",
    "python -m pytest -q; ruff check .",
    "git status; git diff; python -m pytest",
])
def test_a_chain_is_allowed_when_every_part_is(command):
    """One rule was required to cover the whole chain, so two rules each
    covering half allowed nothing -- and no number of rules could ever cover
    `git status; python -m pytest`, while the prompt tells the model to chain
    with ';' because PowerShell 5.1 has no '&&'."""
    decision, _ = engine_with(*ALLOWLIST).evaluate(cmd_op(command))
    assert decision.verdict is Verdict.ALLOW


@pytest.mark.parametrize("command", [
    "python -m pytest -q; some-unknown-tool",
    "git status; curl http://example.com",
])
def test_a_chain_with_an_uncovered_part_is_not_allowed(command):
    """Composition must not become permissiveness: one uncovered part still
    blocks the whole command."""
    decision, _ = engine_with(*ALLOWLIST).evaluate(cmd_op(command))
    assert decision.verdict is not Verdict.ALLOW


def test_the_chained_bypass_is_still_closed():
    decision, _ = engine_with(*ALLOWLIST).evaluate(
        cmd_op("python -m pytest -q; git reset --hard"))
    assert decision.verdict is Verdict.DENY
    assert decision.step == 0


# --- a quoted metacharacter is data --------------------------------------


@pytest.mark.parametrize("command", [
    "git log --grep='#123' --oneline",
    "Select-String -Path src/app.py -Pattern '# TODO'",
    'python -c "print(\'#\')"',
    "git commit -m 'fix #42'",
])
def test_quoted_metacharacters_do_not_force_a_prompt(command):
    assert segment_command(command).trusted


# --- the corpus ----------------------------------------------------------

EVERYDAY = [
    "python -m pytest -q",
    "python -m pytest tests/test_foo.py -v",
    "python -m pip install -r requirements.txt",
    "python -m mypy src",
    "python -m build",
    "python scripts/generate.py",
    "npm test",
    "npm run build",
    "npm ci",
    "npm run lint -- --fix",
    "dotnet build",
    "dotnet test --filter Category=Unit",
    "dotnet restore",
    "cargo test --all",
    "cargo clippy",
    "ruff check .",
    "ruff format src",
    "git status",
    "git diff --stat",
    "git log --oneline -20",
    "git status; python -m pytest -q",
    "npm run build; python -m pytest -q",
    "python -m pytest -q; ruff check .",
    "git log --grep='#42'",
]


def test_most_everyday_commands_are_auto_allowable():
    """The number that decides whether anyone keeps using this."""
    engine = engine_with(*ALLOWLIST)
    allowed = [c for c in EVERYDAY
               if engine.evaluate(cmd_op(c))[0].verdict is Verdict.ALLOW]
    ratio = len(allowed) / len(EVERYDAY)
    missing = [c for c in EVERYDAY if c not in allowed]
    assert ratio >= 0.9, f"only {len(allowed)}/{len(EVERYDAY)} auto-allowed: {missing}"


# --- the corporate environment reaches the build --------------------------


@pytest.mark.parametrize("name,value", [
    ("HTTPS_PROXY", "http://proxy.corp:8080"),
    ("HTTP_PROXY", "http://proxy.corp:8080"),
    ("NO_PROXY", "localhost"),
    ("PIP_INDEX_URL", "https://nexus.corp/simple"),
    ("NPM_CONFIG_REGISTRY", "https://nexus.corp/npm"),
    ("REQUESTS_CA_BUNDLE", "C:/certs/corp.pem"),
    ("NODE_EXTRA_CA_CERTS", "C:/certs/corp.pem"),
    ("SSL_CERT_FILE", "C:/certs/corp.pem"),
    ("VIRTUAL_ENV", "C:/proj/.venv"),
    ("JAVA_HOME", "C:/jdk"),
])
def test_build_tooling_configuration_reaches_the_child(name, value):
    """Stripping these made pip, npm, dotnet and docker fail or hang behind a
    corporate proxy -- exactly the environment this product exists for."""
    env = child_environment({"PATH": "C:/bin", name: value})
    assert env.get(name) == value


@pytest.mark.parametrize("name", [
    "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "MY_PASSWORD",
    "NPM_CONFIG_AUTH_TOKEN", "PIP_EXTRA_INDEX_PASSWORD",
])
def test_credential_shaped_names_are_still_stripped(name):
    """Including inside the newly-forwarded prefixes."""
    assert name not in child_environment({"PATH": "C:/bin", name: "secret"})
