# Binance Crypto Day Trader Swarm

An autonomous swarm of **12 specialized crypto day trading agents** operating on Binance, featuring real-time market scanning, orderbook depth intelligence, 10,000 Monte Carlo bootstrap simulations, dynamic ATR brackets, risk compliance audits, and an interactive **Binance-Themed Virtual Office GUI** (Port 8766).

Built entirely with standard library Python (no third-party pip dependencies required) and interfacing with Binance's official public REST APIs.

---

## Architecture: The 12 Specialized Crypto Agents

The swarm divides the cognitive workload of high-frequency cryptocurrency trading across three collaborative pods (4 agents per pod):

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      BINANCE CRYPTO DAY TRADER SWARM                             │
└──────────────────────────────────────────────────────────────────────────────────┘
         │
         ├─── POD 1: MARKET INTELLIGENCE & DISCOVERY
         │    ├── 📡 @scanner      (Binance 24h Market Scanner & Breakouts)
         │    ├── 📰 @news         (Crypto Catalysts & Breaking News Monitor)
         │    ├── 🐋 @whale        (Whale & Funding Rate Tracker)
         │    └── 🔬 @analyst      (Orderbook Depth & Market Structure)
         │
         ├─── POD 2: STRATEGY & QUANT ENGINE
         │    ├── ⏱️ @entry        (5m/15m Kline Entry Timer)
         │    ├── 🎯 @exit         (Dynamic ATR Bracket & Exit Planner)
         │    ├── ⏳ @timemachine  (10,000 Monte Carlo Bootstrap Replays)
         │    └── 💰 @budget       (Crypto Lot Sizing & Min Notional Filter)
         │
         └─── POD 3: RISK, EXECUTION & COMPLIANCE
              ├── 🛡️ @risk         (Risk & Compliance Officer)
              ├── ⚡ @broker       (Binance REST Order Execution & Paper Broker)
              ├── 🔔 @alerts       (Stop/Target & Volatility Alarms)
              └── 📊 @reporter     (Portfolio Logger & Executive Reporting)
```

### Agent Roles & Specifications

| Agent | Icon | Pod | Primary Mission | Endpoint / Method |
|---|---|---|---|---|
| **@scanner** | 📡 | Intel | Scans all monitored USDT pairs on Binance for 24h volume surges, price breakouts, and opening range expansion. | `GET /api/v3/ticker/24hr` |
| **@news** | 📰 | Intel | Monitors crypto news feeds, regulatory headlines, and protocol updates to avoid event-risk. | Public crypto feeds |
| **@whale** | 🐋 | Intel | Tracks Binance perpetual funding rates, top trader Long/Short sentiment ratios, and large transfer inflows. | `GET /fapi/v1/fundingRate` |
| **@analyst** | 🔬 | Intel | Inspects the top 20 bid/ask levels of the Binance orderbook to verify tight spreads (<0.20%) and liquidity walls. | `GET /api/v3/depth` |
| **@entry** | ⏱️ | Strategy | Monitors 5m and 15m Binance klines bar-by-bar, validating breakout confirmation closes above key resistances. | `GET /api/v3/klines` |
| **@exit** | 🎯 | Strategy | Plans dynamic brackets: Stop Loss placed at swing low / ATR spread, and Take Profit targets at 1.5R (1R) and 2.5R (2R). | Dynamic ATR Bracket |
| **@timemachine**| ⏳ | Strategy | Replays 10,000 Monte Carlo bootstrap sessions from 500-1000 historical 5m bars, computing empirical win probabilities before a dollar is risked. | Bootstrap Re-sampling |
| **@budget** | 💰 | Strategy | Sizes position quantities respecting the $100 USDT account, $2.50 max loss, 50% max position cap, and Binance's $5.00 Min Notional rule. | Capital & Lot Sizing |
| **@risk** | 🛡️ | Risk | Compliance gatekeeper auditing trade limits, daily loss threshold ($7.50 max), max 5 trades/day, and minimum 1.5 Reward:Risk ratio. | PASS / BLOCK Audit |
| **@broker** | ⚡ | Risk | Routes orders in Paper Trading mode (default, simulated instant fills) or live signed Binance REST API orders via HMAC-SHA256. | `POST /api/v3/order` |
| **@alerts** | 🔔 | Risk | Watches real-time prices against active stop loss and take profit brackets, sounding alarms upon triggering. | Price Threshold Daemon |
| **@reporter** | 📊 | Risk | Logs every trade execution to `trader/trade_history.json`, calculates equity curves, win rates, and exportable logs. | Audit Trail Logger |

---

## Capital & Risk Management Rules

The system is configured by default with institutional-grade capital preservation rules:

* **Starting Capital:** `$100.00 USDT`
* **Maximum Risk Per Trade:** `$2.50 USDT` (2.5% of account balance)
* **Maximum Daily Loss Limit:** `$7.50 USDT` (Circuit breaker halts trading after 3 consecutive maximum losses)
* **Maximum Daily Trades:** `5 trades` per session
* **Minimum Reward-to-Risk (R:R):** `1.50` (Target profit must be at least 1.5x the dollar risk to the stop)
* **Mandatory Stop-Loss:** Every trade must have an explicit stop price; naked positions are strictly blocked by `@risk`.
* **Binance Min Notional Filter:** Trades below `$5.00 USDT` total notional value are rejected to comply with exchange rules.
* **Maximum Single Position Allocation:** `50%` of portfolio ($50.00 USDT max per trade on a $100 account).
* **Crypto Decimal Sizing:** Quantities are automatically rounded according to crypto lot precision (e.g., 4 decimals for SOL, 5 for ETH, 6 for BTC).

---

## 10,000 Monte Carlo Bootstrap Simulation Engine

Before placing any trade, `@timemachine` runs a comprehensive quantitative validation:
1. Fetches the latest 500-1,000 5-minute klines directly from Binance.
2. Extracts return distributions from recent candles during matching market hours.
3. Generates **10,000 simulated price paths** covering the next 48 bars (4 hours of trading).
4. Calculates the empirical probability of hitting the **Take Profit Target first** vs the **Stop Loss first**.
5. Discards setups where the Target First probability is below acceptable thresholds.

---

## Binance Virtual Office GUI (Port 8766)

The project includes an interactive web-based Virtual Office designed in **Binance Dark & Gold Neon** style (`#0b0e14`, `#F0B90B`, `#0ecb81`, `#f6465d`):

