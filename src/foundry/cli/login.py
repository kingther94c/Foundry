"""``foundry login`` and ``logout``: the credential a backend is reached with,
kept in the vault, and where it comes from.
"""

from __future__ import annotations

import argparse
import os
import sys

from rich.console import Console

from foundry.cli.wiring import _credential_source
from foundry.core.auth import CredentialVault, EnvKeySource, StaticTokenSource
from foundry.core.config import load_config, user_dir


def cmd_login(args: argparse.Namespace) -> int:
    console = Console()
    home = user_dir()
    vault = CredentialVault(home / "auth.json")

    # Which credential this machine actually uses. Storing an api_key while the
    # configured source reads a token left the user with a "saved" message and
    # an AuthError on the next run, and the error told them to run this very
    # command again.
    config = load_config(home=home)
    source = _credential_source(config, vault)
    gateway = isinstance(source, StaticTokenSource)
    if gateway:
        label = "gateway token"
    elif isinstance(source, EnvKeySource):
        label = "key"
    elif source.vault_field == "anthropic_api_key":
        label = "Anthropic API key"
    else:
        label = "OpenAI API key"
    if getattr(args, "action", None) == "status":
        if getattr(args, "with_api_key", False):
            console.print("[red]login status reads no key[/red]: run it without --with-api-key")
            return 2
        return _login_status(console, source, vault, label)
    if isinstance(source, EnvKeySource):
        console.print(f"[yellow]this backend reads its key from {source.env_var}[/yellow] "
                      "(backend.env_key): set it in your environment; foundry login stores "
                      "nothing for it")
        return 1
    if getattr(args, "with_api_key", False):
        secret = _secret_from_stdin(console, label)
        if secret is None:
            return 1
        source.store(secret)
        console.print(f"[green]saved[/green] {home / 'auth.json'} ({label})")
        return 0

    console.print(
        f"Paste a{'n' if label[0] in 'AEIOU' else ''} {label}. Foundry stores it "
        "encrypted for your Windows account (DPAPI) and never puts it in prompts or logs."
    )

    # getpass.win_getpass reads the console directly through msvcrt and ignores
    # stdin entirely, so the EOFError handler below could never fire and a
    # piped or detached `foundry login` hung forever instead of failing.
    if not sys.stdin.isatty():
        env_var = source.env_var
        console.print(
            f"[red]stdin is not a terminal[/red], so there is nowhere safe to type a "
            f"secret. Set {env_var} in the environment instead, or pipe it to "
            "foundry login --with-api-key."
        )
        return 1

    try:
        import getpass

        secret = getpass.getpass(f"{label.lower()}: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]cancelled[/yellow]")
        return 1
    except OSError as exc:                       # no console attached at all
        console.print(f"[red]cannot read from the console:[/red] {exc}")
        return 1
    if not secret:
        console.print("[red]nothing entered[/red]")
        return 1

    source.store(secret)
    console.print(f"[green]saved[/green] {home / 'auth.json'} ({label})")
    return 0


# More than any key or token is long: what was piped in is something else.
STDIN_SECRET_LIMIT = 16_384


def _secret_from_stdin(console: Console, label: str) -> str | None:
    """The key piped to ``foundry login --with-api-key`` (D-087), as codex's
    ``printenv OPENAI_API_KEY | codex login --with-api-key``.

    Never from a terminal: typed there, the key would be echoed on screen,
    which is the thing the prompt without the flag exists to avoid. And never
    quoted back: a refusal says what is wrong with it, not what it was.
    """
    try:
        at_terminal = sys.stdin.isatty()
    except (AttributeError, ValueError):
        at_terminal = False
    if at_terminal:
        console.print(f"[red]--with-api-key reads the {label} from a pipe[/red], as "
                      "printenv OPENAI_API_KEY | foundry login --with-api-key; at a terminal, "
                      "run foundry login without it and paste the key where it is not shown")
        return None
    # Bytes where there are any, decoded as UTF-8: decoded in the console's
    # code page, a key piped from a UTF-8 file with a byte order mark kept the
    # mark as three more characters of the key. The mark itself is dropped
    # below.
    stream = getattr(sys.stdin, "buffer", None)
    try:
        if stream is not None:
            data = stream.read(STDIN_SECRET_LIMIT + 1)
            too_long = len(data) > STDIN_SECRET_LIMIT
            piped = data.decode("utf-8")
        else:
            piped = sys.stdin.read(STDIN_SECRET_LIMIT + 1)
            too_long = len(piped) > STDIN_SECRET_LIMIT
    except (OSError, ValueError) as exc:     # UnicodeDecodeError is a ValueError
        console.print(f"[red]cannot read stdin[/red] ({type(exc).__name__})")
        return None
    secret = (piped or "").lstrip("\ufeff").strip()
    if not secret:
        console.print(f"[red]nothing on stdin[/red]: pipe the {label} in")
        return None
    if too_long or any(c.isspace() or not c.isprintable() for c in secret):
        console.print(f"[red]what was piped in does not look like the {label}[/red]: one line, "
                      "no spaces, "
                      "is expected (it is not shown here)")
        return None
    return secret


def _login_status(console: Console, source, vault: CredentialVault, label: str) -> int:
    """``foundry login status`` (D-087): where this configuration's credential
    comes from -- never any of it. 0 when there is one, 1 when not."""
    env_var = source.env_var
    if os.environ.get(env_var):
        console.print(f"{label}: from {env_var}")
        return 0
    if isinstance(source, EnvKeySource):
        console.print(f"[yellow]no {label}[/yellow]: this backend reads it from {env_var} "
                      "(backend.env_key), which is not set")
        return 1
    field_name = "token" if isinstance(source, StaticTokenSource) else source.vault_field
    if (vault.load() or {}).get(field_name):
        console.print(f"{label}: stored by foundry login ({vault.path})")
        return 0
    console.print(f"[yellow]no {label}[/yellow]: set {env_var}, or run foundry login")
    return 1


def cmd_logout(args: argparse.Namespace) -> int:
    vault = CredentialVault(user_dir() / "auth.json")
    vault.clear()
    Console().print("[green]credentials removed[/green]")
    return 0
