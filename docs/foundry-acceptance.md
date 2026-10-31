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

## Scorecard: the last thirty changes

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
| v19 perspective agreement (real API) | 230/230 | 11 | 0.50 | as written; 2,000 random record sets and the 21 real cases, no mismatch; it also handles an underflow the reference let through |
| v20 isolated perspectives (real API) | 275/275 | 24 | 0.94 | as written; 3,000 random analyses, the same calls, prompts and forecasts as the reference |
| v21 macro cases | 448/448 | 9 | 1.83 | as written; it also kept `build`'s output unchanged, which the reference did not, and the reference could not read back a case whose `trailing` was not a dict |
| v22 series from free sources | 636/636 | 6 | 1.36 | as written; 17,000 fuzzed calls, no mismatch |
| v23 snapshot tables | 715/715 | 6 | 0.71 | as written but for one word of a docstring; 21,000 fuzzed calls, no mismatch |
| v24 building a snapshot | 790/790 | 10 | 2.30 | as written; on 2,536 fuzzed calls it agrees with the reference but where every target is left out: the reference fetched a page before refusing, Foundry's refuses first, and the spec now says so |
| v25 resolving a snapshot | 834/834 | 7 | 1.51 | as written; 3,000 fuzzed calls, no mismatch |
| v26 many targets, one analysis | 893/893 | 7 | 1.33 | as written; 3,000 random analyses, the same calls, prompts and forecasts as the reference |
| v27 scoring and reflecting on many targets | 920/920 | 8 | 1.59 | as written; 9,000 fuzzed calls, no mismatch |
| v28 forecast now, score later | 978/978 | 7 | 2.02 | one fix to its own test, from a gap in FOUNDRY.md: a subprocess import check found `tools` only where it is installed, which a clone no longer does; 1,200 fuzzed calls, no mismatch |
| v29 blend and agreement over the macro perspectives | 1014/1014 | 8 | 1.57 | as written; 16,000 fuzzed calls, no mismatch |
| v30 walk forward by resolution date | 1067/1067 | 12 | 3.17 | as written; one hidden test asked for more than the spec (that the refusal of another domain's playbook not begin `train refused: `, which the spec leaves open) and was corrected, not the code; 2,400 fuzzed runs of `train`, no mismatch |
| v31 the main text of a fetched page | 1130/1130 | 9 | 1.23 | as written; 40,000 fuzzed pages, no mismatch, and on every one the whole text was unchanged |
| tools v2 | 111/111 | 10 | 0.63 | as written |
| tools v3 | 135/135 | 6 | 0.31 | as written; 20,000 fuzzed inputs, no mismatch |
| tools v4 | 177/177 | 7 | 0.49 | as written; 30,000 fuzzed inputs, no mismatch |
| tools v5 (real API) | 251/251 | 8 | 0.32 | as written; the reference overflowed on weights near the float limit, Foundry's did not; 60,000 other fuzzed calls, no mismatch |
| tools v6 horizons, level changes | 478/478 | 5 | 0.55 | as written; 20,000 fuzzed calls, no mismatch |

Hidden-test counts were re-run against each attempt's workspace for this
table. v7-v17 and tools v2-v4 ran with a subagent playing the model behind
a local relay while the API key had no credit, and v21-v31 and tools v6 the
same way on a machine with no API key; their costs are what those
tokens would cost on the API without caching. v18, v19, v20 and tools v5
ran on the real API and cost $0.89, $0.50, $0.94 and $0.32, the prompt
cache serving 82-94% of the input (about $3.60, $1.32, $4.88 and $0.77
uncached).
The leak audit at exit was clean for twenty-nine. In v8 it found compiled
copies of the scaffold's own modules (`__pycache__` left by a test run on
the spec directory; nothing from the hidden tests or the reference), and
the runner has stripped `__pycache__` from every scaffold since. (The audit
searches every file an attempt leaves for paths outside its workspace; a
file compiled from the attempt's own source names that source, inside the
workspace, and is no longer counted.) Every relay agent's tool calls stayed
inside its relay but one. In v23 the agent ran a five-line Python script of
its own, to move a file's deletion from one patch it had written into
another; the script touched only that reply file, and every other call was
a relay command, a write to its replies or a read of the relay's output,
so nothing reached the model that did not come through Foundry. In v27 the
agent changed one of its patch files three times with the Edit tool, where
it was asked to write the file again instead; that too touched only its
replies. In v30 it added a redirect and an `echo` to one relay command,
which threw that command's output away, and ran the command again.

## Verdict

Every criterion holds for thirty changes in a row (v8's audit finding was
the runner's doing and v23's extra script and v30's extra shell text the
stand-in model's, not Foundry's, and none carried answers). The eight
disagreements the differential checks found were all in the hand-written
references, which Foundry's versions did not have. One hidden test was
wrong too: v30's told two refusals apart by opening words the spec leaves
open, and it was corrected without a change to Foundry's code. Of the three
review changes, two came from gaps in the spec (in v28, FOUNDRY.md still
called `tools` an installed package after the clone stopped installing
it) and the third corrected a word in a docstring. For
changes of this kind -- one well-specified section, a module or two, up
to a few hundred lines with their tests -- Foundry is good enough to merge
after review.

What this does not show: Foundry was never asked to design the changes.
Each of the fixes that made masking work (v15-v18) came from reading the
masker's reports and measuring the leak, and was then written up as a spec
section for Foundry to implement. Where the spec was imprecise (v15), the
implementation was faithfully imprecise too.
