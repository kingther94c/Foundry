## Macro cases: as-of snapshots, per-target outcomes (v21)

So far a case is one company's earnings report, and its outcome is one
number known at the close of the first session that can react. A macro case
is a snapshot of the markets as of the close of a date: documents that
describe the economy and the markets up to that date, and targets -- market
levels such as the 10-year Treasury yield or the S&P 500 -- each forecast
over one or more horizons (`"1w"`, `"1m"`, `"3m"`, as
`tools.dates.parse_horizon` reads them). Each target and horizon resolves on
its own date, weeks or months after the snapshot, so the outcome is written
later, piece by piece, and a small manifest records which pieces are known.
A macro case has no company to hide, so its documents are analyzed as they
are; and nothing in them stops a model from remembering what followed their
date, so a macro case may only be forecast when its date is after the
model's knowledge cutoff.

This version adds the data types, the files and the checks; nothing calls a
model with a macro case yet. Earnings cases behave exactly as before, and
their files are byte for byte what they were.

### In `researchos.cases`

```python
DOMAINS = ("earnings", "macro")
KINDS = ("bp", "return", "points")   # how a target's change is measured (tools.finance.level_change)
RESOLUTION_FILE = "resolution.json"

@dataclass
class Target:
    id: str                  # e.g. "DGS10"
    name: str                # e.g. "10-year Treasury yield"
    kind: str                # one of KINDS
    horizons: list[str]      # e.g. ["1w", "1m", "3m"]
    as_of_date: str          # ISO date of the observation the forecasts start from
    as_of_value: float       # the level on that date
    trailing: dict[str, float | None] = field(default_factory=dict)
    # horizon -> the change over the same length of time up to as_of_date

@dataclass
class TargetOutcome:
    target: str              # the Target's id
    horizon: str
    kind: str
    start_date: str
    start: float
    end_date: str
    end: float
    change: float            # tools.finance.level_change(start, end, kind)
    up: bool                 # change > 0
```

`Case` gains two fields after `documents`: `domain: str = "earnings"` and
`targets: list[Target] = field(default_factory=list)`.
`Outcome.abnormal_return_1d` becomes `float | None = None`; it stays the
first field, so `Outcome(0.03)` means what it did. `Outcome` gains
`targets: dict[str, TargetOutcome] = field(default_factory=dict)` after
`sources`, keyed by `target_key`.

- `target_key(target_id, horizon) -> str`: `f"{target_id}_{horizon}"`, the
  name a target's forecast over a horizon goes by (a later version makes it
  a property name in a schema). Raises ValueError when `target_id` is not a
  `str` that matches `[A-Za-z0-9_]+` as a whole (ASCII letters, digits and
  underscores only), or when `horizon` is not a valid horizon. A horizon is
  valid when `tools.dates.parse_horizon` accepts it and it has no whitespace
  around it (`horizon == horizon.strip()`): `"1w"` is valid; `" 1w"`, and
  `"1w"` followed by a newline, are not.
- `check_targets(targets) -> None`: raises ValueError, naming the target (by
  its id, or by its position in the list when the id itself is the
  problem), when:
  - `targets` is empty (a macro case forecasts at least one target);
  - an element is not a `Target`;
  - an id is not valid (as for `target_key`), or two targets have the same
    id;
  - a `kind` is not in `KINDS`;
  - `horizons` is not a non-empty list of valid horizons without repeats;
  - `as_of_date` is not a `str` that `tools.dates.parse_iso_date` reads;
  - `as_of_value` is not a number (`tools.stats.is_number`).

  `name` and `trailing` are not checked.
- `target_keys(case) -> list[str]`: the keys of every target of the case
  and every horizon of that target, target by target in the case's order,
  each target's horizons in its order. `[]` when the case has no targets
  (every earnings case); otherwise `check_targets(case.targets)` runs first,
  and its ValueError passes through.
- `macro_case(case_id, as_of, targets, documents) -> Case`: a macro case.
  `as_of` must be a `str` that `parse_iso_date` reads; it is stored in its
  canonical form `YYYY-MM-DD` (`parse_iso_date(as_of).isoformat()`, so
  `" 2026-10-30 "` becomes `"2026-10-30"`). The case has `company=""`,
  `ticker=""`, `aliases=[]`, `fiscal_period=f"as of {as_of}"` (canonical),
  `event_date=as_of` (canonical: every rule that orders or gates cases by
  date applies to the snapshot's date), `documents=list(documents)`,
  `domain="macro"` and `targets=list(targets)`. Raises ValueError when
  `as_of` is not an ISO date string, when `check_targets(targets)` fails,
  or when a target's `as_of_date` is after `as_of` (a snapshot cannot start
  from a level it had not seen yet), naming the target.
