"""Sports odds comparison engine — find mispriced Polymarket markets.

Compares Polymarket prices against traditional bookmaker odds (via The Odds API)
to identify markets where Polymarket is mispriced by > threshold.

Bookmaker odds reflect deep, efficient markets with billions in volume.
When Polymarket disagrees by 5%+, the bookmakers are usually right.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import requests

from src.logger import get_logger

log = get_logger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS = [
    # Soccer — major leagues
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league_qualification",
    "soccer_brazil_campeonato",
    "soccer_usa_mls",
    "soccer_mexico_ligamx",
    "soccer_sweden_allsvenskan",
    "soccer_korea_kleague1",
    "soccer_denmark_superliga",
    "soccer_norway_eliteserien",
    "soccer_finland_veikkausliiga",
    "soccer_argentina_primera_division",
    "soccer_netherlands_eredivisie",
    "soccer_conmebol_copa_libertadores",
    "soccer_belgium_first_div",
    "soccer_austria_bundesliga",
    "soccer_switzerland_superleague",
    "soccer_poland_ekstraklasa",
    "soccer_greece_super_league",
    "soccer_spl",
    "soccer_turkey_super_league",
    "soccer_japan_j_league",
    "soccer_australia_aleague",
    # Tennis — ATP + WTA (swisstony's big category)
    "tennis_atp_french_open",
    "tennis_atp_us_open",
    "tennis_atp_wimbledon",
    "tennis_atp_aus_open",
    "tennis_wta_french_open",
    "tennis_wta_us_open",
    "tennis_wta_wimbledon",
    "tennis_wta_aus_open",
    # US Sports
    "americanfootball_nfl",
    "basketball_nba",
    "basketball_wnba",
    "baseball_mlb",
    "icehockey_nhl",
    # Combat
    "mma_mixed_martial_arts",
    "boxing_boxing",
]


@dataclass
class BookmakerOdds:
    sport: str
    home_team: str
    away_team: str
    commence_time: str
    home_prob: float
    away_prob: float
    draw_prob: float = 0.0
    bookmaker: str = ""
    raw_odds: dict = field(default_factory=dict)


@dataclass
class OddsEdge:
    polymarket_question: str
    polymarket_price: float
    bookmaker_prob: float
    edge: float
    side: str
    sport: str
    home_team: str
    away_team: str
    bookmaker: str
    confidence: str
    poly_condition_id: str = ""
    poly_token_id: str = ""


def decimal_to_prob(odds: float) -> float:
    if odds <= 1.0:
        return 0.0
    return 1.0 / odds


def remove_vig(probs: list[float]) -> list[float]:
    """Remove bookmaker vig/juice to get fair probabilities."""
    total = sum(probs)
    if total <= 0:
        return probs
    return [p / total for p in probs]


def _normalize(name: str) -> str:
    """Normalize team/player name for fuzzy matching."""
    name = name.lower().strip()
    name = re.sub(r"\b(fc|sc|cf|ac|as|us|ss|fk|if|bk|sk|afc|ssc)\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _contains_name(haystack: str, name: str) -> float:
    """Check if name appears as a substring in haystack, return similarity score.

    Better than full-string SequenceMatcher for matching short team/player names
    inside long Polymarket questions.
    """
    h = _normalize(haystack)
    n = _normalize(name)
    if not n:
        return 0.0
    if n in h:
        return 0.95
    words = n.split()
    if len(words) >= 2:
        matched = sum(1 for w in words if w in h)
        return matched / len(words) * 0.9
    return _similarity(n, h)


def _extract_team_from_question(question: str) -> tuple[str, str, str]:
    """Extract team/player names from common Polymarket question formats.

    Returns (team_a, team_b, hint) where hint is "" or "draw".
    """
    q = question.strip().rstrip("?")

    # Draw pattern first: "Will X vs Y end in a draw"
    m = re.search(r"(?:will\s+)?(.+?)\s+(?:vs\.?|v\.?)\s+(.+?)\s+end in a draw", q, re.IGNORECASE)
    if m:
        a = re.sub(r"^(?:will|can)\s+", "", m.group(1).strip(), flags=re.IGNORECASE)
        return (a, m.group(2).strip(), "draw")

    # "Will [Team] win/beat/defeat [on date / against / ...]"
    m = re.search(r"(?:will|can)\s+(.+?)\s+(?:win|beat|defeat|to win)", q, re.IGNORECASE)
    if m:
        team = m.group(1).strip()
        team = re.sub(r"\s+on\s+\d{4}-\d{2}-\d{2}$", "", team)
        team = re.sub(r"\s+on\s+\w+\s+\d+$", "", team)
        team = re.sub(r"\s+the\s+\w+$", "", team)
        return (team, "", "")

    # "[Team] to win [Tournament]"
    m = re.search(r"(.+?)\s+to\s+win", q, re.IGNORECASE)
    if m:
        return (m.group(1).strip(), "", "")

    # "Who will win: [A] vs [B]" or "[A] vs. [B]"
    m = re.search(r"(?:who will win[:\s]+)?(.+?)\s+(?:vs\.?|v\.?)\s+(.+?)(?:\s*[:\-?]|$)", q, re.IGNORECASE)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        b = re.sub(r"\s+end in a draw$", "", b, flags=re.IGNORECASE)
        a = re.sub(r"^(?:will|can)\s+", "", a, flags=re.IGNORECASE)
        return (a, b, "")

    # "[A] - [B]" (common in soccer: "Real Madrid - Barcelona")
    m = re.search(r"^(.+?)\s+[-–—]\s+(.+?)$", q)
    if m:
        return (m.group(1).strip(), m.group(2).strip(), "")

    return ("", "", "")


class OddsEngine:
    """Compare bookmaker odds with Polymarket prices."""

    def __init__(self, cfg: dict) -> None:
        odds_cfg = cfg.get("odds_engine", {})
        self._api_key = odds_cfg.get("api_key") or os.getenv("ODDS_API_KEY", "")
        self._min_edge = odds_cfg.get("min_edge", 0.05)
        self._bookmakers = odds_cfg.get("bookmakers", ["pinnacle", "bet365"])
        self._match_threshold = odds_cfg.get("match_threshold", 0.55)
        self._requests_remaining = None
        self._last_fetch = 0.0
        self._cache: dict[str, list[BookmakerOdds]] = {}
        self._cache_ttl = odds_cfg.get("cache_ttl", 300)
        self._dynamic_sports: list[str] = []
        self._last_sport_discovery = 0.0
        self._sport_discovery_ttl = 3600.0

    @property
    def api_key_set(self) -> bool:
        return bool(self._api_key)

    @property
    def requests_remaining(self) -> int | None:
        return self._requests_remaining

    def fetch_odds(self, sport_key: str) -> list[BookmakerOdds]:
        """Fetch odds for a sport from The Odds API."""
        if not self._api_key:
            log.warning("ODDS_API_KEY not set — cannot fetch bookmaker odds")
            return []

        cache_key = sport_key
        if cache_key in self._cache and (time.time() - self._last_fetch) < self._cache_ttl:
            return self._cache[cache_key]

        try:
            resp = requests.get(
                f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": "eu,uk",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                    "bookmakers": ",".join(self._bookmakers),
                },
                timeout=15,
            )
            resp.raise_for_status()

            self._requests_remaining = int(resp.headers.get("x-requests-remaining", -1))
            if self._requests_remaining is not None and self._requests_remaining < 10:
                log.warning(f"Odds API: only {self._requests_remaining} requests remaining")

            events = resp.json()
            results: list[BookmakerOdds] = []

            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                commence = event.get("commence_time", "")
                sport = event.get("sport_key", sport_key)

                for bk in event.get("bookmakers", []):
                    bk_name = bk.get("key", "")
                    for market in bk.get("markets", []):
                        if market.get("key") != "h2h":
                            continue

                        outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                        home_odds = outcomes.get(home, 0)
                        away_odds = outcomes.get(away, 0)
                        draw_odds = outcomes.get("Draw", 0)

                        raw_probs = [decimal_to_prob(home_odds), decimal_to_prob(away_odds)]
                        if draw_odds > 0:
                            raw_probs.append(decimal_to_prob(draw_odds))

                        fair = remove_vig(raw_probs)

                        results.append(BookmakerOdds(
                            sport=sport,
                            home_team=home,
                            away_team=away,
                            commence_time=commence,
                            home_prob=fair[0],
                            away_prob=fair[1],
                            draw_prob=fair[2] if len(fair) > 2 else 0.0,
                            bookmaker=bk_name,
                            raw_odds=outcomes,
                        ))

            self._cache[cache_key] = results
            self._last_fetch = time.time()
            log.info(f"Fetched {len(results)} odds entries for {sport_key} (API remaining: {self._requests_remaining})")
            return results

        except Exception as e:
            log.error(f"Failed to fetch odds for {sport_key}: {e}")
            return []

    def discover_active_sports(self) -> list[str]:
        """Auto-discover active sports from The Odds API.

        Runs at most once per hour. Catches tournaments not in SPORT_KEYS
        (e.g., tennis_atp_washington_open, new boxing events).
        """
        if time.time() - self._last_sport_discovery < self._sport_discovery_ttl:
            return self._dynamic_sports

        active = self.fetch_available_sports()
        if not active:
            return self._dynamic_sports

        new_keys = []
        static_set = set(SPORT_KEYS)
        for sport in active:
            key = sport.get("key", "")
            if not key or key in static_set:
                continue
            if any(prefix in key for prefix in ("tennis_", "soccer_", "basketball_",
                                                  "boxing_", "mma_", "baseball_",
                                                  "americanfootball_", "icehockey_")):
                new_keys.append(key)

        if new_keys != self._dynamic_sports:
            added = set(new_keys) - set(self._dynamic_sports)
            if added:
                log.info(f"Discovered {len(added)} new active sports: {', '.join(sorted(added))}")
            self._dynamic_sports = new_keys

        self._last_sport_discovery = time.time()
        return self._dynamic_sports

    def fetch_all_odds(self, max_api_calls: int = 8) -> list[BookmakerOdds]:
        """Fetch odds across tracked sports, rotating to conserve API quota.

        With 500 requests/month and 5-min intervals, we can afford ~7 calls/cycle.
        Prioritize sports with the most Polymarket markets.
        Uses dynamic sport discovery to catch active tournaments not in SPORT_KEYS.
        """
        self.discover_active_sports()

        all_odds: list[BookmakerOdds] = []
        calls = 0

        # Return cached odds if still fresh
        fresh = [k for k, v in self._cache.items() if (time.time() - self._last_fetch) < self._cache_ttl]
        for k in fresh:
            all_odds.extend(self._cache[k])

        # Merge dynamic sports (auto-discovered) with static list
        sport_keys = list(SPORT_KEYS)
        if self._dynamic_sports:
            for sk in self._dynamic_sports:
                if sk not in sport_keys:
                    sport_keys.append(sk)

        priority = [
            "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
            "soccer_germany_bundesliga", "baseball_mlb", "basketball_wnba",
            "mma_mixed_martial_arts",
        ]
        remaining = [k for k in sport_keys if k not in priority]

        if not hasattr(self, "_scan_offset"):
            self._scan_offset = 0

        # Fetch priority sports + a rotating slice of the rest
        batch_size = max(1, max_api_calls - len(priority))
        rotate_slice = remaining[self._scan_offset:self._scan_offset + batch_size]
        self._scan_offset = (self._scan_offset + batch_size) % max(1, len(remaining))

        for sport in priority + rotate_slice:
            if sport in fresh:
                continue
            if calls >= max_api_calls:
                break
            odds = self.fetch_odds(sport)
            all_odds.extend(odds)
            calls += 1

        return all_odds

    def fetch_available_sports(self) -> list[dict]:
        """Fetch list of available sports with active events."""
        if not self._api_key:
            return []
        try:
            resp = requests.get(
                f"{ODDS_API_BASE}/sports",
                params={"apiKey": self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            return [s for s in resp.json() if s.get("active")]
        except Exception as e:
            log.error(f"Failed to fetch sports list: {e}")
            return []

    def match_polymarket(
        self, poly_markets: list[dict], bookmaker_odds: list[BookmakerOdds]
    ) -> list[tuple[dict, BookmakerOdds, str]]:
        """Match Polymarket markets to bookmaker events.

        Returns: [(poly_market, bookmaker_odds, matched_outcome), ...]
        matched_outcome is "home", "away", or "draw"
        """
        matches: list[tuple[dict, BookmakerOdds, str]] = []

        for pm in poly_markets:
            question = pm.get("question", "")
            best_match = None
            best_score = 0.0
            best_outcome = ""

            team_a, team_b, hint = _extract_team_from_question(question)

            for bk in bookmaker_odds:
                # If extraction hinted "draw", prioritize draw matching
                if hint == "draw" and bk.draw_prob > 0 and team_a:
                    team_sim = max(
                        _contains_name(team_a, bk.home_team),
                        _contains_name(team_a, bk.away_team),
                    )
                    if team_b:
                        team_sim = max(team_sim,
                            _contains_name(team_b, bk.home_team),
                            _contains_name(team_b, bk.away_team),
                        )
                    if team_sim >= self._match_threshold and team_sim >= best_score:
                        best_score = team_sim
                        best_match = bk
                        best_outcome = "draw"

                # Strategy 1: extracted team(s) vs bookmaker teams
                if team_a and not team_b:
                    home_sim = max(
                        _contains_name(team_a, bk.home_team),
                        _similarity(bk.home_team, team_a),
                    )
                    away_sim = max(
                        _contains_name(team_a, bk.away_team),
                        _similarity(bk.away_team, team_a),
                    )

                    if home_sim > away_sim and home_sim > self._match_threshold:
                        if home_sim > best_score:
                            best_score = home_sim
                            best_match = bk
                            best_outcome = "home"
                    elif away_sim > self._match_threshold:
                        if away_sim > best_score:
                            best_score = away_sim
                            best_match = bk
                            best_outcome = "away"

                elif team_a and team_b:
                    score_ab = (
                        max(_contains_name(team_a, bk.home_team), _similarity(bk.home_team, team_a))
                        + max(_contains_name(team_b, bk.away_team), _similarity(bk.away_team, team_b))
                    ) / 2
                    score_ba = (
                        max(_contains_name(team_b, bk.home_team), _similarity(bk.home_team, team_b))
                        + max(_contains_name(team_a, bk.away_team), _similarity(bk.away_team, team_a))
                    ) / 2
                    score = max(score_ab, score_ba)

                    if score > best_score and score > self._match_threshold:
                        best_score = score
                        best_match = bk
                        best_outcome = "h2h"

                # Strategy 2: substring check (bookmaker team name found in question)
                home_in_q = _contains_name(question, bk.home_team)
                away_in_q = _contains_name(question, bk.away_team)

                if home_in_q > self._match_threshold and home_in_q > best_score:
                    best_score = home_in_q
                    best_match = bk
                    best_outcome = "home"
                if away_in_q > self._match_threshold and away_in_q > best_score:
                    best_score = away_in_q
                    best_match = bk
                    best_outcome = "away"

                # Strategy 3: draw detection (uses >= so draw wins ties)
                draw_match = re.search(r"end in a draw|draw\??$", question, re.IGNORECASE)
                if draw_match and bk.draw_prob > 0:
                    team_sim = max(
                        _contains_name(question, bk.home_team),
                        _contains_name(question, bk.away_team),
                    )
                    if team_sim > self._match_threshold and team_sim >= best_score:
                        best_score = team_sim
                        best_match = bk
                        best_outcome = "draw"

            if best_match and best_score > self._match_threshold:
                matches.append((pm, best_match, best_outcome))

        log.info(f"Matched {len(matches)}/{len(poly_markets)} Polymarket markets to bookmaker events")
        return matches

    def find_edges(
        self, poly_markets: list[dict], bookmaker_odds: list[BookmakerOdds]
    ) -> list[OddsEdge]:
        """Find mispriced markets where Polymarket disagrees with bookmakers."""
        matched = self.match_polymarket(poly_markets, bookmaker_odds)
        edges: list[OddsEdge] = []

        for pm, bk, outcome_type in matched:
            import json as _json
            raw_prices = pm.get("outcomePrices", [0.5, 0.5])
            if isinstance(raw_prices, str):
                try:
                    raw_prices = _json.loads(raw_prices)
                except Exception:
                    raw_prices = [0.5, 0.5]
            yes_price = float(pm.get("yes_price", raw_prices[0] if raw_prices else 0.5) or 0.5)
            question = pm.get("question", "")

            if outcome_type == "home":
                fair_prob = bk.home_prob
            elif outcome_type == "away":
                fair_prob = bk.away_prob
            elif outcome_type == "draw":
                fair_prob = bk.draw_prob
            elif outcome_type == "h2h":
                fair_prob = bk.home_prob
            else:
                continue

            # Check if "No" is the answer (e.g., "Will X win?" → betting No)
            is_no_market = "win" in question.lower() and outcome_type in ("home", "away")

            poly_prob = yes_price
            edge = fair_prob - poly_prob

            if abs(edge) >= self._min_edge:
                if edge > 0:
                    side = "BUY YES"
                    confidence = "high" if edge > 0.10 else "medium" if edge > 0.07 else "low"
                else:
                    side = "BUY NO"
                    edge = abs(edge)
                    fair_prob = 1.0 - fair_prob
                    poly_prob = 1.0 - yes_price
                    confidence = "high" if edge > 0.10 else "medium" if edge > 0.07 else "low"

                edges.append(OddsEdge(
                    polymarket_question=question,
                    polymarket_price=poly_prob,
                    bookmaker_prob=fair_prob,
                    edge=round(edge, 4),
                    side=side,
                    sport=bk.sport,
                    home_team=bk.home_team,
                    away_team=bk.away_team,
                    bookmaker=bk.bookmaker,
                    confidence=confidence,
                    poly_condition_id=pm.get("condition_id", pm.get("conditionId", "")),
                    poly_token_id=pm.get("yes_token_id", ""),
                ))

        edges.sort(key=lambda e: e.edge, reverse=True)
        return edges

    def format_report(self, edges: list[OddsEdge]) -> str:
        lines = [
            "# Odds Comparison Report",
            "",
            f"Found {len(edges)} mispriced markets (edge >= {self._min_edge:.0%})",
            "",
        ]
        if not edges:
            lines.append("No actionable edges found.")
            return "\n".join(lines)

        lines.extend([
            f"{'Market':<45} {'Side':<10} {'Poly':>6} {'Book':>6} {'Edge':>6} {'Conf':<6} {'Source':<12}",
            "─" * 95,
        ])
        for e in edges[:20]:
            q = e.polymarket_question[:43]
            lines.append(
                f"{q:<45} {e.side:<10} {e.polymarket_price:.1%}  {e.bookmaker_prob:.1%}  "
                f"{e.edge:+.1%}  {e.confidence:<6} {e.bookmaker:<12}"
            )
        if len(edges) > 20:
            lines.append(f"... and {len(edges) - 20} more")

        return "\n".join(lines)
