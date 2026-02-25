"""Unit tests for kicad_pcb.compat — version detection and capability gating."""
from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.adapters import FakeFs, FakeRunner, KicadCliAdapter, RunResult
from kicad_pcb.compat import (
    CAPABILITY_MAP,
    MINIMUM_VERSION,
    CliCapability,
    KiCadVersion,
    parse_version,
    require_capability,
)
from kicad_pcb.errors import ToolError

# ---------------------------------------------------------------------------
# KiCadVersion — comparisons and string representation
# ---------------------------------------------------------------------------


class TestKiCadVersion:
    def test_equal(self) -> None:
        assert KiCadVersion(9, 0, 7) == KiCadVersion(9, 0, 7)

    def test_major_ordering(self) -> None:
        assert KiCadVersion(9, 0, 0) > KiCadVersion(8, 0, 0)
        assert KiCadVersion(7, 0, 0) < KiCadVersion(8, 0, 0)

    def test_minor_ordering(self) -> None:
        assert KiCadVersion(8, 1, 0) > KiCadVersion(8, 0, 9)

    def test_patch_ordering(self) -> None:
        assert KiCadVersion(7, 0, 1) > KiCadVersion(7, 0, 0)
        assert KiCadVersion(7, 0, 0) < KiCadVersion(7, 0, 1)

    def test_ge_same(self) -> None:
        assert KiCadVersion(7, 0, 0) >= KiCadVersion(7, 0, 0)

    def test_le_same(self) -> None:
        assert KiCadVersion(7, 0, 0) <= KiCadVersion(7, 0, 0)

    def test_str(self) -> None:
        assert str(KiCadVersion(9, 0, 7)) == "9.0.7"
        assert str(KiCadVersion(7, 0, 0)) == "7.0.0"

    def test_hashable(self) -> None:
        s = {KiCadVersion(9, 0, 7), KiCadVersion(9, 0, 7)}
        assert len(s) == 1


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_bare_version_string(self) -> None:
        assert parse_version("9.0.7") == KiCadVersion(9, 0, 7)

    def test_minimum_version_parses(self) -> None:
        assert parse_version("7.0.0") == KiCadVersion(7, 0, 0)

    def test_version_embedded_in_banner(self) -> None:
        banner = "Application: kicad-cli\n9.0.7 release build"
        assert parse_version(banner) == KiCadVersion(9, 0, 7)

    def test_version_with_extra_text(self) -> None:
        assert parse_version("kicad-cli 8.0.3 (stable)") == KiCadVersion(8, 0, 3)

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract version"):
            parse_version("")

    def test_raises_on_no_version(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract version"):
            parse_version("no version here at all")

    def test_raises_on_partial_version(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract version"):
            parse_version("9.0")


# ---------------------------------------------------------------------------
# MINIMUM_VERSION constant
# ---------------------------------------------------------------------------


class TestMinimumVersion:
    def test_minimum_is_7(self) -> None:
        assert KiCadVersion(7, 0, 0) == MINIMUM_VERSION

    def test_current_kicad9_meets_minimum(self) -> None:
        assert KiCadVersion(9, 0, 7) >= MINIMUM_VERSION

    def test_kicad6_does_not_meet_minimum(self) -> None:
        assert KiCadVersion(6, 9, 9) < MINIMUM_VERSION


# ---------------------------------------------------------------------------
# CAPABILITY_MAP — coverage and sanity
# ---------------------------------------------------------------------------


class TestCapabilityMap:
    def test_all_capabilities_have_map_entry(self) -> None:
        """Every CliCapability member must appear in CAPABILITY_MAP."""
        missing = [c for c in CliCapability if c not in CAPABILITY_MAP]
        assert missing == [], f"Capabilities missing from CAPABILITY_MAP: {missing}"

    def test_all_min_versions_meet_minimum(self) -> None:
        """No capability should require a version older than MINIMUM_VERSION."""
        below = [
            (cap, ver)
            for cap, ver in CAPABILITY_MAP.items()
            if ver < MINIMUM_VERSION
        ]
        assert below == [], f"Capabilities below MINIMUM_VERSION: {below}"

    def test_step_no_unspecified_requires_8(self) -> None:
        assert CAPABILITY_MAP[CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED] == KiCadVersion(8, 0, 0)

    def test_glb_requires_8(self) -> None:
        assert CAPABILITY_MAP[CliCapability.PCB_EXPORT_GLB] == KiCadVersion(8, 0, 0)

    def test_drc_json_requires_7(self) -> None:
        assert CAPABILITY_MAP[CliCapability.DRC_JSON_REPORT] == KiCadVersion(7, 0, 0)


# ---------------------------------------------------------------------------
# require_capability
# ---------------------------------------------------------------------------


class TestRequireCapability:
    def test_none_version_always_passes(self) -> None:
        """Unknown version → no error (benefit of the doubt)."""
        require_capability(None, CliCapability.DRC_JSON_REPORT)
        require_capability(None, CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)

    def test_sufficient_version_passes(self) -> None:
        require_capability(KiCadVersion(9, 0, 7), CliCapability.DRC_JSON_REPORT)

    def test_exact_minimum_passes(self) -> None:
        require_capability(KiCadVersion(7, 0, 0), CliCapability.DRC_JSON_REPORT)

    def test_version_below_minimum_raises(self) -> None:
        with pytest.raises(ToolError, match="6.0.0") as exc_info:
            require_capability(KiCadVersion(6, 0, 0), CliCapability.DRC_JSON_REPORT)
        assert "7.0.0" in str(exc_info.value)  # minimum version in message
        assert "pcb drc --format json" in str(exc_info.value)

    def test_kicad7_blocks_step_no_unspecified(self) -> None:
        """KiCad 7 does not support --no-unspecified; must raise ToolError."""
        with pytest.raises(ToolError, match="8.0.0"):
            require_capability(
                KiCadVersion(7, 0, 0), CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED
            )

    def test_kicad8_allows_step_no_unspecified(self) -> None:
        require_capability(KiCadVersion(8, 0, 0), CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)

    def test_kicad9_allows_step_no_unspecified(self) -> None:
        require_capability(KiCadVersion(9, 0, 0), CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)

    def test_error_message_includes_download_link(self) -> None:
        with pytest.raises(ToolError, match="kicad.org/download"):
            require_capability(KiCadVersion(6, 0, 0), CliCapability.ERC_JSON_REPORT)


# ---------------------------------------------------------------------------
# KicadCliAdapter.detected_version — lazy detection
# ---------------------------------------------------------------------------


class TestKicadCliAdapterDetectedVersion:
    def test_injected_version_returned_directly(self) -> None:
        """Version injected at construction time is returned without a CLI call."""
        runner = FakeRunner({})
        adapter = KicadCliAdapter(runner=runner, version=KiCadVersion(9, 0, 7))
        assert adapter.detected_version == KiCadVersion(9, 0, 7)
        assert runner.calls == []  # no --version call made

    def test_lazy_detection_parses_stdout(self) -> None:
        """When no version injected, adapter calls kicad-cli --version lazily."""
        runner = FakeRunner({"--version": RunResult(0, "9.0.7", "")})
        adapter = KicadCliAdapter(runner=runner)
        assert adapter.detected_version == KiCadVersion(9, 0, 7)

    def test_lazy_detection_parses_stderr(self) -> None:
        """Version in stderr (some kicad-cli builds) is also accepted."""
        runner = FakeRunner({"--version": RunResult(0, "", "9.0.7")})
        adapter = KicadCliAdapter(runner=runner)
        assert adapter.detected_version == KiCadVersion(9, 0, 7)

    def test_empty_version_output_yields_none(self) -> None:
        """FakeRunner default (empty stdout+stderr) → detected_version is None."""
        runner = FakeRunner({})
        adapter = KicadCliAdapter(runner=runner)
        assert adapter.detected_version is None

    def test_unrecognised_version_string_yields_none(self) -> None:
        runner = FakeRunner({"--version": RunResult(0, "not-a-version", "")})
        adapter = KicadCliAdapter(runner=runner)
        assert adapter.detected_version is None

    def test_version_detection_is_cached(self) -> None:
        """Second access to detected_version must not re-run the CLI."""
        runner = FakeRunner({"--version": RunResult(0, "9.0.7", "")})
        adapter = KicadCliAdapter(runner=runner)
        _ = adapter.detected_version
        _ = adapter.detected_version
        version_calls = [c for c in runner.calls if "--version" in c]
        assert len(version_calls) == 1


# ---------------------------------------------------------------------------
# KicadCliAdapter.require_capability
# ---------------------------------------------------------------------------


class TestKicadCliAdapterRequireCapability:
    def test_sufficient_version_no_error(self) -> None:
        adapter = KicadCliAdapter(runner=FakeRunner({}), version=KiCadVersion(9, 0, 7))
        adapter.require_capability(CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)

    def test_insufficient_version_raises(self) -> None:
        adapter = KicadCliAdapter(runner=FakeRunner({}), version=KiCadVersion(7, 0, 0))
        with pytest.raises(ToolError, match="8.0.0"):
            adapter.require_capability(CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)

    def test_unknown_version_no_error(self) -> None:
        """When version cannot be detected, capability checks must not block."""
        adapter = KicadCliAdapter(runner=FakeRunner({}))  # returns empty string
        adapter.require_capability(CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)

    def test_export_step_gates_on_version(self) -> None:
        """export_step must raise ToolError for kicad-cli < 8.0."""
        runner = FakeRunner({"pcb export step": RunResult(0, "", "")})
        fs = FakeFs(files={"/tmp/out.step": "binary"})
        adapter = KicadCliAdapter(
            runner=runner, fs=fs, version=KiCadVersion(7, 0, 0)
        )
        with pytest.raises(ToolError, match="--no-unspecified"):
            adapter.export_step(Path("/tmp/board.kicad_pcb"), Path("/tmp/out.step"))

    def test_export_glb_gates_on_version(self) -> None:
        """export_glb must raise ToolError for kicad-cli < 8.0."""
        runner = FakeRunner({"pcb export glb": RunResult(0, "", "")})
        adapter = KicadCliAdapter(runner=runner, version=KiCadVersion(7, 0, 0))
        with pytest.raises(ToolError, match="pcb export glb"):
            adapter.export_glb(Path("/tmp/board.kicad_pcb"), Path("/tmp/out.glb"))
