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


print("=== SPOT BALANCES ===")
c, d = gate_get("/api/v4/spot/accounts")
if c == 200:
    for a in d:
        t = float(a.get("available", 0)) + float(a.get("locked", 0))
        if t > 0:
            print("  " + a["currency"] + ": " + str(round(t, 6)))

print("\n=== FUTURES POSITIONS ===")
c, d = gate_get("/api/v4/futures/usdt/positions")
if c == 200:
    found = False
    for p in d:
        if p.get("size", 0) != 0:
            found = True
            print("  " + p["contract"] + " size=" + str(p["size"]) + " pnl=" + str(p.get("unrealised_pnl")))
    if not found:
        print("  No positions")

print("\n=== UNIFIED ACCOUNT ===")
c, d = gate_get("/api/v4/unified/accounts")
if c == 200:
    total = d.get("total", "")
    avail = d.get("available", "")
    print("  total=" + str(total) + " available=" + str(avail))
    balances = d.get("balances", {})
    for cur, info in balances.items():
        amt = float(info.get("available", 0))
        if amt > 0.0001:
            print("  " + cur + ": " + str(amt))
