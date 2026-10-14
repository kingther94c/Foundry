"""Command line.

    python -m researchos {train,evaluate} --root DIR --playbook FILE --ids ID[,ID...] [--seed N]
                                          [--knowledge-cutoff YYYY-MM-DD] [--mask-give-up CONF]
    python -m researchos build --root DIR --spec FILE --closes FILE --market-closes FILE
    python -m researchos compare A.json B.json [--metric NAME] [--resamples N] [--seed S]
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from dataclasses import asdict
from pathlib import Path

from researchos.cases import CASE_FILE, load_outcome
from researchos.compare import compare, load_report
from researchos.llm import ClaudeJSON, LLMError, anthropic_client
from researchos.loop import Roles, evaluate, parse_cutoff, train
from researchos.masking import MaskConfig
from researchos.sources import build_case, fetch_pages, load_case_spec, load_closes


def _iso_date(value: str) -> str:
    """argparse type: an ISO date, passed on as the string given (stripped)."""
    try:
        parse_cutoff(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {value!r}") from None
    return value.strip()


def _give_up_confidence(value: str) -> float:
    """argparse type: a number with 0 < CONF <= 1."""
    try:
        conf = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not 0 < conf <= 1:  # also rejects nan
        raise argparse.ArgumentTypeError(f"must be a number with 0 < CONF <= 1: {value!r}")
    return conf


def _brier_metric(value: str) -> str:
    """argparse type: a metric name starting with "brier_" (compare assumes lower is better)."""
    if not value.startswith("brier_"):
        raise argparse.ArgumentTypeError(f"must name a Brier score (start with 'brier_'): "
                                         f"{value!r}")
    return value


def _resamples(value: str) -> int:
    """argparse type: an integer >= 1."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {value!r}")
    return n


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
        p.add_argument("--mask-give-up", dest="mask_give_up", type=_give_up_confidence,
                       default=None, metavar="CONF",
                       help="stop masking a case (as identifiable) once the guesser names it "
                            "with at least this confidence (0 < CONF <= 1)")
    p = sub.add_parser("build")
    p.add_argument("--root", required=True)
    p.add_argument("--spec", required=True, help="JSON file holding one CaseSpec")
    p.add_argument("--closes", required=True, help="JSON file of the stock's daily closes")
    p.add_argument("--market-closes", required=True, dest="market_closes",
                   help="JSON file of the market's daily closes")
    p = sub.add_parser("compare", help="paired comparison of two evaluation reports")
    p.add_argument("report_a", metavar="A.json", help="evaluation report of the baseline")
    p.add_argument("report_b", metavar="B.json", help="evaluation report compared with it")
    p.add_argument("--metric", type=_brier_metric, default="brier_judge",
                   help="per-case Brier score to compare (default brier_judge)")
    p.add_argument("--resamples", type=_resamples, default=10000,
                   help="bootstrap resamples (default 10000)")
    p.add_argument("--seed", type=int, default=0)
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
    if args.command == "compare":
        return _compare(args)
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
        kwargs = {"seed": args.seed, "knowledge_cutoff": args.knowledge_cutoff}
        if args.mask_give_up is not None:
            kwargs["mask_config"] = MaskConfig(give_up_confidence=args.mask_give_up)
        result = run(args.root, ids, args.playbook, roles, **kwargs)
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


def _compare(args) -> int:
    try:
        a = load_report(args.report_a)
        b = load_report(args.report_b)
        result = compare(a, b, metric=args.metric, resamples=args.resamples, seed=args.seed)
    except OSError as exc:  # missing file, a directory, unreadable
        print(f"error: cannot read report: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # not JSON, not a report, a case scored twice
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
