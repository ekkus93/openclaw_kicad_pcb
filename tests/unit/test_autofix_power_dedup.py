"""Tests for autofix Layer 3b: power-net pin deduplication."""

from __future__ import annotations

from kicad_pcb.ir.autofix import _fix_power_net_pin_duplicates, _is_power_net, autofix_circuit_ir

# ---------------------------------------------------------------------------
# _is_power_net
# ---------------------------------------------------------------------------


def test_gnd_is_power() -> None:
    assert _is_power_net("GND")
    assert _is_power_net("gnd")


def test_vcc_is_power() -> None:
    assert _is_power_net("VCC")


def test_plus_5v_is_power() -> None:
    assert _is_power_net("+5V")
    assert _is_power_net("+3.3V")
    assert _is_power_net("+12V")
    assert _is_power_net("-12V")


def test_signal_net_is_not_power() -> None:
    assert not _is_power_net("RED_LED_NODE")
    assert not _is_power_net("555_THRESHOLD_TRIGGER")
    assert not _is_power_net("OSC_FEEDBACK")
    assert not _is_power_net("NET1")


# ---------------------------------------------------------------------------
# _fix_power_net_pin_duplicates
# ---------------------------------------------------------------------------


def _net(name: str, *pins: tuple[str, str]) -> dict:
    return {"name": name, "pins": [{"ref": r, "pin": p} for r, p in pins]}


def test_led_cathode_removed_from_gnd() -> None:
    """D1 pin 2 in both GND and RED_LED_NODE → removed from GND."""
    nets = [
        _net("GND", ("D1", "2"), ("R1", "2")),
        _net("RED_LED_NODE", ("D1", "2"), ("R1", "1")),
    ]
    fixed, fixes = _fix_power_net_pin_duplicates(nets)

    gnd = next(n for n in fixed if n["name"] == "GND")
    red = next(n for n in fixed if n["name"] == "RED_LED_NODE")

    # D1 pin 2 removed from GND
    assert not any(p["ref"] == "D1" and p["pin"] == "2" for p in gnd["pins"])
    # R1 pin 2 stays in GND (it's only in GND)
    assert any(p["ref"] == "R1" and p["pin"] == "2" for p in gnd["pins"])
    # D1 pin 2 kept in RED_LED_NODE
    assert any(p["ref"] == "D1" and p["pin"] == "2" for p in red["pins"])
    assert len(fixes) >= 1
    assert any("D1" in f and "2" in f for f in fixes)


def test_three_leds_all_deduplicated() -> None:
    """All three LED cathodes removed from GND; GND dropped when it becomes empty."""
    nets = [
        _net("GND", ("D1", "2"), ("D2", "2"), ("D3", "2")),
        _net("RED_LED_NODE", ("D1", "2"), ("R1", "1")),
        _net("YELLOW_LED_NODE", ("D2", "2"), ("R2", "1")),
        _net("GREEN_LED_NODE", ("D3", "2"), ("R3", "1")),
    ]
    fixed, fixes = _fix_power_net_pin_duplicates(nets)

    net_names = {n["name"] for n in fixed}
    # GND had all pins deduplicated and should be dropped
    assert "GND" not in net_names
    # Signal nets preserved
    assert "RED_LED_NODE" in net_names
    assert len(fixes) >= 3


def test_empty_power_net_dropped() -> None:
    """Power net with all pins deduplicated is dropped entirely."""
    nets = [
        _net("GND", ("D1", "2")),
        _net("RED_LED_NODE", ("D1", "2"), ("R1", "1")),
    ]
    fixed, fixes = _fix_power_net_pin_duplicates(nets)
    net_names = [n["name"] for n in fixed]
    assert "GND" not in net_names
    assert any("dropped" in f for f in fixes)


def test_no_collision_leaves_nets_unchanged() -> None:
    """When no pin appears in both power and signal nets, nothing changes."""
    nets = [
        _net("GND", ("R1", "2")),
        _net("RED_LED_NODE", ("D1", "2"), ("R1", "1")),
    ]
    fixed, fixes = _fix_power_net_pin_duplicates(nets)
    assert fixes == []
    assert len(fixed) == 2


def test_pin_only_in_power_net_not_touched() -> None:
    """A pin exclusively in a power net is never removed."""
    nets = [
        _net("GND", ("R1", "2"), ("C1", "2")),
        _net("+5V", ("R1", "1"), ("C1", "1")),
    ]
    fixed, fixes = _fix_power_net_pin_duplicates(nets)
    assert fixes == []


# ---------------------------------------------------------------------------
# End-to-end via autofix_circuit_ir
# ---------------------------------------------------------------------------


def test_autofix_removes_led_cathode_from_gnd() -> None:
    """Full autofix pipeline applies power dedup for LED cathodes."""
    raw = {
        "version": "1",
        "components": [
            {"ref": "D1", "symbol": "Device:LED", "value": "Red"},
            {"ref": "R1", "symbol": "Device:R", "value": "330"},
        ],
        "nets": [
            {"name": "GND", "pins": [{"ref": "D1", "pin": "2"}, {"ref": "R1", "pin": "2"}]},
            {
                "name": "RED_LED_NODE",
                "pins": [
                    {"ref": "D1", "pin": "2"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {"name": "+5V", "pins": [{"ref": "R1", "pin": "1"}]},
        ],
    }
    outcome = autofix_circuit_ir(raw)
    gnd = next(n for n in outcome.ir_dict["nets"] if n["name"] == "GND")
    assert not any(p["ref"] == "D1" for p in gnd["pins"])
    assert any("D1" in f for f in outcome.fixes_applied)
