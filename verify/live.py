"""What a run against a real endpoint needs: the key, and what the run cost.

The key is never copied, linked or written. It is found here, in the process
the user started, and handed to Foundry in the environment variable its
temporary home's configuration names (backend.env_key). Foundry keeps that
variable from every command it runs and scrubs its value from what it
records; `scan` then looks through the run's folder for the key itself.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = "FOUNDRY_VERIFY_KEY"

# US$ per million tokens: input, output, input read from cache. Anthropic's
# first-party prices as of 2026-11; Haiku's are for prompts up to 100K tokens.
PRICES = {
    "claude-fable-5-2": (8.0, 40.0, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def obtain(prompt="Anthropic API key for the live runs (not shown, not stored): "):
    """The key: the variable when it is set, else what `foundry login` stored
    for Anthropic, read in this process, else typed at a prompt that shows
    nothing."""
    key = os.environ.get(VARIABLE, "").strip()
    if key:
        return key
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from foundry.core.auth import CredentialVault
        from foundry.core.config import user_dir
        stored = CredentialVault(user_dir() / "auth.json").load() or {}
    except Exception:                      # no vault here, or not this user's
        stored = {}
    key = str(stored.get("anthropic_api_key") or "").strip()
    if key:
        return key
    import getpass
    key = getpass.getpass(prompt).strip()
    if not key:
        sys.exit("no key entered")
    return key


def scan(folder, key):
    """The files under ``folder`` that hold ``key``, relative to it."""
    folder, needle, found = Path(folder), key.encode("utf-8"), []
    for path in folder.rglob("*"):
        try:
            if (path.is_file() and path.stat().st_size < 50_000_000
                    and needle in path.read_bytes()):
                found.append(path.relative_to(folder).as_posix())
        except OSError:
            pass
    return sorted(found)


def usage_of(sessions):
    """The tokens the journals under ``sessions`` recorded."""
    used = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "records": 0}
    sessions = Path(sessions)
    for journal in (sorted(sessions.glob("*/events.jsonl")) if sessions.is_dir() else []):
        for line in journal.read_text(encoding="utf-8").splitlines():
            record = json.loads(line) if line.strip() else {}
            if record.get("type") != "token_usage":
                continue
            data = record.get("data") or record.get("payload") or {}
            for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
                used[name] += int(data.get(name) or 0)
            used["records"] += 1
    return used


def cost(model, used):
    """An estimate in US$ of what ``used`` cost on ``model``, or None for a
    model with no price here. It leaves out the premium on tokens written to
    the cache, so the bill is somewhat higher."""
    price = PRICES.get(model)
    if price is None:
        return None
    fresh = used["input_tokens"] - used["cached_input_tokens"]
    return round((fresh * price[0] + used["cached_input_tokens"] * price[2]
                  + used["output_tokens"] * price[1]) / 1e6, 4)
