"""AI-powered edge detection for Polymarket prediction markets.

Uses an LLM (Anthropic Claude or OpenAI) to analyze market questions,
estimate true probabilities, and find mispriced markets where we have
an informational edge.

Strategy: "green/yellow light" approach --
  1. Scan active markets
  2. Ask the LLM to estimate the true probability for each question
  3. Compare LLM estimate vs. market price
  4. Trade when |model_prob - market_prob| exceeds a threshold
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

from src.logger import get_logger
from src.polymarket.types import Market, Opportunity, Outcome, Side

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Confidence levels (ordered for comparison)
# ---------------------------------------------------------------------------

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MarketAnalysis:
    """Result of AI analysis for a single market."""

    market: Market
    estimated_prob: float          # LLM's estimated YES probability (0.0-1.0)
    confidence: str                # "low" / "medium" / "high"
    reasoning: str
    key_factors: list[str]
    edge: float                    # |estimated_prob - market YES price|
    recommended_side: str          # "YES" or "NO"
    timestamp: float = 0.0        # time.time() when analysis was produced


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    analysis: MarketAnalysis
    expires_at: float


# ---------------------------------------------------------------------------
# System prompt for the LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a quantitative prediction market analyst. Your job is to estimate \
the true probability that a prediction market question resolves YES.

You have deep expertise in geopolitics, economics, science, technology, \
sports, culture, and current events. You calibrate probabilities carefully: \
a 70% estimate should resolve YES about 70% of the time.

Rules:
- Always give a numeric probability between 0.01 and 0.99 (never 0 or 1).
- State your confidence: "low", "medium", or "high".
- Be concise in your reasoning (2-4 sentences).
- List 2-5 key factors that drive your estimate.
- Respond ONLY with valid JSON -- no markdown fences, no commentary outside the JSON.

Response format (strict JSON):
{
  "estimated_probability": 0.65,
  "confidence": "medium",
  "reasoning": "Brief explanation of your estimate.",
  "key_factors": ["factor 1", "factor 2", "factor 3"]
}\
"""


# ---------------------------------------------------------------------------
# AIEdgeAnalyzer
# ---------------------------------------------------------------------------


