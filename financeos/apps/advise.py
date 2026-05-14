"""LLM Advisor — turns BudgetFinding lists into prose narrative.

Uses the kernel-level LLM client (`financeos.os.llm`) so any provider
configured via .env (anthropic, openai, ollama, groq, openrouter, etc.) can
be plugged in without touching this module.

Implements the Advisor Conformance Gate from spec/CONFORMANCE.md:

- Rule A1 (BLOCKING): The LLM is given ONLY the structured findings + a
  static system prompt. No raw signals, no extracted PDF text.
- Rule A2 (BLOCKING): Every numeric token in the output is validated
  against the set of values in the input findings (with ±0.1 tolerance).
  If any token is unaccounted for, retry once with a stricter prompt; on
  second failure return None and the caller falls back to structured-only.
- Rule A3 (NORMATIVE): The system prompt instructs the LLM to label every
  recommendation as a recommendation, not a factual claim.

Tests inject a chat-callable to mock the LLM without hitting a real provider.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Set

from financeos.apps.compare import BudgetFinding
from financeos.os.llm import LLMResponse, user_msg

NUMERIC_TOKEN_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?%?")
# Strip fiscal-year-shaped substrings BEFORE numeric extraction so '2024-25'
# does not parse as the two integers 2024 and -25 (false positive). Matches
# both YYYY-YY (FinanceOS canonical) and YYYY-YYYY (RBI raw) forms.
FISCAL_YEAR_RE = re.compile(r"\d{4}-\d{2,4}")
NUMBER_TOLERANCE = 0.1
ALWAYS_ALLOWED_NUMBERS = {0.0, 100.0, 1.0, 2.0, 3.0, 4.0, 5.0}

SYSTEM_PROMPT = """You are a public-finance analyst writing a policy brief for the state's Finance Secretary. The Secretary already has the numbers. Your job is to interpret them: explain the gaps, hypothesize causes, and name specific levers.

Hard rules — your output will be auto-rejected if you violate them:

1. NUMBERS: You may only state numeric values that appear in the findings array. Do not invent numbers. Do not say "raise rate by 2pp" or "yield ₹1,000 crore" or "by 2030" — those numbers will trigger rejection. Quote only: state values, peer medians, peer exemplar values, ranks (1/5, etc.), and CAGR percentages. Round to one decimal place.

2. HYPOTHESIS REQUIRED: For each weakness (bottom-quartile finding), state a CAUSAL HYPOTHESIS. What likely drives the gap — rate? base? compliance? sectoral mix? institutional design? Use causal hedges: "likely reflects", "may be driven by", "consistent with". A description of the gap without a hypothesis is a failed brief.

3. LEVER REQUIRED: For each weakness, name a SPECIFIC policy lever the state could pull. Concrete examples: "raise diesel VAT rate", "tighten GST input-tax-credit verification", "consolidate state PSU dividends into a single fund", "shift social-security pensions to Aadhaar-linked DBT", "renegotiate market-loan tenure mix toward longer maturities", "switch power subsidies from cross-subsidy to direct benefit transfer". Vague phrases like "could examine the peer approach", "may benefit from review", "should consider strengthening" are auto-rejected. Name what to do, not what to think about.

4. RISK / COUNTER: For each lever, name one risk or counter-consideration in a clause. Be honest about regressivity, political cost, implementation difficulty, or second-order effects. Levers without risks read as advocacy, not analysis.

5. STRENGTHS: Acknowledge top-quartile metrics in one short paragraph at the start. Do NOT propose levers for things the state already leads on.

6. SCOPE: Do not predict outcome magnitudes or timelines. Do not attribute causes you cannot defend from public knowledge of how state finances generally work. Stick to mechanisms, not promised results.

