---
name: trader-scanner
description: Binance crypto breakout market scanner using 100% self-contained Python standard library.
---

# Trader-Scanner: Binance Crypto Breakout Scanner

Scans Binance public 24-hour ticker data for crypto breakout candidates, volatility surges, and range expansions across USDT pairs.

## Usage

```bash
# Scan default pairs from trader/pairs.txt and update trader/watchlist.json
python trader-scanner/scan.py

# Scan top 30 USDT pairs by 24h volume across all of Binance
python trader-scanner/scan.py --top 30

# Scan specific symbols
python trader-scanner/scan.py --symbols BTCUSDT,SOLUSDT,SUIUSDT

# Offline test mode
python trader-scanner/scan.py --offline tests/fixtures/tickers_24hr.json

# Raw JSON output
python trader-scanner/scan.py --json
```

## Setup Identification
- `BREAKOUT_24H_HIGH`: Price within 2.5% of 24h high, range position >= 85%, positive momentum.
- `MOMENTUM_EXPANSION`: Price change >= +5.0% and daily range >= 7.0%.
- `RANGE_COMPRESSION`: Daily range <= 3.5% coiled tightly within 2.0% of the 24h high.
- `BULLISH_TREND`: Positive 24h upward momentum >= +2.0%.
- `RANGE_BOUND`: Trading within normal daily parameters.
