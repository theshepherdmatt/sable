#!/usr/bin/env python3
"""Generate a rising sine sweep as raw s16le stereo @ 44100 and write it to
stdout -- a TEST audio source for CAVA. Pipe into Sable's input fifo:

    python3 tools/test_tone.py > /tmp/sable-cava.fifo

This is for bench verification only. It is NOT the MPD pipeline and touches no
live audio. Runs until killed / the reader goes away.
"""
import array
import math
import sys

SR = 44100


def main():
    t = 0.0
    f = 120.0
    out = sys.stdout.buffer
    while True:
        buf = array.array("h")   # signed 16-bit, native (LE on the Pi)
        for _ in range(2205):    # ~50ms per chunk
            s = int(0.35 * 32767 * math.sin(2 * math.pi * f * t))
            buf.append(s)        # L
            buf.append(s)        # R
            t += 1.0 / SR
            f += 0.03            # sweep up so bars move across the spectrum
            if f > 9000.0:
                f = 120.0
        try:
            out.write(buf.tobytes())
            out.flush()
        except (BrokenPipeError, OSError):
            break


if __name__ == "__main__":
    main()
