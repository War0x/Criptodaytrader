#!/usr/bin/env python3
"""Binance Crypto Position Sizer for trader-budget. Standard library only.

Upgraded with Gemini 4 Hydrogen strict risk & capital sizing mathematics:
  - Strict dual constraint:
      1) real_risk = abs(entry - final_stop) * qty <= max_risk_usd ($2.50)
      2) total_cost = entry * qty * (1 + fee_rate) <= available_cash ($100.00)
  - Accounts for Binance lot step sizes, min_notional, and fee reserves
  - Provides both programmatic API (calculate_position_size) and CLI (--write)
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

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
    "fee_rate": 0.001,
    "slippage_tolerance_pct": 0.2,
}


@dataclass(frozen=True)
class PositionSizingResult:
    valid: bool
    reason: str
    side: str
    entry_price: float
    original_stop: float
    final_stop: float
    stop_adjusted: bool
    qty: float
    notional_cost: float
    fee_reserve: float
    total_capital_used: float
    real_risk_usd: float
    max_risk_usd: float
    available_cash: float
    risk_utilization_pct: float
    capital_utilization_pct: float
    take_profit_1_5r: float


def _floor_to_step(value: float, step_size: float) -> float:
    if step_size <= 0:
        return value
    precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 0
    steps = math.floor((value / step_size) + 1e-12)
    return round(steps * step_size, precision + 4)


def _ceil_to_step(value: float, step_size: float) -> float:
    if step_size <= 0:
        return value
    precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 0
    steps = math.ceil((value / step_size) - 1e-12)
    return round(steps * step_size, precision + 4)


def get_step_size(symbol: str, explicit_step: Optional[float] = None, price: Optional[float] = None) -> float:
    if explicit_step is not None and explicit_step > 0:
        return float(explicit_step)
    sym = (symbol or "").strip().upper()
    if sym in BINANCE_STEP_SIZES:
        return BINANCE_STEP_SIZES[sym]
    if price and price >= 1000:
        return 0.00001
    elif price and price >= 100:
        return 0.0001
    elif price and price >= 10:
        return 0.001
    elif price and price >= 1:
        return 0.01
    return 0.1


def calculate_position_size(
    entry_price: float,
    stop_price: float,
    available_cash: float,
    max_risk_usd: float = 2.50,
    side: str = "LONG",
    step_size: float = 0.0001,
    min_qty: float = 0.0,
    min_notional: float = 5.0,
    fee_rate: float = 0.001,
    min_stop_pct: float = 0.003,
    allow_stop_adjustment: bool = True,
    rr_target: float = 1.5,
) -> PositionSizingResult:
    """Calcula el tamaño de posición estricto cumpliendo simultáneamente riesgo y capital."""
    side_norm = side.upper()
    if side_norm not in ("LONG", "SHORT", "BUY", "SELL"):
        raise ValueError(f"side inválido: {side}")
    direction = 1.0 if side_norm in ("LONG", "BUY") else -1.0

    if entry_price <= 0 or available_cash <= 0 or max_risk_usd <= 0:
        return PositionSizingResult(
            valid=False,
            reason="INVALID_INPUTS",
            side=side_norm,
            entry_price=entry_price,
            original_stop=stop_price,
            final_stop=stop_price,
            stop_adjusted=False,
            qty=0.0,
            notional_cost=0.0,
            fee_reserve=0.0,
            total_capital_used=0.0,
            real_risk_usd=0.0,
            max_risk_usd=max_risk_usd,
            available_cash=available_cash,
            risk_utilization_pct=0.0,
            capital_utilization_pct=0.0,
            take_profit_1_5r=entry_price,
        )

    effective_cash = available_cash / (1.0 + max(0.0, fee_rate))
    max_qty_by_cash = _floor_to_step(effective_cash / entry_price, step_size)

    stop_dist = abs(entry_price - stop_price)
    min_safe_stop_dist = entry_price * max(0.0, min_stop_pct)
    final_stop = stop_price
    stop_adjusted = False
    reason = "OK"

    # Caso 1: Stop demasiado estrecho
    if stop_dist < min_safe_stop_dist and allow_stop_adjustment:
        stop_dist = min_safe_stop_dist
        final_stop = entry_price - direction * stop_dist
        stop_adjusted = True
        reason = "STOP_WIDENED_TO_MIN_SAFE_PCT"

    qty_by_risk = _floor_to_step(max_risk_usd / stop_dist, step_size) if stop_dist > 0 else 0.0
    qty = min(qty_by_risk, max_qty_by_cash)

    required_min_qty = max(min_qty, _ceil_to_step(min_notional / entry_price, step_size) if min_notional > 0 else 0.0)

    if qty < required_min_qty or qty <= 0:
        if allow_stop_adjustment and required_min_qty <= max_qty_by_cash and required_min_qty > 0:
            tightened_dist = max_risk_usd / required_min_qty
            if tightened_dist >= min_safe_stop_dist:
                stop_dist = tightened_dist
                final_stop = entry_price - direction * stop_dist
                qty = min(required_min_qty, max_qty_by_cash)
                stop_adjusted = True
                reason = "STOP_TIGHTENED_TO_FIT_MIN_ORDER_AND_RISK"
            else:
                qty = 0.0
                reason = "REJECTED_STOP_TOO_WIDE_EVEN_AT_MIN_SAFE"
        else:
            qty = 0.0
            reason = "REJECTED_INSUFFICIENT_CAPITAL_OR_BELOW_MIN_NOTIONAL"

    notional_cost = qty * entry_price
    fee_reserve = notional_cost * max(0.0, fee_rate)
    total_capital_used = notional_cost + fee_reserve
    real_risk_usd = abs(entry_price - final_stop) * qty

    if qty > 0 and (real_risk_usd > max_risk_usd + 1e-9 or total_capital_used > available_cash + 1e-9):
        qty = max(0.0, _floor_to_step(qty - step_size, step_size))
        notional_cost = qty * entry_price
        fee_reserve = notional_cost * max(0.0, fee_rate)
        total_capital_used = notional_cost + fee_reserve
        real_risk_usd = abs(entry_price - final_stop) * qty

    valid = (
        qty > 0
        and qty >= min_qty
        and notional_cost >= min_notional
        and real_risk_usd <= max_risk_usd + 1e-9
        and total_capital_used <= available_cash + 1e-9
    )

    if not valid and reason == "OK":
        reason = "REJECTED_HARD_CONSTRAINT_CHECK"

    tp = entry_price + direction * (abs(entry_price - final_stop) * rr_target)

    return PositionSizingResult(
        valid=valid,
        reason=reason,
        side=side_norm,
        entry_price=round(entry_price, 8),
        original_stop=round(stop_price, 8),
        final_stop=round(final_stop, 8),
        stop_adjusted=stop_adjusted,
        qty=qty,
        notional_cost=round(notional_cost, 6),
        fee_reserve=round(fee_reserve, 6),
        total_capital_used=round(total_capital_used, 6),
        real_risk_usd=round(real_risk_usd, 6),
        max_risk_usd=round(max_risk_usd, 6),
        available_cash=round(available_cash, 6),
        risk_utilization_pct=round((real_risk_usd / max_risk_usd) * 100.0, 2) if max_risk_usd > 0 else 0.0,
        capital_utilization_pct=round((total_capital_used / available_cash) * 100.0, 2) if available_cash > 0 else 0.0,
        take_profit_1_5r=round(tp, 8),
    )


def size_trade(plan: Dict[str, Any], config: Optional[Dict[str, Any]] = None, portfolio: Optional[Dict[str, Any]] = None, limit_price: Optional[float] = None, step_size: Optional[float] = None) -> Dict[str, Any]:
    if not plan or not plan.get("symbol") or not plan.get("entry"):
        return {"refusal": "No valid trade plan found in trader/plan.json. Run trader-scanner and trader-entry/exit first."}

    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    symbol = plan["symbol"].strip().upper()
    entry = float(limit_price) if limit_price else float(plan["entry"])
    stop = float(plan.get("stop", entry * 0.98))
    target = float(plan.get("target", entry * 1.03))

    budget = float(cfg.get("budget", 100.0))
    cash = float(portfolio.get("cash", budget)) if portfolio else budget
    max_loss_usd = float(cfg.get("max_loss_per_trade_usd", 2.5))
    min_notional = float(cfg.get("min_notional_usdt", 5.0))
    fee_rate = float(cfg.get("fee_rate", 0.001))
    step_val = get_step_size(symbol, explicit_step=step_size, price=entry)

    res = calculate_position_size(
        entry_price=entry,
        stop_price=stop,
        available_cash=cash,
        max_risk_usd=max_loss_usd,
        side="LONG",
        step_size=step_val,
        min_notional=min_notional,
        fee_rate=fee_rate,
        allow_stop_adjustment=True,
        rr_target=float(cfg.get("min_reward_risk", 1.5)),
    )

    if not res.valid:
        return {"refusal": f"Position sizing rejected for {symbol}: {res.reason}"}

    decimals = max(0, int(round(-math.log10(step_val)))) if step_val < 1 else 0
    formatted_qty = f"{res.qty:.{decimals}f}"

    why = (
        f"Binance {symbol} Day Trade: {formatted_qty} {symbol} (${res.notional_cost:.2f} USDT), "
        f"risk: ${res.real_risk_usd:.2f} <= ${max_loss_usd:.2f} at stop ${res.final_stop:.4f}"
    )

    order = {
        "symbol": symbol,
        "side": "BUY",
        "qty": formatted_qty,
        "qty_float": res.qty,
        "step_size": step_val,
        "notional": res.notional_cost,
        "type": "LIMIT" if limit_price else "MARKET",
        "order_class": "bracket",
        "entry_price": entry,
        "limit_price": entry if limit_price else None,
        "stop_loss_stop_price": res.final_stop,
        "take_profit_limit_price": res.take_profit_1_5r,
        "risk_per_unit": round(abs(entry - res.final_stop), 8),
        "reward_risk": round(abs(res.take_profit_1_5r - entry) / abs(entry - res.final_stop), 2) if abs(entry - res.final_stop) > 0 else 1.5,
        "max_loss": res.real_risk_usd,
        "why": why,
        "plan": plan,
    }

    return {
        "symbol": symbol,
        "order": order,
        "budget": budget,
        "cash": cash,
        "qty": res.qty,
        "decimals": decimals,
        "step_size": step_val,
        "notional": res.notional_cost,
        "max_loss": res.real_risk_usd,
        "sizing_details": asdict(res),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculadora estricta de tamaño de posición")
    parser.add_argument("--entry", type=float, help="Precio de entrada")
    parser.add_argument("--stop", type=float, help="Precio de stop")
    parser.add_argument("--cash", type=float, default=100.0, help="Efectivo disponible")
    parser.add_argument("--max-risk", type=float, default=2.50, help="Riesgo máximo en USD")
    parser.add_argument("--side", choices=["LONG", "SHORT"], default="LONG")
    parser.add_argument("--write", "-w", action="store_true", help="Leer plan.json y escribir orders.json")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    trader_dir = os.path.join(base_dir, "trader")

    if args.write or (not args.entry and not args.stop):
        plan_path = os.path.join(trader_dir, "plan.json")
        cfg_path = os.path.join(trader_dir, "config.json")
        port_path = os.path.join(trader_dir, "portfolio.json")

        plan = {}
        if os.path.exists(plan_path):
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)

        cfg = DEFAULT_CONFIG
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

        port = {}
        if os.path.exists(port_path):
            with open(port_path, "r", encoding="utf-8") as f:
                port = json.load(f)

        res = size_trade(plan, cfg, port)
        if args.write and "order" in res:
            orders_path = os.path.join(trader_dir, "orders.json")
            with open(orders_path, "w", encoding="utf-8") as f:
                json.dump(res["order"], f, indent=2)
            print(f"[budget] Bracket order written to {orders_path}")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    res = calculate_position_size(
        entry_price=args.entry,
        stop_price=args.stop,
        available_cash=args.cash,
        max_risk_usd=args.max_risk,
        side=args.side,
    )
    print(json.dumps(asdict(res), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
