from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.electrical_equivalence import compare_circuit_ir_equivalence
from kicad_pcb.errors import UserError
from kicad_pcb.refinement import schematic_semantics
from kicad_pcb.refinement.electrical import (
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.refinement.schematic_semantics import extract_schematic_semantics


def _ir(
    *,
    net_name: str = "SIGNAL",
    footprint: str | None = None,
) -> CircuitIR:
    return CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k", footprint=footprint),
            ComponentIR(ref="R2", symbol="Device:R", value="20k", footprint=footprint),
        ],
        nets=[
            NetIR(
                name=net_name,
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
            )
        ],
    )


def test_unnamed_net_rename_with_same_terminal_partition_passes() -> None:
    source = _ir(net_name="Net-(R1-Pin1)")
    generated = _ir(net_name="Net-(R2-Pin1)")

    report = compare_circuit_ir_equivalence(source, generated)

    assert report.status == "passed"
    assert report.mismatches == ()


def test_unnamed_net_partition_change_fails() -> None:
    source = _ir(net_name="Net-(R1-Pin1)")
    generated = CircuitIR(
        version="1",
        components=source.components,
        nets=[
            NetIR(
                name="Net-(R2-Pin1)",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="2")],
            )
        ],
    )

    report = compare_circuit_ir_equivalence(source, generated)

    assert report.status == "failed"
    assert [m.code for m in report.mismatches] == ["UNNAMED_NET_PARTITIONS_MISMATCH"]


def test_footprint_change_fails_when_enabled() -> None:
    report = compare_circuit_ir_equivalence(
        _ir(footprint="Resistor_SMD:R_0603_1608Metric"),
        _ir(footprint="Resistor_SMD:R_0805_2012Metric"),
        compare_footprints=True,
    )

    assert report.status == "failed"
    assert {m.code for m in report.mismatches} == {"COMPONENT_FOOTPRINT_MISMATCH"}


def test_single_suffix_ref_without_multi_unit_evidence_is_not_aliased() -> None:
    source = CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1", symbol="Device:R", value="10k")],
        nets=[NetIR(name="N", pins=[PinRefIR(ref="R1", pin="1")])],
    )
    generated = CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1A", symbol="Device:R", value="10k")],
        nets=[NetIR(name="N", pins=[PinRefIR(ref="R1A", pin="1")])],
    )

    report = compare_circuit_ir_equivalence(source, generated)

    assert report.status == "failed"
    assert "COMPONENT_REFS_MISMATCH" in {m.code for m in report.mismatches}


def test_explicit_no_connect_maps_to_exact_terminal(tmp_path: Path) -> None:
    schematic = tmp_path / "nc.kicad_sch"
    schematic.write_text(_simple_schematic(no_connect=(10.0, 20.0)), encoding="utf-8")

    snapshot = extract_schematic_semantics(schematic)

    assert [(item.ref, item.pin, item.unit) for item in snapshot.no_connect_terminals] == [
        ("R1", "1", "1")
    ]


def test_rotated_explicit_no_connect_uses_kicad_coordinate_transform(tmp_path: Path) -> None:
    schematic = tmp_path / "nc_rotated.kicad_sch"
    schematic.write_text(
        _simple_schematic(rotation=90, no_connect=(10.0, 12.38)),
        encoding="utf-8",
    )

    snapshot = extract_schematic_semantics(schematic)

    assert [(item.ref, item.pin, item.unit) for item in snapshot.no_connect_terminals] == [
        ("R1", "2", "1")
    ]


def test_resolved_library_geometry_can_match_flattened_embedded_symbol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schematic = tmp_path / "resolved_nc.kicad_sch"
    schematic.write_text(_simple_schematic(no_connect=(15.08, 20.0)), encoding="utf-8")
    fake_dir = tmp_path / "symbols"
    fake_dir.mkdir()

    monkeypatch.setattr(
        schematic_semantics,
        "resolve_symbol_dirs",
        lambda: SimpleNamespace(dirs=(fake_dir,)),
    )
    monkeypatch.setattr(
        schematic_semantics,
        "read_lib_symbol_unit_pin_at",
        lambda _lib, _symbol, *, symbols_dir: {
            "1": {"1": (5.08, 0.0, 180.0), "2": (12.7, 0.0, 180.0)}
        },
    )

    snapshot = extract_schematic_semantics(schematic)

    assert [(item.ref, item.pin, item.unit) for item in snapshot.no_connect_terminals] == [
        ("R1", "1", "1")
    ]


def test_unresolvable_no_connect_fails_closed(tmp_path: Path) -> None:
    schematic = tmp_path / "bad_nc.kicad_sch"
    schematic.write_text(_simple_schematic(no_connect=(99.0, 99.0)), encoding="utf-8")

    with pytest.raises(UserError, match="Cannot unambiguously map"):
        extract_schematic_semantics(schematic)


class _WritingAdapter:
    detected_version = KiCadVersion(9, 0, 0)

    def __init__(self, xml: str) -> None:
        self.xml = xml

    def export_netlist(self, _schematic: Path, output: Path):
        output.write_text(self.xml, encoding="utf-8")
        return _RunResult(), self.xml


class _RunResult:
    ok = True
    returncode = 0
    stdout = ""
    stderr = ""


def test_actual_artifact_verifier_checks_accepted_footprint(tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    candidate = tmp_path / "candidate.kicad_sch"
    text = _simple_schematic(footprint="Resistor_SMD:R_0603_1608Metric")
    accepted.write_text(text, encoding="utf-8")
    candidate.write_text(text, encoding="utf-8")
    authoritative = CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1", symbol="Device:R", value="10k")],
        nets=[NetIR(name="SIGNAL", pins=[PinRefIR(ref="R1", pin="1")])],
    )
    baseline = build_schematic_electrical_baseline(authoritative, accepted)
    xml = """<export>
<design><source>candidate.kicad_sch</source></design>
<components>
  <comp ref="R1">
    <value>10k</value>
    <footprint>Resistor_SMD:R_0603_1608Metric</footprint>
    <libsource lib="Device" part="R"/>
  </comp>
</components>
<nets><net code="1" name="SIGNAL"><node ref="R1" pin="1"/></net></nets>
</export>"""

    report = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=baseline,
        candidate_schematic=candidate,
        adapter=_WritingAdapter(xml),  # type: ignore[arg-type]
        work_dir=tmp_path,
    )

    assert report.passed
    assert report.mismatches == ()


def _simple_schematic(
    *,
    rotation: int = 0,
    no_connect: tuple[float, float] | None = None,
    footprint: str = "",
) -> str:
    nc = (
        f'  (no_connect (at {no_connect[0]} {no_connect[1]}) (uuid "nc-1"))\n'
        if no_connect is not None
        else ""
    )
    return f"""(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "root")
  (lib_symbols
    (symbol "Device:R"
      (pin passive line
        (at 0 0 0)
        (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin passive line
        (at 7.62 0 180)
        (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27)))))))
  (symbol
    (lib_id "Device:R")
    (at 10 20 {rotation})
    (unit 1)
    (in_bom yes)
    (on_board yes)
    (uuid "r1")
    (property "Reference" "R1")
    (property "Value" "10k")
    (property "Footprint" "{footprint}"))
{nc}  (sheet_instances (path "/" (page "1")))
)
"""


def test_readability_fixture_explicit_no_connects_resolve_to_jack_ring_pins() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    snapshot = extract_schematic_semantics(fixture)

    assert {(item.ref, item.pin, item.unit) for item in snapshot.no_connect_terminals} == {
        ("J1", "R", "1"),
        ("J2", "R", "1"),
    }
