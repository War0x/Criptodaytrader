#!/usr/bin/env python3
"""Binance Crypto Position Sizer for trader-budget. Standard library only.

Reads:
  - trader/config.json
  - trader/portfolio.json
  - trader/plan.json (produced by trader-exit)

Calculates:
  - Crypto position size matching max_loss_per_trade_usd ($2.50) / per-unit risk
  - Respects min_notional_usdt ($5.00), max_position_pct (50% of budget = $50 max), and cash available
  - Rounds crypto quantity to symbol-specific precision (e.g. 5 for BTC, 4 for ETH, 3 for SOL, etc.)
  - Writes bracket order to trader/orders.json
"""

import argparse
import datetime
import json
import math
import os
import sys

# Binance typical crypto quantity precisions (step sizes)
CRYPTO_PRECISION = {
    "BTCUSDT": 5,
    "ETHUSDT": 4,
    "SOLUSDT": 3,
    "BNBUSDT": 3,
    "AVAXUSDT": 2,
    "LINKUSDT": 2,
    "NEARUSDT": 2,
    "SUIUSDT": 1,
    "DOGEUSDT": 1,
    "XRPUSDT": 1,
    "ADAUSDT": 1,
}

# Binance typical lot step sizes
BINANCE_STEP_SIZES = {
    "BTCUSDT": 0.00001,
    "ETHUSDT": 0.0001,
    "SOLUSDT": 0.001,
    "BNBUSDT": 0.001,
    "AVAXUSDT": 0.01,
    "LINKUSDT": 0.01,
    "NEARUSDT": 0.01,
    "SUIUSDT": 0.1,
    "DOGEUSDT": 0.1,
    "XRPUSDT": 0.1,
    "ADAUSDT": 0.1,
}

DEFAULT_CONFIG = {
    "budget": 100.0,
    "currency": "USDT",
    "exchange": "binance",
    "paper": True,
    "max_loss_per_trade_usd": 2.5,
    "max_daily_loss_usd": 7.5,
    "max_trades_per_day": 5,
    "min_reward_risk": 1.5,
    "require_stop": True,
    "max_position_pct": 50,
    "min_notional_usdt": 5.0,
    "slippage_tolerance_pct": 0.2,
}


def get_crypto_precision(symbol, price=None):
    sym = (symbol or "").strip().upper()
    if sym in CRYPTO_PRECISION:
        return CRYPTO_PRECISION[sym]
    if price:
        if price >= 1000:
            return 5
        elif price >= 100:
            return 4
        elif price >= 10:
            return 3
        elif price >= 1:
            return 2
        else:
            return 1
    return 3


def get_step_size(symbol, plan=None, config=None, explicit_step=None, price=None):
    if explicit_step is not None and explicit_step > 0:
        return float(explicit_step)
    if plan:
        if plan.get("step_size") and float(plan["step_size"]) > 0:
            return float(plan["step_size"])
        if plan.get("stepSize") and float(plan["stepSize"]) > 0:
            return float(plan["stepSize"])
    if config:
        if config.get("step_size") and float(config["step_size"]) > 0:
            return float(config["step_size"])
        if config.get("stepSize") and float(config["stepSize"]) > 0:
            return float(config["stepSize"])
    sym = (symbol or "").strip().upper()
    if sym in BINANCE_STEP_SIZES:
        return BINANCE_STEP_SIZES[sym]
    prec = get_crypto_precision(symbol, price)
    return round(10 ** (-prec), prec)


def floor_to_step_size(val, step_size):
    if not step_size or step_size <= 0:
        return val, 8
    step_str = f"{step_size:.8f}".rstrip("0")
    if "." in step_str:
        decimals = len(step_str.split(".")[1])
    else:
        decimals = 0
    units = math.floor(val / step_size + 1e-9)
    floored = units * step_size
    return round(floored, decimals), decimals


