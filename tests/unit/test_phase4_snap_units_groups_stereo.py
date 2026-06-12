"""Phase 4 snap: stereo channel detection and split tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    StereoChannel,
    detect_stereo_channels,
)

pytestmark = pytest.mark.unit


def _make_ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Minimal CircuitIR factory.

    *components* is ``[(ref, symbol), ...]``.
    *nets* is ``[(net_name, [(ref, pin), ...]), ...]``.
    """
    ir_components = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    if not ir_components:
        ir_components = [ComponentIR(ref="_DUMMY", symbol="_")]
    if not ir_nets:
        ir_nets = [NetIR(name="_NC", pins=[PinRefIR(ref=ir_components[0].ref, pin="1")])]
    return CircuitIR(version=version, components=ir_components, nets=ir_nets)


def _stereo_ir() -> CircuitIR:
    """Minimal stereo headphone amp circuit with distinct L/R channel nets.

    Signal paths:
      J1 --[IN_L]--> R1 --[MID_L]--> U1 --[OUT_L]--> J2   (left channel)
      J3 --[IN_R]--> R2 --[MID_R]--> U2 --[OUT_R]--> J4   (right channel)
      J5 shared (power connector, no stereo suffix)        (mono)
    """
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn", "value": ""},
                {"ref": "R1", "symbol": "Device:R", "value": "10k"},
                {"ref": "U1", "symbol": "Amplifier:TL071", "value": "TL071"},
                {"ref": "J2", "symbol": "Connector:Conn", "value": ""},
                {"ref": "J3", "symbol": "Connector:Conn", "value": ""},
                {"ref": "R2", "symbol": "Device:R", "value": "10k"},
                {"ref": "U2", "symbol": "Amplifier:TL071", "value": "TL071"},
                {"ref": "J4", "symbol": "Connector:Conn", "value": ""},
                {"ref": "J5", "symbol": "Connector:Conn", "value": ""},
            ],
            "nets": [
                {"name": "IN_L", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
                {"name": "MID_L", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "U1", "pin": "3"}]},
                {"name": "OUT_L", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "J2", "pin": "1"}]},
                {"name": "IN_R", "pins": [{"ref": "J3", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
                {"name": "MID_R", "pins": [{"ref": "R2", "pin": "2"}, {"ref": "U2", "pin": "3"}]},
                {"name": "OUT_R", "pins": [{"ref": "U2", "pin": "1"}, {"ref": "J4", "pin": "1"}]},
                # J5 on a non-stereo net (mono)
                {
                    "name": "POWER",
                    "pins": [{"ref": "J5", "pin": "1"}, {"ref": "U1", "pin": "8"}],
                },
            ],
        }
    )


