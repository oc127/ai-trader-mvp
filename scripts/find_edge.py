"""Find mispriced Polymarket markets by comparing with bookmaker odds.

Usage:
    uv run python scripts/find_edge.py                # scan all sports
    uv run python scripts/find_edge.py --sport soccer  # scan soccer only
    uv run python scripts/find_edge.py --sports        # list available sports
    uv run python scripts/find_edge.py --min-edge 0.08 # 8% minimum edge

Requires: ODDS_API_KEY env var (free at https://the-odds-api.com/)
"""

import argparse
import os
import sys

import requests


ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GAMMA_API = "https://gamma-api.polymarket.com"

SPORT_GROUPS = {
    "soccer": [
        "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
        "soccer_germany_bundesliga", "soccer_france_ligue_one",
        "soccer_uefa_champs_league", "soccer_uefa_europa_league",
        "soccer_brazil_serie_a", "soccer_mls",
        "soccer_sweden_allsvenskan", "soccer_korea_kleague1",
        "soccer_denmark_superliga", "soccer_norway_eliteserien",
        "soccer_finland_veikkausliiga",
    ],
    "tennis": [
        "tennis_atp_french_open", "tennis_atp_us_open", "tennis_atp_wimbledon",
        "tennis_wta_french_open", "tennis_wta_us_open", "tennis_wta_wimbledon",
    ],
    "us_sports": [
        "americanfootball_nfl", "basketball_nba", "basketball_wnba",
        "baseball_mlb", "icehockey_nhl",
    ],
    "mma": ["mma_mixed_martial_arts"],
}


def get_api_key() -> str:
    key = os.getenv("ODDS_API_KEY", "")
    if not key:
        print("ERROR: ODDS_API_KEY not set.")
        print("Get a free key at: https://the-odds-api.com/")
        print("Then: export ODDS_API_KEY=your_key_here")
        print("  or add ODDS_API_KEY=your_key to .env")
        sys.exit(1)
    return key


def list_sports(api_key: str):
    resp = requests.get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key}, timeout=15)
    resp.raise_for_status()
    sports = [s for s in resp.json() if s.get("active")]
    print(f"\n  Available sports ({len(sports)} active):\n")
    for s in sorted(sports, key=lambda x: x.get("group", "")):
        print(f"  {s['key']:<45} {s.get('title', '')}")
    remaining = resp.headers.get("x-requests-remaining", "?")
    print(f"\n  API requests remaining: {remaining}")


