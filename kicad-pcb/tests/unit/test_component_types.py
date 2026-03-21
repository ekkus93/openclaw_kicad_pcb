"""Unit tests for kicad_pcb.component_types — Rule 5 additions.

Covers:
  - R5-1: GND_ALIASES constant
  - R5-2: normalize_gnd_net_name()
  - R5-5: POWER_NET_PREFIXES includes "0V"
    - shared rail polarity vocabulary used by power-net logic and layout linting
"""

from __future__ import annotations

import pytest
from kicad_pcb.component_types import (
    GND_ALIASES,
    NEGATIVE_POWER_NET_PREFIXES,
    POSITIVE_POWER_NET_PREFIXES,
    POWER_NET_PREFIXES,
    is_ground_like_name,
    is_power_net,
    normalize_gnd_net_name,
    power_rail_polarity,
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


class TestGroundLikeName:
    @pytest.mark.parametrize(
        "name",
        ["GND", "0V", "VSS", "AGND", "AGND_STAR", "INPUT_GND", "SIGNAL_0V_RETURN"],
    )
    def test_ground_family_names_detected(self, name: str) -> None:
        assert is_ground_like_name(name) is True

    @pytest.mark.parametrize("name", ["VCC", "AVDD", "SIG", "INPUT", "RETURN_PATH"])
    def test_non_ground_names_not_detected(self, name: str) -> None:
        assert is_ground_like_name(name) is False


class TestSharedRailVocabulary:
    def test_positive_aliases_are_in_shared_prefixes(self) -> None:
        for alias in ("AVCC", "AVDD", "DVDD", "VPOS", "VAA", "VS+"):
            assert alias in POSITIVE_POWER_NET_PREFIXES

    def test_negative_aliases_are_in_shared_prefixes(self) -> None:
        for alias in ("AVEE", "DVEE", "VNEG", "VBB", "VS-"):
            assert alias in NEGATIVE_POWER_NET_PREFIXES

    @pytest.mark.parametrize(
        ("net_name", "expected"),
        [
            ("AVDD", "positive"),
            ("DVDD", "positive"),
            ("AVCC", "positive"),
            ("VPOS", "positive"),
            ("VAA", "positive"),
            ("VS+", "positive"),
            ("AVEE", "negative"),
            ("DVEE", "negative"),
            ("VNEG", "negative"),
            ("VBB", "negative"),
            ("VS-", "negative"),
            ("GND", None),
            ("VSS", None),
        ],
    )
    def test_power_rail_polarity_recognizes_extended_aliases(
        self,
        net_name: str,
        expected: str | None,
    ) -> None:
        assert power_rail_polarity(net_name) == expected

    @pytest.mark.parametrize(
        "net_name",
        ["AVCC", "AVDD", "DVDD", "VPOS", "VAA", "VS+", "AVEE", "DVEE", "VNEG", "VBB", "VS-"],
    )
    def test_is_power_net_uses_shared_rail_aliases(self, net_name: str) -> None:
        assert is_power_net(net_name) is True
