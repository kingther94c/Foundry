# First real run: Q2 2026 earnings, July to September 2026

Seventeen large-cap reports, all published after the models' knowledge
cutoff (June 2026), so no model in the loop can have seen how any of these
stocks reacted. Built with `researchos.sources`, trained and scored with
`researchos.loop`, every role played by the package's default model.

What is here: `cases.json` (each case's identity, dates, document sources and
sizes, the outcome, how masking went, and the sealed prediction),
`playbook.json` (the trained playbook: weights, 62 lessons, history),
`train.json` and `eval_baseline.json` (the loop's output and token usage).
The documents themselves are not checked in: they are third-party pages.

## Data

- **Documents**: for each report, the results release (mostly the 8-K
  exhibit on sec.gov), a full call transcript, and pre-report consensus
  extracted from earnings previews -- found and fetched through the API's
  server-side web search and fetch, and cut out of their pages by line
  number.
- **Outcomes**: daily closes for each stock and for SPY; the abnormal return
  is the stock's return minus SPY's on the first session that could react
  (the next session for after-close reports).
- Intel and Micron were dropped: no full call transcript could be fetched.
  That leaves 15 cases: the ten earliest for training (July 14-29), the five
  latest held out (July 30 to September 24).

## Masking

Every one of the 15 masks ended identifiable. After four masker rounds and
200 to 400 redactions per case, the guesser still named the company at 0.97
or above. Renaming does not hide a megacap: its segment structure and its
ratios identify it. These cases were used anyway (`--knowledge-cutoff
2026-06-30`). Recognizing the company gives the analyst priors but not the
answer, because the reactions are after its cutoff. Blind training proper
needs smaller, less-followed companies.

## Results

Each training forecast was sealed before its outcome was opened, using only
the lessons from earlier cases, so the training scores are honest forecasts
too.

| | n | up | Brier (weighted) | Brier (judge) | Brier (coin) | Accuracy (judge) |
|---|---|---|---|---|---|---|
| Training, in order | 10 | 5 | 0.174 | 0.164 | 0.250 | 9 / 10 |
| -- first five | 5 | 2 | 0.180 | 0.192 | 0.250 | 4 / 5 |
| -- last five | 5 | 3 | 0.168 | 0.137 | 0.250 | 5 / 5 |
| Held out, untrained playbook | 5 | 3 | 0.260 | 0.254 | 0.250 | 2 / 5 |

**Do not read a trend into this.** The first five training cases were
forecast with an almost empty playbook and still scored 4 / 5, and the
untrained analyst scored 2 / 5 on the held-out cases. With five cases a
side, case-to-case variance swamps any effect of training. The comparison
that would isolate the playbook is the trained playbook on the same held-out
cases. That run failed partway: the API key ran out of credit. To finish it:

```
python -m researchos evaluate --root cases --playbook playbook.json \
    --ids case12,case13,case14,case15,case16 --knowledge-cutoff 2026-06-30
```

What did come out of training: weights moved little from uniform
(expectations_gap 0.156 and management_tone 0.152 at the top, growth 0.133 at
the bottom). The 62 lessons are concrete and general -- for example, "when
the same facts drive the bearish case in most lenses, treat them as one piece
of evidence, not several", or "if the Street already modeled an EPS decline,
a small beat with a soft guide is a met lowered bar, not a fresh positive".
None names a company or product.

## Cost

At Opus-class list prices: masking about $28 (15 cases, about $1.84 each),
training about $11 (10 cases), and the baseline evaluation about $3.
Building the cases took 9.1 million Sonnet-class input tokens, 172 searches
and 273 fetches across two passes; the first pass was lost to the cutter that
the line-number change replaced.

Masking is the expensive step, because each round re-sends documents that
differ from the last round's, so nothing is cached. On megacaps it bought
nothing: every mask ended identifiable. Analysis is cheap by comparison,
because the seven perspectives share one cached copy of the documents.
