"""Verify the canonical registries load and have plausible content."""
from __future__ import annotations

from financeos.drivers.registries.loader import load_registries


def test_states_registry_contains_south_5():
    regs = load_registries()
    for code in ["KA", "TN", "AP", "TG", "KL"]:
        assert code in regs.state_codes, f"{code} missing from states registry"


def test_states_registry_has_full_names():
    regs = load_registries()
    assert regs.state_names["KA"] == "Karnataka"
    assert regs.state_names["TN"] == "Tamil Nadu"


def test_major_heads_registry_contains_essentials():
    regs = load_registries()
    for code in ["2210", "2202", "2049", "0040", "1601", "6003"]:
        assert code in regs.major_head_codes, f"{code} missing from major_heads registry"


def test_major_head_codes_are_4_digit_strings():
    regs = load_registries()
    for code in regs.major_head_codes:
        assert isinstance(code, str)
        assert len(code) == 4
        assert code.isdigit()


def test_functional_categories_only_reference_known_major_heads():
    regs = load_registries()
    for category, codes in regs.functional_categories.items():
        for code in codes:
            assert code in regs.major_head_codes, (
                f"functional_categories['{category}'] references unknown major_head '{code}'"
            )


def test_functional_categories_cover_health_and_education():
    regs = load_registries()
    assert "health" in regs.functional_categories
    assert "education" in regs.functional_categories
    assert "2210" in regs.functional_categories["health"]
    assert "2202" in regs.functional_categories["education"]
