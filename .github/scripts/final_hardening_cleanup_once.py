from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Expected {label} block not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


wizard = Path("src/kicad_pcb_web/services/wizard.py")
replace_once(
    wizard,
    '''    if revision_provenance is not None:
        if revision_provenance.provider != current.provider:
            mismatches.append("provider")
        if revision_provenance.model != current.model:
            mismatches.append("model")
        if revision_provenance.prompt_version != current.prompt_version:
            mismatches.append("prompt_version")
        if (
            revision_provenance.endpoint_identity is not None
            and revision_provenance.endpoint_identity != current.endpoint_identity
        ):
            mismatches.append("endpoint_identity")
    else:
        if session.llm_provider != current.provider:
            mismatches.append("provider")
        if session.llm_model != current.model:
            mismatches.append("model")
        if session.prompt_version != current.prompt_version:
            mismatches.append("prompt_version")
''',
    '''    if revision_provenance is None:
        mismatches.append("revision_provenance")
    else:
        if revision_provenance.provider != current.provider:
            mismatches.append("provider")
        if revision_provenance.model != current.model:
            mismatches.append("model")
        if revision_provenance.prompt_version != current.prompt_version:
            mismatches.append("prompt_version")
        if revision_provenance.endpoint_identity != current.endpoint_identity:
            mismatches.append("endpoint_identity")
        if revision_provenance.config_revision != current.config_revision:
            mismatches.append("config_revision")
''',
    "strict provenance",
)

base = Path("src/kicad_pcb_web/services/llm/base.py")
replace_once(
    base,
    'from typing import Any, Literal, Protocol\n',
    'from typing import Any, Literal, Protocol\nfrom urllib.parse import urlsplit\n',
    "urlsplit import",
)
replace_once(
    base,
    'LOGGER = logging.getLogger("uvicorn.error")\n\n\n@dataclass(frozen=True)\n',
    '''LOGGER = logging.getLogger("uvicorn.error")


def _safe_base_url_for_log(value: str) -> str:
    """Return origin-only provider metadata without credentials, path, query, or fragment."""

    parsed = urlsplit(value)
    safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
    return f"{parsed.scheme}://{safe_netloc}"


@dataclass(frozen=True)
''',
    "safe log URL helper",
)
replace_once(
    base,
    '                    "base_url": self.base_url,\n',
    '                    "base_url": _safe_base_url_for_log(self.base_url),\n',
    "safe logged base URL",
)

netlists = Path("src/kicad_pcb_web/services/netlists.py")
replace_once(
    netlists,
    '''    Raises ``RuntimeError`` if any step fails — missing tools, failed SVG
    export, or failed PNG conversion.
''',
    '''    Raises ``PreviewGenerationError`` for optional preview-only failures. A missing
    primary schematic remains a fatal persistence error and is never degraded.
''',
    "preview exception contract docstring",
)

provenance_tests = Path("tests/web/test_web_wizard_provenance.py")
replace_once(
    provenance_tests,
    '''    prompt_version: str = "v1",
    base_url: str = "https://provider-a.example/v1",
) -> WebSettings:
''',
    '''    prompt_version: str = "v1",
    base_url: str = "https://provider-a.example/v1",
    temperature: float = 0.2,
) -> WebSettings:
''',
    "provenance settings signature",
)
replace_once(
    provenance_tests,
    '''            api_key="TOP-SECRET-PROVIDER-KEY",
            system_prompt_version=prompt_version,
''',
    '''            api_key="TOP-SECRET-PROVIDER-KEY",
            system_prompt_version=prompt_version,
            temperature=temperature,
''',
    "provenance temperature setting",
)
replace_once(
    provenance_tests,
    '''def test_legacy_session_remains_readable_but_missing_model_provenance_blocks_mutation(
    tmp_path: Path,
) -> None:
    original_settings = _settings(tmp_path)
    legacy = _session(original_settings, session_id="wiz_legacy_provenance").model_copy(
        update={"llm_model": None, "spec_provenance": None}
    )
    _persist_session(original_settings, legacy)

    restarted_settings = _settings(tmp_path, model="model-b")
    readable = read_wizard_session(restarted_settings, legacy.id)
    assert readable.spec is not None
    assert readable.spec.project_name == "Divider"

    with pytest.raises(ConflictError) as caught:
        post_wizard_message(
            settings=restarted_settings,
            session_id=legacy.id,
            request=WizardMessageRequest(message="Continue this session."),
            llm_client=FailIfCalledClient(),
        )

    assert caught.value.code == "WIZARD_LLM_PROVENANCE_MISMATCH"
    assert "model" in caught.value.details["mismatch_fields"]
''',
    '''def test_legacy_session_remains_readable_but_missing_revision_provenance_blocks_mutation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    legacy = _session(settings, session_id="wiz_legacy_provenance").model_copy(
        update={"spec_provenance": None}
    )
    _persist_session(settings, legacy)

    readable = read_wizard_session(settings, legacy.id)
    assert readable.spec is not None
    assert readable.spec.project_name == "Divider"

    with pytest.raises(ConflictError) as caught:
        post_wizard_message(
            settings=settings,
            session_id=legacy.id,
            request=WizardMessageRequest(message="Continue this session."),
            llm_client=FailIfCalledClient(),
        )

    assert caught.value.code == "WIZARD_LLM_PROVENANCE_MISMATCH"
    assert "revision_provenance" in caught.value.details["mismatch_fields"]


def test_spec_revision_rejects_changed_config_revision(tmp_path: Path) -> None:
    original_settings = _settings(tmp_path)
    original = _persist_session(original_settings, _session(original_settings))
    changed_settings = _settings(tmp_path, temperature=0.7)

    with pytest.raises(ConflictError) as caught:
        post_wizard_message(
            settings=changed_settings,
            session_id=original.id,
            request=WizardMessageRequest(message="Continue under changed sampling."),
            llm_client=FailIfCalledClient(),
        )

    assert caught.value.code == "WIZARD_LLM_PROVENANCE_MISMATCH"
    assert "config_revision" in caught.value.details["mismatch_fields"]
''',
    "legacy and config provenance regressions",
)

retry_tests = Path("tests/web/test_web_llm_retry_hardening.py")
replace_once(
    retry_tests,
    'from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client\n',
    'from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client\n'
    'from kicad_pcb_web.services.llm.base import _safe_base_url_for_log\n',
    "safe log helper test import",
)
text = retry_tests.read_text(encoding="utf-8")
append = '''

def test_provider_base_url_logging_strips_credentials_path_query_and_fragment() -> None:
    safe = _safe_base_url_for_log(
        "https://operator:TOP-SECRET@example.invalid:8443/v1/chat?api_key=SECRET#fragment"
    )

    assert safe == "https://example.invalid:8443"
    assert "operator" not in safe
    assert "TOP-SECRET" not in safe
    assert "api_key" not in safe
    assert "/v1" not in safe
'''
if "test_provider_base_url_logging_strips_credentials_path_query_and_fragment" in text:
    raise SystemExit("Safe base URL test already exists")
retry_tests.write_text(text + append, encoding="utf-8")
