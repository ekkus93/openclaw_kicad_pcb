from pathlib import Path

path = Path("src/kicad_pcb_web/services/wizard.py")
text = path.read_text(encoding="utf-8")

old_approval = '''        if session.unsupported_reasons:
            raise ConflictError(
                "Cannot approve a spec that the wizard marked as unsupported.",
                details={"session_id": session_id},
            )
        if session.status != "spec_ready_for_review":
'''
new_approval = '''        if session.open_questions or session.spec.open_questions:
            raise ConflictError(
                "Cannot approve a spec while open questions remain unresolved.",
                code="WIZARD_SPEC_HAS_OPEN_QUESTIONS",
                details={"session_id": session_id},
            )
        if session.unsupported_reasons or session.spec.unsupported_reasons:
            raise ConflictError(
                "Cannot approve a spec that the wizard marked as unsupported.",
                code="WIZARD_SPEC_UNSUPPORTED",
                details={"session_id": session_id},
            )
        underspecified_custom_blocks = [
            block.name
            for block in session.spec.blocks
            if block.block_type == "custom" and not block.required_components
        ]
        if underspecified_custom_blocks:
            raise ConflictError(
                "Cannot approve a spec with underspecified custom blocks.",
                code="WIZARD_SPEC_UNDERSPECIFIED_CUSTOM_BLOCKS",
                details={
                    "session_id": session_id,
                    "block_names": underspecified_custom_blocks,
                },
            )
        if session.status != "spec_ready_for_review":
'''
if old_approval not in text:
    raise SystemExit("Expected approval gate block not found")
text = text.replace(old_approval, new_approval, 1)

old_project = '''        if (
            (not active_status and not retry_failed_project)
            or session.ir_json is None
            or session.ir_validation is None
            or not session.ir_validation.valid
        ):
'''
new_project = '''        if (
            (not active_status and not retry_failed_project)
            or not session.spec_approved
            or session.ir_json is None
            or session.ir_validation is None
            or not session.ir_validation.valid
        ):
'''
if old_project not in text:
    raise SystemExit("Expected project generation gate block not found")
text = text.replace(old_project, new_project, 1)
path.write_text(text, encoding="utf-8")