class TestDetectStereoChannels:
    """Phase 7 — detect_stereo_channels."""

    def test_detect_stereo_channels_from_net_suffix(self) -> None:  # spec test
        """Components on _L nets → L; on _R nets → R; mixed/none → mono."""
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        assert channels["J1"] == "L"
        assert channels["R1"] == "L"
        assert channels["U1"] == "L"  # only L nets in signal chain
        assert channels["J2"] == "L"
        assert channels["J3"] == "R"
        assert channels["R2"] == "R"
        assert channels["U2"] == "R"
        assert channels["J4"] == "R"

    def test_mono_component_on_mixed_nets(self) -> None:
        """A component on both _L and _R signal nets is classified as mono."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R"), ("J2", "Connector")],
            [
                ("NET_L", [("J1", "1"), ("R1", "1")]),
                ("NET_R", [("R1", "2"), ("J2", "1")]),
            ],
        )
        channels = detect_stereo_channels(ir)
        assert channels["R1"] == "mono"  # has both L and R nets

    def test_dash_suffix_recognised(self) -> None:
        """Nets ending in -L / -R (dash separator) are detected correctly."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R")],
            [("OUT-L", [("J1", "1"), ("R1", "1")])],
        )
        channels = detect_stereo_channels(ir)
        assert channels["J1"] == "L"
        assert channels["R1"] == "L"

    def test_all_components_present_in_result(self) -> None:
        """Every component in ir is represented in the returned dict."""
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        for comp in ir.components:
            assert comp.ref in channels

    def test_no_stereo_nets_all_mono(self) -> None:
        """With no stereo-suffix nets, every component is mono."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R"), ("J2", "Connector")],
            [("NET", [("J1", "1"), ("R1", "1"), ("J2", "1")])],
        )
        channels = detect_stereo_channels(ir)
        assert all(v == "mono" for v in channels.values())

    def test_stereo_channel_type_alias(self) -> None:
        """StereoChannel is exported from layout and is a Literal type alias."""
        # Runtime check: the values returned are valid StereoChannel literals.
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        valid: set[StereoChannel] = {"L", "R", "mono"}
        assert all(v in valid for v in channels.values())


class TestApplyStereoSplit:
    """Phase 7 — _apply_stereo_split post-layout y remapping."""

    # Shared page constants matching graphviz_layout defaults.
    _ORIGIN_Y: float = _gv_mod.ORIGIN_Y
    _PAGE_MAX_Y: float = _gv_mod.PAGE_MAX_Y
    _PAGE_H: float = _gv_mod.PAGE_MAX_Y - _gv_mod.ORIGIN_Y

    def _mid(self) -> float:
        return self._ORIGIN_Y + self._PAGE_H * 0.5

    def test_left_channel_above_midline(self) -> None:  # spec test
        """L-channel components land in the top half (y < midline)."""
        channels: dict[str, str] = {"J1": "L", "R1": "L"}
        # Give them y values spread across the usable page.
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, self._ORIGIN_Y + 10.0, None),
            "R1": (60.0, self._ORIGIN_Y + 60.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        midline = self._mid()
        assert result["J1"][1] < midline, f"J1 y={result['J1'][1]} not above midline {midline}"
        assert result["R1"][1] < midline, f"R1 y={result['R1'][1]} not above midline {midline}"

    def test_right_channel_below_midline(self) -> None:  # spec test
        """R-channel components land in the bottom half (y ≥ midline)."""
        channels: dict[str, str] = {"R2": "R", "U2": "R"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "R2": (30.0, self._ORIGIN_Y + 10.0, None),
            "U2": (60.0, self._ORIGIN_Y + 60.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        midline = self._mid()
        assert result["R2"][1] >= midline, f"R2 y={result['R2'][1]} not below midline {midline}"
        assert result["U2"][1] >= midline, f"U2 y={result['U2'][1]} not below midline {midline}"

    def test_mono_component_y_unchanged(self) -> None:
        """Mono components keep their original y position."""
        channels: dict[str, str] = {"J5": "mono", "R_shared": "L"}
        original_y = self._ORIGIN_Y + 30.0
        positions: dict[str, tuple[float, float, float | None]] = {
            "J5": (10.0, original_y, None),
            "R_shared": (20.0, self._ORIGIN_Y + 20.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result["J5"][1] == pytest.approx(original_y)

    def test_no_stereo_channels_returns_unchanged(self) -> None:
        """When no L/R channels exist, positions are returned as-is."""
        channels: dict[str, str] = {"J1": "mono", "R1": "mono"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "R1": (60.0, 100.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result is positions  # fast-path: same object returned

    def test_x_coordinate_preserved(self) -> None:
        """apply_stereo_split only changes y; x and rotation are preserved."""
        channels: dict[str, str] = {"R1": "L", "R2": "R"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (42.5, self._ORIGIN_Y + 40.0, 0.0),
            "R2": (80.0, self._ORIGIN_Y + 40.0, 90.0),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result["R1"][0] == pytest.approx(42.5)
        assert result["R1"][2] == pytest.approx(0.0)
        assert result["R2"][0] == pytest.approx(80.0)
        assert result["R2"][2] == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# Phase 5 — _apply_post_layout_snaps coordinator
# ---------------------------------------------------------------------------
