"""Time helpers."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current UTC time as a NAIVE datetime.

    Replaces the deprecated ``datetime.utcnow()``. Naive on purpose: every
    timestamp in the database is a naive UTC string ('%Y-%m-%d %H:%M:%S'),
    and these values are compared/subtracted with ones parsed from it.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
