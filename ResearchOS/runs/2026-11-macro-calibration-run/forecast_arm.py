"""Make this run's forecasts, a snapshot at a time, metering what each arm spends.

    python forecast_arm.py setup                  check cases/ against inputs.sha256 and copy
                                                  them into arm-holistic/ and arm-isolated/
    python forecast_arm.py next ARM [--count 3]   forecast the next COUNT unsealed snapshots of
                                                  ARM (holistic or isolated), in date order
    python forecast_arm.py all                    alternate the arms three snapshots at a time
                                                  until each arm has sealed or failed every
                                                  snapshot, or stopped at its cap
    python forecast_arm.py status                 what each arm has sealed, failed and spent
    python forecast_arm.py restore                in a fresh clone, whose arms hold only the
                                                  committed prediction.json files: copy the
                                                  rest of cases/ back in (checked first)

ANTHROPIC_API_KEY must be set. Only the anthropic client reads it; it is never printed or
written. Each snapshot is one `python -m researchos forecast` call, with the arm's
--perspective-mode, --knowledge-cutoff 2026-06-30 and the initial macro playbook (the file
arm-<mode>/playbook.json is never created, so forecast starts from Playbook.initial each
time). Every API call is metered from the usage the API returns, at RATES (dollars per
million tokens), into spend-<mode>.jsonl: the time, the snapshot, the model asked for and
the model that served it, the four token counts and the cost.

- The cap: before each snapshot, the arm's spend plus the most a snapshot has cost so far
  in either arm (at least ESTIMATE) must stay within CAP, or the arm stops there.
- A snapshot whose forecast fails (a refusal, max_tokens, output that is not the schema's)
  is written to failed-<mode>.jsonl with the reason and is not tried again.
- A failed API request (after the client's own retries) is not the model's failure: the
  run stops with exit 1 and records nothing for that snapshot, so running the same command
  again resumes at it.
- A snapshot any of whose calls was served by a model other than MODEL is named by
  `status`, for `primary.py --exclude`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE.parents[2] / "tools")]

from researchos.__main__ import main as researchos_main  # noqa: E402

ARMS = ("holistic", "isolated")
CUTOFF = "2026-06-30"
CAP = 15.0  # dollars per arm
ESTIMATE = 1.05  # dollars: the top of PLAN.md's estimate for one snapshot of one arm
MODEL = "claude-opus-5-5"
API_FAILED = "Claude API request failed"  # how llm.ClaudeJSON reports anthropic.APIError
# Dollars per million tokens for claude-opus-5-5: input, cache writes (5 minutes), cache
# reads, output. Check them once against the Console's figure for the first snapshot.
RATES = {"input_tokens": 5.00, "cache_creation_input_tokens": 6.25,
         "cache_read_input_tokens": 0.50, "output_tokens": 25.00}


def real_client():
    import anthropic

    return anthropic.Anthropic(max_retries=8)  # a long run meets the odd 429 or 529


def arm_dir(arm: str) -> Path:
    return HERE / f"arm-{arm}"


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def _hashes() -> dict[str, str]:
    """cases/<id>/case.json -> its SHA-256, from inputs.sha256."""
    hashes = {}
    for line in (HERE / "inputs.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split()
            hashes[rel] = digest
    return hashes


def snapshot_ids() -> list[str]:
    return sorted(Path(rel).parent.name for rel in _hashes())


def _matches(path: Path, digest: str) -> bool:
    return path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _checked_hashes() -> dict[str, str]:
    hashes = _hashes()
    for rel, digest in hashes.items():
        if not _matches(HERE / rel, digest):
            raise SystemExit(f"{rel} does not match inputs.sha256")
    return hashes


def restore() -> int:
    """In a fresh clone: put cases/ back into arms that git brought back without them."""
    hashes = _checked_hashes()
    copied = 0
    for arm in ARMS:
        for rel, digest in hashes.items():
            case_dir = (HERE / rel).parent
            dest = arm_dir(arm) / case_dir.name
            dest.mkdir(parents=True, exist_ok=True)
            for source in sorted(case_dir.iterdir()):
                target = dest / source.name
                if target.exists():
                    if source.name == "case.json" and not _matches(target, digest):
                        raise SystemExit(f"{target} does not match inputs.sha256")
                    continue
                shutil.copy(source, target)
                copied += 1
    print(f"{copied} files restored into " + " and ".join(arm_dir(arm).name for arm in ARMS)
          + "; no prediction.json was touched")
    return 0


def setup() -> int:
    hashes = _checked_hashes()
    for arm in ARMS:
        if arm_dir(arm).exists():
            raise SystemExit(f"{arm_dir(arm).name} exists already; the arms are set up once")
    for arm in ARMS:
        for rel in hashes:
            case_dir = (HERE / rel).parent
            shutil.copytree(case_dir, arm_dir(arm) / case_dir.name)
    print(f"{len(hashes)} snapshots match inputs.sha256; copied into "
          + " and ".join(arm_dir(arm).name for arm in ARMS))
    return 0


def spend(arm: str) -> list[dict]:
    return _read_lines(HERE / f"spend-{arm}.jsonl")


def spent(arm: str) -> float:
    return sum(r["cost"] for r in spend(arm))


def most_per_snapshot() -> float:
    per = {}
    for arm in ARMS:
        for record in spend(arm):
            key = (arm, record["case"])
            per[key] = per.get(key, 0.0) + record["cost"]
    return max([ESTIMATE, *per.values()])


def cost_of(usage) -> float:
    return sum((getattr(usage, key, 0) or 0) * rate / 1e6 for key, rate in RATES.items())


class Metered:
    """An anthropic client whose beta.messages.create notes every response's usage."""

    def __init__(self, client, arm: str, case_id: str):
        self._client, self._arm, self._case = client, arm, case_id
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        response = self._client.beta.messages.create(**kwargs)
        usage = getattr(response, "usage", None)
        record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "case": self._case, "asked": kwargs.get("model"),
                  "served": getattr(response, "model", None)}
        record.update({key: getattr(usage, key, 0) or 0 for key in RATES})
        record["cost"] = round(cost_of(usage), 6)
        _append(HERE / f"spend-{self._arm}.jsonl", record)
        return response


