"""Run this plan end to end with a fake model, before any money is spent on the real one.

    python dry_run.py OUT     OUT: a new directory outside this run's folder

Checks the eighteen snapshots in cases/ against inputs.sha256, copies them into OUT (the
run's own cases/ are never touched), and drives the researchos commands as the plan does:
forecast in both arms, primary.py, resolve, score, compare, blend and agreement with
--perspectives macro, then train on fresh copies of the snapshots whose keys have all
resolved. The fake model answers every schema with numbers drawn from a hash of the
prompt, so the endpoints it prints say nothing about any model; what it shows is that
every step runs on these cases, how many calls each makes, and how long the prompts are.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE.parents[2] / "tools")]

from researchos.__main__ import main  # noqa: E402
from researchos.cases import load_case, load_resolution, unresolved_targets  # noqa: E402
from researchos.playbook import learned_through, load_playbook  # noqa: E402

CUTOFF = "2026-06-30"
MODES = {"arm-holistic": "holistic", "arm-isolated": "isolated"}


def _fill(schema: dict, seed: str, name: str = ""):
    """A value of the strict schema, numbers in [0.05, 0.95] drawn from a hash of seed."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {key: _fill(sub, f"{seed}/{key}", key) for key, sub in schema["properties"].items()}
    if kind == "array":
        return [_fill(schema["items"], f"{seed}/0", name)]
    if kind in ("number", "integer"):
        return round(0.05 + (zlib.crc32(seed.encode("utf-8")) % 9001) / 10000, 2)
    if kind == "boolean":
        return False
    return f"{name or 'text'} {zlib.crc32(seed.encode('utf-8')) % 1000}"


class FakeClient:
    """Stands in for anthropic.Anthropic() and notes the length of every request's parts."""

    def __init__(self, log: list):
        self.log = log
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        blocks = [block.get("text", "") for message in kwargs["messages"]
                  for block in message["content"]]
        prompt = blocks[-1] if blocks else ""
        first = prompt.splitlines()[0] if prompt else ""
        self.log.append({"first": first, "system": len(kwargs.get("system") or ""),
                         "prefix": sum(len(b) for b in blocks[:-1]), "prompt": len(prompt)})
        body = _fill(kwargs["output_config"]["format"]["schema"], f"{first}|{len(blocks)}")
        usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=None,
                                cache_creation_input_tokens=None)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])


def _cli(args: list[str], log: list | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    factory = (lambda: FakeClient(log)) if log is not None else None
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args, client_factory=factory)
    return code, out.getvalue(), err.getvalue()


def _ok(args: list[str], log: list | None = None) -> str:
    code, out, err = _cli(args, log)
    if code:
        raise SystemExit(f"{args[0]} exited {code}: {err.strip()}")
    return out


def _copy(dest: Path, ids: list[str]) -> None:
    dest.mkdir(parents=True)
    for case_id in ids:
        shutil.copytree(HERE / "cases" / case_id, dest / case_id)


def main_dry(out: Path) -> int:
    out = out.resolve()
    if out == HERE or HERE in out.parents:
        raise SystemExit("OUT must be outside this run's folder: arm-* here are the real arms")
    if out.exists():
        raise SystemExit(f"{out} exists; give a new directory")
    hashes = {}
    for line in (HERE / "inputs.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split()
        hashes[rel.split("/")[1]] = digest
    ids = sorted(hashes)
    for case_id in ids:
        path = HERE / "cases" / case_id / "case.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[case_id]:
            raise SystemExit(f"{path} does not match inputs.sha256")
    print(f"{len(ids)} snapshots match inputs.sha256")
    series, joined = str(HERE / "series"), ",".join(ids)

    logs: dict[str, list] = {}
    for arm, mode in MODES.items():
        _copy(out / arm, ids)
        logs[arm] = []
        result = json.loads(_ok(["forecast", "--root", str(out / arm), "--playbook",
                                 str(out / "none.json"), "--ids", joined, "--knowledge-cutoff",
                                 CUTOFF, "--perspective-mode", mode], logs[arm]))
        print(f"{arm}: {sum(e['sealed'] for e in result['cases'])} sealed in "
              f"{len(logs[arm])} calls")
    log = logs["arm-holistic"]
    prefix = sorted(e["prefix"] for e in log)
    judge = max(e["prompt"] for e in log if not e["first"].startswith("Perspective:"))
    print(f"shared prefix {prefix[0]:,} to {prefix[-1]:,} characters; longest judge prompt "
          f"{judge:,}; other prompts up to "
          f"{max(e['prompt'] for e in log if e['first'].startswith('Perspective:')):,}")

    shutil.copy(HERE / "primary.py", out / "primary.py")
    done = subprocess.run([sys.executable, str(out / "primary.py"), "--out",
                           str(out / "primary.json")], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"primary.py exited {done.returncode}: {done.stderr.strip()}")
    print("primary.py: ran on", json.loads((out / "primary.json").read_text(encoding="utf-8"))
          ["snapshots_in_both_arms"], "snapshots in both arms")

    for arm in MODES:
        _ok(["resolve", "--root", str(out / arm), "--ids", joined, "--series-dir", series])
        _ok(["score", "--root", str(out / arm), "--ids", joined, "--out",
             str(out / f"score_{arm}.json")])
    report = json.loads((out / "score_arm-holistic.json").read_text(encoding="utf-8"))
    print(f"score: {report['n']} keys resolved, {len(report['pending'])} pending")
    _ok(["compare", "-a", str(out / "score_arm-holistic.json"), "-b",
         str(out / "score_arm-isolated.json"), "--metric", "brier_judge"])
    for rule in ("uniform", "median", "hedge", "leader", "pool"):
        _ok(["blend", str(out / "score_arm-isolated.json"), "--rule", rule,
             "--perspectives", "macro"])
    _ok(["agreement", str(out / "score_arm-isolated.json"), "--perspectives", "macro"])
    print("compare, blend (every rule) and agreement: ran")

    _copy(out / "train", ids)
    _ok(["resolve", "--root", str(out / "train"), "--ids", joined, "--series-dir", series])
    ready = [c for c in ids if not unresolved_targets(load_case(out / "train", c),
                                                       load_resolution(out / "train", c))]
    playbook_path, train_log = out / "train_playbook.json", []
    records = json.loads(_ok(["train", "--root", str(out / "train"), "--playbook",
                              str(playbook_path), "--ids", ",".join(ready),
                              "--knowledge-cutoff", CUTOFF], train_log))
    playbook = load_playbook(playbook_path)
    print(f"train on the {len(ready)} snapshots whose keys have all resolved: {len(records)} "
          f"keys learned in {len(train_log)} calls, {len(playbook.lessons)} lessons, learned "
          f"through {learned_through(playbook)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main_dry(Path(sys.argv[1])))
