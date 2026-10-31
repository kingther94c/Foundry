"""The v25 reference's changes to researchos/__main__.py, applied as text replacements.

It anchors on the end of the parser, on the check that --ids names a case, and on _emit,
so it applies to __main__.py whatever form v24's snapshot command took.
"""

REPLACEMENTS = [
    (
        "    return parser\n",
        "    p = sub.add_parser(\"resolve\")\n"
        "    p.add_argument(\"--root\", required=True)\n"
        "    p.add_argument(\"--ids\", required=True)\n"
        "    p.add_argument(\"--series-dir\", dest=\"series_dir\", required=True)\n"
        "    _add_out(p)\n"
        "    return parser\n",
    ),
    (
        "    if not ids:\n"
        "        print(\"error: --ids needs at least one case id\", file=sys.stderr)\n"
        "        return 2\n",
        "    if not ids:\n"
        "        print(\"error: --ids needs at least one case id\", file=sys.stderr)\n"
        "        return 2\n"
        "    if args.command == \"resolve\":\n"
        "        return _resolve(args, ids)\n",
    ),
    (
        "def _emit(data, out) -> int:\n",
        "def _resolve(args, ids) -> int:\n"
        "    from researchos.snapshot import resolve_cases\n"
        "    try:\n"
        "        result = resolve_cases(args.root, ids, series_dir=args.series_dir)\n"
        "    except (OSError, ValueError) as exc:\n"
        "        print(f\"error: {exc}\", file=sys.stderr)\n"
        "        return 1\n"
        "    return _emit({\"cases\": result}, args.out)\n"
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
