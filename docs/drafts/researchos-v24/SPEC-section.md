## Building a snapshot (v24)

One configuration file describes every snapshot of a run. It lists:
- the targets and their horizons;
- the economic series and the positioning to show;
- the official texts to include, each with the date it was published;
- the calendar of scheduled events.

`snapshot` turns it into the macro case of one date, using only what was
known on that date:
- the series as stored by `fetch-series`, cut at the date (v22);
- for each kind of text, the latest one published by then;
- the events scheduled after it.

The tables of v23 are the case's first documents. No outcome is written:
the case's targets resolve later.

In `researchos.snapshot`:

- `load_macro_config(path) -> dict`: the configuration, read with
  `read_json`. It returns the config with these defaults filled in:
  - `context`, `positioning`, `documents` and `calendar` default to `[]`;
  - a target's `optional` defaults to `False`;
  - a positioning entry's `open_interest` defaults to `None`.

  It raises ValueError naming `path` when:
  - the file is not a JSON object;
  - `case_prefix` is not a `str` that matches `[A-Za-z0-9_-]+` as a whole;
  - `horizons` is not a non-empty list of valid horizons (as
    `cases.target_key` accepts them) without repeats;
  - `targets` is not a non-empty list of objects, each with:
    - an `id` that `cases.target_key` accepts, the ids all different;
    - a `name` and a `unit` that are strings;
    - a `kind` in `cases.KINDS`;
    - an `optional`, if given, that is a `bool`;
  - `context` is not a list of objects, each with an `id` that is a series
    id (v22) and a `name` and `unit` that are strings;
  - `positioning` is not a list of objects, each with:
    - an `id` and a `net` that are series ids;
    - a `name` that is a string;
    - an `open_interest` that is a series id or null;
    - a `release_lag_days` that is an `int`, not a `bool`, of at least 0;
  - `documents` is not a list of objects, each with:
    - a `kind` that is a non-empty string;
    - a `title` that is a string;
    - a `dated` that is a string `parse_iso_date` reads;
    - exactly one of `url` and `text`, which is a string;
  - `calendar` is not a list of objects, each with a `date` that is a string
    `parse_iso_date` reads and an `event` that is a string.

  Keys it does not name are ignored. Its OSError (a missing file) passes
  through.

- `build_snapshot(root, config, as_of, *, series_dir, fetch_page) -> dict`:
  builds and saves the macro case of `as_of` (a date argument) from a
  config as `load_macro_config` returns it. `fetch_page(url)` returns an
  object with a `text` attribute (`sources.get_page` with a user agent, in
  the CLI).

  1. The case id is `f"{config['case_prefix']}-{as_of}"`, with `as_of` in
     the form `YYYY-MM-DD`.
  2. When `<root>/<case_id>/prediction.json` or `outcome.json` exists, it
     raises ValueError naming the case before anything is read or written:
     a case is never rebuilt under a forecast that was already sealed or
     an outcome that was already written.
  3. Each target's series is `series.load_series(series_dir, id, as_of)`; a
     missing file (OSError) counts as no observations. A target whose
     series has no observation on or before `as_of` is missing.
     - A missing optional target is left out of the case, with a warning
       on the `researchos` logger naming it.
     - When any target that is not optional is missing, it raises
       ValueError naming every such target, before anything is written.
  4. Each other target becomes a `cases.Target` with:
     - `id`, `name` and `kind` from the config;
     - `horizons` a new list of the config's horizons;
     - `as_of_date` and `as_of_value` from the last observation on or before
       `as_of` (the date in the form `YYYY-MM-DD`);
     - `trailing` mapping each horizon `h` to `trailing_change(obs, as_of,
       h, kind)`.
  5. The documents, in this order, each with `source=""` unless said
     otherwise:
     - `Document("dashboard", f"Markets as of {as_of}",
       target_table(as_of, <the config entries of the targets kept>,
       <their series>))`;
     - when `context` is not empty, `Document("economy", f"Economic data as
       of {as_of}", context_table(as_of, context, <their series>,
       <published>))`, where each series is `load_series(series_dir, id,
       as_of)`, a missing file giving no observations, and `<published>`
       holds the ids whose file `<series_dir>/<id>@<as_of>.json` exists;
     - when `positioning` is not empty, `Document("positioning",
       f"Positioning as of {as_of}", positioning_table(as_of, positioning,
       <series>))`, where `<series>` maps every `net` and `open_interest` id
       to `load_series(series_dir, id, as_of)`, a missing file giving no
       observations;
     - when `calendar` is not empty, `Document("calendar", f"Calendar after
       {as_of}", calendar_list(as_of, calendar, <through>))`, where
       `<through>` is the latest `tools.dates.horizon_end(as_of, h)` over
       the config's horizons;
     - one document per `kind` of the config's `documents`, in the order in
       which the kinds first appear there. Of the entries of that kind dated
       on or before `as_of` it takes the one with the latest `dated` (of two
       on the same date, the later entry); a kind with no such entry gives
       no document. Its title is `f"{title} ({dated})"`, with `dated` in the
       form `YYYY-MM-DD`. With `text`, the document's text is that text.
       With `url`, its text is `fetch_page(url).text` and its `source` is
       the url. When `fetch_page` raises OSError or ValueError, the document
       is left out, with a warning naming the url. Nothing is fetched for an
       entry that is not taken.
  6. The case is `cases.macro_case(case_id, as_of, targets, documents)`. It
     is saved with `save_case(root, case)` (no outcome). Its manifest is
     saved with `save_resolution(root, case_id,
     resolution_manifest(case))`, nothing resolved.
  7. It returns `{"case_id": ..., "as_of": ..., "targets": [<ids kept>],
     "left_out": [<optional ids missing>], "documents": [<kind of each
     document, in order>], "keys": target_keys(case)}`.

  The same series, config and pages give the same files byte for byte.
  Building the case again before it is sealed or resolved rewrites it.

CLI:

```text
python -m researchos snapshot --root DIR --config FILE --as-of YYYY-MM-DD --series-dir DIR
                              [--user-agent TEXT] [--out FILE]
```

- It runs `load_macro_config(--config)`, then `build_snapshot` with
  `fetch_page = lambda url: sources.get_page(url, user_agent=...)`. The user
  agent is chosen as `build` chooses it.
- It emits the summary as every command does. No client is built.
- Exit 2 on bad arguments (`--as-of` not an ISO date, a missing option).
- Exit 1, with a message on stderr, on OSError or ValueError.
