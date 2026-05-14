"""Tests for the LLM Advisor — focusing on the honesty guardrail.

The real Anthropic/OpenAI APIs are never called from these tests. The
`chat_callable` (signature: system, user, max_tokens -> LLMResponse) is
replaced by a function that returns canned LLMResponse objects, letting us
exercise the validator and retry loop deterministically.

The guardrail rules (spec/CONFORMANCE.md §A1-A3):
- A1: only structured findings + system prompt are passed to the LLM
- A2: every numeric token in the output must be in the findings
- A3: recommendations must be labelled as recommendations
"""
from __future__ import annotations

from typing import Callable, List

import pytest

from financeos.apps.advise import (
    BudgetAdvisor,
    assert_config_has_key,
    build_allowed_numbers,
    build_user_message,
    extract_numeric_tokens,
    find_invented_numbers,
)
from financeos.apps.compare import BudgetFinding
from financeos.os.llm import LLMResponse
from financeos.os.llm.config import LLMConfig


def _finding(metric_id: str, value: float, peer_median: float = 50.0,
             exemplar: str = "TG", exemplar_value: float = 80.0,
             rank: int = 3, n_peers: int = 5) -> BudgetFinding:
    return BudgetFinding(
        state="KA", fiscal_year="2024-25", estimate_type="RE",
        metric_id=metric_id, family="revenue_side", label=metric_id,
        value=value, unit="PCT",
        peer_median=peer_median, peer_exemplar_state=exemplar,
        peer_exemplar_value=exemplar_value, rank_in_peers=rank,
        n_peers=n_peers, flag="within_peers", higher_is_better=True,
    )


def _resp(text: str, in_tok: int = 100, out_tok: int = 50,
          model: str = "test-model") -> LLMResponse:
    return LLMResponse(
        content=text, tool_calls=[], stop_reason="stop", model=model,
        usage={"prompt_tokens": in_tok, "completion_tokens": out_tok,
               "total_tokens": in_tok + out_tok},
    )


def _fake_chat(*responses: LLMResponse) -> Callable[[str, str, int], LLMResponse]:
    """Build a chat_callable that returns each canned LLMResponse in turn."""
    state = {"i": 0}
    def call(system: str, user: str, max_tokens: int) -> LLMResponse:
        i = state["i"]
        state["i"] += 1
        return responses[i] if i < len(responses) else _resp("")
    return call


# -- extractor --

def test_extract_simple_numbers():
    assert extract_numeric_tokens("70.0% and -10.1%") == [70.0, -10.1]


def test_extract_with_commas():
    assert extract_numeric_tokens("₹26,000 crore") == [26000.0]


def test_extract_handles_slashes_as_separators():
    nums = extract_numeric_tokens("rank 1/5 in peers")
    assert 1.0 in nums and 5.0 in nums


def test_extract_returns_empty_for_no_numbers():
    assert extract_numeric_tokens("Karnataka leads its peer group on revenue.") == []


def test_extract_strips_fiscal_year_yyyy_yy():
    """Fiscal year '2024-25' must not parse as the integers 2024 and -25."""
    nums = extract_numeric_tokens("In fiscal year 2024-25, Karnataka leads.")
    assert 2024.0 not in nums
    assert -25.0 not in nums
    assert nums == []


def test_extract_strips_fiscal_year_yyyy_yyyy():
    """Defensive: also strip RBI's raw 2024-2025 form if it leaks through."""
    nums = extract_numeric_tokens("Source year 2024-2025 data.")
    assert 2024.0 not in nums
    assert -2025.0 not in nums
    assert nums == []


def test_extract_does_not_strip_lone_year():
    """Lone years (e.g. 'by 2030') must still be caught — those are predictions
    the guardrail should reject when not in findings."""
    nums = extract_numeric_tokens("Karnataka should reach this by 2030.")
    assert 2030.0 in nums


# -- allowed set --

def test_allowed_set_contains_finding_values_and_peer_metrics():
    f = _finding("own_tax_share_pct", 70.0)
    allowed = build_allowed_numbers([f])
    assert 70.0 in allowed
    assert 50.0 in allowed
    assert 80.0 in allowed
    assert 3.0 in allowed
    assert 5.0 in allowed
    assert 100.0 in allowed
    assert 0.0 in allowed


