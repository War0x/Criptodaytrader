---
name: trader-exit
description: Dynamic ATR bracket and swing low exit planner calculating 1R (1.5x) and 2R (2.5x) take-profit levels.
---

# Trader Exit (Binance Crypto Dynamic Exit Planner)

`trader-exit` sets protective stop-loss levels and bracket profit targets BEFORE an order is submitted to safeguard capital.

## Responsibilities
- Calculates dynamic stop-loss using recent 15-bar swing low or 1.5x ATR(14) buffer.
- Formulates bracket targets: Take-Profit 1R (1.5x risk) and Take-Profit 2R (2.5x risk).
- Formulates bracket exit rules (50% scale at TP1, move stop to breakeven, 50% runner at TP2).
- Writes trade bracket configuration to `trader/plan.json`.

## Usage
```bash
# Formulate exit plan based on trader/entry.json
python trader-exit/exit.py plan

# Formulate exit plan for specific symbol and entry price
python trader-exit/exit.py plan --symbol DOGEUSDT --entry 0.06550

# View current trade plan
python trader-exit/exit.py show
```

## Output Schema (`trader/plan.json`)
```json
{
  "created_by": "trader-exit",
  "created_at": "2026-11-13T20:30:00Z",
  "symbol": "DOGEUSDT",
  "side": "BUY",
  "entry": 0.06550,
  "stop": 0.06533,
  "stop_rule": "atr_dynamic",
  "stop_detail": "Dynamic 1.5x ATR (0.00011) safety cushion",
  "target": 0.06576,
  "target_1r": 0.06576,
  "target_2r": 0.06593,
  "risk_per_unit": 0.00017,
  "risk_pct": 0.26,
  "reward_risk": 1.53,
  "reward_risk_2r": 2.53,
  "atr_14": 0.00011,
  "bracket": {
    "order_type": "BRACKET_OCO",
    "tp1_pct": 50,
    "tp2_pct": 50,
    "breakeven_trigger": 0.06576,
    "breakeven_rule": "Move stop-loss to entry price when Target 1R is reached"
  },
  "status": "ready"
}
```
