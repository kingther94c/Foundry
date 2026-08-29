"""run_command: PowerShell 5.1 execution with a bounded, killable process tree.

Every invocation is stateless -- a fresh process, an explicit cwd, a filtered
environment. A persistent shell session would be faster and is the top source of
flakiness in agent runtimes, more so on Windows.

The shell is Windows PowerShell 5.1 (D-018): preinstalled everywhere, no extra
distribution. It has no ``&&``/``||``, so the tool description tells the model to
use ``;`` -- and the segmenter still treats those operators as separators, so a
model that emits one anyway cannot slip a second command past policy.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from foundry.core.conversation import ToolSchema
from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.tools.base import Operation, ToolContext, ToolKind, ToolOutput, truncate_middle
from foundry.core.winapi import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    ProcessJob,
    child_environment,
    decode_output,
    taskkill_tree,
)
from foundry.core.workspace import PathRejected

DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
MAX_CAPTURE_BYTES = 2_000_000

POWERSHELL = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]

# PowerShell 5.1's -Command reports 1 for every failure: `SystemExit(3)` comes
# back as 1, and a test suite's exit code is evidence a validation claim is
# checked against. This epilogue restores the real code. It is a fixed, runtime-
# owned suffix -- never model-controlled -- so the approved command and the
# executed command remain the same operation.
_EXIT_CODE_EPILOGUE = (
    "\n$__foundry_ok = $?\n"
    "$__foundry_code = $LASTEXITCODE\n"
    "if ($null -ne $__foundry_code) { exit $__foundry_code }\n"
    "if (-not $__foundry_ok) { exit 1 }\n"
    "exit 0\n"
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    duration_ms: int
    timed_out: bool = False
    encoding: str = "utf-8"


def run_process(command: str, *, cwd: str, timeout_s: int,
                env: dict[str, str] | None = None) -> CommandResult:
    """Run one command under a job object so the whole tree can be killed.

    The job also releases pipe handles a grandchild inherited, which is what
    keeps ``communicate()`` from blocking forever after a kill.
    """
    argv = (POWERSHELL + [command + _EXIT_CODE_EPILOGUE] if IS_WINDOWS
            else ["/bin/sh", "-c", command])
    creationflags = 0
    if IS_WINDOWS:
        creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

    started = time.monotonic()
    job = ProcessJob()
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        if IS_WINDOWS and job.active:
            # Millisecond race here is documented in winapi.ProcessJob.
            job.assign(int(process._handle))  # type: ignore[attr-defined]

        try:
            stdout, stderr = process.communicate(timeout=timeout_s)
            timed_out = False
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            if not (IS_WINDOWS and job.terminate()):
                taskkill_tree(process.pid)
                process.kill()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                stdout, stderr = b"", b""
            exit_code = None
    finally:
        job.close()

    duration_ms = int((time.monotonic() - started) * 1000)
    decoded = decode_output(stdout[:MAX_CAPTURE_BYTES])
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout[:MAX_CAPTURE_BYTES],
        stderr=stderr[:MAX_CAPTURE_BYTES],
        duration_ms=duration_ms,
        timed_out=timed_out,
        encoding=decoded.encoding,
    )


@dataclass(slots=True)
class RunCommand:
    name: str = "run_command"
    kind: ToolKind = ToolKind.MUTATOR
    recorder: Any = None  # SessionStore | None -- records command evidence
    last_event_ordinal: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=(
                "Run one command in the workspace with Windows PowerShell 5.1. "
                "PowerShell 5.1 has no '&&' or '||': separate commands with ';'. "
                "Runs from the workspace root unless cwd is given; every run is a "
                "fresh process, so directory changes do not persist. "
                "Example: {\"command\": \"python -m pytest -q\"}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "description": "Workspace-relative working directory."},
                    "timeout_s": {"type": "integer", "description": f"Default {DEFAULT_TIMEOUT_S}, max {MAX_TIMEOUT_S}."},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def validate(self, args: dict[str, Any]) -> Operation:
        extra = set(args) - {"command", "cwd", "timeout_s"}
        if extra:
            raise InvalidToolCall(f"unknown argument(s): {', '.join(sorted(extra))}")
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise InvalidToolCall("command must be a non-empty string")
        cwd = args.get("cwd", ".")
        if not isinstance(cwd, str):
            raise InvalidToolCall("cwd must be a string")
        timeout = args.get("timeout_s", DEFAULT_TIMEOUT_S)
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise InvalidToolCall("timeout_s must be an integer")
        if not 1 <= timeout <= MAX_TIMEOUT_S:
            raise InvalidToolCall(f"timeout_s must be between 1 and {MAX_TIMEOUT_S}")

        return Operation(
            tool=self.name, kind=self.kind,
            args={"command": command, "cwd": cwd, "timeout_s": timeout},
            display=command,   # what policy matched and the user approved
            target=command,
        )

    def execute(self, op: Operation, ctx: ToolContext) -> ToolOutput:
        try:
            workdir = ctx.workspace.resolve(op.args["cwd"])
        except PathRejected as exc:
            raise ToolError(f"invalid cwd: {exc}") from exc
        if not workdir.absolute.is_dir():
            raise ToolError(f"cwd is not a directory: {op.args['cwd']}")

        env = child_environment(ctx.env_policy)
        result = run_process(op.args["command"], cwd=str(workdir.absolute),
                             timeout_s=op.args["timeout_s"], env=env)

        ordinal = 0
        if self.recorder is not None:
            ordinal = self.recorder.record_command(
                argv=[op.args["command"]], cwd=str(workdir.absolute),
                exit_code=result.exit_code, duration_ms=result.duration_ms,
                stdout=result.stdout, stderr=result.stderr,
            )
        self.last_event_ordinal = ordinal
        self.history.append({
            "command": op.args["command"],
            "exit_code": result.exit_code,
            "event_ordinal": ordinal,
        })

        stdout = decode_output(result.stdout).text
        stderr = decode_output(result.stderr).text
        combined = stdout
        if stderr.strip():
            combined = f"{stdout}\n[stderr]\n{stderr}" if stdout.strip() else f"[stderr]\n{stderr}"

        artifact_id = ""
        body, truncated = truncate_middle(combined, ctx.max_output_bytes)
        if truncated and ctx.artifacts is not None:
            ref = ctx.artifacts.put_text(combined)
            artifact_id = ref.artifact_id
            body += f"\n\n[full output saved; read_artifact artifact_id=\"{artifact_id}\"]"

        if result.timed_out:
            header = f"TIMED OUT after {op.args['timeout_s']}s (process tree terminated)"
        else:
            header = f"exit code {result.exit_code} in {result.duration_ms}ms"

        return ToolOutput(
            content=f"$ {op.args['command']}\n{header}\n\n{body}".rstrip(),
            is_error=result.timed_out or (result.exit_code not in (0, None)),
            artifact_id=artifact_id,
            truncated=truncated,
            metadata={
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "event_ordinal": ordinal,
                "timed_out": result.timed_out,
            },
        )