def floor_to_precision(val, decimals):
    factor = 10 ** decimals
    return math.floor(val * factor + 1e-9) / factor


def format_qty(val, decimals):
    formatted = f"{val:.{decimals}f}"
    return formatted


def load_json(path, desc="file"):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to read {desc} at {path}: {e}")


def size_trade(plan, config, portfolio=None, limit_price=None, step_size=None):
    if not plan or not plan.get("symbol") or not plan.get("entry"):
        return {"refusal": "No valid trade plan found in trader/plan.json. Run trader-scanner and trader-entry/exit first."}

    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    symbol = plan["symbol"].strip().upper()
    side = plan.get("side", "BUY").strip().upper()
    if side != "BUY":
        return {"refusal": f"Only BUY/Long day trading is currently supported, got {side}."}

    entry = float(limit_price) if limit_price else float(plan["entry"])
    stop = float(plan["stop"]) if plan.get("stop") else None
    target_1r = float(plan["target_1r"]) if plan.get("target_1r") else None
    target_2r = float(plan["target_2r"]) if plan.get("target_2r") else None
    target = float(plan["target"]) if plan.get("target") else (target_1r or target_2r)

    if not stop or stop <= 0:
        return {"refusal": f"Stop loss is required for {symbol}."}
    if stop >= entry:
        return {"refusal": f"Stop loss (${stop}) must be below entry price (${entry}) for a BUY."}

    risk_per_unit = entry - stop
    if not target or target <= entry:
        return {"refusal": f"Target price (${target}) must be above entry price (${entry})."}

    reward_per_unit = target - entry
    reward_risk = round(reward_per_unit / risk_per_unit, 2)
    min_rr = float(cfg.get("min_reward_risk", 1.5))
    if reward_risk < min_rr:
        return {"refusal": f"Reward:Risk ratio {reward_risk} is below required minimum {min_rr}."}

    budget = float(cfg.get("budget", 100.0))
    cash = float(portfolio.get("cash", budget)) if portfolio else budget
    max_loss_usd = float(cfg.get("max_loss_per_trade_usd", 2.5))
    max_pos_pct = float(cfg.get("max_position_pct", 50.0))
    min_notional = float(cfg.get("min_notional_usdt", 5.0))

    # Constraint 1: Max loss
    qty_from_risk = max_loss_usd / risk_per_unit

    # Constraint 2: Position cap percentage of budget
    max_order_usd = budget * (max_pos_pct / 100.0)
    qty_from_cap = max_order_usd / entry

    # Constraint 3: Cash available
    qty_from_cash = cash / entry

    # Lowest constraint wins
    raw_qty = min(qty_from_risk, qty_from_cap, qty_from_cash)
    step_size_val = get_step_size(symbol, plan=plan, config=cfg, explicit_step=step_size, price=entry)
    qty, decimals = floor_to_step_size(raw_qty, step_size_val)

    if qty <= 0:
        return {
            "refusal": f"Calculated quantity is 0 {symbol}. Risk per unit (${risk_per_unit:.4f}) is too high for ${max_loss_usd} max loss."
        }

    notional = round(qty * entry, 2)
    actual_loss = round(qty * risk_per_unit, 2)

    if notional < min_notional:
        return {
            "refusal": f"Order notional value (${notional:.2f}) is below Binance minimum requirement of ${min_notional:.2f} USDT."
        }

    bound_by = "max loss"
    if abs(raw_qty - qty_from_cap) < 1e-6:
        bound_by = "position cap"
    elif abs(raw_qty - qty_from_cash) < 1e-6:
        bound_by = "cash available"

    why = (
        f"Binance {symbol} Day Trade: {format_qty(qty, decimals)} {symbol} (${notional:.2f} USDT), "
        f"bound by {bound_by} (risk: ${actual_loss:.2f} <= ${max_loss_usd:.2f} at stop ${stop:.2f})"
    )

    order = {
        "symbol": symbol,
        "side": "BUY",
        "qty": format_qty(qty, decimals),
        "qty_float": qty,
        "step_size": step_size_val,
        "notional": notional,
        "type": "LIMIT" if limit_price else "MARKET",
        "order_class": "bracket",
        "entry_price": entry,
        "limit_price": entry if limit_price else None,
        "stop_loss_stop_price": round(stop, 8),
        "take_profit_limit_price": round(target, 8),
        "risk_per_unit": round(risk_per_unit, 8),
        "reward_risk": reward_risk,
        "max_loss": actual_loss,
        "why": why,
        "plan": plan,
    }

    return {
        "symbol": symbol,
        "order": order,
        "budget": budget,
        "cash": cash,
        "qty": qty,
        "decimals": decimals,
        "step_size": step_size_val,
        "notional": notional,
        "bound_by": bound_by,
        "max_loss": actual_loss,
        "reward_risk": reward_risk,
        "refusal": None,
    }


