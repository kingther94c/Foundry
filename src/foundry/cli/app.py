"""The command line: the parser and ``main``, which hands each command to its
module.

- cli/wiring.py builds a session: the runtime and what it needs.
- cli/session_commands.py: ``run``, ``resume``, ``fork``, ``undo``,
  ``sessions``, ``archive``, ``delete``, ``prune``; the interactive prompt is
  cli/repl.py.
- cli/unattended.py: ``exec``, ``review``, ``record``.
- cli/login.py: ``login``, ``logout``.
- cli/mcp_commands.py: ``mcp``, ``mcp-server``.
- cli/setup_commands.py: ``doctor``, ``policy``, ``features``, ``memories``,
  ``report``.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.markup import escape

from foundry import __version__
from foundry.cli.codex_flags import hints as codex_hints
from foundry.cli.completion import SHELLS
from foundry.cli.completion import script as completion_script
from foundry.cli.login import cmd_login, cmd_logout
from foundry.cli.mcp_commands import cmd_mcp, cmd_mcp_server
from foundry.cli.session_commands import (
    cmd_archive,
    cmd_delete,
    cmd_prune,
    cmd_session,
    cmd_sessions,
    cmd_undo,
)
from foundry.cli.setup_commands import (
    cmd_doctor,
    cmd_features,
    cmd_memories,
    cmd_policy,
    cmd_report,
)
from foundry.cli.unattended import cmd_exec, cmd_record, cmd_review
from foundry.core.errors import FoundryError
from foundry.core.events import EXIT_CODES, TerminalStatus
from foundry.core.policy.engine import Mode


def cmd_completion(args: argparse.Namespace) -> int:
    """``foundry completion [powershell|bash]`` (D-093): a Tab-completion
    script for the shell, printed for a profile line to load."""
    text = completion_script(args.shell, build_parser())
    sys.stdout.flush()
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(text)
    else:
        # As bytes: printed as text on Windows, every newline became CRLF,
        # and bash reads the carriage return as part of each line.
        stream.write(text.encode("utf-8"))
        stream.flush()
    return 0


def _config_flags(parser: argparse.ArgumentParser, *, modes: tuple[Mode, ...] = tuple(Mode)) -> None:
    """The settings a run can be given on its command line (D-042). They win
    over every file and the environment; secrets are not among them."""
    parser.add_argument("-m", "--model", help="the model to use")
    parser.add_argument("--mode", choices=[m.value for m in modes],
                        help="the permission mode" + (
                            " (default dont_ask: nobody is there to approve)"
                            if Mode.DEFAULT not in modes else ""))
    parser.add_argument("-c", "--config", action="append", metavar="KEY=VALUE",
                        help="a setting, such as -c runtime.max_tool_rounds=60 "
                             "(repeatable; no secrets)")
    parser.add_argument("-p", "--profile",
                        help="a [profiles.<name>] table from your config.toml")
    parser.add_argument("--enable", action="append", metavar="FEATURE",
                        help="turn a feature on for this run, as -c features.FEATURE=true "
                             "(repeatable); foundry features list names them")
    parser.add_argument("--disable", action="append", metavar="FEATURE",
                        help="turn a feature off for this run, as -c features.FEATURE=false "
                             "(repeatable)")


def _color_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--color", choices=("always", "never", "auto"), default="auto",
                        help="colour what is said on stderr: always, never, or auto (on a "
                             "terminal; the default)")


def _trust_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trust", action="store_true",
                        help="trust this repository, as a yes when asked does: its "
                             "instructions and settings are read, in this run and later "
                             "ones")


def _ephemeral_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ephemeral", action="store_true",
                        help="keep no record of the run once it ends: nothing to resume, "
                             "nothing in foundry sessions")


def _image_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-i", "--image", action="append", metavar="PATH",
                        help="attach an image to the first task (repeatable)")


class _Parser(argparse.ArgumentParser):
    """Says, when it refuses one of codex's flags, what Foundry takes instead
    (D-097). Only a command line it is already refusing is looked at."""

    def parse_args(self, args=None, namespace=None):
        parsed, unrecognized = self.parse_known_args(args, namespace)
        if unrecognized:
            argv = sys.argv[1:] if args is None else list(args)
            # The subcommand's usage, not the list of every subcommand.
            refusing = next((action.choices[argv[0]] for action in self._actions
                             if isinstance(action, argparse._SubParsersAction)
                             and argv and argv[0] in action.choices), self)
            refusing.error("\n".join([f"unrecognized arguments: {' '.join(unrecognized)}",
                                       *codex_hints(argv, unrecognized)]))
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="foundry", description="A local coding-agent runtime.")
    parser.add_argument("-V", "--version", action="version", version=f"foundry {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start a session (default)")
    run.add_argument("task", nargs="?",
                     help="the first message; at a terminal the session goes on "
                          "after it, otherwise it ends with it")
    run.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                     help="repository to work in (default: here)")
    _image_flag(run)
    _config_flags(run)
    run.set_defaults(func=cmd_session)

    resume = sub.add_parser("resume", help="continue a recorded session")
    resume.add_argument("resume", nargs="?", default=None, metavar="session",
                        help="the session id or name, or last for the newest in this workspace "
                             "(default: at a terminal, pick from the recent ones; "
                             "otherwise last)")
    resume.add_argument("task", nargs="?",
                        help="the first message; at a terminal the session goes on "
                             "after it, otherwise it ends with it")
    resume.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                        help="repository the session worked in")
    resume.add_argument("--last", action="store_true",
                        help="the newest session, as codex's resume --last; a word given "
                             "is then the task")
    resume.add_argument("--all", action="store_true",
                        help="from every workspace's sessions, continued where each was "
                             "recorded")
    _image_flag(resume)
    _config_flags(resume)
    resume.set_defaults(func=cmd_session)

    fork = sub.add_parser("fork", help="continue a copy of a recorded session, apart "
                                       "from it")
    fork.add_argument("resume", nargs="?", default=None, metavar="session",
                      help="the session id or name, or last for the newest in this "
                           "workspace (default: at a terminal, pick from the recent ones; "
                           "otherwise last)")
    fork.add_argument("task", nargs="?",
                      help="the first message; at a terminal the session goes on after "
                           "it, otherwise it ends with it")
    fork.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                      help="repository the session worked in")
    fork.add_argument("--last", action="store_true",
                      help="the newest session, as codex's fork --last; a word given is "
                           "then the task")
    fork.add_argument("--all", action="store_true",
                      help="from every workspace's sessions, continued where each was "
                           "recorded")
    _image_flag(fork)
    _config_flags(fork)
    fork.set_defaults(func=cmd_session)

    undo = sub.add_parser("undo", help="revert the files a recorded session patched")
    undo.add_argument("session", nargs="?", default="last",
                      help="the session id or name (default: last, the newest in this "
                           "workspace)")
    undo.add_argument("--last", action="store_true",
                      help="only the session's newest patch")
    undo.add_argument("--yes", action="store_true", help="do not ask first")
    undo.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    undo.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                      help="repository the session worked in")
    undo.set_defaults(func=cmd_undo)

    execute = sub.add_parser("exec", help="run one task headlessly (approvals are denied)")
    execute.add_argument("task", nargs="?", help="the task; - reads it from stdin")
    execute.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                         help="repository to work in (default: here)")
    execute.add_argument("--json", action="store_true", help="emit the event stream as JSONL")
    execute.add_argument("--resume", metavar="SESSION",
                         help="continue a recorded session (an id, or last); the task "
                              "defaults to carrying on where it stopped")
    execute.add_argument("--fork", metavar="SESSION",
                         help="continue a copy of a recorded session, apart from it (an "
                              "id, a name, or last); the task defaults as with --resume")
    execute.add_argument("-o", "--output-last-message", metavar="FILE",
                         help="write the run's final summary or reply to FILE")
    _ephemeral_flag(execute)
    _color_flag(execute)
    _trust_flag(execute)
    execute.add_argument("--output-schema", metavar="FILE",
                         help="a JSON Schema the run's result must match; the result is "
                              "printed as JSON, and written by -o")
    _image_flag(execute)
    _config_flags(execute, modes=(Mode.DONT_ASK, Mode.PLAN, Mode.ACCEPT_EDITS))
    execute.set_defaults(func=cmd_exec)

    review = sub.add_parser("review", help="review changes headlessly, read-only, as codex "
                                           "review does")
    targets = review.add_mutually_exclusive_group()
    targets.add_argument("--uncommitted", action="store_true",
                         help="the staged, unstaged and untracked changes")
    targets.add_argument("--base", metavar="BRANCH",
                         help="what this branch changed since it left BRANCH")
    targets.add_argument("--commit", metavar="SHA", help="what one commit changed")
    targets.add_argument("prompt", nargs="?", metavar="PROMPT",
                         help="the review's instructions instead; - reads them from stdin")
    review.add_argument("--title", help="the commit's title, shown with it (with --commit)")
    review.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                        help="repository to review (default: here)")
    review.add_argument("--json", action="store_true", help="emit the event stream as JSONL")
    review.add_argument("-o", "--output-last-message", metavar="FILE",
                        help="write the review to FILE")
    _ephemeral_flag(review)
    _color_flag(review)
    _trust_flag(review)
    # Read-only whatever the mode; dont_ask, as nobody is there to approve.
    _config_flags(review, modes=(Mode.DONT_ASK,))
    review.set_defaults(func=cmd_review)

    record = sub.add_parser("record", help="capture a session as a replay fixture")
    record.add_argument("task")
    record.add_argument("--output", required=True, help="fixture file to write")
    record.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                        help="repository to work in (default: here)")
    _config_flags(record)
    record.set_defaults(func=cmd_record)

    login = sub.add_parser("login", help="store an API key, or say where it comes from")
    login.add_argument("action", nargs="?", choices=("status",),
                       help="status: where this configuration's key comes from")
    login.add_argument("--with-api-key", action="store_true",
                       help="read the key (or gateway token) from stdin, as "
                            "printenv OPENAI_API_KEY | foundry login --with-api-key")
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="remove stored credentials")
    logout.set_defaults(func=cmd_logout)

    sessions = sub.add_parser("sessions", help="list or show recorded sessions")
    sessions.add_argument("session_id", nargs="?", help="a session id or name: its records")
    sessions.add_argument("--limit", type=int, default=20)
    sessions.add_argument("--archived", action="store_true",
                          help="the archived sessions (foundry archive), not the others")
    sessions.set_defaults(func=cmd_sessions)

    for name, said in (("archive", "move a recorded session out of every list, kept whole"),
                       ("unarchive", "bring an archived session back")):
        archiving = sub.add_parser(name, help=said)
        archiving.add_argument("session", help="the session's id or name")
        archiving.set_defaults(func=cmd_archive)

    delete = sub.add_parser("delete", help="delete a recorded session for good")
    delete.add_argument("session", help="the session's id or name")
    delete.add_argument("--force", action="store_true",
                        help="do not ask first (takes only an id)")
    delete.set_defaults(func=cmd_delete)

    prune = sub.add_parser("prune", help="delete the sessions last written more than N days "
                                         "ago (session_retention_days)")
    prune.add_argument("--days", type=int,
                       help="N; session_retention_days in your config if not given")
    prune.add_argument("--dry-run", action="store_true",
                       help="list them; delete nothing")
    prune.add_argument("--force", action="store_true", help="do not ask first")
    prune.set_defaults(func=cmd_prune)

    report = sub.add_parser("report", help="summarize recorded sessions")
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="check the environment")
    doctor.set_defaults(func=cmd_doctor)

    policy = sub.add_parser("policy", help="what the policy decides, as codex execpolicy")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)
    check = policy_sub.add_parser("check", help="what the policy would decide for a command "
                                                "here, without running it")
    check.add_argument("command", nargs="+", metavar="COMMAND",
                       help="the command, as the model would run it; after --, or quoted")
    check.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                       help="the repository it would run in (default: here)")
    check.add_argument("--json", action="store_true", help="the decisions as JSON")
    _config_flags(check)
    check.set_defaults(func=cmd_policy)

    completion = sub.add_parser("completion",
                                help="print a Tab-completion script for your shell")
    completion.add_argument("shell", nargs="?", choices=SHELLS, default="powershell",
                            help="powershell (the default) or bash")
    completion.set_defaults(func=cmd_completion)

    serve = sub.add_parser("mcp-server",
                           help="be an MCP server over stdio, offering foundry and "
                                "foundry-reply (approvals are denied)")
    _config_flags(serve, modes=(Mode.DONT_ASK, Mode.PLAN, Mode.ACCEPT_EDITS))
    serve.set_defaults(func=cmd_mcp_server)

    features = sub.add_parser("features", help="codex's feature flags: what each is here, "
                                               "and turning one on or off in your "
                                               "config.toml")
    features.set_defaults(func=cmd_features)
    features_sub = features.add_subparsers(dest="features_command")
    features_sub.add_parser("list", help="each feature, whether it is on, and what it is here")
    for name, said in (("enable", "on"), ("disable", "off")):
        switch = features_sub.add_parser(name, help=f"turn a feature {said} in your "
                                                    "config.toml, for later sessions")
        switch.add_argument("name", metavar="FEATURE")

    memories = sub.add_parser("memories", help="this workspace's memories: shown, updated "
                                               "now, or forgotten")
    memories.set_defaults(func=cmd_memories, workspace=".", quiet=False)
    memories_sub = memories.add_subparsers(dest="memories_command")
    for name, said in (("show", "where they are, and how many sessions are in them"),
                       ("update", "read the sessions due now, and merge the notes"),
                       ("reset", "forget them; sessions from now on are read for them")):
        memory_action = memories_sub.add_parser(name, help=said)
        memory_action.add_argument("-C", "--cd", "--workspace", dest="workspace", default=".",
                                   help="the repository (default: here)")
        if name == "update":
            memory_action.add_argument("-p", "--profile",
                                       help="a [profiles.<name>] table from your config.toml")
            memory_action.add_argument("--quiet", action="store_true",
                                       help="say nothing (a session's start runs it so)")

    mcp = sub.add_parser("mcp", help="list, add or remove MCP servers in your config.toml")
    mcp.set_defaults(func=cmd_mcp)
    mcp_sub = mcp.add_subparsers(dest="mcp_command")
    mcp_list = mcp_sub.add_parser("list", help="the MCP servers your configuration names")
    mcp_list.add_argument("--json", action="store_true")
    mcp_list.add_argument("--profile", help="as a session with this profile would see them")
    mcp_get = mcp_sub.add_parser("get", help="one MCP server's settings, in full")
    mcp_get.add_argument("name")
    mcp_get.add_argument("--json", action="store_true")
    mcp_get.add_argument("--profile", help="as a session with this profile would see it")
    mcp_add = mcp_sub.add_parser(
        "add", help="add an MCP server to your config.toml",
        usage="foundry mcp add NAME [--env KEY=VALUE] [--env-var NAME] [--read-only] "
              "-- COMMAND [ARGS...]\n       foundry mcp add NAME --url URL "
              "[--bearer-token-env-var VAR] [--read-only]")
    mcp_add.add_argument("name", help="letters, digits, _ and -; its tools are mcp__NAME__*")
    mcp_add.add_argument("--url", help="the address of a server already running "
                                       "(streamable HTTP)")
    mcp_add.add_argument("--bearer-token-env-var", metavar="VAR",
                         help="the environment variable holding its token")
    mcp_add.add_argument("--env", action="append", metavar="KEY=VALUE",
                         help="a plain value for the server's environment (repeatable)")
    mcp_add.add_argument("--env-var", action="append", metavar="NAME",
                         help="a variable passed through from your environment, by name "
                              "(repeatable): where a secret goes")
    mcp_add.add_argument("--read-only", action="store_true",
                         help="you vouch its tools change nothing")
    # The command after -- is taken off before parsing (_split_command): a
    # REMAINDER argument would swallow the options that come after NAME.
    mcp_add.set_defaults(server_command=[])
    mcp_remove = mcp_sub.add_parser("remove", help="take an MCP server out of your config.toml")
    mcp_remove.add_argument("name")

    return parser


def _split_command(argv: list[str]) -> tuple[list[str], list[str] | None]:
    """``foundry mcp add NAME ... -- COMMAND ARGS``: the server's command is
    everything after the first ``--``, its own options included."""
    if argv[:2] == ["mcp", "add"] and "--" in argv:
        cut = argv.index("--")
        return argv[:cut], argv[cut + 1:]
    return argv, None


def _value_options(parser: argparse.ArgumentParser, command: str) -> set[str]:
    """The options of ``command`` that take a value, from the parser itself."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and command in action.choices:
            return {flag for option in action.choices[command]._actions
                    if option.option_strings and option.nargs != 0
                    for flag in option.option_strings}
    return set()


