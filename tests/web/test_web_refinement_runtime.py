from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb_web.services.refinement_runtime import build_refinement_runtime


def test_refinement_runtime_records_explicit_provider_model_and_implementation(
    tmp_path: Path,
) -> None:
    runtime = build_refinement_runtime(
        authoritative_ir=object(),  # type: ignore[arg-type]
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        work_dir=tmp_path / "work",
        evidence_root=tmp_path / "evidence",
        provider="openai-compatible",
        model="vision-model",
        product_version="0.1.0",
        implementation_sha="a" * 40,
    )

    assert runtime.provenance is not None
    assert runtime.provenance.provider == "openai-compatible"
    assert runtime.provenance.model == "vision-model"
    assert runtime.provenance.product_version == "0.1.0"
    assert runtime.provenance.implementation_sha == "a" * 40
    assert runtime.work_dir == tmp_path / "work"
    assert runtime.evidence_root == tmp_path / "evidence"


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("", "vision-model"),
        ("provider", ""),
        ("bad\nprovider", "vision-model"),
    ],
)
def test_refinement_runtime_rejects_invalid_provenance(
    tmp_path: Path,
    provider: str,
    model: str,
) -> None:
    with pytest.raises(ValueError):
        build_refinement_runtime(
            authoritative_ir=object(),  # type: ignore[arg-type]
            adapter=object(),  # type: ignore[arg-type]
            llm_client=object(),  # type: ignore[arg-type]
            work_dir=tmp_path / "work",
            evidence_root=tmp_path / "evidence",
            provider=provider,
            model=model,
        )


def test_refinement_runtime_rejects_malformed_implementation_sha(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="implementation_sha"):
        build_refinement_runtime(
            authoritative_ir=object(),  # type: ignore[arg-type]
            adapter=object(),  # type: ignore[arg-type]
            llm_client=object(),  # type: ignore[arg-type]
            work_dir=tmp_path / "work",
            evidence_root=tmp_path / "evidence",
            provider="provider",
            model="vision-model",
            implementation_sha="not-a-git-sha",
        )
