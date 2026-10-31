

# ---- walk forward by resolution date (v30 reference) ----------------------------------

from tools.dates import parse_iso_date as _v30_parse_iso_date
from tools.stats import hedge_update as _v30_hedge_update


def update_weights_probs(playbook, probs, actual_up, eta=0.5) -> None:
    """Hedge update of the weights from bare probabilities (squared error as the loss)."""
    y = 1.0 if actual_up else 0.0
    playbook.weights = _v30_hedge_update(playbook.weights,
                                         {name: (p - y) ** 2 for name, p in probs.items()}, eta)


def learned_through(playbook):
    """The latest date the playbook learned from: resolution_date, else event_date."""
    latest = None
    for record in playbook.history:
        if not isinstance(record, dict) or "skipped" in record:
            continue
        value = record.get("resolution_date")
        if value is None:
            value = record.get("event_date")
        if value is None:
            continue
        when = _v30_parse_iso_date(value)
        if when is None:
            raise ValueError(f"playbook history record for case {record.get('case_id')!r} has "
                             f"the date {value!r}, which is not an ISO date (YYYY-MM-DD)")
        if latest is None or when > latest:
            latest = when
    return latest
