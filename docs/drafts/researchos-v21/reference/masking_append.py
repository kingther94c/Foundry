

def unmasked(case: Case) -> MaskedCase:
    """The case's documents as they are (v21 reference)."""
    return MaskedCase(case_id=case.case_id,
                      documents=[Document(kind=d.kind, title=d.title, text=d.text, source="")
                                 for d in case.documents],
                      status="unmasked", rounds=0, mapping={}, factor=1.0, year_offset=0,
                      final_guesses=[], abstracted=False, redaction_rounds=[])
