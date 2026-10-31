"""The v22 reference's changes to researchos/__main__.py, applied as text replacements."""

REPLACEMENTS = [
    (
        "    python -m researchos agreement FILE [FILE ...] [--out FILE]\n",
        "    python -m researchos agreement FILE [FILE ...] [--out FILE]\n"
        "    python -m researchos fetch-series --source {fred,cftc} --id ID --file FILE\n"
        "                                      [--start D] [--vintage D] [--limit N]\n"
        "                                      [--oi-file FILE] [--user-agent TEXT] [--out FILE]\n",
    ),
    (
        "from researchos.sources import build_case, fetch_pages, load_case_spec, load_closes, url_fetcher\n",
        "from researchos.series import fetch_cftc, fetch_fred, save_series\n"
        "from researchos.sources import build_case, fetch_pages, load_case_spec, load_closes, url_fetcher\n",
    ),
    (
        "def _add_out(p: argparse.ArgumentParser) -> None:\n",
        "def _cftc_limit(value: str) -> int:\n"
        "    \"\"\"argparse type: an integer from 1 to 50000.\"\"\"\n"
        "    try:\n"
        "        n = int(value)\n"
        "    except ValueError:\n"
        "        raise argparse.ArgumentTypeError(f\"not an integer: {value!r}\") from None\n"
        "    if not 1 <= n <= 50000:\n"
        "        raise argparse.ArgumentTypeError(f\"must be from 1 to 50000: {value!r}\")\n"
        "    return n\n"
        "\n"
        "\n"
        "def _add_out(p: argparse.ArgumentParser) -> None:\n",
    ),
    (
        "    _add_out(p)\n    return parser\n",
        "    _add_out(p)\n"
        "    p = sub.add_parser(\"fetch-series\")\n"
        "    p.add_argument(\"--source\", required=True, choices=(\"fred\", \"cftc\"))\n"
        "    p.add_argument(\"--id\", required=True, dest=\"series_id\")\n"
        "    p.add_argument(\"--file\", required=True, dest=\"series_file\")\n"
        "    p.add_argument(\"--start\", type=_iso_date, default=None)\n"
        "    p.add_argument(\"--vintage\", type=_iso_date, default=None)\n"
        "    p.add_argument(\"--limit\", type=_cftc_limit, default=None)\n"
        "    p.add_argument(\"--oi-file\", dest=\"oi_file\", default=None)\n"
        "    p.add_argument(\"--user-agent\", dest=\"user_agent\", default=None)\n"
        "    _add_out(p)\n"
        "    return parser\n",
    ),
    (
        "            args.files = [*args.files, *extras]\n",
        "            args.files = [*args.files, *extras]\n"
        "        if args.command == \"fetch-series\":\n"
        "            if args.source == \"cftc\" and (args.start or args.vintage):\n"
        "                parser.error(\"--start and --vintage are for --source fred\")\n"
        "            if args.source == \"fred\" and (args.limit is not None or args.oi_file):\n"
        "                parser.error(\"--limit and --oi-file are for --source cftc\")\n",
    ),
    (
        "    if args.command == \"agreement\":\n        return _agreement(args)\n",
        "    if args.command == \"agreement\":\n        return _agreement(args)\n"
        "    if args.command == \"fetch-series\":\n        return _fetch_series(args)\n",
    ),
    (
        "def _compare_sides(args)",
        "def _fetch_series(args) -> int:\n"
        "    agent = _user_agent(args.user_agent)\n"
        "    try:\n"
        "        if args.source == \"fred\":\n"
        "            series = fetch_fred(args.series_id, user_agent=agent, start=args.start,\n"
        "                                vintage=args.vintage)\n"
        "            save_series(args.series_file, series)\n"
        "        else:\n"
        "            kwargs = {} if args.limit is None else {\"limit\": args.limit}\n"
        "            series, oi = fetch_cftc(args.series_id, user_agent=agent, **kwargs)\n"
        "            save_series(args.series_file, series)\n"
        "            if args.oi_file:\n"
        "                save_series(args.oi_file, oi)\n"
        "    except (OSError, ValueError) as exc:\n"
        "        print(f\"error: {exc}\", file=sys.stderr)\n"
        "        return 1\n"
        "    result = {\"source\": args.source, \"id\": args.series_id, \"observations\": len(series),\n"
        "              \"first\": series[0][0].isoformat() if series else None,\n"
        "              \"last\": series[-1][0].isoformat() if series else None,\n"
        "              \"file\": args.series_file}\n"
        "    if args.source == \"cftc\":\n"
        "        result[\"oi_file\"] = args.oi_file\n"
        "    return _emit(result, args.out)\n"
        "\n"
        "\n"
        "def _compare_sides(args)",
    ),
]


def apply(text: str) -> str:
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise SystemExit(f"main_patch: expected one {old[:50]!r}")
        text = text.replace(old, new)
    return text
