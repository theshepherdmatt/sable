"""Synthetic input source for bench testing.

Feeds scripted or stdin commands into the same handler the IPC socket uses, so
the boot/clock/menu flow can be exercised with no GPIO and no live services.
"""


def run_script(handle, steps, log=print):
    """steps: list of (cmd, arg) tuples, dispatched in order."""
    for cmd, arg in steps:
        log("input:", cmd, "" if arg is None else arg)
        handle(cmd, arg)


def run_stdin(handle, log=print):
    """Read 'cmd arg' lines from stdin (Ctrl-D to stop)."""
    import sys

    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        arg = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else (
            parts[1] if len(parts) > 1 else None)
        handle(cmd, arg)
