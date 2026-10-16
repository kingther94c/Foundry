"""Command line.

    python -m researchos {train,evaluate} --root DIR --playbook FILE --ids ID[,ID...] [--seed N]
                                          [--knowledge-cutoff YYYY-MM-DD] [--mask-give-up CONF]
                                          [--mask-abstract] [--mask-history-years N]
                                          [--out FILE]
    python -m researchos build --root DIR --spec FILE --closes FILE --market-closes FILE
                               [--release-url URL] [--transcript-url URL]
                               [--expectations-url URL] [--user-agent TEXT]
                               [--out FILE]
    python -m researchos compare A.json B.json [--metric NAME] [--resamples N] [--seed S]
                                 [--out FILE]
    python -m researchos compare -a FILE [-a FILE ...] -b FILE [-b FILE ...] [--metric NAME]
                                 [--resamples N] [--seed S] [--out FILE]
    python -m researchos blend FILE [FILE ...] [--rule NAME] [--eta X] [--out FILE]

Each command prints its result as JSON; with --out FILE it writes the result to
FILE instead (researchos.jsonio.write_json) and prints nothing.

train and evaluate refuse a case whose event date is not an ISO date, or is on or
before the latest event date the playbook has learned from (its forecast would use
reactions it could not have known): exit 1, with the reason on stderr.

build finds the case's documents through the API's server-side web search and
fetch. Given any --release-url, --transcript-url or --expectations-url (each may
be repeated) it fetches exactly those addresses instead, with no web tools
(researchos.sources.url_fetcher), sending --user-agent (default: the
RESEARCHOS_USER_AGENT environment variable, else "ResearchOS/0.1 (research use)");
sites such as SEC EDGAR ask for one naming who is fetching.

A file compare reads is what `evaluate` or `train` printed (bare or wrapped as
{"result": ...}); with -a/-b each side is the merge of its files' cases, in the
order given (researchos.compare.merge_reports).

blend reads such files the same way, merges them in the order given, and prints
researchos.blend.replay of their cases: a rule for combining the perspectives
(uniform, median, hedge, leader) replayed walk-forward over the scored records.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from tools.stats import is_number

from researchos.blend import RULES, replay
from researchos.cases import CASE_FILE, load_outcome
from researchos.compare import compare, load_report, merge_reports
from researchos.jsonio import write_json
from researchos.llm import ClaudeJSON, LLMError, anthropic_client
from researchos.loop import Roles, evaluate, parse_cutoff, train
from researchos.masking import MaskConfig
from researchos.sources import build_case, fetch_pages, load_case_spec, load_closes, url_fetcher

USER_AGENT_ENV = "RESEARCHOS_USER_AGENT"
DEFAULT_USER_AGENT = "ResearchOS/0.1 (research use)"


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


def _history_years(value: str) -> int:
    """argparse type: an integer N >= 0."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be an integer N >= 0: {value!r}")
    return n


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


def _positive_number(value: str) -> float:
    """argparse type: a finite number > 0."""
    try:
        x = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not (is_number(x) and x > 0):  # also rejects nan and infinity
        raise argparse.ArgumentTypeError(f"must be a positive number: {value!r}")
    return x


