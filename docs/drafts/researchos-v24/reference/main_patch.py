"""The v24 reference's changes to researchos/__main__.py, applied as text replacements.

Written against __main__.py as it stands with v22 landed: it anchors on the end of the
parser and on the dispatch of fetch-series, whatever their exact form, by inserting before
`    return parser\n` and before `    ids = [i.strip()`.
"""

REPLACEMENTS = [
    (
        "    return parser\n",
        "    p = sub.add_parser(\"snapshot\")\n"
        "    p.add_argument(\"--root\", required=True)\n"
        "    p.add_argument(\"--config\", required=True)\n"
        "    p.add_argument(\"--as-of\", dest=\"as_of\", required=True, type=_iso_date)\n"
        "    p.add_argument(\"--series-dir\", dest=\"series_dir\", required=True)\n"
        "    p.add_argument(\"--user-agent\", dest=\"user_agent\", default=None)\n"
        "    _add_out(p)\n"
        "    return parser\n",
    ),
    (
        "    ids = [i.strip() for i in args.ids.split(\",\") if i.strip()]\n",
        "    if args.command == \"snapshot\":\n"
        "        return _snapshot(args)\n"
        "    ids = [i.strip() for i in args.ids.split(\",\") if i.strip()]\n",
    ),
    (
        "def _emit(data, out) -> int:\n",
        "def _snapshot(args) -> int:\n"
        "    from researchos.snapshot import build_snapshot, load_macro_config\n"
        "    from researchos.sources import get_page\n"
        "    agent = _user_agent(args.user_agent)\n"
        "    try:\n"
        "        config = load_macro_config(args.config)\n"
        "        result = build_snapshot(args.root, config, args.as_of, series_dir=args.series_dir,\n"
        "                                fetch_page=lambda url: get_page(url, user_agent=agent))\n"
        "    except (OSError, ValueError) as exc:\n"
        "        print(f\"error: {exc}\", file=sys.stderr)\n"
        "        return 1\n"
        "    return _emit(result, args.out)\n"
        "\n"
        "\n"
        "def _emit(data, out) -> int:\n",
    ),
]


def apply(text: str) -> str:
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise SystemExit(f"main_patch: expected one {old[:50]!r}")
        text = text.replace(old, new, 1)
    return text
