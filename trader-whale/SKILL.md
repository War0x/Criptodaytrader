---
name: trader-whale
description: Binance Futures funding rate and whale sentiment ratio tracker.
---

# Trader-Whale: Binance Whale & Funding Tracker

Monitors smart money flow, open interest sentiment, top trader position & account ratios, and 8-hour funding rates on Binance Futures.

## Capabilities

- **Real-Time 8h Funding Rate & APR**: Queries Binance Futures public funding endpoints (`/fapi/v1/fundingRate` and `/fapi/v1/premiumIndex`) to calculate 8-hour funding rates and annualized APR (`rate * 3 * 365 * 100`).
- **Next Funding Countdown**: Computes exact UTC timestamp and countdown in minutes until the next 8-hour funding fee settlement.
- **Top Trader Long/Short Position Ratio**: Analyzes top trader contract volume ratios (`/futures/data/topLongShortPositionRatio`).
- **Top Trader Long/Short Account Ratio**: Tracks net long vs net short accounts among top traders (`/futures/data/topLongShortAccountRatio`).
- **Open Interest & USDT Notional**: Computes total derivative market exposure in both contract units and live USDT notional value.
- **High-Performance Architecture**: 1-call bulk premium index lookup combined with concurrent multithreaded fetching across target symbols (`ThreadPoolExecutor`).
- **Resilient Fallback & Offline Engine**: Built-in fallback dataset and offline fixture loading (`--offline tests/fixtures/whale_data.json`) ensuring 100% test and offline stability.

## Usage

```bash
# Analyze all watchlist or default pairs (saves to trader/whale.json)
python trader-whale/whale.py

# Inspect specific token
python trader-whale/whale.py --symbol SOLUSDT

# Inspect multiple tokens
python trader-whale/whale.py --symbols BTCUSDT,ETHUSDT,SOLUSDT

# Output raw JSON to stdout
python trader-whale/whale.py --json

# Run in offline mode using fixture data
python trader-whale/whale.py --offline tests/fixtures/whale_data.json

# Skip saving to trader/whale.json
python trader-whale/whale.py --no-save
```

## Signals & Sentiments Detected

1. `WHALE_HEAVY_LONG` (Bias: `STRONG_LONG`): Top traders ratio >= 66% long (or L/S ratio >= 1.90). High institutional accumulation supporting trend continuation.
2. `WHALE_HEAVY_SHORT` (Bias: `STRONG_SHORT`): Top traders short ratio >= 62% (or L/S ratio <= 0.65). Smart money distribution / overhead selling pressure.
3. `NEGATIVE_FUNDING_SQUEEZE` (Bias: `STRONG_LONG`): Negative funding rate (< -0.00001). Short traders pay longs, setting up prime short-squeeze breakout conditions.
4. `OVERHEATED_LONG_FUNDING` (Bias: `NEUTRAL_TO_SHORT`): Annualized funding rate >= 25.0% APR. Heavy carrying cost for long leverage; elevated risk of a long flush.
5. `MODERATE_LONG_BIAS` (Bias: `LONG`): Top traders ratio >= 55.0% long. Steady positive smart money flow.
6. `NEUTRAL_FLOW` (Bias: `NEUTRAL`): Balanced derivatives positioning with normal rate equilibrium.

## Output Schema (`trader/whale.json`)

```json
{
  "updated_at": "2026-11-13T20:48:50Z",
  "exchange": "binance_futures",
  "tracked_symbols": 9,
  "metrics": {
    "BTCUSDT": {
      "symbol": "BTCUSDT",
      "funding_rate": 0.000081,
      "mark_price": 68880.58,
      "annualized_rate_pct": 8.92,
      "top_trader_long_pct": 67.5,
      "top_trader_short_pct": 32.5,
      "long_short_ratio": 2.08,
      "top_account_long_pct": 78.1,
      "top_account_short_pct": 21.9,
      "top_account_ls_ratio": 3.56,
      "open_interest": 98270.5,
      "open_interest_usdt": 6769800000.0,
      "next_funding_utc": "2026-11-14T00:00:00+00:00",
      "funding_countdown_min": 190,
      "sentiment": "WHALE_HEAVY_LONG",
      "trade_bias": "STRONG_LONG",
      "signal": "Whales heavily clustered on long side (67.5% / ratio 2.08). Bullish trend support."
    }
  }
}
```
