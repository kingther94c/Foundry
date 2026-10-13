"""Command line.

    python -m researchos {train,evaluate} --root DIR --playbook FILE --ids ID[,ID...] [--seed N]
                                          [--knowledge-cutoff YYYY-MM-DD]
    python -m researchos build --root DIR --spec FILE --closes FILE --market-closes FILE
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from dataclasses import asdict
from pathlib import Path

from researchos.cases import CASE_FILE, load_outcome
from researchos.llm import ClaudeJSON, LLMError, anthropic_client
from researchos.loop import Roles, evaluate, parse_cutoff, train
from researchos.sources import build_case, fetch_pages, load_case_spec, load_closes


def _iso_date(value: str) -> str:
    """argparse type: an ISO date, passed on as the string given (stripped)."""
    try:
        parse_cutoff(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {value!r}") from None
    return value.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m researchos")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("train", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        p.add_argument("--playbook", required=True)
        p.add_argument("--ids", required=True, help="comma-separated case ids")
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--knowledge-cutoff", dest="knowledge_cutoff", type=_iso_date,
                       default=None,
                       help="last date the models can know about (YYYY-MM-DD); identifiable "
                            "cases whose event date is after it are analyzed, not skipped")
    p = sub.add_parser("build")
    p.add_argument("--root", required=True)
    p.add_argument("--spec", required=True, help="JSON file holding one CaseSpec")
    p.add_argument("--closes", required=True, help="JSON file of the stock's daily closes")
    p.add_argument("--market-closes", required=True, dest="market_closes",
                   help="JSON file of the market's daily closes")
    return parser


def build_roles(client) -> Roles:
    return Roles(
        masker=ClaudeJSON(client, effort="medium"),
        guesser=ClaudeJSON(client, effort="medium"),
        analyst=ClaudeJSON(client, effort="high"),
        judge=ClaudeJSON(client, effort="high"),
        reflector=ClaudeJSON(client, effort="high"),
    )


def main(argv=None, *, client_factory=None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already wrote the message to stderr
        return 0 if exc.code in (0, None) else 2
    if args.command == "build":
        return _build(args, client_factory)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        print("error: --ids needs at least one case id", file=sys.stderr)
        return 2

    missing = [i for i in ids if not (Path(args.root) / i / CASE_FILE).is_file()]
    if missing:
        print(f"error: case not found under {args.root}: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        client = (client_factory or anthropic_client)()
        roles = build_roles(client)
        run = train if args.command == "train" else evaluate
        result = run(args.root, ids, args.playbook, roles, seed=args.seed,
                     knowledge_cutoff=args.knowledge_cutoff)
    except LLMError as exc:
        print(f"error: LLM call failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: missing file: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _build(args, client_factory) -> int:
    try:
        spec = load_case_spec(args.spec)
        stock_closes = load_closes(args.closes)
        market_closes = load_closes(args.market_closes)
        client = (client_factory or anthropic_client)()
        agent = ClaudeJSON(client, effort="medium")
        fetch = functools.partial(fetch_pages, client)
        case = build_case(args.root, spec, fetch=fetch, agent=agent,
                          stock_closes=stock_closes, market_closes=market_closes)
        outcome = load_outcome(args.root, case.case_id)
    except LLMError as exc:
        print(f"error: LLM call failed: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        print(f"error: document not found: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, IsADirectoryError) as exc:
        print(f"error: missing file: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # includes malformed JSON
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"case_id": case.case_id, "outcome": asdict(outcome)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
