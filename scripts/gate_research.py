"""Deep research: what can this Gate.io account do to make money."""

import requests, hashlib, hmac, time, json

key = "8233cab99779979a7528562ffeedf162"
secret = "ee53ab3fe248c72c68177ae0bf7eb1aaae6fb00967a2bc47313f8c68cf3b066a"
host = "api.gateio.ws"


def gate_get(path, query=""):
    ts = str(int(time.time()))
    bh = hashlib.sha512(b"").hexdigest()
    msg = "GET\n" + path + "\n" + query + "\n" + bh + "\n" + ts
    sign = hmac.new(secret.encode(), msg.encode(), hashlib.sha512).hexdigest()
    url = "https://" + host + path
    if query:
        url = url + "?" + query
    r = requests.get(url, headers={"KEY": key, "SIGN": sign, "Timestamp": ts}, timeout=10)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"error": r.text[:200]}


def gate_public(path, query=""):
    url = "https://" + host + path
    if query:
        url = url + "?" + query
    r = requests.get(url, timeout=10)
    try:
        return r.json()
    except Exception:
        return []


# ============================================
# 1. ALL perp contracts funding rates (top 20)
# ============================================
print("=" * 70)
print("TOP 20 FUNDING RATES (positive = short earns)")
print("=" * 70)

contracts = gate_public("/api/v4/futures/usdt/contracts")
rates = []
for c in contracts:
    name = c.get("name", "")
    rate = float(c.get("funding_rate", 0))
    vol = float(c.get("trade_size", 0))
    mark = float(c.get("mark_price", 0))
    rates.append((name, rate, vol, mark))

rates.sort(key=lambda x: -x[1])
print(f"  {'Contract':<18} {'Rate/8h':>10} {'APY':>8} {'24h Vol':>14}")
print("  " + "-" * 54)
for name, rate, vol, mark in rates[:20]:
    apy = rate * 3 * 365 * 100
    print(f"  {name:<18} {rate*100:>+9.4f}% {apy:>+7.1f}% {vol:>14,.0f}")

print("\nBOTTOM 10 (most negative):")
print(f"  {'Contract':<18} {'Rate/8h':>10} {'APY':>8}")
print("  " + "-" * 40)
for name, rate, vol, mark in rates[-10:]:
    apy = rate * 3 * 365 * 100
    print(f"  {name:<18} {rate*100:>+9.4f}% {apy:>+7.1f}%")


# ============================================
# 2. HYPE — check if perp exists and its rate
# ============================================
print("\n" + "=" * 70)
print("HYPE ANALYSIS")
print("=" * 70)
hype_found = False
for name, rate, vol, mark in rates:
    if "HYPE" in name:
        apy = rate * 3 * 365 * 100
        print(f"  {name}: rate={rate*100:+.4f}%/8h  APY={apy:+.1f}%  price=${mark:.2f}")
        hype_found = True
if not hype_found:
    print("  No HYPE perpetual contract found")


# ============================================
# 3. Earn products for holdings
# ============================================
print("\n" + "=" * 70)
print("EARN PRODUCTS (for your holdings)")
print("=" * 70)

for coin in ["ETH", "HYPE", "BTC", "USDT", "GT"]:
    data = gate_public("/api/v4/earn/uni/lends/" + coin)
    if isinstance(data, list) and data:
        for item in data[:1]:
            apy = item.get("current_min_rate", "0")
            apy_float = float(apy) * 365 * 100
            print(f"  {coin} Earn: {apy_float:.2f}% APY")
    elif isinstance(data, dict):
        rate = data.get("current_min_rate", "0")
        apy_float = float(rate) * 365 * 100 if rate != "0" else 0
        print(f"  {coin} Earn: {apy_float:.2f}% APY")
    else:
        print(f"  {coin} Earn: not available")


# ============================================
# 4. Spot holdings with USD value
# ============================================
print("\n" + "=" * 70)
print("PORTFOLIO VALUE BREAKDOWN")
print("=" * 70)

code, spot_data = gate_get("/api/v4/spot/accounts")
tickers = gate_public("/api/v4/spot/tickers")
ticker_map = {}
for t in tickers:
    pair = t.get("currency_pair", "")
    last = t.get("last", "0")
    ticker_map[pair] = float(last)

holdings = []
if code == 200:
    for a in spot_data:
        cur = a["currency"]
        amt = float(a.get("available", 0)) + float(a.get("locked", 0))
        if amt <= 0.0001:
            continue
        usd = 0.0
        if cur == "USDT" or cur == "GUSD":
            usd = amt
        elif cur + "_USDT" in ticker_map:
            usd = amt * ticker_map[cur + "_USDT"]
        holdings.append((cur, amt, usd))

holdings.sort(key=lambda x: -x[2])
total_usd = sum(h[2] for h in holdings)

print(f"  {'Coin':<12} {'Amount':>15} {'USD Value':>12} {'%':>6}")
print("  " + "-" * 50)
for cur, amt, usd in holdings:
    if usd >= 1.0:
        pct = usd / total_usd * 100 if total_usd > 0 else 0
        print(f"  {cur:<12} {amt:>15.4f} {usd:>11,.2f}  {pct:>5.1f}%")
print("  " + "-" * 50)

dust_usd = sum(h[2] for h in holdings if 0 < h[2] < 1.0)
dust_count = sum(1 for h in holdings if 0 < h[2] < 1.0)
unknown = sum(1 for h in holdings if h[2] == 0 and h[1] > 0)
print(f"  Dust (<$1): {dust_count} coins, ~${dust_usd:.2f}")
print(f"  No price data: {unknown} coins")
print(f"  TOTAL: ${total_usd:,.2f}")


# ============================================
# 5. Funding arb opportunities (spot + short)
# ============================================
print("\n" + "=" * 70)
print("FUNDING ARB OPPORTUNITIES (buy spot + short perp)")
print("=" * 70)
print("  Coins with rate > 0.005%/8h (5.5% APY) and decent volume:")
print(f"  {'Contract':<15} {'Rate/8h':>10} {'APY':>8} {'Action'}")
print("  " + "-" * 50)

for name, rate, vol, mark in rates:
    if rate > 0.00005 and vol > 100000:
        coin = name.replace("_USDT", "")
        apy = rate * 3 * 365 * 100
        print(f"  {coin:<15} {rate*100:>+9.4f}% {apy:>+7.1f}%  Buy spot + short perp")