def test_allowed_set_includes_abs_of_negative_values():
    """A finding of -15.8 (revenue deficit) may be paraphrased as 15.8."""
    f = _finding("revenue_balance_pct", -15.8, peer_median=-10.1)
    allowed = build_allowed_numbers([f])
    assert -15.8 in allowed
    assert 15.8 in allowed
    assert -10.1 in allowed
    assert 10.1 in allowed


def test_paraphrasing_negative_finding_as_positive_passes_validation():
    f = _finding("revenue_balance_pct", -10.1, peer_median=-15.8)
    allowed = build_allowed_numbers([f])
    text = "AP runs a 15.8% revenue deficit on the peer median basis, with its own at 10.1%."
    assert find_invented_numbers(text, allowed) == []


def test_sign_flip_on_positive_finding_is_still_caught():
    """Asymmetry: a positive finding (+70) cannot be paraphrased as negative (-70)."""
    f = _finding("own_tax_share_pct", 70.0)
    allowed = build_allowed_numbers([f])
    text = "Karnataka has a -70.0% tax share."  # nonsense — flipping a positive is a real fab
    invented = find_invented_numbers(text, allowed)
    assert -70.0 in invented


# -- guardrail: invented number detection --

def test_no_invented_numbers_when_text_uses_only_findings():
    f = _finding("own_tax_share_pct", 70.0)
    allowed = build_allowed_numbers([f])
    text = "Karnataka's own tax share at 70.0% is above the peer median of 50.0%, ranking 3 of 5."
    assert find_invented_numbers(text, allowed) == []


def test_detects_invented_year():
    f = _finding("own_tax_share_pct", 70.0)
    allowed = build_allowed_numbers([f])
    text = "Karnataka could grow this share to 75% by 2030."
    invented = find_invented_numbers(text, allowed)
    assert 75.0 in invented or 2030.0 in invented


def test_detects_invented_money_amount():
    f = _finding("own_tax_share_pct", 70.0)
    allowed = build_allowed_numbers([f])
    text = "This translates to roughly ₹26,500 crore in revenue."
    assert 26500.0 in find_invented_numbers(text, allowed)


def test_tolerates_one_decimal_rounding():
    f = _finding("own_tax_share_pct", 69.96)
    allowed = build_allowed_numbers([f])
    text = "Karnataka leads at 70.0% on this metric."
    assert find_invented_numbers(text, allowed) == []


# -- advisor end-to-end with mocked chat callable --

def test_clean_first_attempt_returns_narrative():
    f = _finding("own_tax_share_pct", 70.0)
    advisor = BudgetAdvisor(_fake_chat(
        _resp("Karnataka stands at 70.0% on own tax share, above the peer median of 50.0%.")
    ))
    result = advisor.advise("KA", "2024-25", "RE", [f])
    assert result.narrative is not None
    assert result.attempts == 1
    assert result.rejected_numbers == []
    assert result.cost_input_tokens == 100
    assert result.cost_output_tokens == 50
    assert result.model == "test-model"


def test_invented_number_triggers_retry_and_succeeds():
    f = _finding("own_tax_share_pct", 70.0)
    advisor = BudgetAdvisor(_fake_chat(
        _resp("Karnataka could push this to 85% by 2027."),
        _resp("Karnataka's 70.0% own tax share leads the peer group."),
    ))
    result = advisor.advise("KA", "2024-25", "RE", [f])
    assert result.narrative is not None
    assert result.attempts == 2
    assert result.rejected_numbers == []
    # Token costs accumulate across retries
    assert result.cost_input_tokens == 200
    assert result.cost_output_tokens == 100


def test_two_failures_in_a_row_returns_no_narrative():
    f = _finding("own_tax_share_pct", 70.0)
    advisor = BudgetAdvisor(_fake_chat(
        _resp("Karnataka could push this to 85%."),
        _resp("Targeting 90% would be ambitious."),
    ))
    result = advisor.advise("KA", "2024-25", "RE", [f])
    assert result.narrative is None
    assert result.attempts == 2
    assert 90.0 in result.rejected_numbers


