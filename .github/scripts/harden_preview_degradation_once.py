from pathlib import Path

netlists_path = Path("src/kicad_pcb_web/services/netlists.py")
text = netlists_path.read_text(encoding="utf-8")

replacements = [
    (
        'LOGGER = logging.getLogger(__name__)\n\n\n@dataclass(frozen=True)\n',
        'LOGGER = logging.getLogger(__name__)\n\n\nclass PreviewGenerationError(RuntimeError):\n'
        '    """Optional preview export/conversion failure safe for degraded-mode reporting."""\n\n\n'
        '@dataclass(frozen=True)\n',
    ),
    (
        '''    if not schematic_path.is_file():
        raise RuntimeError(f"Schematic file not found: {schematic_path}")
''',
        '''    if not schematic_path.is_file():
        raise PersistenceError(
            "Generated schematic is missing before preview generation.",
            code="GENERATED_SCHEMATIC_MISSING",
        )
''',
    ),
    (
        '''            stderr = getattr(result, "stderr", b"") or b""
            raise RuntimeError(
                f"kicad-cli SVG export failed (exit {result.returncode}): "
                f"{stderr.decode(errors='replace').strip() or '(no output)'}"
            )
''',
        '''            stderr = getattr(result, "stderr", b"") or b""
            LOGGER.warning(
                "kicad-cli preview SVG export failed",
                extra={"returncode": result.returncode, "stderr": stderr.decode(errors="replace")},
            )
            raise PreviewGenerationError(
                f"kicad-cli SVG preview export failed with exit {result.returncode}."
            )
''',
    ),
    (
        '''        if not svgs:
            raise RuntimeError(
                f"kicad-cli reported success but produced no SVG file. Expected an SVG in {svg_dir}"
            )
''',
        '''        if not svgs:
            raise PreviewGenerationError(
                "kicad-cli reported success but produced no SVG preview file."
            )
''',
    ),
    (
        '''        if conv.returncode != 0:
            raise RuntimeError(
                f"rsvg-convert PNG conversion failed (exit {conv.returncode}): "
                f"{conv.stderr.decode(errors='replace').strip() or '(no output)'}"
            )
''',
        '''        if conv.returncode != 0:
            LOGGER.warning(
                "rsvg-convert preview PNG conversion failed",
                extra={
                    "returncode": conv.returncode,
                    "stderr": conv.stderr.decode(errors="replace"),
                },
            )
            raise PreviewGenerationError(
                f"rsvg-convert PNG preview conversion failed with exit {conv.returncode}."
            )
''',
    ),
    (
        '        except RuntimeError as exc:\n            LOGGER.warning("Schematic preview generation skipped (non-fatal): %s", exc)\n',
        '        except PreviewGenerationError as exc:\n'
        '            LOGGER.warning("Schematic preview generation skipped (non-fatal): %s", exc)\n',
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"Expected netlists block not found: {old[:100]!r}")
    text = text.replace(old, new, 1)

for old in (
    'raise RuntimeError(\n            "kicad-cli is not installed or not on PATH. "',
    'raise RuntimeError(\n            "rsvg-convert is not installed or not on PATH. "',
    'raise RuntimeError(\n                f"rsvg-convert reported success but {png_path.name} was not created."',
):
    if old not in text:
        raise SystemExit(f"Expected preview RuntimeError not found: {old!r}")
    text = text.replace(old, old.replace("RuntimeError", "PreviewGenerationError"), 1)

netlists_path.write_text(text, encoding="utf-8")

test_path = Path("tests/unit/test_preview_nonfatal.py")
test_text = test_path.read_text(encoding="utf-8")

old_import = '''from kicad_pcb_web.services.netlists import (
    _generate_schematic_preview,
    generate_project_from_netlist_job,
)
'''
new_import = '''from kicad_pcb_web.services.netlists import (
    PreviewGenerationError,
    _generate_schematic_preview,
    generate_project_from_netlist_job,
)
'''
if old_import not in test_text:
    raise SystemExit("Expected preview test import block not found")
test_text = test_text.replace(old_import, new_import, 1)

old_mock = '            side_effect=RuntimeError("preview dependency missing"),\n'
new_mock = '            side_effect=PreviewGenerationError("preview dependency missing"),\n'
if old_mock not in test_text:
    raise SystemExit("Expected preview degradation mock not found")
test_text = test_text.replace(old_mock, new_mock, 1)

marker = '\n\ndef test_non_preview_generation_failure_still_fails_job(tmp_path: Path) -> None:\n'
if marker not in test_text:
    raise SystemExit("Expected preview test insertion marker not found")
new_test = '''

def test_missing_generated_schematic_is_fatal_not_preview_degradation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    symbol_index = SimpleNamespace(directories=())
    ir = SimpleNamespace(components=[object()], nets=[object()])

    def fake_apply(project, _request) -> ApplyNetlistResult:
        managed = project.path / "OpenClaw_Managed.kicad_sch"
        managed.write_text("(kicad_sch)", encoding="utf-8")
        missing = project.path / "missing-primary.kicad_sch"
        return ApplyNetlistResult(
            schematic_path=missing,
            managed_schematic_path=managed,
            symbols_added=1,
            symbols_updated=0,
            managed_items_written=1,
            nets_applied=1,
            kicad_cli_used=False,
            heuristic_profile_name="generic_digital",
            label_mode_name="auto",
        )

    with (
        patch(
            "kicad_pcb_web.services.netlists._validate_with_optional_autofix",
            return_value=(tmp_path / "effective.json", symbol_index, ir, []),
        ),
        patch(
            "kicad_pcb_web.services.netlists._apply_netlist_to_project",
            side_effect=fake_apply,
        ),
        patch("kicad_pcb_web.services.netlists.shutil.which", return_value="/usr/bin/tool"),
    ):
        job = generate_project_from_netlist_job(
            settings=settings,
            request=CreateJobFromNetlistRequest(
                project_name="MissingPrimarySchematic",
                netlist_json=_VALID_NETLIST,
                validation="internal",
            ),
        )

    assert job.status == "failed"
    assert "project.zip" not in job.artifacts
    assert job.error is not None
    assert job.error["code"] == "INTERNAL_SERVER_ERROR"

'''
test_path.write_text(test_text.replace(marker, new_test + marker, 1), encoding="utf-8")
