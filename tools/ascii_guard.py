#!/usr/bin/env python3
"""Fail if any given source file contains non-ASCII bytes.

The old index.js shipped curly quotes / em-dashes that caused a SyntaxError at
load. This guard runs in CI / pre-commit so it can never happen again.
Usage: ascii_guard.py <file> [<file> ...]
"""
import sys


def check(path):
    bad = []
    with open(path, "rb") as fh:
        for n, line in enumerate(fh, 1):
            for col, b in enumerate(line, 1):
                if b > 0x7F:
                    bad.append((n, col, b))
                    break
    return bad


def main(argv):
    rc = 0
    for path in argv[1:]:
        for n, col, b in check(path):
            rc = 1
            print("%s:%d:%d: non-ASCII byte 0x%02X" % (path, n, col, b))
    if rc == 0:
        print("ascii_guard: OK (%d file(s))" % (len(argv) - 1))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
