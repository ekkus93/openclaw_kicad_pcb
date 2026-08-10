from __future__ import annotations

import re
from pathlib import Path

path = Path('.github/scripts/apply_wizard_llm_robustness.py')
text = path.read_text(encoding='utf-8')

old_replace = '''def replace_once(path: str, old: str, new: str) -> None:\n    text = read(path)\n    if text.count(old) != 1:\n        raise RuntimeError(\n            f"expected exactly one match in {path}: {old[:100]!r}; got {text.count(old)}"\n        )\n    write(path, text.replace(old, new, 1))\n\n\ndef regex_replace_once(path: str, pattern: str, new: str) -> None:\n    text = read(path)\n    updated, count = re.subn(pattern, new, text, count=1, flags=re.S)\n    if count != 1:\n        raise RuntimeError(f"expected exactly one regex match in {path}: {pattern!r}; got {count}")\n    write(path, updated)\n'''
new_replace = '''def _indent_nonblank(value: str, prefix: str) -> str:\n    return "\\n".join(prefix + line if line else line for line in value.split("\\n"))\n\n\ndef replace_once(path: str, old: str, new: str) -> None:\n    text = read(path)\n    if text.count(old) == 0:\n        indented_old = _indent_nonblank(old, "    ")\n        if text.count(indented_old) >= 1:\n            old = indented_old\n            new = _indent_nonblank(new, "    ")\n    count = text.count(old)\n    if (\n        count == 2\n        and path == "src/kicad_pcb_web/services/wizard.py"\n        and '"unsupported_reasons": output.unsupported_reasons' in old\n    ):\n        write(path, text.replace(old, new, 1))\n        return\n    if count != 1:\n        raise RuntimeError(\n            f"expected exactly one match in {path}: {old[:100]!r}; got {count}"\n        )\n    write(path, text.replace(old, new, 1))\n\n\ndef regex_replace_once(path: str, pattern: str, new: str) -> None:\n    text = read(path)\n    match = re.search(pattern, text, flags=re.S)\n    if match is None:\n        raise RuntimeError(f"expected exactly one regex match in {path}: {pattern!r}; got 0")\n    if re.search(pattern, text[match.end():], flags=re.S) is not None:\n        raise RuntimeError(f"expected exactly one regex match in {path}: {pattern!r}; got >1")\n    leading = re.match(r"[ \\t]*", match.group(0)).group(0)\n    if leading and new and not new.startswith(leading):\n        new = _indent_nonblank(new, leading)\n    write(path, text[:match.start()] + new + text[match.end():])\n'''
if text.count(old_replace) != 1:
    raise SystemExit('helper replacement functions no longer match expected source')
text = text.replace(old_replace, new_replace, 1)

# Preserve generated Python newline escapes.
old_line_1 = '                            f"{exc.repair_message} Return corrected JSON only.\\n"\n'
new_line_1 = '                            f"{exc.repair_message} Return corrected JSON only.\\\\n"\n'
old_line_2 = '                            f"JSON schema:\\n{schema_json}"\n'
new_line_2 = '                            f"JSON schema:\\\\n{schema_json}"\n'
if text.count(old_line_1) != 1 or text.count(old_line_2) != 1:
    raise SystemExit('structured-output newline template no longer matches expected helper source')
text = text.replace(old_line_1, new_line_1, 1).replace(old_line_2, new_line_2, 1)

# Preserve the existing semantic-repair prompt wording contract.
old_prompt = '                "The previous Circuit IR attempt failed. "\n'
new_prompt = '                "The previous Circuit IR attempt failed validation. "\n'
if text.count(old_prompt) != 1:
    raise SystemExit('IR repair prompt template no longer matches expected helper source')
text = text.replace(old_prompt, new_prompt, 1)

# Strengthen an existing web regression to the new typed structural error contract.
anchor = '# Focused unit regressions covering D1-D7.\n'
test_update = '''replace_once(\n    "tests/web/test_web_wizard.py",\n    '    assert payload["code"] == "LLM_PROVIDER_FAILED"\\n    session_id = payload["details"]["session_id"]\\n    persisted = client.get(f"/api/wizard/sessions/{session_id}")\\n    assert persisted.status_code == 200\\n    session = persisted.json()\\n    assert session["status"] == "failed"\\n    assert session["error"]["code"] == "LLM_PROVIDER_FAILED"\\n',\n    '    assert payload["code"] == "LLM_INVALID_STRUCTURED_OUTPUT"\\n    session_id = payload["details"]["session_id"]\\n    persisted = client.get(f"/api/wizard/sessions/{session_id}")\\n    assert persisted.status_code == 200\\n    session = persisted.json()\\n    assert session["status"] == "failed"\\n    assert session["failure_kind"] == "operational"\\n    assert session["error"]["code"] == "LLM_INVALID_STRUCTURED_OUTPUT"\\n',\n)\n\n'''
if text.count(anchor) != 1:
    raise SystemExit('focused regression anchor no longer matches expected helper source')
text = text.replace(anchor, test_update + anchor, 1)

# Keep the one-attempt primitive to five arguments; max_attempts was logging-only.
for needle in (
    '        max_attempts: int,\n',
    '                "max_attempts": max_attempts,\n',
):
    if text.count(needle) != 1:
        raise SystemExit(f'expected exactly one helper simplification match: {needle!r}')
    text = text.replace(needle, '', 1)
text, removed_calls = re.subn(r'(?m)^[ \t]+max_attempts=max_attempts,\n', '', text)
if removed_calls != 2:
    raise SystemExit(f'expected exactly two max_attempts call arguments, got {removed_calls}')

# Avoid reusing a loop-local int variable for an Optional dict.get result (mypy).
old_size = '''            size = sizes.get(path)\n            if size is None:\n                continue\n            if _unlink_debug_artifact(path):\n                total_bytes -= size\n'''
new_size = '''            size_to_remove = sizes.get(path)\n            if size_to_remove is None:\n                continue\n            if _unlink_debug_artifact(path):\n                total_bytes -= size_to_remove\n'''
if text.count(old_size) != 1:
    raise SystemExit('debug retention size template no longer matches expected helper source')
text = text.replace(old_size, new_size, 1)

# Lint/format only permanent Python source/tests while the temporary helper exists.
old_format = 'subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "format", "."], check=True)\n'
new_format = '''subprocess.run(\n    ["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "format", "src", "tests"],\n    check=True,\n)\nsubprocess.run(\n    [\n        "uv", "run", "--extra", "dev", "--extra", "web", "ruff", "check", "--fix",\n        "--select", "I", "src/kicad_pcb_web/services/wizard.py",\n    ],\n    check=True,\n)\n'''
old_check = 'subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "check", "."], check=True)\n'
new_check = 'subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "check", "src", "tests"], check=True)\n'
old_format_check = 'subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "format", "--check", "."], check=True)\n'
new_format_check = 'subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "format", "--check", "src", "tests"], check=True)\n'
for old, new in (
    (old_format, new_format),
    (old_check, new_check),
    (old_format_check, new_format_check),
):
    if text.count(old) != 1:
        raise SystemExit(f'permanent-source gate template no longer matches: {old!r}')
    text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')