* **Animated Workstations:** 12 desks grouped into 3 Sector Pods, featuring animated dual-screen candlestick charts, worker avatar sprites, typing animations, and real-time speech bubbles.
* **Live Binance Ticker Tape:** Continuous neon marquee with real-time price updates for `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BNBUSDT`, `DOGEUSDT`, `AVAXUSDT`, `LINKUSDT`, `NEARUSDT`, and `SUIUSDT`.
* **World Trading Clocks:** Live clocks for UTC (Binance 24/7), Tokyo (JST), London (GMT), and New York (EST).
* **Telemetry Drawer:** Clicking any agent desk opens a slide-out telemetry drawer displaying the agent's active mission, REST latency, and the latest JSON output.
* **Direct Swarm Controls:**
  * `⚡ Escanear Binance`: Triggers background market scan.
  * `🎯 Ejecutar Paper`: Dispatches paper order execution.
  * `📊 Abrir Historial`: Opens trade history log.
  * `🔄 Actualizar`: Refreshes live balances and metrics.

### Launching the Virtual Office

#### Option 1: One-Click Desktop Launcher
Double-click `Iniciar_Oficina_Crypto.bat`. It will start the Python HTTP server on port 8766 and open the office in standalone App mode in Edge or Chrome.

#### Option 2: Command Line
```bash
python office_app.py
```
Then navigate to: `http://127.0.0.1:8766`

---

## REST API Endpoints

