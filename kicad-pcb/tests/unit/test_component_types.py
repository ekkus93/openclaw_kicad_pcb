"""Unit tests for kicad_pcb.component_types — Rule 5 additions.

Covers:
  - R5-1: GND_ALIASES constant
  - R5-2: normalize_gnd_net_name()
  - R5-5: POWER_NET_PREFIXES includes "0V"
"""

from __future__ import annotations

import pytest
from kicad_pcb.component_types import (
    GND_ALIASES,
    POWER_NET_PREFIXES,
    normalize_gnd_net_name,
)


class TestGndAliasesConstant:
    def test_contains_canonical_gnd(self) -> None:
        assert "GND" in GND_ALIASES

    def test_contains_zero_volt(self) -> None:
        assert "0V" in GND_ALIASES

    def test_contains_ground(self) -> None:
        assert "GROUND" in GND_ALIASES

    def test_all_uppercase(self) -> None:
        """All entries must be uppercase — matching is done with .upper() lookups."""
        for alias in GND_ALIASES:
            assert alias == alias.upper(), f"Alias {alias!r} is not uppercase"


class TestNormalizeGndNetName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("GND", "GND"),
            ("gnd", "GND"),
            ("0V", "GND"),
            ("0v", "GND"),
            ("0V0", "GND"),
            ("GROUND", "GND"),
            ("ground", "GND"),
            ("EARTH", "GND"),
            ("AGND", "GND"),
            ("PGND", "GND"),
            ("DGND", "GND"),
            ("SGND", "GND"),
            ("VSS", "GND"),
            ("vss", "GND"),
        ],
    )
    def test_known_aliases_become_gnd(self, raw: str, expected: str) -> None:
        assert normalize_gnd_net_name(raw) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "net_audio_in",
            "VCC",
            "VDD",
            "+12V",
            "signal_out",
            "FEEDBACK",
        ],
    )
    def test_non_alias_names_unchanged(self, name: str) -> None:
        assert normalize_gnd_net_name(name) == name

    def test_strips_whitespace_before_matching(self) -> None:
        assert normalize_gnd_net_name("  gnd  ") == "GND"
        assert normalize_gnd_net_name("\t0V\n") == "GND"

    def test_empty_after_strip_is_unchanged(self) -> None:
        # Edge case: a name that is only whitespace — strip returns "",
        # which is not in GND_ALIASES, so the original string is returned.
        raw = "   "
        assert normalize_gnd_net_name(raw) == raw

    def test_idempotent(self) -> None:
        assert normalize_gnd_net_name(normalize_gnd_net_name("0V")) == "GND"
        assert normalize_gnd_net_name(normalize_gnd_net_name("GND")) == "GND"


class TestPowerNetPrefixesHasZeroVolt:
    def test_zero_volt_present(self) -> None:
        """POWER_NET_PREFIXES must include '0V' for startswith-based power detection."""
        assert "0V" in POWER_NET_PREFIXES, (
            f"'0V' missing from POWER_NET_PREFIXES: {POWER_NET_PREFIXES!r}"
        )
