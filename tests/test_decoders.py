"""Pure-logic self-tests for the input decoders.

These prove the rotary quadrature decode and the MCP button-matrix decode are
correct WITHOUT any hardware -- which is the only way to verify input handling
while the live plugin owns the real GPIO/I2C. Run: python3 tests/test_decoders.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.inputs.rotary import QuadratureDecoder, accelerate
from sable import controls


def test_rotary_cw():
    d = QuadratureDecoder()
    steps = sum(d.update(c, t) for c, t in
                [(1, 1), (1, 0), (0, 0), (0, 1), (1, 1)])
    assert steps == +1, steps


def test_rotary_ccw():
    d = QuadratureDecoder()
    steps = sum(d.update(c, t) for c, t in
                [(1, 1), (0, 1), (0, 0), (1, 0), (1, 1)])
    assert steps == -1, steps


def test_rotary_idle():
    d = QuadratureDecoder()
    steps = sum(d.update(c, t) for c, t in [(1, 1), (1, 1), (1, 1)])
    assert steps == 0, steps


def test_acceleration():
    assert accelerate(0.05) == 4
    assert accelerate(0.10) == 2
    assert accelerate(0.50) == 1


def test_matrix_decode_play():
    # Drive column 0; row 0 pressed (B2 low) -> logical (0,1) after swap -> "pause".
    reg_b = 0xFF & ~(1 << 2)
    pressed = controls.decode_column(0, reg_b, swap_columns=True)
    assert pressed == [(0, 1)], pressed
    assert controls.actions_for(pressed) == ["pause"], controls.actions_for(pressed)


def test_matrix_decode_no_swap():
    reg_b = 0xFF & ~(1 << 2)
    pressed = controls.decode_column(0, reg_b, swap_columns=False)
    assert pressed == [(0, 0)], pressed
    assert controls.actions_for(pressed) == ["play"], controls.actions_for(pressed)


def test_matrix_power_is_no_action():
    # row3/col1 = power -> handled by gpio-shutdown, never a software action.
    reg_b = 0xFF & ~(1 << 5)         # row 3 -> bit 5
    pressed = controls.decode_column(1, reg_b, swap_columns=True)  # logical col 0
    assert pressed == [(3, 0)], pressed
    assert controls.actions_for(pressed) == [], controls.actions_for(pressed)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d decoder tests passed." % len(tests))


if __name__ == "__main__":
    main()