def _add_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=None, metavar="FILE",
                   help="write the JSON result to FILE (UTF-8) instead of printing it")


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
        p.add_argument("--mask-abstract", dest="mask_abstract", action="store_true",
                       help="rewrite each pre-masked document as a generic brief (abstractor "
                            "agent) before the masker and guesser rounds")
        p.add_argument("--mask-history-years", dest="mask_history_years", type=_history_years,
                       default=None, metavar="N",
                       help="replace every year more than N years before the case's event "
                            "year with 'an earlier year' when masking (N >= 0)")
        _add_out(p)
    p = sub.add_parser("build")
    p.add_argument("--root", required=True)
    p.add_argument("--spec", required=True, help="JSON file holding one CaseSpec")
    p.add_argument("--closes", required=True, help="JSON file of the stock's daily closes")
    p.add_argument("--market-closes", required=True, dest="market_closes",
                   help="JSON file of the market's daily closes")
    for kind, what in (("release", "the results release"),
                       ("transcript", "the earnings call transcript"),
                       ("expectations", "a page stating the pre-report consensus")):
        p.add_argument(f"--{kind}-url", dest=f"{kind}_urls", action="append", default=None,
                       metavar="URL",
                       help=f"fetch {what} from URL (repeat for several) instead of searching "
                            f"the web; with any of the URL options no web tools are used")
    p.add_argument("--user-agent", dest="user_agent", default=None, metavar="TEXT",
                   help=f"User-Agent sent when fetching the URLs (default: the "
                        f"{USER_AGENT_ENV} environment variable, else "
                        f"{DEFAULT_USER_AGENT!r}); sites such as SEC EDGAR ask for one "
                        f"naming who is fetching")
    _add_out(p)
    p = sub.add_parser("compare", help="paired comparison of two reports, or of two sides of "
                                       "several files each")
    # Either two positional files, A.json B.json, or each side as -a FILE ... / -b FILE ...;
    # _compare_sides checks which form was used (argparse cannot say "two, or none").
    p.add_argument("files", nargs="*", metavar="REPORT",
                   help="A.json B.json: the baseline's report, then the report compared with "
                        "it (evaluate or train output)")
    p.add_argument("-a", dest="files_a", action="append", default=None, metavar="FILE",
                   help="a file of the baseline side; repeat for several, merged in the order "
                        "given")
    p.add_argument("-b", dest="files_b", action="append", default=None, metavar="FILE",
                   help="a file of the side compared with it; repeat for several")
    p.add_argument("--metric", type=_brier_metric, default="brier_judge",
                   help="per-case Brier score to compare (default brier_judge)")
    p.add_argument("--resamples", type=_resamples, default=10000,
                   help="bootstrap resamples (default 10000)")
    p.add_argument("--seed", type=int, default=0)
    _add_out(p)
    p = sub.add_parser("blend", help="replay a rule for combining the perspectives "
                                     "walk-forward over scored records")
    p.add_argument("files", nargs="+", metavar="FILE",
                   help="evaluate or train output; several files are merged in the order given")
    p.add_argument("--rule", choices=RULES, default="hedge",
                   help="how to combine the perspectives (default hedge)")
    p.add_argument("--eta", type=_positive_number, default=0.5,
                   help="learning rate of the hedge rule, a positive number (default 0.5)")
    _add_out(p)
    return parser


def build_roles(client, *, abstract: bool = False) -> Roles:
    return Roles(
        masker=ClaudeJSON(client, effort="medium"),
        guesser=ClaudeJSON(client, effort="medium"),
        analyst=ClaudeJSON(client, effort="high"),
        judge=ClaudeJSON(client, effort="high"),
        reflector=ClaudeJSON(client, effort="high"),
        abstractor=ClaudeJSON(client, effort="high") if abstract else None,
    )


