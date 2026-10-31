# Draft: tools v6, horizons and level changes

Stage S1 of the ResearchOS macro-first plan: the date and change arithmetic
every later stage scores a view with. It is drafted here and has not been
built. Foundry builds it the way it built tools v2 to v5
([docs/foundry-acceptance.md](../../foundry-acceptance.md)); this folder holds
what that build is judged by, so **it must stay out of the build workspace**.

| File | What it is |
|---|---|
| `SPEC-section.md` | the section to append to `tools/SPEC.md`; Foundry builds from it |
| `test_v6_acceptance.py` | the hidden acceptance suite (227 tests) |
| `reference.py` | a version written by hand from the spec, for the differential check |
| `check.py` | runs the suite, and fuzzes a build against the reference |

The build:

1. Make the isolated workspace from `tools/` as it is, with
   `SPEC-section.md` appended to its `SPEC.md`, and a fresh `FOUNDRY_HOME`.
   Nothing from this folder goes in.
2. Run Foundry in it, within the budget of 25 rounds and about $3.
3. Judge the result:

   ```bash
   python docs/drafts/tools-v6/check.py suite --tools <workspace>
   python docs/drafts/tools-v6/check.py fuzz --tools <workspace>
   ```

   and run the workspace's whole suite, earlier acceptance suites included.
   Trace every fuzz mismatch to the build, the reference or the spec.
4. If it is accepted: append the section to `tools/SPEC.md`, take the build's
   code and unit tests, move `test_v6_acceptance.py` to `tools/tests/`, add a
   row to the scorecard in `docs/foundry-acceptance.md` and a line to
   `tools/README.md`, and delete this folder.

`check.py suite` with no `--tools` runs the suite against the reference: 227
passed when this was drafted. Against a copy of the reference with two
deliberate bugs (a change at the band's edge counted as up; the first of
several observations on one date kept instead of the last), the suite failed
4 tests and the fuzz found 386 mismatches in 20,000 calls.