class AIEdgeAnalyzer:
    """Scan Polymarket markets for AI-detected edge.

    Uses an LLM to estimate true probabilities for market questions and
    compares with current market prices to find mispriced opportunities.
    """

    def __init__(self, cfg: dict) -> None:
        ai_cfg = cfg.get("polymarket", {}).get("ai_edge", {})

        self._min_edge: float = ai_cfg.get("min_edge", 0.05)
        self._min_confidence: str = ai_cfg.get("min_confidence", "medium")
        self._max_markets: int = ai_cfg.get("max_markets_per_cycle", 10)
        self._cache_seconds: int = ai_cfg.get("analysis_cache_seconds", 600)
        self._model_provider: str = ai_cfg.get("model_provider", "anthropic")

        # Analysis cache: condition_id -> _CacheEntry
        self._cache: dict[str, _CacheEntry] = {}

        # Resolve API key once at init
        self._api_key: Optional[str] = self._resolve_api_key()
        if not self._api_key:
            log.warning(
                "No LLM API key found (checked LLM_API_KEY, ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY). AI edge analysis will be disabled."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_market(self, market: Market) -> Optional[MarketAnalysis]:
        """Analyze a single market question using the configured LLM.

        Returns a MarketAnalysis if the LLM call succeeds, or None if
        the API key is missing, the call fails, or the response cannot
        be parsed.
        """
        if not self._api_key:
            return None

        # Check cache first
        cached = self._get_cached(market.condition_id)
        if cached is not None:
            log.debug("Cache hit for market %s", market.condition_id[:12])
            return cached

        prompt = self._build_prompt(market)

        try:
            raw_response = self._call_llm(prompt)
        except Exception:
            log.exception("LLM call failed for market %s", market.condition_id[:12])
            return None

        if not raw_response:
            log.warning("Empty LLM response for market %s", market.condition_id[:12])
            return None

        analysis = self._parse_analysis(raw_response, market)
        if analysis is not None:
            self._put_cache(market.condition_id, analysis)

        return analysis

    def find_opportunities(self, markets: list[Market]) -> list[Opportunity]:
        """Scan a list of markets for AI-detected edge.

        Filters to active markets, analyzes up to max_markets_per_cycle,
        and returns Opportunity objects sorted by edge descending.
        """
        active = [m for m in markets if m.active]
        batch = active[: self._max_markets]

        if not batch:
            return []

        log.info("AI edge scan: analyzing %d / %d active markets", len(batch), len(active))

        opportunities: list[Opportunity] = []

        for market in batch:
            analysis = self.analyze_market(market)
            if analysis is None:
                continue

            # Filter by minimum edge
            if analysis.edge < self._min_edge:
                log.debug(
                    "Skipping %s: edge %.1f%% < min %.1f%%",
                    market.condition_id[:12],
                    analysis.edge * 100,
                    self._min_edge * 100,
                )
                continue

            # Filter by minimum confidence
            if _CONFIDENCE_RANK.get(analysis.confidence, 0) < _CONFIDENCE_RANK.get(
                self._min_confidence, 1
            ):
                log.debug(
                    "Skipping %s: confidence '%s' < min '%s'",
                    market.condition_id[:12],
                    analysis.confidence,
                    self._min_confidence,
                )
                continue

            opp = self._analysis_to_opportunity(analysis)
            opportunities.append(opp)
            log.info(
                "AI edge found: %s | %s @ %.1f%% vs model %.1f%% | edge %.1f%%",
                market.question[:60],
                analysis.recommended_side,
                analysis.market.yes_price * 100,
                analysis.estimated_prob * 100,
                analysis.edge * 100,
            )

        opportunities.sort(key=lambda o: o.edge, reverse=True)
        return opportunities

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _build_prompt(self, market: Market) -> str:
        """Build the user prompt for a market analysis request."""
        parts = [
            f"Market question: {market.question}",
            f"Current YES price: {market.yes_price:.4f} ({market.yes_price:.1%})",
            f"Current NO price: {market.no_price:.4f} ({market.no_price:.1%})",
            f"24h volume: ${market.volume_24h:,.0f}",
            f"Total volume: ${market.volume:,.0f}",
            f"Liquidity: ${market.liquidity:,.0f}",
        ]
        if market.end_date:
            parts.append(f"Resolution date: {market.end_date}")
        if market.category:
            parts.append(f"Category: {market.category}")

        parts.append(
            "\nEstimate the true probability that this resolves YES. "
            "Respond with JSON only."
        )
        return "\n".join(parts)

    def _call_llm(self, prompt: str) -> str:
        """Call the configured LLM provider and return the raw text response.

        Tries Anthropic first (if configured), then falls back to OpenAI.
        Raises on network/API errors so the caller can handle them.
        """
        if self._model_provider == "anthropic":
            return self._call_anthropic(prompt)
        elif self._model_provider == "openai":
            return self._call_openai(prompt)
        else:
            log.error("Unknown model_provider: %s", self._model_provider)
            return ""

    def _call_anthropic(self, prompt: str) -> str:
        """Call the Anthropic Claude API."""
        try:
            from anthropic import Anthropic
        except ImportError:
            log.warning(
                "anthropic package not installed. "
                "Install with: uv add anthropic"
            )
            return ""

        client = Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from the response
        if message.content and len(message.content) > 0:
            return message.content[0].text
        return ""

    def _call_openai(self, prompt: str) -> str:
        """Call the OpenAI API."""
        try:
            from openai import OpenAI
        except ImportError:
            log.warning(
                "openai package not installed. "
                "Install with: uv add openai"
            )
            return ""

        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            return content or ""
        return ""

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_analysis(self, response: str, market: Market) -> Optional[MarketAnalysis]:
        """Parse an LLM response into a MarketAnalysis.

        Tries JSON extraction first, then falls back to regex parsing.
        Returns None if the response cannot be meaningfully parsed.
        """
        data = self._extract_json(response)
        if data is None:
            data = self._regex_fallback(response)

        if data is None:
            log.warning(
                "Could not parse LLM response for market %s",
                market.condition_id[:12],
            )
            return None

        # Validate and clamp probability
        prob = data.get("estimated_probability")
        if prob is None:
            log.warning("No probability in LLM response for %s", market.condition_id[:12])
            return None

        try:
            prob = float(prob)
        except (TypeError, ValueError):
            log.warning("Invalid probability value: %s", prob)
            return None

        prob = max(0.01, min(0.99, prob))

        # Extract other fields with safe defaults
        confidence = str(data.get("confidence", "low")).lower()
        if confidence not in _CONFIDENCE_RANK:
            confidence = "low"

        reasoning = str(data.get("reasoning", ""))
        key_factors = data.get("key_factors", [])
        if not isinstance(key_factors, list):
            key_factors = [str(key_factors)]
        key_factors = [str(f) for f in key_factors]

        # Calculate edge and recommended side
        yes_price = market.yes_price
        no_price = market.no_price
        model_no = 1.0 - prob

        yes_edge = prob - yes_price        # positive = YES is underpriced
        no_edge = model_no - no_price      # positive = NO is underpriced

        if yes_edge >= no_edge:
            edge = yes_edge
            recommended_side = "YES"
        else:
            edge = no_edge
            recommended_side = "NO"

        # Edge should be absolute for filtering; sign indicates direction
        edge = abs(edge)

        return MarketAnalysis(
            market=market,
            estimated_prob=round(prob, 4),
            confidence=confidence,
            reasoning=reasoning,
            key_factors=key_factors,
            edge=round(edge, 4),
            recommended_side=recommended_side,
            timestamp=time.time(),
        )

    def _extract_json(self, text: str) -> Optional[dict]:
        """Try to extract a JSON object from the LLM response.

        Handles both raw JSON and JSON wrapped in markdown code fences.
        """
        # Try ```json ... ``` blocks first
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to find a raw JSON object
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _regex_fallback(self, text: str) -> Optional[dict]:
        """Fall back to regex extraction when JSON parsing fails."""
        result: dict = {}

        # Look for probability
        prob_match = re.search(
            r"(?:estimated_probability|probability|prob)[\"']?\s*[:=]\s*([0-9]*\.?[0-9]+)",
            text,
            re.IGNORECASE,
        )
        if prob_match:
            result["estimated_probability"] = float(prob_match.group(1))
        else:
            # Try standalone decimal that looks like a probability
            standalone = re.search(r"\b(0\.\d{1,4})\b", text)
            if standalone:
                result["estimated_probability"] = float(standalone.group(1))

        # Look for confidence
        conf_match = re.search(
            r"(?:confidence)[\"']?\s*[:=]\s*[\"']?(low|medium|high)[\"']?",
            text,
            re.IGNORECASE,
        )
        if conf_match:
            result["confidence"] = conf_match.group(1).lower()

        # Look for reasoning
        reason_match = re.search(
            r"(?:reasoning)[\"']?\s*[:=]\s*[\"'](.+?)[\"']",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if reason_match:
            result["reasoning"] = reason_match.group(1).strip()

        if "estimated_probability" not in result:
            return None

        return result

    # ------------------------------------------------------------------
    # Opportunity conversion
    # ------------------------------------------------------------------

    def _analysis_to_opportunity(self, analysis: MarketAnalysis) -> Opportunity:
        """Convert a MarketAnalysis into an Opportunity for the trading system."""
        market = analysis.market

        if analysis.recommended_side == "YES":
            outcome = Outcome.YES
            model_prob = analysis.estimated_prob
            market_prob = market.yes_price
        else:
            outcome = Outcome.NO
            model_prob = 1.0 - analysis.estimated_prob
            market_prob = market.no_price

        edge = model_prob - market_prob
        ev = edge  # simplified EV = edge for binary markets

        # Kelly criterion
        if 0 < market_prob < 1:
            b = (1.0 - market_prob) / market_prob
            kelly = max((b * model_prob - (1 - model_prob)) / b, 0) if b > 0 else 0
        else:
            kelly = 0.0

        return Opportunity(
            market=market,
            outcome=outcome,
            side=Side.BUY,
            model_prob=round(model_prob, 4),
            market_prob=round(market_prob, 4),
            edge=round(edge, 4),
            ev=round(ev, 4),
            kelly_fraction=round(kelly, 6),
            confidence=analysis.confidence,
            reason=(
                f"AI: {analysis.recommended_side}@{market_prob:.0%} "
                f"vs model {model_prob:.0%} | {analysis.reasoning[:80]}"
            ),
        )

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _get_cached(self, condition_id: str) -> Optional[MarketAnalysis]:
        """Return cached analysis if still valid, else None."""
        entry = self._cache.get(condition_id)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._cache[condition_id]
            return None
        return entry.analysis

    def _put_cache(self, condition_id: str, analysis: MarketAnalysis) -> None:
        """Store an analysis in the cache."""
        self._cache[condition_id] = _CacheEntry(
            analysis=analysis,
            expires_at=time.time() + self._cache_seconds,
        )

    # ------------------------------------------------------------------
    # API key resolution
    # ------------------------------------------------------------------

    def _resolve_api_key(self) -> Optional[str]:
        """Find an API key from environment variables.

        Checks provider-specific keys first, then the generic LLM_API_KEY.
        """
        if self._model_provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY")
        elif self._model_provider == "openai":
            key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        else:
            key = os.environ.get("LLM_API_KEY")

        return key if key else None
