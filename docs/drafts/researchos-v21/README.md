# Draft: ResearchOS v21, macro cases (S2 of the macro pivot)

Held out of `ResearchOS/` until Foundry's build is accepted:

- `SPEC-section.md`: appended to `ResearchOS/SPEC.md` in the build workspace.
- `test_macro_cases_acceptance.py`: the hidden acceptance suite (173 tests).
- `reference/`: a hand-written version: `cases.py` replaces the module, and the
  two `*_append.py` files are appended to `masking.py` and `loop.py`.
- `check.py`: `suite` runs the hidden suite with every existing ResearchOS test,
  against the reference or (`--build WS`) a build's package. `fuzz --build WS`
  runs about 22,000 calls on both versions and compares them.

Run both with an interpreter that has pytest and anthropic. Against the
reference, `suite` passes 878 tests. A copy of the reference with two deliberate
bugs fails 9 of them, and the fuzzer finds 208 mismatches.

When the build is accepted:

1. Append the section to `ResearchOS/SPEC.md`.
2. Copy the build's code and unit tests into `ResearchOS/`.
3. Move the suite to `ResearchOS/tests/`.
4. Add a scorecard row to `docs/foundry-acceptance.md`.
5. Delete this directory.
