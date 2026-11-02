"""Shared database helpers."""
import json


def _safe_json(raw, default=None):
    """Parse a JSON column value, returning ``default`` on bad/empty data.

    A single corrupt current_specs/target_specs cell must not crash a whole
    queue read — that would freeze every polled UI that lists the queue.
    """
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default
