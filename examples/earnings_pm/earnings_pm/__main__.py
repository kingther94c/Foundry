"""Command line: python -m earnings_pm TICKER.

Progress goes to stderr, one line per event. The markdown memo goes to stdout.
With --out DIR, <DIR>/<TICKER>.md and <DIR>/<TICKER>.json are written too.

Exit codes: 0 ok, 1 output files could not be written, 2 bad arguments,
3 the model refused, 4 the agent could not produce a memo, 5 Claude API error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

import anthropic

from .agent import DEFAULT_MODEL, AgentError, AgentRefused, run_agent
from .edgar import USER_AGENT_ENV, EdgarClient

EXIT_OK = 0
EXIT_OUTPUT = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_AGENT = 4
EXIT_API = 5

EFFORTS = ("low", "medium", "high", "xhigh", "max")
API_KEY_ENV = "ANTHROPIC_API_KEY"

# Letters, digits, dot and dash (BRK.B, BRK-B); also keeps --out paths inside DIR.
_TICKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,14}")


def _ticker(value: str) -> str:
    text = value.strip()
    if not _TICKER.fullmatch(text):
        raise argparse.ArgumentTypeError(
            f"invalid ticker {value!r}: use letters, digits, '.' or '-'"
        )
    return text.upper()


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {value!r}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earnings_pm",
        description="Write a portfolio manager's memo on a company's latest earnings.",
    )
    parser.add_argument("ticker", type=_ticker, help="stock ticker, e.g. MSFT")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"default {DEFAULT_MODEL}")
    parser.add_argument("--effort", choices=EFFORTS, default="high", help="default high")
    parser.add_argument(
        "--max-requests", type=_positive_int, default=20, help="default 20"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="also write <DIR>/<TICKER>.md and .json"
    )
    return parser


def _write(stream: TextIO, text: str) -> None:
    """Write text, degrading characters the stream's encoding cannot carry."""
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, errors="replace").decode(encoding))
    stream.flush()


def _error(message: str) -> None:
    _write(sys.stderr, f"earnings_pm: error: {message}\n")


def _progress(kind: str, detail: str) -> None:
    line = " ".join(f"[{kind}] {detail}".split())
    _write(sys.stderr, line + "\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: 2 for bad arguments, 0 for --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    factory = client_factory if client_factory is not None else anthropic.Anthropic
    edgar = EdgarClient() if os.environ.get(USER_AGENT_ENV, "").strip() else None
    _progress("start", f"{args.ticker} model={args.model} effort={args.effort} "
              f"edgar={'on' if edgar is not None else 'off'}")

    try:
        client = factory()
        result = run_agent(
            args.ticker,
            client=client,
            edgar=edgar,
            model=args.model,
            effort=args.effort,
            max_requests=args.max_requests,
            on_event=_progress,
        )
    except AgentRefused as exc:
        _error(str(exc))
        return EXIT_REFUSED
    except AgentError as exc:
        _error(str(exc))
        return EXIT_AGENT
    except anthropic.AuthenticationError as exc:
        _error(f"authentication failed; check {API_KEY_ENV}. ({exc.message})")
        return EXIT_API
    except anthropic.APIError as exc:
        _error(f"Claude API error ({type(exc).__name__}): {exc.message}")
        return EXIT_API
    except anthropic.AnthropicError as exc:
        # Raised by the client constructor when no API key is configured.
        _error(f"{exc}. Set {API_KEY_ENV}.")
        return EXIT_API

    _write(sys.stdout, result.markdown if result.markdown.endswith("\n")
           else result.markdown + "\n")

    if args.out is not None:
        record = {
            "memo": result.memo,
            "model": args.model,
            "effort": args.effort,
            "requests": result.requests,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        md_path = args.out / f"{args.ticker}.md"
        json_path = args.out / f"{args.ticker}.json"
        try:
            args.out.mkdir(parents=True, exist_ok=True)
            md_path.write_text(result.markdown, encoding="utf-8")
            json_path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            _error(f"could not write output: {exc}")
            return EXIT_OUTPUT
        _progress("wrote", f"{md_path} {json_path}")

    _progress("done", f"requests={result.requests} input_tokens={result.input_tokens} "
              f"output_tokens={result.output_tokens}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
