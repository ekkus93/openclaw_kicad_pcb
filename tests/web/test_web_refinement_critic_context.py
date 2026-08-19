from __future__ import annotations

from kicad_pcb.refinement.vision_context import (
    VisionComponentObject,
    VisionJunctionObject,
    VisionLabelObject,
    VisionNetObject,
    VisionObjectMap,
    VisionPinObject,
    VisionReviewRegion,
    VisionWireObject,
)
from kicad_pcb_web.services.refinement_llm import _critic_context_payload


def _context() -> VisionObjectMap:
    return VisionObjectMap(
        schema_version="1.0",
        source_schematic_hash="a" * 64,
        render_png_hash="b" * 64,
        sheet_id="1",
        page_mm=(420.0, 297.0),
        image_px=(4200, 2970),
        components=(
            VisionComponentObject(
                "component:component-uuid",
                "component-uuid",
                "U1",
                "1",
                "Regulator_Linear:LM7805_TO220",
                "LM7805",
                100.0,
                80.0,
                0,
                1000.0,
                800.0,
            ),
        ),
        pins=(
            VisionPinObject(
                "pin:U1:1:1",
                "U1",
                "1",
                "1",
                ((95.0, 80.0),),
                ((950.0, 800.0),),
            ),
        ),
        wires=(
            VisionWireObject(
                "wire:wire-uuid",
                "wire-uuid",
                ((95.0, 80.0), (80.0, 80.0)),
                ((950.0, 800.0), (800.0, 800.0)),
            ),
        ),
        labels=(
            VisionLabelObject(
                "label:label-uuid",
                "label-uuid",
                "label",
                "VIN",
                80.0,
                80.0,
                800.0,
                800.0,
            ),
        ),
        junctions=(
            VisionJunctionObject(
                "junction:junction-uuid",
                "junction-uuid",
                80.0,
                80.0,
                800.0,
                800.0,
            ),
        ),
        nets=(VisionNetObject("net:VIN", "VIN", ("pin:U1:1:1",)),),
        deterministic_metrics={"component_overlap_count": 0},
        review_regions=(
            VisionReviewRegion(
                "r00-c00",
                0,
                0,
                0,
                "c" * 64,
                (0.0, 0.0, 420.0, 297.0),
                (1260, 891),
                (3.0, 3.0),
            ),
        ),
    )


def test_critic_context_projection_keeps_binding_and_mm_geometry_only() -> None:
    context = _context()

    payload = _critic_context_payload(context)

    assert payload["source_schematic_hash"] == context.source_schematic_hash
    assert payload["render_png_hash"] == context.render_png_hash
    assert payload["deterministic_metrics"] == context.deterministic_metrics

    region = payload["review_regions"][0]
    assert region["view_box_mm"] == (0.0, 0.0, 420.0, 297.0)
    assert region["image_px"] == (1260, 891)
    assert region["pixels_per_mm"] == (3.0, 3.0)
    assert "png_hash" not in region
    assert "row" not in region
    assert "column" not in region

    component = payload["components"][0]
    assert component["object_id"] == "component:component-uuid"
    assert component["ref"] == "U1"
    assert component["unit"] == "1"
    assert (component["x_mm"], component["y_mm"]) == (100.0, 80.0)
    assert "uuid" not in component
    assert "symbol_id" not in component
    assert "value" not in component
    assert "x_px" not in component
    assert "y_px" not in component

    pin = payload["pins"][0]
    assert pin["object_id"] == "pin:U1:1:1"
    assert pin["positions_mm"] == ((95.0, 80.0),)
    assert "ref" not in pin
    assert "unit" not in pin
    assert "pin" not in pin
    assert "positions_px" not in pin

    wire = payload["wires"][0]
    assert wire["object_id"] == "wire:wire-uuid"
    assert wire["points_mm"] == ((95.0, 80.0), (80.0, 80.0))
    assert "uuid" not in wire
    assert "points_px" not in wire

    label = payload["labels"][0]
    junction = payload["junctions"][0]
    assert label["object_id"] == "label:label-uuid"
    assert junction["object_id"] == "junction:junction-uuid"
    assert "uuid" not in label
    assert "x_px" not in label
    assert "uuid" not in junction
    assert "x_px" not in junction

    net = payload["nets"][0]
    assert net == {"object_id": "net:VIN", "name": "VIN"}
