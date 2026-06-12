from __future__ import annotations

import math
from unittest.mock import patch

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout import snap as _gv_snap_mod
from kicad_pcb.graphviz_layout.snap import _deoverlap_positions

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_Policy_Snap:
    def test_apply_post_layout_snaps_respects_disabled_layout_policy(self) -> None:
        """The post-snap coordinator should honor the layout policy seam."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
            ],
            nets=[
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")],
                ),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (101.6, 101.6, None),
            "CDEC": (68.58, 149.86, None),
        }

        disabled_policy = _gv_mod.LayoutHeuristicPolicy(
            enable_decoupling_snap=False,
            enable_opamp_locality=False,
            enable_input_stage_cohesion=False,
            enable_output_stage_cohesion=False,
        )
        enabled = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
        )
        disabled = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            heuristic_policy=disabled_policy,
        )

        assert math.isclose(enabled["CDEC"][0], enabled["U1"][0], abs_tol=0.01)
        assert math.isclose(
            enabled["CDEC"][1],
            enabled["U1"][1] - _gv_mod.GRID_ROW_MM,
            abs_tol=0.01,
        )
        assert math.isclose(disabled["CDEC"][0], positions["CDEC"][0], abs_tol=0.01)
        assert not math.isclose(disabled["CDEC"][0], disabled["U1"][0], abs_tol=0.01)
        assert not math.isclose(
            disabled["CDEC"][1],
            disabled["U1"][1] - _gv_mod.GRID_ROW_MM,
            abs_tol=0.01,
        )

    def test_apply_post_layout_snaps_deoverlaps_collisions_from_late_passes(self) -> None:
        """The coordinator should resolve collisions reintroduced after the late passes."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="SIG",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.80, 76.20, None),
            "R2": (63.50, 88.90, None),
        }

        def _late_collision(
            snapshot: dict[str, tuple[float, float, float | None]],
            *_args: object,
            **_kwargs: object,
        ) -> dict[str, tuple[float, float, float | None]]:
            result = dict(snapshot)
            result["R1"] = (50.80, 76.20, None)
            result["R2"] = (50.80, 76.20, None)
            return result

        with patch.object(_gv_snap_mod, "_snap_input_connector_signal_attachment", _late_collision):
            result = _gv_mod.apply_post_layout_snaps(
                positions,
                ir,
                feedback_refs=set(),
                annotations={},
                channels={ref: "mono" for ref in positions},
                decoupling_map={},
            )

        assert result["R1"] != result["R2"]
        assert math.isclose(result["R1"][0], result["R2"][0], abs_tol=0.01)
        assert result["R2"][1] - result["R1"][1] >= 11.42

    def test_deoverlap_positions_separates_exact_overlap_even_for_skip_pair(self) -> None:
        """Skip pairs should not preserve a literal same-cell collision."""
        result = _deoverlap_positions(
            {
                "U1": (91.44, 129.54, None),
                "U2": (91.44, 129.54, None),
            },
            skip_pairs=frozenset({("U1", "U2")}),
        )

        assert result["U1"] != result["U2"]
        assert math.isclose(result["U1"][0], result["U2"][0], abs_tol=0.01)
        assert result["U2"][1] - result["U1"][1] >= 11.42