def fetch_odds(api_key: str, sport_key: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            params={
                "apiKey": api_key,
                "regions": "eu,uk",
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        if resp.status_code == 422:
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Error fetching {sport_key}: {e}")
        return []


def fetch_polymarket_sports() -> list[dict]:
    """Fetch active sports markets from Polymarket."""
    try:
        all_markets = []
        offset = 0
        while True:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                    "offset": offset,
                },
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_markets.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
            if offset > 500:
                break

        sports_keywords = [
            "win on 20", "vs.", "vs ", "O/U ", "Over/Under",
            "Spread:", "draw", "goals", "points", "sets",
            "ATP", "WTA", "UFC", "NBA", "NFL", "NHL", "MLB", "MLS",
            "Premier League", "La Liga", "Serie A", "Bundesliga",
            "Champions League", "WNBA",
        ]

        sports_markets = []
        for m in all_markets:
            q = m.get("question", "")
            if any(kw.lower() in q.lower() for kw in sports_keywords):
                sports_markets.append(m)

        return sports_markets
    except Exception as e:
        print(f"  Error fetching Polymarket markets: {e}")
        return []


def decimal_to_prob(odds: float) -> float:
    if odds <= 1.0:
        return 0.0
    return 1.0 / odds


def remove_vig(probs: list[float]) -> list[float]:
    total = sum(probs)
    if total <= 0:
        return probs
    return [p / total for p in probs]


def normalize(name: str) -> str:
    import re
    name = name.lower().strip()
    name = re.sub(r"\b(fc|sc|cf|ac|as|us|ss|fk|if|bk|sk|afc|ssc)\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def find_edges(poly_markets: list[dict], odds_events: list[dict], min_edge: float) -> list[dict]:
    """Find mispriced markets."""
    import re

    edges = []

    # Build bookmaker fair probs for each event
    bk_events = []
    for event in odds_events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        sport = event.get("sport_key", "")
        commence = event.get("commence_time", "")

        for bk in event.get("bookmakers", []):
            bk_name = bk.get("key", "")
            for market in bk.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                home_odds = outcomes.get(home, 0)
                away_odds = outcomes.get(away, 0)
                draw_odds = outcomes.get("Draw", 0)

                raw = [decimal_to_prob(home_odds), decimal_to_prob(away_odds)]
                if draw_odds > 0:
                    raw.append(decimal_to_prob(draw_odds))
                fair = remove_vig(raw)

                bk_events.append({
                    "home": home, "away": away, "sport": sport,
                    "commence": commence, "bookmaker": bk_name,
                    "home_prob": fair[0], "away_prob": fair[1],
                    "draw_prob": fair[2] if len(fair) > 2 else 0,
                    "raw": outcomes,
                })

    # Match each Polymarket market to bookmaker events
    for pm in poly_markets:
        question = pm.get("question", "")
        yes_price_raw = pm.get("outcomePrices", None)
        if yes_price_raw and isinstance(yes_price_raw, (list, str)):
            if isinstance(yes_price_raw, str):
                import json
                try:
                    yes_price_raw = json.loads(yes_price_raw)
                except Exception:
                    yes_price_raw = [0.5, 0.5]
            yes_price = float(yes_price_raw[0]) if yes_price_raw else 0.5
        else:
            yes_price = 0.5

        best = None
        best_score = 0
        best_outcome = ""

        for bk in bk_events:
            # "Will X win" pattern
            win_match = re.search(r"will\s+(.+?)\s+win", question, re.IGNORECASE)
            if win_match:
                team = win_match.group(1).strip()
                # Remove date suffix like "on 2026-07-21?"
                team = re.sub(r"\s+on\s+\d{4}-\d{2}-\d{2}\??$", "", team).strip()
                home_sim = similarity(bk["home"], team)
                away_sim = similarity(bk["away"], team)

                if home_sim > away_sim and home_sim > 0.50 and home_sim > best_score:
                    best = bk
                    best_score = home_sim
                    best_outcome = "home_win"
                elif away_sim >= home_sim and away_sim > 0.50 and away_sim > best_score:
                    best = bk
                    best_score = away_sim
                    best_outcome = "away_win"

            # "X vs Y" pattern
            vs_match = re.search(r"(.+?)\s+vs\.?\s+(.+?)(?:\s*[:?]|$)", question, re.IGNORECASE)
            if vs_match:
                q_home = vs_match.group(1).strip()
                q_away = vs_match.group(2).strip()
                score = (similarity(bk["home"], q_home) + similarity(bk["away"], q_away)) / 2
                if score > 0.50 and score > best_score:
                    best = bk
                    best_score = score
                    best_outcome = "h2h_home"

            # "end in a draw" pattern
            if re.search(r"draw|end in a draw", question, re.IGNORECASE) and bk["draw_prob"] > 0:
                team_sim = max(similarity(bk["home"], question), similarity(bk["away"], question))
                if team_sim > 0.40 and team_sim > best_score:
                    best = bk
                    best_score = team_sim
                    best_outcome = "draw"

        if not best:
            continue

        # Calculate edge
        if best_outcome == "home_win":
            book_prob = best["home_prob"]
            poly_prob = yes_price
        elif best_outcome == "away_win":
            book_prob = best["away_prob"]
            poly_prob = yes_price
        elif best_outcome == "h2h_home":
            book_prob = best["home_prob"]
            poly_prob = yes_price
        elif best_outcome == "draw":
            book_prob = best["draw_prob"]
            poly_prob = yes_price
        else:
            continue

        edge = book_prob - poly_prob

        if abs(edge) >= min_edge:
            side = "BUY YES" if edge > 0 else "BUY NO"
            display_edge = abs(edge)

            edges.append({
                "question": question[:55],
                "side": side,
                "poly_price": poly_prob if edge > 0 else 1 - yes_price,
                "book_prob": book_prob if edge > 0 else 1 - book_prob,
                "edge": display_edge,
                "bookmaker": best["bookmaker"],
                "match": f"{best['home']} vs {best['away']}",
                "sport": best["sport"],
                "confidence": "HIGH" if display_edge > 0.10 else "MED" if display_edge > 0.07 else "LOW",
                "match_score": best_score,
                "condition_id": pm.get("conditionId", pm.get("condition_id", "")),
            })

    edges.sort(key=lambda e: e["edge"], reverse=True)
    return edges


def main():
    parser = argparse.ArgumentParser(description="Find mispriced Polymarket markets")
    parser.add_argument("--sport", type=str, help="Sport group: soccer, tennis, us_sports, mma, or specific key")
    parser.add_argument("--sports", action="store_true", help="List available sports")
    parser.add_argument("--min-edge", type=float, default=0.05, help="Minimum edge threshold (default: 0.05)")
    parser.add_argument("--raw", action="store_true", help="Show raw odds data")
    args = parser.parse_args()

    api_key = get_api_key()

    if args.sports:
        list_sports(api_key)
        return

    # Determine which sports to scan
    if args.sport:
        if args.sport in SPORT_GROUPS:
            sport_keys = SPORT_GROUPS[args.sport]
        else:
            sport_keys = [args.sport]
    else:
        sport_keys = []
        for group in SPORT_GROUPS.values():
            sport_keys.extend(group)

    # Fetch bookmaker odds
    print(f"\n  Fetching bookmaker odds for {len(sport_keys)} sport(s)...")
    all_events = []
    for sk in sport_keys:
        events = fetch_odds(api_key, sk)
        if events:
            all_events.extend(events)
            print(f"  {sk}: {len(events)} events")

    if not all_events:
        print("  No bookmaker events found.")
        return

    print(f"\n  Total: {len(all_events)} events with odds")

    if args.raw:
        print(f"\n  {'Event':<45} {'Home':>6} {'Draw':>6} {'Away':>6} {'Source':<12}")
        print(f"  {'─'*80}")
        seen = set()
        for ev in all_events:
            home = ev.get("home_team", "")
            away = ev.get("away_team", "")
            key = f"{home}_{away}"
            if key in seen:
                continue
            seen.add(key)
            for bk in ev.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                    h = outcomes.get(home, 0)
                    a = outcomes.get(away, 0)
                    d = outcomes.get("Draw", 0)
                    label = f"{home[:20]} vs {away[:20]}"
                    print(f"  {label:<45} {h:>6.2f} {d:>6.2f} {a:>6.2f} {bk['key']:<12}")
        return

    # Fetch Polymarket sports markets
    print(f"\n  Fetching Polymarket sports markets...")
    poly_markets = fetch_polymarket_sports()
    print(f"  Found {len(poly_markets)} sports markets on Polymarket")

    if not poly_markets:
        print("  No Polymarket sports markets to compare.")
        return

    # Find edges
    edges = find_edges(poly_markets, all_events, args.min_edge)

    print(f"\n{'='*95}")
    print(f"  ODDS COMPARISON — {len(edges)} mispriced markets (edge >= {args.min_edge:.0%})")
    print(f"{'='*95}")

    if not edges:
        print("  No edges found. Polymarket prices match bookmakers.")
        return

    print(f"\n  {'Market':<45} {'Side':<10} {'Poly':>6} {'Book':>6} {'Edge':>6} {'Conf':<5} {'Bookmaker':<12}")
    print(f"  {'─'*90}")

    for e in edges[:30]:
        print(
            f"  {e['question']:<45} {e['side']:<10} "
            f"{e['poly_price']:.1%}  {e['book_prob']:.1%}  "
            f"{e['edge']:+.1%}  {e['confidence']:<5} {e['bookmaker']:<12}"
        )

    if len(edges) > 30:
        print(f"\n  ... and {len(edges) - 30} more")

    # Summary
    total_edge_value = sum(e["edge"] for e in edges)
    high_conf = sum(1 for e in edges if e["confidence"] == "HIGH")
    print(f"\n  Summary: {len(edges)} edges | {high_conf} high confidence | avg edge: {total_edge_value/len(edges):.1%}")
    print(f"\n  To act on these: review each match, verify the edge is real, then trade on Polymarket.")
    print(f"  Remember: bookmaker odds are the 'truth' — they have more volume and better models.\n")


if __name__ == "__main__":
    main()
