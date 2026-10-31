

def require_after_cutoff(case_id, case, knowledge_cutoff) -> None:
    """v21 reference."""
    cutoff = parse_cutoff(knowledge_cutoff)
    if cutoff is None:
        raise ValueError(f"case {case_id}: a macro case needs a knowledge cutoff")
    value = case.event_date
    when = parse_iso_date(value) if isinstance(value, str) else None
    if when is None:
        raise ValueError(f"case {case_id}: event_date {value!r} is not an ISO date")
    if when <= cutoff:
        raise ValueError(f"case {case_id}: {when.isoformat()} is on or before the knowledge "
                         f"cutoff {cutoff.isoformat()}")


def single_domain(cases) -> str:
    """v21 reference."""
    from researchos.cases import DOMAINS
    found: list[str] = []
    for case in cases:
        if case.domain not in DOMAINS:
            raise ValueError(f"case {case.case_id}: unknown domain {case.domain!r}")
        if case.domain not in found:
            found.append(case.domain)
    if len(found) > 1:
        raise ValueError(f"the cases mix domains {found[0]!r} and {found[1]!r}; a run is one domain")
    return found[0] if found else "earnings"
