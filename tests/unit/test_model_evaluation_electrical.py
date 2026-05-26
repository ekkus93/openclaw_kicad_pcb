from __future__ import annotations

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.evaluation.electrical import compare_circuit_ir_equivalence


def _make_ir(*, symbol: str = "Device:R", net_name: str = "NET1") -> CircuitIR:
    return CircuitIR(
        version="1.0",
        components=[
            ComponentIR(ref="R1", symbol=symbol, value="10k"),
        ],
        nets=[
            NetIR(name=net_name, pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R1", pin="2")]),
        ],
    )


def test_compare_circuit_ir_equivalence_passes_for_identical_ir() -> None:
    report = compare_circuit_ir_equivalence(_make_ir(), _make_ir())

    assert report.status == "passed"
    assert report.mismatches == ()


def test_compare_circuit_ir_equivalence_detects_component_and_net_mismatches() -> None:
    report = compare_circuit_ir_equivalence(
        _make_ir(),
        _make_ir(symbol="Device:C", net_name="ALT_NET"),
    )

    assert report.status == "failed"
    assert {mismatch.field for mismatch in report.mismatches} == {
        "component_symbol:R1",
        "net_names",
    }
