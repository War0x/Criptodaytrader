#!/usr/bin/env python3
"""Binance Crypto Risk & Compliance Checker for trader-risk. Standard library only.

Audits proposed orders in trader/orders.json against:
  1. Format & Asset rules (USDT crypto pairs, valid sides, positive quantities)
  2. Account cash & position cap (<= 50% budget)
  3. Minimum notional value (>= $5.00 USDT Binance requirement)
  4. Max loss per trade (<= $2.50 USDT)
  5. Daily loss limit (<= $7.50 USDT cumulative risk)
  6. Maximum daily trade limit (<= 5 trades/day)
  7. Minimum Reward:Risk ratio (>= 1.5 R)
  8. Mandatory Stop Loss verification
  9. Orderbook spread check (Binance API depth check with offline fallback)

Outputs PASS or BLOCK with full audit breakdown and SHA-256 fingerprinting.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

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


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fingerprint_order(order):
    keys = ["symbol", "side", "qty", "notional", "type", "stop_loss_stop_price", "take_profit_limit_price"]
    core = {k: order.get(k) for k in keys if k in order}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def fetch_binance_spread(symbol, timeout=3.0):
    """Optional online spread check using Binance public orderbook depth."""
    try:
        url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit=5"
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoTrader/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                spread_pct = ((best_ask - best_bid) / best_bid) * 100.0
                return spread_pct, best_bid, best_ask
    except Exception:
        pass
    return None, None, None


def audit_orders(orders_doc, config, portfolio, trade_history=None, check_network=True):
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    if isinstance(orders_doc, dict):
        if "orders" in orders_doc and isinstance(orders_doc["orders"], list):
            orders = orders_doc["orders"]
        elif "symbol" in orders_doc:
            orders = [orders_doc]
        else:
            orders = orders_doc.get("orders", [])
    elif isinstance(orders_doc, list):
        orders = orders_doc
    else:
        orders = []

    if not isinstance(orders, list) or not orders:
        return {
            "overall": "BLOCK",
            "pass_count": 0,
            "passed_count": 0,
            "block_count": 0,
            "blocked_count": 0,
            "summary": "No orders found in orders document to check.",
            "results": [],
            "orders": [],
        }

    budget = float(cfg.get("budget", 100.0))
    cash = float(portfolio.get("cash", budget)) if portfolio else budget
    max_loss_trade = float(cfg.get("max_loss_per_trade_usd", 2.5))
    max_daily_loss = float(cfg.get("max_daily_loss_usd", 7.5))
    max_trades_day = int(cfg.get("max_trades_per_day", 5))
    min_rr = float(cfg.get("min_reward_risk", 1.5))
    min_notional = float(cfg.get("min_notional_usdt", 5.0))
    max_pos_pct = float(cfg.get("max_position_pct", 50.0))
    max_order_usd = budget * (max_pos_pct / 100.0)
    req_stop = bool(cfg.get("require_stop", True))
    max_slippage = float(cfg.get("slippage_tolerance_pct", 0.2))

    # Calculate current day's executed trades and loss
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    history = trade_history if isinstance(trade_history, list) else []
    today_trades = [t for t in history if str(t.get("timestamp", ""))[:10] == today_str]
    today_trade_count = len(today_trades)
    today_loss_at_risk = sum(float(t.get("max_loss", 0.0)) for t in today_trades)

    results = []
    accum_cash_spent = 0.0
    accum_loss_added = 0.0
    accum_trades_added = 0

    for idx, order in enumerate(orders, 1):
        reasons = []
        warnings = []
        info = []

        symbol = str(order.get("symbol", "")).strip().upper()
        side = str(order.get("side", "")).strip().upper()
        qty = float(order.get("qty", order.get("qty_float", 0.0)))
        notional = float(order.get("notional", 0.0))
        entry = float(order.get("entry_price") or order.get("limit_price") or order.get("entry") or order.get("price") or 0.0)
        stop = float(order.get("stop_loss_stop_price") or order.get("stop_price") or order.get("stop") or 0.0) if (
            order.get("stop_loss_stop_price") or order.get("stop_price") or order.get("stop")
        ) else None
        target = float(order.get("take_profit_limit_price") or order.get("target_price") or order.get("target") or 0.0) if (
            order.get("take_profit_limit_price") or order.get("target_price") or order.get("target")
        ) else None
        max_loss = float(order.get("max_loss", 0.0))
        rr = float(order.get("reward_risk", 0.0))

        # 1. Format & Asset Validation
        if not symbol:
            reasons.append("Format: Missing trading symbol.")
        elif not symbol.endswith("USDT"):
            reasons.append(f"Asset: Pair {symbol} is not a supported USDT spot pair.")

        if side not in ("BUY", "SELL"):
            reasons.append(f"Format: Invalid side '{side}', must be BUY or SELL.")

        if qty <= 0:
            reasons.append("Format: Quantity must be greater than zero.")

        if entry <= 0:
            reasons.append("Format: Entry price must be specified and greater than zero.")

        if notional <= 0 and entry > 0 and qty > 0:
            notional = round(qty * entry, 2)

        # 2. Minimum Notional
        if notional < min_notional - 0.01:
            reasons.append(f"Min Notional: Order value ${notional:.2f} USDT is below Binance minimum ${min_notional:.2f} USDT.")
        else:
            info.append(f"Notional: ${notional:.2f} USDT (>= ${min_notional:.2f} min)")

        # 3. Position Cap
        if notional > max_order_usd + 0.01:
            reasons.append(
                f"Position Cap: Order value ${notional:.2f} USDT exceeds maximum allowed position size "
                f"${max_order_usd:.2f} USDT ({max_pos_pct}% of budget)."
            )

        # 4. Cash Available
        remaining_cash = cash - accum_cash_spent
        if notional > remaining_cash + 0.01:
            reasons.append(
                f"Insufficient Funds: Order needs ${notional:.2f} USDT, but only ${remaining_cash:.2f} USDT cash is available."
            )

        # 5. Mandatory Stop Loss
        if req_stop:
            if not stop or stop <= 0:
                reasons.append("Mandatory Stop: No valid stop loss price specified.")
            elif side == "BUY" and entry > 0 and stop >= entry:
                reasons.append(f"Invalid Stop: Stop loss (${stop:.6f}) must be strictly below entry (${entry:.6f}) for a BUY.")
            elif side == "SELL" and entry > 0 and stop <= entry:
                reasons.append(f"Invalid Stop: Stop loss (${stop:.6f}) must be strictly above entry (${entry:.6f}) for a SELL.")
            else:
                info.append(f"Stop Loss: ${stop:.6f} USDT confirmed")

        # 6. Max Loss Per Trade
        calc_loss = round(qty * abs(entry - stop), 2) if (entry > 0 and stop) else max_loss
        if calc_loss > max_loss_trade + 0.01:
            reasons.append(
                f"Max Loss Per Trade: Risk of ${calc_loss:.2f} USDT exceeds limit of ${max_loss_trade:.2f} USDT."
            )
        else:
            info.append(f"Trade Risk: ${calc_loss:.2f} USDT <= ${max_loss_trade:.2f} limit")

        # 7. Reward to Risk & Take Profit Direction
        if min_rr > 0:
            if not target or target <= 0:
                reasons.append("Reward/Risk: Missing take profit target price.")
            elif side == "BUY" and entry > 0 and target <= entry:
                reasons.append(f"Invalid Target: Take profit (${target:.6f}) must be strictly above entry (${entry:.6f}) for a BUY.")
            elif side == "SELL" and entry > 0 and target >= entry:
                reasons.append(f"Invalid Target: Take profit (${target:.6f}) must be strictly below entry (${entry:.6f}) for a SELL.")
            elif entry > 0 and stop:
                calc_rr = round(abs(target - entry) / abs(entry - stop), 2)
                if calc_rr < min_rr - 0.01:
                    reasons.append(f"Reward/Risk: Ratio {calc_rr:.2f} R is below required minimum {min_rr:.2f} R.")
                else:
                    info.append(f"Reward/Risk: {calc_rr:.2f} R >= {min_rr:.2f} R required")
            elif rr < min_rr - 0.01:
                reasons.append(f"Reward/Risk: Ratio {rr:.2f} R is below required minimum {min_rr:.2f} R.")

        # 8. Daily Loss Limit
        proj_daily_loss = today_loss_at_risk + accum_loss_added + calc_loss
        if proj_daily_loss > max_daily_loss + 0.01:
            reasons.append(
                f"Daily Loss Limit: Cumulative risk would reach ${proj_daily_loss:.2f} USDT, "
                f"exceeding daily cap of ${max_daily_loss:.2f} USDT."
            )
        else:
            info.append(f"Cumulative Daily Risk: ${proj_daily_loss:.2f} / ${max_daily_loss:.2f} USDT")

        # 9. Daily Trade Count Limit
        proj_trades = today_trade_count + accum_trades_added + 1
        if proj_trades > max_trades_day:
            reasons.append(
                f"Daily Trade Cap: This trade would be #{proj_trades} today, exceeding the {max_trades_day} trades/day limit."
            )
        else:
            info.append(f"Trade Count: #{proj_trades} of max {max_trades_day} today")

        # 10. Spread & Orderbook Check
        if check_network and symbol:
            spread_pct, best_bid, best_ask = fetch_binance_spread(symbol)
            if spread_pct is not None:
                if spread_pct > max_slippage:
                    reasons.append(
                        f"Spread Too Wide: Current Binance spread is {spread_pct:.3f}%, "
                        f"exceeding slippage tolerance of {max_slippage}% (Bid: ${best_bid}, Ask: ${best_ask})."
                    )
                else:
                    info.append(f"Binance Spread: {spread_pct:.4f}% within tolerance (<= {max_slippage}%)")
            else:
                warnings.append("Spread Check: Binance orderbook endpoint offline or unreachable; bypassed safely.")

        verdict = "BLOCK" if reasons else "PASS"

        if verdict == "PASS":
            accum_cash_spent += notional
            accum_loss_added += calc_loss
            accum_trades_added += 1

        results.append({
            "index": idx,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "notional": notional,
            "entry": entry,
            "stop": stop,
            "target": target,
            "verdict": verdict,
            "fingerprint": fingerprint_order(order),
            "reasons": reasons,
            "warnings": warnings,
            "info": info,
        })

    pass_count = sum(1 for r in results if r["verdict"] == "PASS")
    block_count = sum(1 for r in results if r["verdict"] == "BLOCK")
    overall = "PASS" if block_count == 0 else "BLOCK"

    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "pass_count": pass_count,
        "passed_count": pass_count,
        "block_count": block_count,
        "blocked_count": block_count,
        "cash_before": cash,
        "cash_after": round(cash - accum_cash_spent, 2),
        "results": results,
        "orders": results,
    }


def write_risk_state(out_path, audit_report):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(audit_report, f, indent=2)
    os.replace(tmp, out_path)


def print_audit_report(report):
    print("=" * 65)
    print(" BINANCE CRYPTO RISK & COMPLIANCE OFFICER REPORT")
    print("=" * 65)
    print(f" Timestamp:    {report['timestamp']}")
    print(f" Overall:      [{report['overall']}] ({report['pass_count']} PASS, {report['block_count']} BLOCK)")
    print(f" Cash Balance: ${report['cash_before']:.2f} USDT -> ${report['cash_after']:.2f} USDT after passes")
    print("-" * 65)

    for r in report["results"]:
        status_box = "[PASS]" if r["verdict"] == "PASS" else "[BLOCK]"
        print(f"{status_box} #{r['index']} {r['side']} {r['qty']} {r['symbol']} (${r['notional']:.2f} USDT)")
        print(f"       Fingerprint: {r['fingerprint']}")
        for inf in r["info"]:
            print(f"       + {inf}")
        for w in r["warnings"]:
            print(f"       ~ WARNING: {w}")
        for rea in r["reasons"]:
            print(f"       - BLOCKED BY: {rea}")
        print("-" * 65)


def main():
    parser = argparse.ArgumentParser(description="trader-risk: Binance Crypto Pre-Trade Risk Checker")
    parser.add_argument("--orders", default="trader/orders.json", help="Path to orders.json")
    parser.add_argument("--config", default="trader/config.json", help="Path to config.json")
    parser.add_argument("--portfolio", default="trader/portfolio.json", help="Path to portfolio.json")
    parser.add_argument("--history", default="trader/trade_history.json", help="Path to trade_history.json")
    parser.add_argument("--out", default="trader/risk.json", help="Path to output risk audit file")
    parser.add_argument("--no-write", action="store_true", help="Do not save risk audit to disk")
    parser.add_argument("--offline", action="store_true", help="Skip live network spread verification")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    orders_path = os.path.join(base_dir, args.orders) if not os.path.isabs(args.orders) else args.orders
    cfg_path = os.path.join(base_dir, args.config) if not os.path.isabs(args.config) else args.config
    port_path = os.path.join(base_dir, args.portfolio) if not os.path.isabs(args.portfolio) else args.portfolio
    hist_path = os.path.join(base_dir, args.history) if not os.path.isabs(args.history) else args.history
    out_path = os.path.join(base_dir, args.out) if not os.path.isabs(args.out) else args.out

    orders_doc = load_json(orders_path, {})
    cfg = load_json(cfg_path, DEFAULT_CONFIG)
    portfolio = load_json(port_path, {"cash": 100.0})
    history = load_json(hist_path, [])

    report = audit_orders(orders_doc, cfg, portfolio, trade_history=history, check_network=not args.offline)

    if not args.no_write:
        write_risk_state(out_path, report)
        # Also mirror to trader/state/risk.json if desired
        state_dir = os.path.join(base_dir, "trader", "state")
        if os.path.exists(state_dir):
            write_risk_state(os.path.join(state_dir, "risk.json"), report)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_audit_report(report)

    sys.exit(0 if report["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
