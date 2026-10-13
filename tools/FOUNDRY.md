# tools

A small library of reusable, pure utility functions. SPEC.md is the
specification and is authoritative: implement exactly what it says.

## Conventions

- Python 3.12, standard library only.
- Every function is pure: no I/O, no global state, no printing.
- Type hints on every public function; a one-line docstring saying what it
  returns and when it returns None.
- Source files are ASCII.
- Tests live in tests/ and run with `python -m pytest -q`.