def test_empty_findings_skips_llm():
    calls = []
    def call(system, user, max_tokens):
        calls.append((system, user))
        return _resp("should not run")
    advisor = BudgetAdvisor(call)
    result = advisor.advise("KA", "2024-25", "RE", [])
    assert result.narrative is None
    assert result.attempts == 0
    assert calls == []


def test_assert_config_has_key_blocks_anthropic_without_key():
    cfg = LLMConfig(provider="anthropic", base_url="https://api.anthropic.com",
                    api_key="no-key", model="claude-haiku-4-5")
    with pytest.raises(ValueError, match="No API key resolved"):
        assert_config_has_key(cfg)


def test_assert_config_has_key_allows_ollama_without_key():
    cfg = LLMConfig(provider="ollama", base_url="http://localhost:11434/v1",
                    api_key="no-key", model="llama3.1")
    # Should not raise — local providers don't need a key
    assert_config_has_key(cfg)


def test_assert_config_has_key_passes_with_real_key():
    cfg = LLMConfig(provider="anthropic", base_url="https://api.anthropic.com",
                    api_key="sk-ant-real", model="claude-haiku-4-5")
    assert_config_has_key(cfg)


def test_advisor_filters_to_flagged_findings_only():
    """When some findings are flagged top/bottom-quartile, only those reach the LLM."""
    f_flagged_top = BudgetFinding(
        state="KA", fiscal_year="2024-25", estimate_type="RE",
        metric_id="own_tax_share_pct", family="revenue_side", label="Own tax",
        value=70.0, unit="PCT", peer_median=64.0, peer_exemplar_state="KA",
        peer_exemplar_value=70.0, rank_in_peers=1, n_peers=5,
        flag="above_peers", higher_is_better=True,
    )
    f_unflagged = BudgetFinding(
        state="KA", fiscal_year="2024-25", estimate_type="RE",
        metric_id="midrange_metric", family="revenue_side", label="Mid metric",
        value=50.0, unit="PCT", peer_median=49.0, peer_exemplar_state="TN",
        peer_exemplar_value=55.0, rank_in_peers=3, n_peers=5,
        flag="within_peers", higher_is_better=True,
    )

    captured = {}
    def call(system, user, max_tokens):
        captured["user"] = user
        return _resp("Karnataka leads at 70.0%.")

    advisor = BudgetAdvisor(call)
    result = advisor.advise("KA", "2024-25", "RE", [f_flagged_top, f_unflagged])
    assert result.narrative is not None
    # The flagged metric MUST be in the user message
    assert "own_tax_share_pct" in captured["user"]
    # The unflagged metric should NOT be in the user message
    assert "midrange_metric" not in captured["user"]


def test_advisor_falls_back_to_all_findings_when_nothing_flagged():
    """If no metric is top/bottom-quartile, pass everything so the LLM has context."""
    f_unflagged = BudgetFinding(
        state="KA", fiscal_year="2024-25", estimate_type="RE",
        metric_id="midrange_metric", family="revenue_side", label="Mid metric",
        value=50.0, unit="PCT", peer_median=49.0, peer_exemplar_state="TN",
        peer_exemplar_value=55.0, rank_in_peers=3, n_peers=5,
        flag="within_peers", higher_is_better=True,
    )
    captured = {}
    def call(system, user, max_tokens):
        captured["user"] = user
        return _resp("KA sits in the middle at 50.0%, near peer median of 49.0%.")
    advisor = BudgetAdvisor(call)
    result = advisor.advise("KA", "2024-25", "RE", [f_unflagged])
    assert result.narrative is not None
    assert "midrange_metric" in captured["user"]  # fallback kicked in


def test_user_message_contains_only_structured_findings():
    """Rule A1: the LLM must not see raw signals or PDF text."""
    f = _finding("own_tax_share_pct", 70.0)
    msg = build_user_message("KA", "2024-25", "RE", [f])
    assert "own_tax_share_pct" in msg
    assert "70.0" in msg
    assert "budget_signals" not in msg
    assert "SELECT" not in msg
    assert "INSERT" not in msg
    assert ".xlsx" not in msg.lower()
    assert "rbi.estates" not in msg