The virtual office server (`office_app.py`) exposes the following endpoints:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` or `/index.html` | Serves the Binance Virtual Office frontend. |
| `GET` | `/api/status` | Returns complete swarm status (config, portfolio, watchlist, plan, orders, history, active tickers, ping). |
| `GET` | `/api/tickers` | Returns fresh 24h ticker prices and % changes for tracked Binance pairs (with 6s server cache). |
| `POST` | `/api/run-scanner` | Executes `trader-scanner/scan.py` in an asynchronous background thread. |
| `POST` | `/api/execute-paper` | Executes `trader-broker/broker.py` in an asynchronous background thread. |
| `POST` | `/api/run-agent` | Accepts `{"agent": "<id>"}` to run any of the 12 individual agent scripts. |
| `POST` | `/api/open-history` | Opens the local trade history or summary file on the host operating system. |

---

## Directory Structure

```
binance-crypto-trader-swarm/
├── Iniciar_Oficina_Crypto.bat    # Windows one-click app mode launcher
├── office_app.py                 # Multi-threaded HTTP server & REST API (Port 8766)
├── virtual_office.html           # Animated Binance Golden Neon UI
├── README.md                     # System documentation
├── trader/                       # Central shared state directory
│   ├── config.json               # Capital, risk limits & exchange parameters
│   ├── pairs.txt                 # Monitored USDT trading pairs
│   ├── portfolio.json            # Cash balance, active positions, portfolio value
│   ├── watchlist.json            # Scanner candidates and breakout alerts
│   ├── entry.json                # Triggered entry signals
│   ├── plan.json                 # Bracket plan (Entry, Stop Loss, Target 1R/2R)
│   ├── orders.json               # Sized bracket order tickets
│   ├── timemachine.json          # Monte Carlo 10,000 replay results
│   └── trade_history.json        # Execution history log
├── trader-scanner/scan.py        # Binance 24h market screener
├── trader-news/news.py           # Catalyst & breaking news monitor
├── trader-whale/whale.py         # Funding rate & whale sentiment tracker
├── trader-analyst/analyst.py     # 20-level orderbook depth analyst
├── trader-entry/entry.py         # 5m/15m kline entry trigger timer
├── trader-exit/exit.py           # ATR bracket stop & profit target planner
├── trader-timemachine/timemachine.py # 10,000 bootstrap Monte Carlo engine
├── trader-budget/size.py         # Crypto lot sizing & Binance min notional
├── trader-risk/check.py          # Risk compliance audit (PASS / BLOCK)
├── trader-broker/broker.py       # Paper trading & HMAC Binance order broker
├── trader-alerts/alerts.py       # Position bracket price monitoring
├── trader-reporter/report.py     # History logger & KPI reporter
└── tests/                        # Verification & integration test suite
```

---

## Configuration Reference (`trader/config.json`)

```json
{
  "budget": 100.0,
  "currency": "USDT",
  "exchange": "binance",
  "paper": true,
  "binance_api_key": "",
  "binance_api_secret": "",
  "use_testnet": false,
  "max_loss_per_trade_usd": 2.5,
  "max_daily_loss_usd": 7.5,
  "max_trades_per_day": 5,
  "min_reward_risk": 1.5,
  "require_stop": true,
  "max_position_pct": 50,
  "min_notional_usdt": 5.0,
  "slippage_tolerance_pct": 0.2
}
```

* **`paper`**: When `true` (default), orders are executed entirely in the local paper simulation engine at live Binance prices without risking funds.
* **`use_testnet`**: Set to `true` if testing live API keys against the Binance Spot Testnet (`https://testnet.binance.vision`).

---

## CLI Manual Execution

Every agent can also be executed independently via standard Python:

```bash
# 1. Run Binance Market Scanner on tracked pairs
python trader-scanner/scan.py

# 2. Check Orderbook Depth and Spread for candidate pair
python trader-analyst/analyst.py --symbol SOLUSDT

# 3. Check Whale Funding Rates
python trader-whale/whale.py

# 4. Check 5m/15m Kline Entry Trigger
python trader-entry/entry.py --symbol SOLUSDT

# 5. Formulate Bracket Exit Plan
python trader-exit/exit.py --symbol SOLUSDT

# 6. Run 10,000 Monte Carlo Replays
python trader-timemachine/timemachine.py --symbol SOLUSDT

# 7. Size Position to $100 budget and $2.50 risk
python trader-budget/size.py

# 8. Run Pre-Trade Risk Audit
python trader-risk/check.py

# 9. Execute Order (Paper Trading)
python trader-broker/broker.py

# 10. Check Open Position Alerts
python trader-alerts/alerts.py
```

---

## Running Automated Tests

Run the test suite using Python's built-in `unittest` runner:

```bash
python -m unittest discover -s tests -v
```

---

## License & Safety Notice

* **Educational & Simulation Tool:** This software is designed for quantitative analysis, paper trading, and algorithmic simulation. Cryptocurrency markets carry extreme volatility.
* **Capital Protection by Default:** `"paper": true` is enforced by default. Never share private API keys or fund live accounts without thorough paper testing.
* **Standard Library Architecture:** Zero external pip dependencies required. Works out-of-the-box on standard Python 3.8+.
