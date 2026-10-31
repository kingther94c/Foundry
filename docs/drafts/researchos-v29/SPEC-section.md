## Blend and agreement over the macro perspectives (v29)

`score_sealed` (v28) writes macro records that `blend` and `agreement`
could read, except for two things. The records hold the ten macro
perspectives, while both functions expect the seven of `PERSPECTIVES`. And
a macro record is not known on its event date: a 1-month view of a
snapshot is known a month later. A replay that learned from it after the
snapshot's own batch would let the next week's forecasts use an outcome
that nobody had yet. This version passes the perspectives in, and replays
by the date each record resolved. With neither, every result is what it
was before, to the bit.

In `researchos.blend`:

- `replay(records, rule="hedge", *, eta=0.5, perspectives=None)` and
  `agreement(records, *, perspectives=None)` take the perspectives to read.
  `perspectives` is None, meaning `PERSPECTIVES`, or a non-empty list of
  distinct strings. Anything else raises ValueError before any record is
  read.
  - Every rule, check and result that named `PERSPECTIVES` now names
    these perspectives, in their order.
  - A scored record needs a probability for each of them (the ValueError
    names the case and the missing perspectives). Other probabilities in
    the record are ignored.
  - The hedge weights start at `1 / n` over the `n` perspectives.
  - `leader` breaks a tie in favor of the first perspective in this order.
  - `uniform`, `median` and `pool` are taken over these perspectives.
  - `agreement` gives one entry per perspective in this order, and the
    `n (n - 1) / 2` pairs a-before-b in this order.

- **Resolution dates.** A scored record's resolution date is its
  `resolution_date`, a `str` that `parse_iso_date` reads, when the record
  has that key, and its event date when it does not. A value that is not
  such a string, or a date before the record's event date, raises
  ValueError naming the case, with the other checks and before anything is
  replayed. `replay` still takes the records in event-date order (ties keep
  the order given) and forecasts them in batches of one event date `b`,
  each case of a batch with the state from before the batch. The state now
  learns from a record only once it has resolved:
  1. Before batch `b` is forecast, the state learns from every record not
     yet learned from whose resolution date is before `b`.
  2. After the batch is forecast, its records join those waiting, and the
     state learns from every waiting record whose resolution date is on or
     before `b`.

  Learning is what it was (a `hedge_update`, and the `leader` sums and
  count), one record at a time, in the order of their resolution dates.
  Records that resolve on the same day keep their replay order. When every
  record resolves on its event date, this is the rule of v11 exactly. A
  record that resolves after the last batch is never learned from, which
  changes no forecast. `agreement` takes no account of dates.

- A replayed case is `{"case_id", "event_date", "resolution_date",
  "actual_up", "prob", "brier_blend"}` when its record has a
  `resolution_date` (the date as `YYYY-MM-DD`), and is unchanged when it
  has none.

CLI: `blend` and `agreement` take `--perspectives {earnings,macro}`.
`targets.perspective_set` of that name is passed on as `perspectives`. It
is passed only when the option is given, so without it the call is
unchanged. A ValueError from `replay` or `agreement`, such as a macro
record read without `--perspectives macro`, exits 1 as before.
