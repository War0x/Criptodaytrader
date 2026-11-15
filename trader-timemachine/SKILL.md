---
name: trader-timemachine
description: Runs 10,000 Monte Carlo bootstrap simulation paths simulating the next 48 candles (4h) from current price.
---

# Trader Time Machine (Binance Crypto Monte Carlo Engine)

`trader-timemachine` stress-tests a day trading plan before capital is committed: runs 10,000 bootstrap simulations across a 4-hour forward horizon (48 x 5m candles).

## Responsibilities
- Fetches recent 500-1000 candles of historical 5m bars from Binance for the active pair (`https://api.binance.com/api/v3/klines`).
- Generates empirical return distributions (high, low, close relative excursions).
- Runs 10,000 Monte Carlo bootstrap paths simulating the next 48 candles from current entry price.
- Checks Stop-Loss vs Target boundaries bar by bar (conservative stop-first order).
- Computes empirical probabilities of reaching Target vs Stop first, expected drawdown, win rate, and terminal price fan quantiles.
- Formulates statistical verdict and writes results to `trader/timemachine.json`.

## Usage
```bash
# Run 10,000 replays based on current trader/plan.json
python trader-timemachine/timemachine.py run

# Run with custom parameters
python trader-timemachine/timemachine.py run --symbol DOGEUSDT --replays 10000 --seed 42

# View ASCII distribution chart
python trader-timemachine/timemachine.py chart
```

## Output Schema (`trader/timemachine.json`)
```json
{
  "created_by": "trader-timemachine",
  "created_at": "2026-11-13T20:30:00Z",
  "symbol": "DOGEUSDT",
  "simulations": 10000,
  "horizon_bars": 48,
  "horizon_hours": 4.0,
  "candles_analyzed": 999,
  "plan": {
    "entry": 0.06550,
    "stop": 0.06533,
    "target": 0.06576,
    "reward_risk": 1.53
  },
  "results": {
    "p_target": 0.3503,
    "p_stop": 0.6497,
    "p_neither": 0.0,
    "win_rate": 0.3503,
    "win_rate_pct": 35.03,
    "expected_drawdown_pct": 0.33,
    "max_drawdown_pct": 2.42,
    "expected_value_r": -0.114,
    "median_final_price": 0.06533,
    "p5_final_price": 0.06533,
    "p95_final_price": 0.06576,
    "verdict": "..."
  },
  "bands": { ... }
}
```
