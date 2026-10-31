"""The v29 reference's change to researchos/__main__.py (check.py applies it)."""

from __future__ import annotations


def _replace(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise AssertionError(f"anchor found {text.count(old)} times: {old[:60]!r}")
    return text.replace(old, new)


OPTION = ('    p.add_argument("--perspectives", choices=("earnings", "macro"), default=None,\n'
          '                   help="the perspectives the records hold (default: earnings)")\n')


def apply(text: str) -> str:
    text = _replace(text, '                   help="learning rate of the hedge rule, a positive '
                          'number (default 0.5)")\n    _add_out(p)\n',
                    '                   help="learning rate of the hedge rule, a positive '
                    'number (default 0.5)")\n' + OPTION + '    _add_out(p)\n')
    text = _replace(text, '''    p = sub.add_parser("agreement", help="do the perspectives disagree? their spread, "
                                         "accuracy and pairwise correlations")
    p.add_argument("files", nargs="+", metavar="FILE",
                   help="evaluate or train output; several files are merged in the order given")
    _add_out(p)
''', '''    p = sub.add_parser("agreement", help="do the perspectives disagree? their spread, "
                                         "accuracy and pairwise correlations")
    p.add_argument("files", nargs="+", metavar="FILE",
                   help="evaluate or train output; several files are merged in the order given")
''' + OPTION + '''    _add_out(p)
''')
    text = _replace(text, "        result = replay(merged[\"cases\"], args.rule, eta=args.eta)\n",
                    "        result = replay(merged[\"cases\"], args.rule, eta=args.eta,\n"
                    "                        **_v29_perspectives(args))\n")
    text = _replace(text, "        result = agreement(merged[\"cases\"])\n",
                    "        result = agreement(merged[\"cases\"], **_v29_perspectives(args))\n")
    text = _replace(text, "def _blend(args) -> int:\n",
                    "def _v29_perspectives(args) -> dict:\n"
                    "    from researchos.targets import perspective_set\n\n"
                    "    if args.perspectives is None:\n"
                    "        return {}\n"
                    "    return {\"perspectives\": perspective_set(args.perspectives)}\n\n\n"
                    "def _blend(args) -> int:\n")
    return text
