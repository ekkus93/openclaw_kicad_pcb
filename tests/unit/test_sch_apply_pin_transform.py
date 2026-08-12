from __future__ import annotations

import math

import pytest

from kicad_pcb._router_geometry_basic import _stub_end
from kicad_pcb.commands._sch_apply_write import _transform_pin_at


@pytest.mark.parametrize(
    ("rotation", "pin_angle", "expected_angle"),
    [
        (0, 0.0, 0.0),
        (0, 90.0, 270.0),
        (0, 180.0, 180.0),
        (0, 270.0, 90.0),
        (90, 0.0, 270.0),
        (90, 90.0, 180.0),
        (90, 180.0, 90.0),
        (90, 270.0, 0.0),
        (180, 0.0, 180.0),
        (180, 90.0, 90.0),
        (180, 180.0, 0.0),
        (180, 270.0, 270.0),
        (270, 0.0, 90.0),
        (270, 90.0, 0.0),
        (270, 180.0, 270.0),
        (270, 270.0, 180.0),
    ],
)
def test_transform_pin_at_rotates_bodyward_pin_angle(
    rotation: int,
    pin_angle: float,
    expected_angle: float,
) -> None:
    transformed = _transform_pin_at({"1": (0.0, 0.0, pin_angle)}, 10.0, 20.0, rotation)

    assert transformed["1"][2] == expected_angle


def test_transform_pin_at_rotates_vertical_pin_position_consistently() -> None:
    transformed = _transform_pin_at({"1": (2.54, -5.08, 90.0)}, 100.0, 50.0, 90)

    x, y, angle = transformed["1"]
    assert x == pytest.approx(105.08)
    assert y == pytest.approx(47.46)
    assert angle == 180.0


def test_rotated_resistor_stubs_do_not_terminate_on_opposite_pins() -> None:
    resistor_pins = {
        "1": (0.0, 0.0, 0.0),
        "2": (5.08, 0.0, 180.0),
    }
    r1 = _transform_pin_at(resistor_pins, 130.81, 105.41, 270)
    r2 = _transform_pin_at(resistor_pins, 130.48, 120.65, 90)

    assert r1["2"] == pytest.approx((130.81, 110.49, 270.0))
    assert r2["1"] == pytest.approx((130.48, 120.65, 270.0))

    r1_stub = _stub_end(*r1["2"])
    r2_stub = _stub_end(*r2["1"])
    r1_opposite_pin = r1["1"][:2]
    r2_opposite_pin = r2["2"][:2]

    assert math.dist(r1_stub, r1_opposite_pin) > 0.01
    assert math.dist(r2_stub, r2_opposite_pin) > 0.01