def _state(arm: str) -> tuple[list[str], list[str], list[str]]:
    sealed = [c for c in snapshot_ids() if (arm_dir(arm) / c / "prediction.json").exists()]
    failed = [r["case"] for r in _read_lines(HERE / f"failed-{arm}.jsonl")]
    left = [c for c in snapshot_ids() if c not in sealed and c not in failed]
    return sealed, failed, left


def forecast_one(arm: str, case_id: str, client_factory) -> tuple[int, str]:
    args = ["forecast", "--root", str(arm_dir(arm)), "--playbook",
            str(arm_dir(arm) / "playbook.json"), "--ids", case_id, "--knowledge-cutoff", CUTOFF,
            "--perspective-mode", arm]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = researchos_main(args, client_factory=lambda: Metered(client_factory(), arm,
                                                                     case_id))
    return code, err.getvalue().strip()


def forecast_next(arm: str, count: int, client_factory=real_client) -> str:
    """Forecast up to `count` snapshots of the arm: "done", "capped", "stopped" or "more"."""
    if not arm_dir(arm).exists():
        raise SystemExit("run `python forecast_arm.py setup` first")
    for _ in range(count):
        sealed, failed, left = _state(arm)
        if not left:
            print(f"{arm}: every snapshot is sealed or failed ({len(sealed)} sealed, "
                  f"{len(failed)} failed)")
            return "done"
        case_id, before, need = left[0], spent(arm), most_per_snapshot()
        if before + need > CAP:
            print(f"{arm}: stops at its cap before {case_id}: ${before:.2f} spent, and a "
                  f"snapshot may cost ${need:.2f} of the ${CAP:.2f} cap")
            return "capped"
        code, err = forecast_one(arm, case_id, client_factory)
        cost = spent(arm) - before
        if code == 0 and (arm_dir(arm) / case_id / "prediction.json").exists():
            print(f"{arm}: {case_id} sealed, ${cost:.2f} (arm ${spent(arm):.2f})", flush=True)
        elif API_FAILED in err:
            print(f"{arm}: {case_id} not forecast, ${cost:.2f} spent on it: {err}\n"
                  "The run stops here; nothing is recorded as failed. Fix the cause and run "
                  "the same command again to resume at this snapshot.", flush=True)
            return "stopped"
        else:
            _append(HERE / f"failed-{arm}.jsonl", {"case": case_id, "exit": code, "reason": err})
            print(f"{arm}: {case_id} FAILED (exit {code}), ${cost:.2f}: {err}", flush=True)
    return "more" if _state(arm)[2] else "done"


def fallbacks(arm: str) -> list[str]:
    """The snapshots of the arm any of whose calls was served by a model other than MODEL."""
    return sorted({r["case"] for r in spend(arm) if r.get("served") != MODEL})


def status() -> int:
    for arm in ARMS:
        sealed, failed, left = _state(arm) if arm_dir(arm).exists() else ([], [], snapshot_ids())
        served = sorted({str(r.get("served")) for r in spend(arm)})
        print(f"{arm}: {len(sealed)} sealed, {len(failed)} failed, {len(left)} left; "
              f"{len(spend(arm))} calls, ${spent(arm):.2f} spent; served by "
              f"{', '.join(served) or '-'}")
        if failed:
            print(f"  failed: {', '.join(failed)}")
    other = sorted(set().union(*(fallbacks(arm) for arm in ARMS)))
    if other:
        print(f"Served by a model other than {MODEL} in: {', '.join(other)}\n"
              f"  report primary.py both as is and with --exclude {','.join(other)}")
    return 0


def run_all(client_factory=real_client) -> int:
    finished = set()
    while len(finished) < len(ARMS):
        for arm in ARMS:
            if arm in finished:
                continue
            result = forecast_next(arm, 3, client_factory)
            if result == "stopped":
                status()
                return 1
            if result != "more":
                finished.add(arm)
    return status()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    sub.add_parser("restore")
    nxt = sub.add_parser("next")
    nxt.add_argument("arm", choices=ARMS)
    nxt.add_argument("--count", type=int, default=3)
    sub.add_parser("all")
    sub.add_parser("status")
    args = p.parse_args(argv)
    if args.command == "setup":
        return setup()
    if args.command == "restore":
        return restore()
    if args.command == "next":
        result = forecast_next(args.arm, args.count)
        status()
        return 1 if result == "stopped" else 0
    if args.command == "all":
        return run_all()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