7. FORMAT: Three paragraphs.
   Para 1: where the state stands overall + 1-2 strengths (with one-line interpretation of why each strength matters).
   Para 2: the most consequential weakness — hypothesis + lever + risk.
   Para 3: one secondary weakness — hypothesis + lever + risk.
   No headings, no bullet lists, no markdown beyond paragraph breaks. The text will be embedded under existing markdown headers.

Be direct. The Secretary has read worse briefs and has read more polite ones. They want actionable interpretation, not prose padding."""

RETRY_SYSTEM_SUFFIX = """

PREVIOUS ATTEMPT FAILED: it introduced these numeric values that are not in the findings: {bad_numbers}. These are exactly the kind of fabrications you must not produce. Restate the analysis using only numbers visible in the findings array."""


# ChatCallable: (system_prompt, user_message, max_tokens) -> LLMResponse
ChatCallable = Callable[[str, str, int], LLMResponse]


@dataclass
class AdvisorResult:
    state: str
    narrative: Optional[str]
    attempts: int
    rejected_numbers: List[float]
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0
    model: Optional[str] = None


def extract_numeric_tokens(text: str) -> List[float]:
    """Pull every numeric token out of `text`. Handles %, commas, signs.

    Fiscal-year strings (e.g. '2024-25') are stripped first so the dash is
    not interpreted as a minus sign on the second integer.
    """
    text = FISCAL_YEAR_RE.sub(" ", text)
    out: List[float] = []
    for t in NUMERIC_TOKEN_RE.findall(text):
        cleaned = t.replace(",", "").rstrip("%")
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def build_allowed_numbers(findings: Sequence[BudgetFinding]) -> Set[float]:
    """Set of numeric values the LLM is allowed to mention.

    For negative finding values we also allow the absolute value: a finding
    of '-15.8% revenue balance' may legitimately be paraphrased by the LLM
    as 'a 15.8% deficit'. The reverse (positive findings → negative
    paraphrase) is NOT allowed because that would be a sign flip — a real
    fabrication.
    """
    allowed: Set[float] = set(ALWAYS_ALLOWED_NUMBERS)
    for f in findings:
        for v in (f.value, f.peer_median, f.peer_exemplar_value):
            if v is not None:
                allowed.add(round(v, 1))
                allowed.add(round(v, 2))
                if v < 0:
                    allowed.add(round(abs(v), 1))
                    allowed.add(round(abs(v), 2))
        if f.rank_in_peers is not None:
            allowed.add(float(f.rank_in_peers))
        allowed.add(float(f.n_peers))
    return allowed


def find_invented_numbers(text: str, allowed: Set[float],
                          tolerance: float = NUMBER_TOLERANCE) -> List[float]:
    """Return numeric tokens in `text` not within ±tolerance of any allowed value."""
    invented: List[float] = []
    for v in extract_numeric_tokens(text):
        if not any(abs(v - a) <= tolerance for a in allowed):
            invented.append(v)
    return invented


def build_user_message(state: str, fiscal_year: str, estimate_type: str,
                       findings: Sequence[BudgetFinding]) -> str:
    """Construct the user message — JSON only, no editorial commentary (Rule A1)."""
    payload = {
        "state": state,
        "fiscal_year": fiscal_year,
        "estimate_type": estimate_type,
        "findings": [
            {
                "metric_id": f.metric_id,
                "label": f.label,
                "family": f.family,
                "value": round(f.value, 1),
                "unit": f.unit,
                "peer_median": round(f.peer_median, 1) if f.peer_median is not None else None,
                "peer_exemplar_state": f.peer_exemplar_state,
                "peer_exemplar_value": (
                    round(f.peer_exemplar_value, 1) if f.peer_exemplar_value is not None else None
                ),
                "rank_in_peers": f.rank_in_peers,
                "n_peers": f.n_peers,
                "flag": f.flag,
                "higher_is_better": f.higher_is_better,
            }
            for f in findings
        ],
    }
    return (
        "Write the budget brief narrative for this state, following all rules in the system prompt.\n\n"
        f"Findings JSON:\n```json\n{json.dumps(payload, indent=2)}\n```"
    )


_KEYLESS_PROVIDERS = ("ollama", "lmstudio")


def assert_config_has_key(cfg) -> None:
    """Raise ValueError if a key-requiring provider is missing its key.

    Called by make_llm_chat_callable for both client-passed and client-built
    paths so we always fail before issuing a 401-bound API request.
    """
    if cfg.api_key in ("", "no-key") and cfg.provider not in _KEYLESS_PROVIDERS:
        raise ValueError(
            f"No API key resolved for provider '{cfg.provider}'. "
            f"Set ANTHROPIC_API_KEY (for anthropic) or LLM_API_KEY in your .env."
        )


def make_llm_chat_callable(client=None) -> ChatCallable:
    """Build a (system, user, max_tokens) -> LLMResponse callable backed by an LLMClient.

    If `client` is None, builds one from `load_config()` — which reads
    LLM_PROVIDER / LLM_MODEL / API key env vars. In either case, validates
    that a key-requiring provider has a resolved key before returning.
    """
    if client is None:
        from financeos.os.llm import LLMClient, load_config
        cfg = load_config()
        assert_config_has_key(cfg)
        client = LLMClient(cfg)
    else:
        assert_config_has_key(client.config)

    def call(system_prompt: str, user_message: str, max_tokens: int) -> LLMResponse:
        return client.chat(
            [user_msg(user_message)],
            system=system_prompt,
            max_tokens=max_tokens,
        )

    return call


class BudgetAdvisor:
    """Wraps an LLM chat callable with the honesty guardrail."""

    MAX_ATTEMPTS = 2
    NARRATIVE_MAX_TOKENS = 1536  # 3 paragraphs of hypothesis+lever+risk

    def __init__(self, chat_callable: ChatCallable):
        self.chat_callable = chat_callable

    def advise(self, state: str, fiscal_year: str, estimate_type: str,
               findings: Sequence[BudgetFinding]) -> AdvisorResult:
        if not findings:
            return AdvisorResult(state=state, narrative=None, attempts=0,
                                 rejected_numbers=[])

        # Filter findings passed to the LLM to those flagged as notable
        # (top or bottom quartile vs peers). The validator's allowed-numbers
        # set is built from the FULL findings list — so the LLM may quote
        # peer medians that come from unflagged context — but the prose
        # input itself stays signal-dense.
        notable = [f for f in findings if f.flag in ("above_peers", "below_peers")]
        llm_input = notable if notable else list(findings)  # fallback if nothing notable

        allowed = build_allowed_numbers(findings)
        user_msg_text = build_user_message(state, fiscal_year, estimate_type, llm_input)

        last_invented: List[float] = []
        cost_in = cost_out = 0
        last_model: Optional[str] = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            system = SYSTEM_PROMPT
            if attempt > 1 and last_invented:
                system = SYSTEM_PROMPT + RETRY_SYSTEM_SUFFIX.format(bad_numbers=last_invented)

            resp = self.chat_callable(system, user_msg_text, self.NARRATIVE_MAX_TOKENS)
            text = (resp.content or "").strip()
            cost_in += resp.usage.get("prompt_tokens", 0)
            cost_out += resp.usage.get("completion_tokens", 0)
            last_model = resp.model

            invented = find_invented_numbers(text, allowed)
            if not invented:
                return AdvisorResult(
                    state=state, narrative=text, attempts=attempt,
                    rejected_numbers=[],
                    cost_input_tokens=cost_in, cost_output_tokens=cost_out,
                    model=last_model,
                )
            last_invented = invented

        return AdvisorResult(
            state=state, narrative=None, attempts=self.MAX_ATTEMPTS,
            rejected_numbers=last_invented,
            cost_input_tokens=cost_in, cost_output_tokens=cost_out,
            model=last_model,
        )
