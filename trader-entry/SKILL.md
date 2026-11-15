---
name: trader-entry
description: Validates breakout conditions on 5m and 15m Binance klines with volume confirmation for day trading entries.
---

# Trader Entry (Binance Crypto Entry Timer)

`trader-entry` monitors crypto candle formations on Binance public REST endpoints to detect high-probability breakout entry triggers.

## Responsibilities
- Fetches 5m and 15m public klines from `https://api.binance.com/api/v3/klines`.
- Detects breakout candle close above recent resistance / range high with above-average volume surge.
- Formulates entry metrics and writes `trader/entry.json`.

## Usage
```bash
# Check entry conditions for default watchlist candidate
python trader-entry/entry.py check

# Check entry conditions for a specific pair
python trader-entry/entry.py check --symbol DOGEUSDT

# Force trigger for testing downstream execution pipeline
python trader-entry/entry.py check --symbol DOGEUSDT --force
```

## Output Schema (`trader/entry.json`)
```json
{
  "created_by": "trader-entry",
  "symbol": "DOGEUSDT",
  "setup": "breakout",
  "side": "buy",
  "date": "2026-11-13",
  "status": "triggered",
  "time": "2026-11-13 20:30:00 UTC",
  "price": 0.06550,
  "level": 0.06581,
  "resistance_5m": 0.06581,
  "resistance_15m": 0.06581,
  "interval": "5m",
  "volume": 435523.0,
  "avg_volume": 528191.7,
  "volume_ratio": 0.82,
  "source": "binance",
  "checked_at": "2026-11-13T20:30:00Z",
  "reason": "5m candle closed at 0.06550 above resistance..."
}
```
