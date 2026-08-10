from pathlib import Path

README = Path("README.md")
HARDENING_TODO = Path("docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_TODO_2026-08-09.md")

readme = README.read_text(encoding="utf-8")
# Keep the closure edit strictly scoped: restore the pre-closure command example if a
# whole-file contents-API update accidentally changed it.
readme = readme.replace(
    "    --name MyProject \\\n    --out-dir . \\\n    --netlist circuit.json \\\n    --symbols-dir /path/to/symbols \\\n    --validate kicad      # default; requires kicad-cli\n  # --validate internal # internal syntax+lint only; no kicad-cli required\n",
    "    --name MyProject \\\n    --netlist circuit.json \\\n    --symbols-dir /path/to/symbols \\\n  --validate kicad      # default; requires kicad-cli\n  # --validate internal # internal syntax+lint only; no kicad-cli required\n",
)
if 'default_host = "127.0.0.1"' in readme or "default_port = 8000" in readme:
    raise SystemExit("stale README bind settings remain")
if "badge.svg?branch=webapp" not in readme or "query=branch%3Awebapp" not in readme:
    raise SystemExit("webapp-scoped CI badge/link missing")
README.write_text(readme, encoding="utf-8")

text = HARDENING_TODO.read_text(encoding="utf-8")
marker = "## Completion status\n"
if marker not in text:
    title = "# KiCad PCB Web App Post-Review Hardening TODO — 2026-08-09\n\n"
    if not text.startswith(title):
        raise SystemExit("unexpected hardening TODO title")
    banner = (
        "## Completion status\n\n"
        "**COMPLETED.** The authoritative implementation and validation disposition is recorded in "
        "[`KICAD_WEBAPP_POST_REVIEW_HARDENING_COMPLETION_2026-08-09.md`](KICAD_WEBAPP_POST_REVIEW_HARDENING_COMPLETION_2026-08-09.md).\n\n"
        "This checklist is preserved as planning history. Unchecked boxes are not evidence that the "
        "hardening implementation is incomplete: the document contains mutually exclusive alternatives, "
        "conditional/manual steps, and items explicitly dispositioned as `NOT PERFORMED`. Do not "
        "mechanically convert every planning checkbox to `[x]`; use the completion evidence document as "
        "the authoritative closure record.\n\n"
        "---\n\n"
    )
    text = title + banner + text[len(title):]
HARDENING_TODO.write_text(text, encoding="utf-8")