def write_orders_file(path, orders, cfg):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "exchange": "binance",
        "note": "Proposed orders sized according to Binance lot precision, max loss, and min notional rules.",
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description="trader-budget: Crypto Position Sizer for Binance")
    parser.add_argument("--plan", default="trader/plan.json", help="Path to plan.json")
    parser.add_argument("--config", default="trader/config.json", help="Path to config.json")
    parser.add_argument("--portfolio", default="trader/portfolio.json", help="Path to portfolio.json")
    parser.add_argument("--orders", default="trader/orders.json", help="Path to orders.json")
    parser.add_argument("--limit", type=float, help="Override entry as limit order at this price")
    parser.add_argument("--step-size", type=float, help="Override lot step size (e.g. 0.001)")
    parser.add_argument("--write", action="store_true", help="Write sized order to orders.json")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    # Resolve paths relative to project root if executed from within subdirectory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plan_path = os.path.join(base_dir, args.plan) if not os.path.isabs(args.plan) else args.plan
    cfg_path = os.path.join(base_dir, args.config) if not os.path.isabs(args.config) else args.config
    port_path = os.path.join(base_dir, args.portfolio) if not os.path.isabs(args.portfolio) else args.portfolio
    orders_path = os.path.join(base_dir, args.orders) if not os.path.isabs(args.orders) else args.orders

    plan = load_json(plan_path, "plan")
    config = load_json(cfg_path, "config") or DEFAULT_CONFIG
    portfolio = load_json(port_path, "portfolio")

    result = size_trade(plan, config, portfolio, limit_price=args.limit, step_size=args.step_size)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    if result.get("refusal"):
        print(f"[-] Sizing Refusal: {result['refusal']}")
        sys.exit(1)

    order = result["order"]
    print("=" * 60)
    print(f" BINANCE CRYPTO POSITION SIZER - {order['symbol']}")
    print("=" * 60)
    print(f"  Action:       {order['side']} {order['qty']} {order['symbol']}")
    print(f"  Step Size:    {order.get('step_size')}")
    print(f"  Entry Price:  ${order['entry_price']:.6f} USDT")
    print(f"  Notional:     ${order['notional']:.2f} USDT")
    print(f"  Stop Loss:    ${order['stop_loss_stop_price']:.6f} USDT")
    print(f"  Take Profit:  ${order['take_profit_limit_price']:.6f} USDT")
    print(f"  Per-Unit Risk:${order['risk_per_unit']:.6f} USDT")
    print(f"  Reward/Risk:  {order['reward_risk']:.2f} R")
    print(f"  Max Risk $:   ${order['max_loss']:.2f} USDT (Bound by: {result['bound_by']})")
    print("-" * 60)
    print(f"  Why: {order['why']}")
    print("=" * 60)

    if args.write:
        write_orders_file(orders_path, [order], config)
        print(f"[+] Sized order successfully written to {orders_path}")


if __name__ == "__main__":
    main()
