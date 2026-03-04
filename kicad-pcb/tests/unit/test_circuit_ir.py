"""Unit tests for kicad_pcb.circuit_ir — Rule 5 GND normalisation.

Covers:
  - R5-3: NetIR.name field_validator normalises GND aliases at construction time,
    ensuring every IR consumer always sees "GND" instead of "0V" / "GROUND" / …
"""

from __future__ import annotations

import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pin(ref: str, pin: str = "1") -> PinRefIR:
    return PinRefIR(ref=ref, pin=pin, unit=None)


def _net(name: str, *pin_args: tuple[str, str]) -> NetIR:
    return NetIR(
        name=name,
        pins=[_pin(ref, pin) for ref, pin in pin_args],
    )


def _simple_ir(net_name: str) -> CircuitIR:
    """Build a minimal one-net CircuitIR with ref R1 carrying *net_name*."""
    return CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1", symbol="R", value="10k")],
        nets=[_net(net_name, ("R1", "1"))],
        options=None,
    )


# ---------------------------------------------------------------------------
# Tests: NetIR standalone
# ---------------------------------------------------------------------------


class TestNetIRNormalization:
    @pytest.mark.parametrize(
        "raw",
        ["0V", "0v", "0V0", "GROUND", "ground", "EARTH", "AGND", "PGND", "DGND", "gnd"],
    )
    def test_gnd_aliases_normalized_on_netiR_construction(self, raw: str) -> None:
        """NetIR normalises any GND alias to 'GND' at construction time."""
        net = _net(raw, ("R1", "1"))
        assert net.name == "GND", f"Expected 'GND' for input {raw!r}, got {net.name!r}"

    def test_canonical_gnd_unchanged(self) -> None:
        net = _net("GND", ("R1", "1"))
        assert net.name == "GND"

    def test_signal_net_unchanged(self) -> None:
        net = _net("net_audio_in", ("R1", "1"))
        assert net.name == "net_audio_in"

    def test_vcc_unchanged(self) -> None:
        net = _net("VCC", ("R1", "1"))
        assert net.name == "VCC"

    def test_strips_whitespace_then_normalizes(self) -> None:
        net = _net("  0V  ", ("R1", "1"))
        assert net.name == "GND"


# ---------------------------------------------------------------------------
# Tests: CircuitIR (full model)
# ---------------------------------------------------------------------------


class TestCircuitIRNormalization:
    def test_zero_volt_net_becomes_gnd_in_full_ir(self) -> None:
        """When an IR is loaded with a '0V' net, it must surface as 'GND'."""
        ir = _simple_ir("0V")
        assert ir.nets[0].name == "GND"

    def test_ground_net_becomes_gnd_in_full_ir(self) -> None:
        ir = _simple_ir("GROUND")
        assert ir.nets[0].name == "GND"

    def test_signal_nets_pass_through_unmodified(self) -> None:
        ir = _simple_ir("net_audio_in")
        assert ir.nets[0].name == "net_audio_in"

    def test_mixed_nets_normalized_correctly(self) -> None:
        """In a multi-net IR, only GND aliases are normalised."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="R", value="10k"),
                ComponentIR(ref="R2", symbol="R", value="10k"),
                ComponentIR(ref="R3", symbol="R", value="10k"),
            ],
            nets=[
                _net("IN_SIGNAL", ("R1", "1")),
                _net("0V", ("R1", "2"), ("R2", "2")),
                _net("OUT_SIGNAL", ("R2", "1"), ("R3", "1")),
                _net("GROUND", ("R3", "2")),
            ],
            options=None,
        )
        net_names = {n.name for n in ir.nets}
        assert "0V" not in net_names, "'0V' should have been normalised to 'GND'"
        assert "GROUND" not in net_names, "'GROUND' should have been normalised to 'GND'"
        assert "GND" in net_names
        assert "IN_SIGNAL" in net_names
        assert "OUT_SIGNAL" in net_names

    def test_load_from_json_dict_normalizes(self) -> None:
        """model_validate() (used by CircuitIR.load) also normalises aliases."""
        payload = {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "R", "value": "10k"}],
            "nets": [{"name": "0V", "pins": [{"ref": "R1", "pin": "1"}]}],
        }
        ir = CircuitIR.model_validate(payload)
        assert ir.nets[0].name == "GND"
