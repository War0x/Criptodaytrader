#!/usr/bin/env python3
"""Binance Crypto Day Trader - Dynamic Exit Planner (trader-exit).
Calculates dynamic stop-loss (swing low / ATR) and bracket targets (1.5x risk 1R, 2.5x risk 2R).
Standard library only (urllib.request, json, time, datetime, sys, os, math).
"""

import argparse
import datetime
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
CONFIG_PATH = os.path.join(TRADER_DIR, "config.json")
ENTRY_PATH = os.path.join(TRADER_DIR, "entry.json")
PLAN_PATH = os.path.join(TRADER_DIR, "plan.json")

BINANCE_API = "https://api.binance.com/api/v3"
USER_AGENT = "BinanceCryptoTraderSwarm/1.0"


def fetch_klines(symbol: str, interval: str = "5m", limit: int = 50):
    """Fetch klines from Binance public REST API."""
    url = f"{BINANCE_API}/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception:
        return None


def calculate_atr(bars, period=14):
    """Calculate Average True Range on bar dicts."""
    if len(bars) < 2:
        return 0.0
    tr_list = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        prev_c = bars[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    recent_tr = tr_list[-period:] if len(tr_list) >= period else tr_list
    return sum(recent_tr) / len(recent_tr) if recent_tr else 0.0


def calculate_precision(price: float) -> int:
    """Return appropriate decimal places for the asset price."""
    if price >= 1000:
        return 2
    elif price >= 10:
        return 4
    elif price >= 1:
        return 4
    elif price >= 0.01:
        return 5
    else:
        return 6


def plan_exit(symbol: str = None, entry_price: float = None, stop_mode: str = "auto", custom_stop: float = None):
    """
    Formulates bracket exit rules with dynamic stop loss and targets (1.5x and 2.5x risk).
    """
    # 1. Resolve symbol and entry price
    if symbol is None or entry_price is None:
        if os.path.exists(ENTRY_PATH):
            try:
                with open(ENTRY_PATH, "r", encoding="utf-8") as f:
                    entry_doc = json.load(f)
                    doc_sym = entry_doc.get("symbol")
                    if symbol is None and doc_sym:
                        symbol = doc_sym
                        if entry_price is None and entry_doc.get("price"):
                            entry_price = float(entry_doc.get("price"))
                    elif symbol is not None and doc_sym and doc_sym.upper() == symbol.upper():
                        if entry_price is None and entry_doc.get("price"):
                            entry_price = float(entry_doc.get("price"))
            except Exception:
                pass

    if symbol is None:
        symbol = "SOLUSDT"

    # 2. Fetch recent bars for ATR and Swing Low calculations
    klines_5m = fetch_klines(symbol, interval="5m", limit=40)
    bars = []
    if klines_5m:
        for k in klines_5m:
            bars.append({
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

    if not bars:
        # Fallback synthetic bars
        base = entry_price or 80.0
        bars = [{"high": base * 1.005, "low": base * 0.995, "close": base, "volume": 1000} for _ in range(20)]

    if entry_price is None or entry_price <= 0:
        entry_price = bars[-1]["close"]

    precision = calculate_precision(entry_price)
    atr = calculate_atr(bars, period=14)
    if atr <= 0:
        atr = entry_price * 0.008  # 0.8% fallback ATR proxy

    # 3. Calculate Stop Loss
    recent_lows = [b["low"] for b in bars[-15:]]
    swing_low = min(recent_lows) if recent_lows else entry_price - (atr * 1.5)

    if custom_stop and custom_stop > 0:
        stop_price = custom_stop
        stop_rule = "custom"
        stop_detail = f"Manual stop fixed at {stop_price:.{precision}f}"
    elif stop_mode == "atr":
        stop_price = entry_price - (atr * 1.5)
        stop_rule = "atr_1.5x"
        stop_detail = f"1.5x ATR ({atr:.{precision}f}) below entry"
    elif stop_mode == "swing_low":
        stop_price = swing_low * 0.999  # 0.1% buffer below swing low
        stop_rule = "swing_low"
        stop_detail = f"Recent swing low {swing_low:.{precision}f} buffered by 0.1%"
    else:  # auto
        # Choose swing low if sensible (risk between 0.4% and 2.5%), else ATR proxy
        tentative_risk = (entry_price - swing_low) / entry_price
        if 0.004 <= tentative_risk <= 0.025:
            stop_price = swing_low * 0.999
            stop_rule = "swing_low"
            stop_detail = f"Recent swing low {swing_low:.{precision}f} buffered by 0.1% ({tentative_risk*100:.2f}% risk)"
        else:
            stop_price = entry_price - (atr * 1.5)
            stop_rule = "atr_dynamic"
            stop_detail = f"Dynamic 1.5x ATR ({atr:.{precision}f}) safety cushion"

    # Sanity bounds: stop MUST be strictly lower than entry and positive
    if stop_price >= entry_price or stop_price <= 0:
        stop_price = entry_price * 0.985
        stop_rule = "fallback_1.5pct"
        stop_detail = "Fallback 1.5% stop distance"

    risk_per_unit = entry_price - stop_price
    min_risk = entry_price * 0.002  # At least 0.2% risk to avoid tick noise
    if risk_per_unit < min_risk:
        risk_per_unit = min_risk
        stop_price = entry_price - risk_per_unit

    if stop_price <= 0:
        stop_price = entry_price * 0.985
        risk_per_unit = entry_price - stop_price

    # 4. Calculate Targets: 1R (1.5x risk) and 2R (2.5x risk)
    entry_rounded = round(entry_price, precision)
    stop_rounded = round(stop_price, precision)
    if stop_rounded >= entry_rounded:
        stop_rounded = round(entry_rounded - (10 ** -precision), precision)

    risk_rounded = round(entry_rounded - stop_rounded, precision)
    tick = 10 ** -precision

    target_1r = round(entry_rounded + (1.5 * risk_rounded), precision)
    # Guarantee mathematically that rounded target / risk is >= 1.50
    while round((target_1r - entry_rounded) / risk_rounded, 4) < 1.50:
        target_1r = round(target_1r + tick, precision)

    target_2r = round(entry_rounded + (2.5 * risk_rounded), precision)
    while round((target_2r - entry_rounded) / risk_rounded, 4) < 2.50:
        target_2r = round(target_2r + tick, precision)

    primary_target = target_1r
    reward_risk_1r = round((target_1r - entry_rounded) / risk_rounded, 2)
    reward_risk_2r = round((target_2r - entry_rounded) / risk_rounded, 2)

    now_dt = datetime.datetime.now(datetime.timezone.utc)
    iso_time = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    plan_data = {
        "created_by": "trader-exit",
        "created_at": iso_time,
        "symbol": symbol.upper(),
        "side": "BUY",
        "entry": entry_rounded,
        "stop": stop_rounded,
        "stop_rule": stop_rule,
        "stop_detail": stop_detail,
        "target": primary_target,
        "target_1r": target_1r,
        "target_2r": target_2r,
        "risk_per_unit": risk_rounded,
        "risk_pct": round((risk_rounded / entry_rounded) * 100, 2),
        "reward_risk": reward_risk_1r,
        "reward_risk_2r": reward_risk_2r,
        "atr_14": round(atr, precision),
        "bracket": {
            "order_type": "BRACKET_OCO",
            "tp1_pct": 50,
            "tp2_pct": 50,
            "breakeven_trigger": round(target_1r, precision),
            "breakeven_rule": "Move stop-loss to entry price when Target 1R is reached"
        },
        "status": "ready"
    }

    return plan_data


def save_plan(plan_data: dict, path: str = PLAN_PATH):
    """Write plan to trader/plan.json."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan_data, f, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser(description="Binance Crypto Exit Planner")
    parser.add_argument("command", nargs="?", default="plan", choices=["plan", "show"], help="Action to perform")
    parser.add_argument("--symbol", "-s", type=str, default=None, help="Crypto pair (e.g. SOLUSDT)")
    parser.add_argument("--entry", "-e", type=float, default=None, help="Entry price")
    parser.add_argument("--stop-mode", type=str, default="auto", choices=["auto", "swing_low", "atr"], help="Stop method")
    parser.add_argument("--stop", type=float, default=None, help="Explicit stop-loss price")
    parser.add_argument("--write", "-w", action="store_true", default=True, help="Save to trader/plan.json")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if args.command == "show":
        if os.path.exists(PLAN_PATH):
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                print(f.read())
            sys.exit(0)
        else:
            print("No plan.json found.")
            sys.exit(1)

    plan = plan_exit(symbol=args.symbol, entry_price=args.entry, stop_mode=args.stop_mode, custom_stop=args.stop)

    if args.write:
        save_plan(plan)

    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(f"\n=======================================================")
        print(f" BINANCE EXIT PLAN: {plan['symbol']} ({plan['side']})")
        print(f"=======================================================")
        prec = calculate_precision(plan['entry'])
        print(f" Status:          {plan['status'].upper()}")
        print(f" Entry Price:     ${plan['entry']:.{prec}f}")
        print(f" Stop-Loss:       ${plan['stop']:.{prec}f} ({plan['stop_rule']} | -{plan['risk_pct']}%)")
        print(f" Stop Detail:     {plan['stop_detail']}")
        print(f" Target 1R:       ${plan['target_1r']:.{prec}f} (Reward/Risk: {plan['reward_risk']}x)")
        print(f" Target 2R:       ${plan['target_2r']:.{prec}f} (Reward/Risk: {plan['reward_risk_2r']}x)")
        print(f" Risk per Unit:   ${plan['risk_per_unit']:.{prec}f}")
        print(f" ATR (14-period): ${plan['atr_14']:.{prec}f}")
        print(f" Bracket Rules:   50% scale at TP1, 50% runner at TP2")
        print(f" Breakeven Move:  Stop moves to ${plan['entry']:.{prec}f} once TP1 hits")
        if args.write:
            print(f" Written to:      {PLAN_PATH}")
        print(f"=======================================================\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
