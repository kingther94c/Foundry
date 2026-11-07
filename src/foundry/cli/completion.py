"""Tab completion for the shell (D-093), as codex's ``completion`` command
prints a script for one: ``foundry completion powershell`` (the default) or
``foundry completion bash`` (Git Bash).

The script completes subcommands, each one's flags, and the values a flag or
argument takes from a fixed list (``--mode``'s, ``login status``). It is built
from the parser itself, so it cannot fall behind it. A flag that takes a path
or a name, and anything after ``--``, is left to the shell's own completion.
The script holds no setting and runs nothing: it only answers what may come
next on the line.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

ROOT = "foundry"
SHELLS = ("powershell", "bash")


def command_tree(parser: argparse.ArgumentParser) -> dict[str, dict[str, Any]]:
    """Each command, by its words from ``foundry`` on: its subcommands, flags,
    the flags that take a value, the values those take from a fixed list, and
    the values an argument does."""
    tree: dict[str, dict[str, Any]] = {}
    _walk(parser, ROOT, tree)
    # `foundry -m gpt-5` is `foundry run -m gpt-5` (D-042): what run takes is
    # offered before any subcommand too.
    root, run = tree[ROOT], tree.get(f"{ROOT} run")
    if run is not None:
        for key in ("flags", "takes_value"):
            root[key] = sorted(set(root[key]) | set(run[key]))
        root["choices"] = {**run["choices"], **root["choices"]}
    return tree


def _walk(parser: argparse.ArgumentParser, path: str, tree: dict[str, dict[str, Any]]) -> None:
    node: dict[str, Any] = {"subcommands": [], "flags": [], "takes_value": [],
                            "choices": {}, "values": []}
    tree[path] = node
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                node["subcommands"].append(name)
                _walk(sub, f"{path} {name}", tree)
        elif action.option_strings:
            if action.help == argparse.SUPPRESS:
                continue
            node["flags"].extend(action.option_strings)
            if action.nargs != 0:
                node["takes_value"].extend(action.option_strings)
                if action.choices:
                    for option in action.option_strings:
                        node["choices"][option] = [str(c) for c in action.choices]
        elif action.choices:
            node["values"].extend(str(c) for c in action.choices)
    for key in ("subcommands", "flags", "takes_value", "values"):
        node[key] = sorted(dict.fromkeys(node[key]))


def script(shell: str, parser: argparse.ArgumentParser) -> str:
    tree = command_tree(parser)
    if shell == "powershell":
        return _powershell(tree)
    if shell == "bash":
        return _bash(tree)
    raise ValueError(f"no completion for {shell!r}: {', '.join(SHELLS)}")


_POWERSHELL = r"""# Tab completion for foundry in PowerShell (D-093). To have it in every
# session, add this line to your profile (notepad $PROFILE):
#     foundry completion powershell | Out-String | Invoke-Expression
Register-ArgumentCompleter -Native -CommandName foundry, foundry.exe -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $tree = @'
__TREE__
'@ | ConvertFrom-Json
    $words = @($commandAst.CommandElements |
        Where-Object { $_.Extent.EndOffset -le $cursorPosition } |
        ForEach-Object { $_.Extent.Text })
    if ($wordToComplete -ne '' -and $words.Count -gt 1 -and $words[-1] -eq $wordToComplete) {
        $words = @($words[0..($words.Count - 2)])
    }
    $path = 'foundry'
    $skip = $false
    for ($i = 1; $i -lt $words.Count; $i++) {
        $word = $words[$i]
        if ($skip) { $skip = $false; continue }
        if ($word -eq '--') { return }
        $node = $tree.$path
        if ($word.StartsWith('-')) {
            if ($node.takes_value -contains $word) { $skip = $true }
            continue
        }
        if ($node.subcommands -contains $word) { $path = "$path $word" }
    }
    $node = $tree.$path
    $previous = $words[$words.Count - 1]
    $choices = $null
    if ($previous.StartsWith('-')) { $choices = $node.choices.$previous }
    if ($choices) {
        $candidates = @($choices)
    } elseif ($node.takes_value -contains $previous) {
        return
    } elseif ($wordToComplete.StartsWith('-')) {
        $candidates = @($node.flags)
    } else {
        $candidates = @($node.subcommands) + @($node.values)
    }
    $candidates | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}
"""


def _powershell(tree: dict[str, dict[str, Any]]) -> str:
    # In a single-quoted here-string nothing is expanded; JSON never puts
    # "'@" at the start of a line.
    return _POWERSHELL.replace("__TREE__", json.dumps(tree, indent=None, sort_keys=True))


_BASH = r"""# Tab completion for foundry in bash (D-093). To have it in every
# session, add this line to ~/.bashrc:
#     eval "$(foundry completion bash)"
__TABLES__
_foundry() {
    local cur="${COMP_WORDS[COMP_CWORD]}" prev="${COMP_WORDS[COMP_CWORD-1]}"
    local path="foundry" word i skip=0
    COMPREPLY=()
    for ((i = 1; i < COMP_CWORD; i++)); do
        word="${COMP_WORDS[i]}"
        if ((skip)); then skip=0; continue; fi
        [[ $word == -- ]] && return 0
        if [[ $word == -* ]]; then
            [[ " ${_foundry_takes_value[$path]} " == *" $word "* ]] && skip=1
            continue
        fi
        [[ " ${_foundry_subcommands[$path]} " == *" $word "* ]] && path="$path $word"
    done
    if [[ $prev == -* && -n ${_foundry_choices["$path|$prev"]+set} ]]; then
        COMPREPLY=($(compgen -W "${_foundry_choices["$path|$prev"]}" -- "$cur"))
    elif [[ $prev == -* && " ${_foundry_takes_value[$path]} " == *" $prev "* ]]; then
        return 0
    elif [[ $cur == -* ]]; then
        COMPREPLY=($(compgen -W "${_foundry_flags[$path]}" -- "$cur"))
    else
        COMPREPLY=($(compgen -W "${_foundry_subcommands[$path]} ${_foundry_values[$path]}" -- "$cur"))
    fi
}
complete -o default -F _foundry foundry foundry.exe
"""


def _bash_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _bash(tree: dict[str, dict[str, Any]]) -> str:
    lines = []
    for table in ("subcommands", "flags", "takes_value", "values"):
        entries = " ".join(f"[{_bash_quote(path)}]={_bash_quote(' '.join(node[table]))}"
                           for path, node in sorted(tree.items()))
        lines.append(f"declare -gA _foundry_{table}=({entries})")
    entries = " ".join(f"[{_bash_quote(path + '|' + flag)}]={_bash_quote(' '.join(values))}"
                       for path, node in sorted(tree.items())
                       for flag, values in sorted(node["choices"].items()))
    lines.append(f"declare -gA _foundry_choices=({entries})")
    return _BASH.replace("__TABLES__", "\n".join(lines))
