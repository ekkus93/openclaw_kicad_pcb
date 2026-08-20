from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb_web.settings import LlmSettings, WebSettings


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "smoke_n3_first_fixture.py"
    spec = importlib.util.spec_from_file_location("smoke_n3_first_fixture", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _context(module, tmp_path: Path):
    return module.SmokeContext(
        settings=WebSettings(
            data_dir=tmp_path / "data",
            jobs_dir=tmp_path / "jobs",
            llm=LlmSettings(
                provider="openai",
                model="vision-model",
                vision_enabled=True,
            ),
        ),
        config=module.RefinementFeatureConfig(enabled=True),
        adapter=object(),
        llm_client=object(),
    )


def test_n3_first_fixture_smoke_runs_real_analyze_path_and_cleans_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    baseline = tmp_path / "baseline.kicad_sch"
    baseline.write_text("(kicad_sch smoke)", encoding="utf-8")
    prepared = SimpleNamespace(
        fixture=SimpleNamespace(fixture_id="n1-crowded-power-regulator"),
        authoritative_ir=object(),
        baseline_schematic=baseline,
    )
    captured: dict[str, object] = {}

    def prepare(request):
        captured["request"] = request
        return SimpleNamespace(fixtures=(prepared,))

    def analyze(*, accepted_path: Path, runtime, max_critic_repairs: int):
        captured["runtime"] = runtime
        captured["max_critic_repairs"] = max_critic_repairs
        assert accepted_path.read_text(encoding="utf-8") == "(kicad_sch smoke)"
        return SimpleNamespace(
            accepted_hash="a" * 64,
            context=SimpleNamespace(render_png_hash="b" * 64),
            critic=SimpleNamespace(issues=(object(), object())),
        )

    monkeypatch.setattr(module, "prepare_refinement_evaluation_corpus", prepare)
    monkeypatch.setattr(module, "analyze_schematic_refinement", analyze)
    monkeypatch.setattr(
        module,
        "get_llm_provider_capabilities",
        lambda _provider: SimpleNamespace(supports_image_input=True),
    )
    context = _context(module, tmp_path)

    result = module.run_smoke(
        repo_root=tmp_path,
        manifest_path=Path("manifest.json"),
        expectations_path=Path("expectations.json"),
        work_root=tmp_path / "work",
        implementation_sha="c" * 40,
        context=context,
    )

    request = captured["request"]
    assert request.fixture_ids == ("n1-crowded-power-regulator",)
    assert request.provenance.implementation_sha == "c" * 40
    assert request.iteration_limits.max_critic_repairs == 0
    assert request.loop_limits.max_rounds == 3
    runtime = captured["runtime"]
    assert runtime.authoritative_ir is prepared.authoritative_ir
    assert runtime.adapter is context.adapter
    assert runtime.llm_client is context.llm_client
    assert captured["max_critic_repairs"] == 0
    assert result == {
        "status": "smoke_passed",
        "fixture_id": "n1-crowded-power-regulator",
        "provider": "openai",
        "model": "vision-model",
        "accepted_hash": "a" * 64,
        "render_png_hash": "b" * 64,
        "issue_count": 2,
    }
    assert list((tmp_path / "work").iterdir()) == []


def test_n3_first_fixture_smoke_rejects_missing_vision_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "get_llm_provider_capabilities",
        lambda _provider: SimpleNamespace(supports_image_input=False),
    )

    with pytest.raises(Exception) as exc_info:
        module.run_smoke(
            repo_root=tmp_path,
            manifest_path=Path("manifest.json"),
            expectations_path=None,
            work_root=tmp_path / "work",
            implementation_sha="c" * 40,
            context=_context(module, tmp_path),
        )

    assert getattr(exc_info.value, "code", None) == "REFINEMENT_EVALUATION_VISION_REQUIRED"