- `target_from_dict(data) -> Target`: `id`, `kind`, `horizons`,
  `as_of_date` and `as_of_value` are required (KeyError when one is
  missing); `name` defaults to `""` and `trailing` to `{}`. `horizons`
  becomes a new list and `trailing` a new dict.
- `target_outcome_from_dict(data) -> TargetOutcome`: every field required
  (KeyError when one is missing).

Files:

- `case.json` is `asdict(case)`, except that the keys `domain` and
  `targets` are left out when the case's domain is `"earnings"` and it has
  no targets, so such a file is exactly what it was before v21.
  `outcome.json` is `asdict(outcome)` without the key `targets` when
  `outcome.targets` is empty.
- `save_case(root, case, outcome=None) -> None`: as before when `outcome`
  is given. With `None`, only `case.json` is written; an existing
  `outcome.json` is left as it is.
- `save_outcome(root, case_id, outcome) -> None`: writes `outcome.json`
  under `<root>/<case_id>/` (creating the directory), in the same format.
- `load_case` also reads `domain` (default `"earnings"`) and `targets`
  (default `[]`, each element through `target_from_dict`). It still never
  opens `outcome.json`.
- `load_outcome`: a file without a `targets` key is read exactly as before
  (KeyError when `abnormal_return_1d` is missing). With a `targets` key,
  `abnormal_return_1d` defaults to `None` and every entry of `targets`
  becomes a `TargetOutcome` through `target_outcome_from_dict`, its key
  kept.

The resolution manifest, `<root>/<case_id>/resolution.json`, records which
of a case's targets have resolved. It holds dates and flags, never a level
or a change, so it can be read before a forecast is sealed, as
`outcome.json` cannot. A target's horizon ends on
`tools.dates.horizon_end(as_of, horizon)`, where `as_of` is the case's
`event_date`; that date is also the target's resolution date, the first
date on which its outcome can be known.

- `resolution_manifest(case, resolved=()) -> dict`:

  ```json
  {"as_of": "2026-10-30",
   "targets": {"DGS10_1w": {"horizon_end": "2026-11-06", "resolved": true},
               "DGS10_1m": {"horizon_end": "2026-11-30", "resolved": false}}}
  ```

  `as_of` is the case's `event_date` in its canonical form; `targets` has
  one entry per key of `target_keys(case)`, in that order, with the horizon
  end as an ISO date and `resolved` True exactly for the keys in
  `resolved` (any iterable of keys). Raises ValueError when the case's
  domain is not `"macro"`, when its `event_date` is not a `str` that
  `parse_iso_date` reads, when `target_keys(case)` fails, when a horizon end
  is outside the dates Python can represent (the ValueError of
  `horizon_end`), or when `resolved` names a key the case does not have.
- `save_resolution(root, case_id, manifest) -> None`: writes the manifest
  as `resolution.json` (creating the directory).
- `load_resolution(root, case_id) -> dict | None`: the manifest; `None` when
  there is no `resolution.json`. It never opens `outcome.json`.
- `unresolved_targets(case, manifest) -> list[str]`: the keys of
  `target_keys(case)`, in that order, that the manifest does not mark
  resolved. A key is resolved only when `manifest["targets"][key]` is a
  dict whose `"resolved"` is `True`; anything else counts as unresolved: a
  `manifest` of `None` (or one that is not a dict), a missing or non-dict
  `targets`, a missing key, a `resolved` that is not `True`. Keys the
  manifest has and the case does not are ignored. ValueError from
  `target_keys` passes through.

### In `researchos.masking`

- `unmasked(case) -> MaskedCase`: the case's documents as they are, for a
  case with nothing to hide: `MaskedCase(case_id=case.case_id,
  documents=[...], status="unmasked", rounds=0, mapping={}, factor=1.0,
  year_offset=0, final_guesses=[], abstracted=False, redaction_rounds=[])`,
  where `documents` has one new `Document` per document of the case, in
  order, with the same `kind`, `title` and `text` and `source=""` (as a
  masked case's documents have). Nothing is changed in the text; changing
  the result does not change the case. No agent is called and no file is
  read or written.

### In `researchos.loop`

- `require_after_cutoff(case_id, case, knowledge_cutoff) -> None`: a macro
  case can only be forecast when its date is after the model's knowledge
  cutoff. Raises ValueError naming `case_id` when `knowledge_cutoff` is
  `None`, when the case's `event_date` is not a `str` that
  `parse_iso_date` reads, or when that date is on or before the cutoff.
  `knowledge_cutoff` is anything `parse_cutoff` accepts, and
  `parse_cutoff`'s ValueError passes through.
- `single_domain(cases) -> str`: the domain every case in `cases` (an
  iterable of `Case`) shares; `"earnings"` when there are none. Raises
  ValueError naming the domain when a case's domain is not in `DOMAINS`,
  and naming both when the cases have more than one domain: forecasts,
  playbooks and perspectives differ by domain, so a run is one domain.

Nothing calls these yet; the CLI is unchanged.
