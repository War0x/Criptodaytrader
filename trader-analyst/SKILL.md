---
name: trader-analyst
description: Binance crypto orderbook depth, bid-ask spread, slippage, and market structure analyst.
---

# Trader-Analyst: Orderbook & Market Structure Analyst

Evaluates real-time Level 2 orderbook depth, bid-ask spreads, liquidity walls, market structure, and estimated slippage on Binance.

## Usage

```bash
# Analyze the top candidate from trader/watchlist.json
python trader-analyst/analyst.py

# Analyze a specific symbol
python trader-analyst/analyst.py --symbol SOLUSDT

# Analyze multiple symbols
python trader-analyst/analyst.py --symbols DOGEUSDT,NEARUSDT,SOLUSDT

# Raw JSON output
python trader-analyst/analyst.py --json

# Offline mode
python trader-analyst/analyst.py --offline tests/fixtures/sample_depth.json
```

## Features
- Evaluates top 20 bid and ask levels from Binance `api/v3/depth`.
- Calculates bid-ask spread percentage and verifies if within tight limits.
- Simulates realistic slippage on $50 USDT market entry orders.
- Computes orderbook imbalance and liquidity walls.
- Evaluates 24h market structure and range compression/expansion.
- Writes full report to `trader/market_analysis.json`.
