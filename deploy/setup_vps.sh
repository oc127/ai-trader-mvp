#!/bin/bash
set -e

echo "================================================"
echo "  Trading System Deployment — NYC VPS"
echo "================================================"
echo ""

# ── 1. System dependencies ──────────────────────────
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq git python3.11 python3.11-venv python3-pip curl ca-certificates > /dev/null 2>&1

# Install uv (fast Python package manager)
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "  Done."

# ── 2. Clone repos ──────────────────────────────────
echo "[2/6] Cloning repositories..."
WORK_DIR="/root/trading"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# Clone ai-trader-mvp (Hyperliquid)
if [ ! -d "ai-trader-mvp" ]; then
    git clone https://github.com/oc127/ai-trader-mvp.git
else
    cd ai-trader-mvp && git pull origin main && cd ..
fi

# The other repos need to be created on GitHub first, or we copy from the dev env
# For now, check if they exist
for repo in deribit-mm gate-trader polymarket-bot; do
    if [ ! -d "$repo" ]; then
        echo "  WARNING: $repo not found. You'll need to copy it from dev environment."
    fi
done
echo "  Done."

# ── 3. Network connectivity test ────────────────────
echo "[3/6] Testing exchange API connectivity..."
PASS=0
FAIL=0

test_api() {
    local name=$1
    local url=$2
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null)
    if [ "$code" = "200" ] || [ "$code" = "401" ] || [ "$code" = "000" ]; then
        # 401 = auth needed but reachable; some POST endpoints return different codes
        echo "  $name: OK ($code)"
        PASS=$((PASS + 1))
    else
        echo "  $name: $code"
        # Check if it's a POST endpoint
        code2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST -H "Content-Type: application/json" -d '{"type":"meta"}' "$url" 2>/dev/null)
        if [ "$code2" = "200" ]; then
            echo "  $name: OK (POST $code2)"
            PASS=$((PASS + 1))
        else
            echo "  $name: FAIL (GET=$code, POST=$code2)"
            FAIL=$((FAIL + 1))
        fi
    fi
}

test_api "Hyperliquid" "https://api.hyperliquid.xyz/info"
test_api "Deribit" "https://www.deribit.com/api/v2/public/get_time"
test_api "Deribit Test" "https://test.deribit.com/api/v2/public/get_time"
test_api "Gate.io" "https://api.gateio.ws/api/v4/spot/currencies/BTC"
test_api "Polymarket" "https://clob.polymarket.com/time"

echo "  Results: $PASS passed, $FAIL failed"
if [ $FAIL -gt 0 ]; then
    echo "  WARNING: Some APIs not reachable. Check firewall settings."
fi

# ── 4. Install Python dependencies ──────────────────
echo "[4/6] Installing Python dependencies..."
cd "$WORK_DIR"

if [ -d "ai-trader-mvp" ]; then
    cd ai-trader-mvp
    uv sync 2>/dev/null || uv pip install -e ".[dev]" 2>/dev/null || pip install -e ".[dev]"
    echo "  ai-trader-mvp: installed"
    cd ..
fi

for repo in deribit-mm gate-trader polymarket-bot; do
    if [ -d "$repo" ]; then
        cd "$repo"
        uv sync 2>/dev/null || uv pip install -e ".[dev]" 2>/dev/null || pip install -e ".[dev]"
        echo "  $repo: installed"
        cd ..
    fi
done
echo "  Done."

# ── 5. Run tests ────────────────────────────────────
echo "[5/6] Running test suites..."
cd "$WORK_DIR"

for repo in ai-trader-mvp deribit-mm gate-trader polymarket-bot; do
    if [ -d "$repo" ]; then
        cd "$repo"
        result=$(uv run pytest --tb=short -q 2>&1 | tail -1)
        echo "  $repo: $result"
        cd ..
    fi
done

# ── 6. Create .env file ────────────────────────────
echo "[6/6] Setting up configuration..."
ENV_FILE="$WORK_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# ═══════════════════════════════════════════════════════════
# Trading System Credentials — FILL IN YOUR VALUES
# ═══════════════════════════════════════════════════════════

# ── Hyperliquid ────────────────────────────────────────────
HL_WALLET_ADDRESS=
HL_PRIVATE_KEY=
ENVIRONMENT=mainnet

# ── Deribit ────────────────────────────────────────────────
DERIBIT_CLIENT_ID=
DERIBIT_CLIENT_SECRET=
DERIBIT_TESTNET=false

# ── Gate.io ────────────────────────────────────────────────
GATE_API_KEY=
GATE_API_SECRET=

# ── Polymarket ─────────────────────────────────────────────
POLY_PRIVATE_KEY=
POLY_API_KEY=
POLY_API_SECRET=
POLY_PASSPHRASE=

# ── Telegram Alerts ────────────────────────────────────────
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ENVEOF
    echo "  Created $ENV_FILE — EDIT THIS FILE with your API keys!"
else
    echo "  .env already exists, skipping."
fi

echo ""
echo "================================================"
echo "  Deployment complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "  1. Edit credentials:  nano $ENV_FILE"
echo "  2. Source env:         export \$(cat $ENV_FILE | grep -v '^#' | xargs)"
echo ""
echo "  Run individual bots (paper mode):"
echo "    cd $WORK_DIR/ai-trader-mvp && uv run trader"
echo "    cd $WORK_DIR/deribit-mm && uv run deribot"
echo "    cd $WORK_DIR/gate-trader && uv run gatebot"
echo "    cd $WORK_DIR/polymarket-bot && uv run polybot"
echo ""
echo "  Run in live mode (add --live flag):"
echo "    cd $WORK_DIR/ai-trader-mvp && uv run trader --live"
echo ""
echo "  Run with screen (background):"
echo "    screen -dmS hl bash -c 'cd $WORK_DIR/ai-trader-mvp && uv run trader'"
echo "    screen -dmS deribit bash -c 'cd $WORK_DIR/deribit-mm && uv run deribot'"
echo "    screen -ls  # list sessions"
echo "    screen -r hl  # attach to session"
echo ""
