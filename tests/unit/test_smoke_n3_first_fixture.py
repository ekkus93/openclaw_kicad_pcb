from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.errors import UserError
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


def _request(module, tmp_path: Path):
    return module.SmokeRequest(
        repo_root=tmp_path,
        manifest_path=Path("manifest.json"),
        expectations_path=Path("expectations.json"),
        work_root=tmp_path / "work",
        implementation_sha="c" * 40,
    )


def test_n3_first_fixture_smoke_runs_two_real_analyze_passes_and_cleans_work(
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
    captured: dict[str, object] = {"runtimes": [], "max_critic_repairs": []}

    def prepare(request):
        captured["request"] = request
        return SimpleNamespace(fixtures=(prepared,))

    def analyze(*, accepted_path: Path, runtime, max_critic_repairs: int):
        runtimes = captured["runtimes"]
        repairs = captured["max_critic_repairs"]
        assert isinstance(runtimes, list)
        assert isinstance(repairs, list)
        runtimes.append(runtime)
        repairs.append(max_critic_repairs)
        assert accepted_path.read_text(encoding="utf-8") == "(kicad_sch smoke)"
        pass_index = len(runtimes)
        return SimpleNamespace(
            accepted_hash="a" * 64,
            context=SimpleNamespace(render_png_hash="b" * 64),
            critic=SimpleNamespace(issues=tuple(object() for _ in range(pass_index + 1))),
        )

    monkeypatch.setattr(module, "prepare_refinement_evaluation_corpus", prepare)
    monkeypatch.setattr(module, "analyze_schematic_refinement", analyze)
    monkeypatch.setattr(
        module,
        "get_llm_provider_capabilities",
        lambda _provider: SimpleNamespace(supports_image_input=True),
    )
    context = _context(module, tmp_path)

    result = module.run_smoke(_request(module, tmp_path), context)

    request = captured["request"]
    assert request.fixture_ids == ("n1-crowded-power-regulator",)
    assert request.provenance.implementation_sha == "c" * 40
    assert request.iteration_limits.max_critic_repairs == 0
    assert request.loop_limits.max_rounds == 3
    runtimes = captured["runtimes"]
    assert isinstance(runtimes, list)
    assert len(runtimes) == 2
    assert all(runtime.authoritative_ir is prepared.authoritative_ir for runtime in runtimes)
    assert all(runtime.adapter is context.adapter for runtime in runtimes)
    assert all(runtime.llm_client is context.llm_client for runtime in runtimes)
    assert captured["max_critic_repairs"] == [0, 0]
    assert result == {
        "status": "smoke_passed",
        "fixture_id": "n1-crowded-power-regulator",
        "provider": "openai",
        "model": "vision-model",
        "pass_count": 2,
        "passes": [
            {
                "pass_index": 1,
                "accepted_hash": "a" * 64,
                "render_png_hash": "b" * 64,
                "issue_count": 2,
            },
            {
                "pass_index": 2,
                "accepted_hash": "a" * 64,
                "render_png_hash": "b" * 64,
                "issue_count": 3,
            },
        ],
    }
    assert list((tmp_path / "work").iterdir()) == []


def test_n3_first_fixture_smoke_requires_second_pass_to_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    baseline = tmp_path / "baseline.kicad_sch"
    baseline.write_text("(kicad_sch smoke)", encoding="utf-8")
    prepared = SimpleNamespace(
        authoritative_ir=object(),
        baseline_schematic=baseline,
    )
    calls = 0

    monkeypatch.setattr(
        module,
        "prepare_refinement_evaluation_corpus",
        lambda _request: SimpleNamespace(fixtures=(prepared,)),
    )
    monkeypatch.setattr(
        module,
        "get_llm_provider_capabilities",
        lambda _provider: SimpleNamespace(supports_image_input=True),
    )

    def analyze(*, accepted_path: Path, runtime, max_critic_repairs: int):
        del accepted_path, runtime, max_critic_repairs
        nonlocal calls
        calls += 1
        if calls == 2:
            raise UserError("second pass truncated", code="LLM_COMPLETION_TRUNCATED")
        return SimpleNamespace(
            accepted_hash="a" * 64,
            context=SimpleNamespace(render_png_hash="b" * 64),
            critic=SimpleNamespace(issues=()),
        )

    monkeypatch.setattr(module, "analyze_schematic_refinement", analyze)

    with pytest.raises(UserError) as exc_info:
        module.run_smoke(_request(module, tmp_path), _context(module, tmp_path))

    assert calls == 2
    assert exc_info.value.code == "LLM_COMPLETION_TRUNCATED"
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
        module.run_smoke(_request(module, tmp_path), _context(module, tmp_path))

    assert getattr(exc_info.value, "code", None) == "REFINEMENT_EVALUATION_VISION_REQUIRED"