def main(argv=None, *, client_factory=None) -> int:
    parser = _parser()
    try:
        args, extras = parser.parse_known_args(argv)
        if extras:
            # A nargs="*" (or "+") positional takes only the first run of strings, so in
            # `compare A.json --seed 5 B.json` B.json comes back here; it is still one of
            # compare's files, as it was when A and B were two positionals (likewise for
            # blend's files). Anything else is an unrecognized argument, reported exactly
            # as parse_args reports it.
            if (args.command not in ("compare", "blend")
                    or any(s.startswith("-") for s in extras)):
                parser.error(f"unrecognized arguments: {' '.join(extras)}")
            args.files = [*args.files, *extras]
    except SystemExit as exc:  # argparse already wrote the message to stderr
        return 0 if exc.code in (0, None) else 2
    if args.command == "build":
        return _build(args, client_factory)
    if args.command == "compare":
        return _compare(args)
    if args.command == "blend":
        return _blend(args)
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
        roles = build_roles(client, abstract=args.mask_abstract)
        run = train if args.command == "train" else evaluate
        kwargs = {"seed": args.seed, "knowledge_cutoff": args.knowledge_cutoff}
        if (args.mask_give_up is not None or args.mask_abstract
                or args.mask_history_years is not None):
            kwargs["mask_config"] = MaskConfig(give_up_confidence=args.mask_give_up,
                                               abstract=args.mask_abstract,
                                               history_years=args.mask_history_years)
        result = run(args.root, ids, args.playbook, roles, **kwargs)
    except LLMError as exc:
        print(f"error: LLM call failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: missing file: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # a look-ahead or undated case, or one masking cannot date
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit(result, args.out)


def _user_agent(given: str | None) -> str:
    """--user-agent, else $RESEARCHOS_USER_AGENT (unless blank), else DEFAULT_USER_AGENT."""
    if given is not None:
        return given
    from_env = os.environ.get(USER_AGENT_ENV, "")
    return from_env if from_env.strip() else DEFAULT_USER_AGENT


def _build(args, client_factory) -> int:
    urls = {"release": args.release_urls or [], "transcript": args.transcript_urls or [],
            "expectations": args.expectations_urls or []}
    try:
        spec = load_case_spec(args.spec)
        stock_closes = load_closes(args.closes)
        market_closes = load_closes(args.market_closes)
        client = (client_factory or anthropic_client)()
        agent = ClaudeJSON(client, effort="medium")
        if any(urls.values()):
            # The addresses are known: fetch exactly those, with no web tools.
            fetch = url_fetcher(urls, user_agent=_user_agent(args.user_agent))
        else:
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
    return _emit({"case_id": case.case_id, "outcome": asdict(outcome)}, args.out)


def _compare(args) -> int:
    sides = _compare_sides(args)
    if sides is None:
        return 2
    files_a, files_b = sides
    try:
        a = merge_reports([load_report(path) for path in files_a])
        b = merge_reports([load_report(path) for path in files_b])
        result = compare(a, b, metric=args.metric, resamples=args.resamples, seed=args.seed)
    except OSError as exc:  # missing file, a directory, unreadable
        print(f"error: cannot read report: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # not JSON, not a report, a case scored twice on one side
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit(result, args.out)


def _blend(args) -> int:
    try:
        merged = merge_reports([load_report(path) for path in args.files])
        result = replay(merged["cases"], args.rule, eta=args.eta)
    except OSError as exc:  # missing file, a directory, unreadable
        print(f"error: cannot read report: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # not JSON, not a report, or a record replay refuses
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit(result, args.out)


def _compare_sides(args) -> tuple[list[str], list[str]] | None:
    """The files of side A and of side B, or None (after a message on stderr) on bad arguments.

    Either exactly two positional files (A.json B.json: one file a side) or -a and -b, each
    given at least once; the two forms do not mix.
    """
    files, files_a, files_b = args.files, args.files_a or [], args.files_b or []
    if files_a or files_b:
        if files:
            problem = "give the reports either as A.json B.json or with -a/-b, not both"
        elif not (files_a and files_b):
            problem = "-a and -b go together: each side needs at least one file"
        else:
            return files_a, files_b
    elif len(files) == 2:
        return [files[0]], [files[1]]
    else:
        problem = f"compare needs two reports, A.json B.json (or -a/-b), got {len(files)}"
    print(f"error: {problem}", file=sys.stderr)
    return None


def _emit(data, out) -> int:
    """Print data as JSON, or with --out write it to that file and print nothing.

    Returns the exit code: 1, with a message on stderr, if the file cannot be
    written. Whatever the command already did (a saved playbook, a built case)
    stands either way.
    """
    if out is None:
        print(json.dumps(data, indent=2))
        return 0
    try:
        write_json(out, data)
    except (OSError, ValueError) as exc:  # a directory, a missing folder, no permission, ...
        print(f"error: cannot write {out}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