def exec_resume(argv: list[str], parser: argparse.ArgumentParser) -> list[str] | None:
    """`foundry exec resume [--last | SESSION] [TASK]`, as codex spells it
    (D-088), is `foundry exec --resume SESSION|last [TASK]`; `exec fork ...`
    is `exec --fork ...` the same way (D-133).

    Only a first word `resume` or `fork` before any `--` is read this way, so
    `foundry exec -- resume` is still the task. None: the form is used without
    saying which session. `foundry exec review ...`, as codex spells it too,
    is `foundry review ...` (D-131).
    """
    if not argv or argv[0] != "exec":
        return argv
    options, words, first = _exec_words(argv, parser)
    if words and words[0] == "review":
        return ["review", *argv[1:first], *argv[first + 1:]]
    if not words or words[0] not in ("resume", "fork"):
        return argv
    last = ("--last", False) in options
    kept = [token for token, value in options if value or token != "--last"]
    rest = words[1:]
    if last:
        session, task = "last", rest
    elif rest:
        session, task = rest[0], rest[1:]
    else:
        return None
    return ["exec", *kept, f"--{words[0]}", session, *(["--", *task] if task else [])]


def _exec_words(argv: list[str], parser: argparse.ArgumentParser
                ) -> tuple[list[tuple[str, bool]], list[str], int]:
    """`foundry exec`'s options, each with whether it is another's value; its
    words; and where the first word is. What follows a `--` before any word
    is the task: no words."""
    takes_value = _value_options(parser, "exec")
    options: list[tuple[str, bool]] = []    # (token, is an option's value)
    words: list[str] = []
    first = 0                               # where the first word is
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--":
            if words:
                words.extend(argv[i + 1:])
            break
        if token.startswith("-") and token != "-":
            options.append((token, False))
            if token in takes_value and i + 1 < len(argv):
                options.append((argv[i + 1], True))
                i += 2
                continue
        else:
            first = first or i
            words.append(token)
        i += 1
    return options, words, first


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    rewritten = exec_resume(argv, parser)
    if rewritten is None:
        word = _exec_words(argv, parser)[1][0]
        Console(stderr=True).print(f"[red]exec {word} needs a session id, or --last for the "
                                   "newest[/red]")
        return 2
    argv = rewritten
    # `foundry -m gpt-5` is `foundry run -m gpt-5`, as with no arguments at all
    # (D-042). A bare first word stays a subcommand, so a mistyped one is an
    # error rather than a task sent to the model.
    if not argv or (argv[0].startswith("-")
                    and argv[0] not in ("-h", "--help", "-V", "--version")):
        argv = ["run", *argv]
    argv, server_command = _split_command(argv)
    args = parser.parse_args(argv)
    if server_command is not None:
        args.server_command = server_command
    if not getattr(args, "func", None):
        args = parser.parse_args(["run"])
    try:
        return args.func(args)
    except FoundryError as exc:
        # Its words as they are: "[features]" in one is not markup (D-144).
        Console().print(f"[red]{escape(str(exc))}[/red]")
        return EXIT_CODES[TerminalStatus.FAILED]
