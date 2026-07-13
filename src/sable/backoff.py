"""Shared reconnect-backoff helper.

Lives outside volumio/moode so neither listener has to import the other's
backend module (and its backend-specific deps, e.g. python-socketio) just to
get this.
"""


def backoff_delay(attempt, base=2.0, cap=60.0):
    """Reconnect delay for the Nth consecutive failure (1-based)."""
    return min(base * max(1, attempt), cap)
