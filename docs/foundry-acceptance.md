# When Foundry's work is good enough to merge

Foundry built `tools/` and `ResearchOS/` change by change, each from a new
SPEC.md section, in an isolated workspace (a fresh copy of the scaffold, a
fresh `FOUNDRY_HOME`, no repository history, no earlier attempts, no
tests it would be judged by). Each change was then judged against a hidden
acceptance suite written from the spec, reviewed, and compared with a
version written by hand. This file fixes what "good enough" means and
scores the changes against it.

## Criteria

A change counts as accepted when all of these hold:

1. **Correct on the first attempt**: it passes every test of the hidden
   acceptance suite (all earlier suites included) with no repair.
2. **Agrees with an independent version**: on differential checks against
   the hand-written reference (fuzzed inputs, real data) any disagreement
   is traced, and none is Foundry's error.
3. **Mergeable as written**: review changes nothing, or only what a spec
   gap made necessary.
4. **Isolated**: the leak audit of the workspace is clean when Foundry
   exits (it read nothing outside the workspace and wrote nothing
   unexpected), and the agent that played the model, when one did, used
   only its relay.
5. **Within budget**: at most 25 agent rounds and about $3 of model tokens.
6. **Honest finish**: it ends with `finish` reporting completion, and the
   validation it claims is a test run it actually made.

## Scorecard: the last fifteen changes

| Change | Hidden tests | Rounds | Tokens (~$) | Review |
|---|---|---|---|---|
| ResearchOS v7 compare | 92/92 | 10 | 0.72 | as written |
| v8 JSON as Windows writes it | 112/112 | 19 | 2.48 | as written |
| v9 several files a side | 135/135 | 11 | 1.09 | as written; the reference broke the old `A.json B.json` form |
| v10 walk-forward without look-ahead | 146/146 | 18 | 2.94 | as written; the reference mishandled a padded date |
| v11 combination rules | 166/166 | 15 | 2.06 | as written |
| v12 shared helpers from tools | 169/169 | 14 | 1.56 | as written, 25 lines shorter than before |
| v13 abstraction masking | 179/179 | 11 | 1.51 | as written; the reference masked an empty brief |
| v14 fetch by address | 192/192 | 13 | 2.02 | as written; the reference read SVG tooltips as the title |
| v15 redact what the masker sees | 203/203 | 10 | 1.01 | scope widened to abstraction mode: a gap in the spec |
| v16 tables, years long past | 211/211 | 14 | 1.95 | as written |
| v17 any layout | 214/214 | 12 | 1.16 | as written |
| v18 rounds in order (real API) | 220/220 | 20 | 0.89 | as written |
| tools v2 | 111/111 | 10 | 0.63 | as written |
| tools v3 | 135/135 | 6 | 0.31 | as written; 20,000 fuzzed inputs, no mismatch |
| tools v4 | 177/177 | 7 | 0.49 | as written; 30,000 fuzzed inputs, no mismatch |

Hidden-test counts were re-run against each attempt's workspace for this
table. v7-v17 and tools v2-v4 ran with a subagent playing the model behind
a local relay while the API key had no credit; their costs are what those
tokens would cost on the API without caching. v18 ran on the real API and
cost $0.89, its prompt cache serving 92% of the input (about $3.60
uncached).
The leak audit at exit was clean for fourteen. In v8 it found compiled
copies of the scaffold's own modules (`__pycache__` left by a test run on
the spec directory; nothing from the hidden tests or the reference), and
the runner has stripped `__pycache__` from every scaffold since. Every relay
agent's tool calls stayed inside its relay.

## Verdict

Every criterion holds for fifteen changes in a row (v8's audit finding was
the runner's doing, not Foundry's, and carried no answers). The four
disagreements the differential checks found were all bugs in the
hand-written references, which Foundry's versions did not have; the one
review change came from a gap in the spec, not from Foundry's code. For
changes of this kind -- one well-specified section, a module or two, up
to a few hundred lines with their tests -- Foundry is good enough to merge
after review.

What this does not show: Foundry was never asked to design the changes.
Each of the fixes that made masking work (v15-v18) came from reading the
masker's reports and measuring the leak, and was then written up as a spec
section for Foundry to implement. Where the spec was imprecise (v15), the
implementation was faithfully imprecise too.
