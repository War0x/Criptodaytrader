#!/usr/bin/env python3
"""Binance Crypto Real-Time Price, Volatility & Position Alerts for trader-alerts. Standard library only.

Features:
  1. check: Monitors active positions in trader/portfolio.json against Stop Loss and Take Profit targets
     using live Binance public prices. Supports --auto-flatten to auto-close triggered positions.
     Also evaluates custom rules in trader/alerts.json (price targets, stops, pct moves).
  2. quote: Fetches live Binance ticker prices for symbols (or --holdings) and writes to trader/prices.json.
  3. add: Adds custom price target / stop alerts to trader/alerts.json (supports --from-plan, --stop, --target, --pct).
  4. list: Displays active position status, unrealized PnL, and configured price triggers.
  5. reset: Resets triggered alert state so rules can fire again.
  6. clock: Displays Binance 24/7 market status, daily candle reset, and 8h funding rate intervals.
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
import uuid

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_24HR_URL = "https://api.binance.com/api/v3/ticker/24hr"


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def format_price(price):
    """Format crypto prices preserving sub-dollar precision."""
    if price is None:
        return "N/A"
    try:
        p = float(price)
    except (ValueError, TypeError):
        return str(price)
    if p >= 100.0:
        return f"{p:.2f}"
    if p >= 1.0:
        return f"{p:.4f}"
    if p >= 0.001:
        return f"{p:.6f}"
    return f"{p:.8f}"


def fetch_binance_price(symbol, timeout=4.0):
    """Fetch real-time spot price for a single symbol from Binance public REST API."""
    sym = symbol.strip().upper()
    url = f"{BINANCE_PRICE_URL}?symbol={sym}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoAlerts/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return float(data.get("price"))
    except Exception:
        return None


def fetch_multiple_prices(symbols, timeout=5.0, offline_dir=None):
    """Fetch live prices for a list of symbols with batch or individual fallback."""
    prices = {}
    if not symbols:
        return prices

    # Check offline fixtures if offline_dir provided
    if offline_dir and os.path.isdir(offline_dir):
        for s in symbols:
            s_up = s.strip().upper()
            fix_path = os.path.join(offline_dir, f"{s_up}.json")
            if os.path.exists(fix_path):
                data = load_json(fix_path, {})
                if "price" in data:
                    prices[s_up] = float(data["price"])
        if len(prices) == len(symbols):
            return prices

    # Batch fetch from Binance
    try:
        url = BINANCE_PRICE_URL
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoAlerts/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            sym_set = {s.strip().upper() for s in symbols}
            for item in data:
                sym = item.get("symbol", "").upper()
                if sym in sym_set:
                    prices[sym] = float(item.get("price", 0.0))
            if len(prices) == len(symbols):
                return prices
    except Exception:
        pass

    # Individual fallback
    for s in symbols:
        s_up = s.strip().upper()
        if s_up not in prices:
            p = fetch_binance_price(s_up, timeout=timeout)
            if p is not None:
                prices[s_up] = p

    return prices


def flatten_position_internal(symbol, portfolio, trade_history, live_price, reason="Exit trigger"):
    """Closes an open position, updates portfolio cash and appends to trade_history."""
    sym = symbol.strip().upper()
    positions = list(portfolio.get("positions", []))
    pos = next((p for p in positions if p["symbol"] == sym), None)
    if not pos:
        return portfolio, None

    qty = float(pos["qty"])
    entry_price = float(pos["entry_price"])
    notional = round(qty * live_price, 2)
    pnl = round((live_price - entry_price) * qty, 4)
    pnl_pct = round(((live_price - entry_price) / entry_price) * 100.0, 2)
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cash = round(float(portfolio.get("cash", 0.0)) + notional, 2)
    remaining_positions = [p for p in positions if p["symbol"] != sym]
    pos_val = sum(p["qty"] * p.get("current_price", p["entry_price"]) for p in remaining_positions)
    port_val = round(cash + pos_val, 2)

    updated_portfolio = {
        "cash": cash,
        "portfolio_value": port_val,
        "currency": "USDT",
        "as_of": now_iso,
        "mode": portfolio.get("mode", "PAPER TRADING"),
        "exchange": "binance",
        "positions": remaining_positions,
    }

    trade_record = {
        "trade_id": f"AUTOFLAT-{uuid.uuid4().hex[:8].upper()}",
        "symbol": sym,
        "side": "SELL",
        "qty": qty,
        "price": live_price,
        "notional": notional,
        "pnl": round(pnl, 2),
        "pnl_pct": pnl_pct,
        "status": "FILLED",
        "mode": portfolio.get("mode", "PAPER"),
        "timestamp": now_iso,
        "why": f"Auto-Flatten [{reason}]: Realized PnL ${pnl:+.2f} ({pnl_pct:+.2f}%)",
    }
    return updated_portfolio, trade_record


def evaluate_rules(rules, prices, state=None, now_iso=None):
    """Evaluates custom alert rules (stop, target, above, below, pct_move) against prices."""
    if state is None:
        state = {"rung": {}}
    rung = state.setdefault("rung", {})
    if now_iso is None:
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    triggered = []
    for r in rules:
        rid = r.get("id") or f"{r.get('symbol', 'GEN')}-{r.get('type')}"
        sym = r.get("symbol", "").upper()
        rtype = r.get("type", "").lower()
        price = prices.get(sym)

        if price is None:
            continue

        threshold = r.get("price")
        is_hit = False
        msg = ""

        if rtype in ("stop", "below"):
            if threshold is not None and price <= threshold:
                is_hit = True
                msg = f"🚨 [RULE TRIGGERED] {sym} is ${format_price(price)} <= {rtype} ${format_price(threshold)}"
        elif rtype in ("target", "above"):
            if threshold is not None and price >= threshold:
                is_hit = True
                msg = f"🎯 [RULE TRIGGERED] {sym} is ${format_price(price)} >= {rtype} ${format_price(threshold)}"
        elif rtype == "pct_move":
            pct_target = r.get("pct", 3.0)
            ref_price = r.get("ref", price)
            if ref_price and ref_price > 0:
                pct_change = ((price - ref_price) / ref_price) * 100.0
                if abs(pct_change) >= pct_target:
                    is_hit = True
                    msg = f"⚡ [VOLATILITY ALERT] {sym} moved {pct_change:+.2f}% to ${format_price(price)} (threshold {pct_target}%)"

        if is_hit:
            if rid not in rung:
                triggered.append({
                    "id": rid,
                    "symbol": sym,
                    "type": rtype,
                    "price": price,
                    "threshold": threshold,
                    "message": msg,
                    "timestamp": now_iso,
                })
                rung[rid] = now_iso
        else:
            # If rearm enabled, clear rung status when price recovers
            if r.get("rearm") and rid in rung:
                del rung[rid]

    state["last_check"] = now_iso
    return triggered, state


def check_positions(portfolio_path, prices_path=None, auto_flatten=False, history_path=None, alerts_path=None, offline_dir=None):
    """Checks open positions and custom rules against live prices. Auto-flattens if configured."""
    portfolio = load_json(portfolio_path, {"cash": 100.0, "portfolio_value": 100.0, "positions": []})
    positions = portfolio.get("positions", [])
    alerts_doc = load_json(alerts_path, {"rules": [], "rung": {}}) if alerts_path else {"rules": [], "rung": {}}
    rules = alerts_doc.get("rules", [])

    symbols_to_check = set()
    for pos in positions:
        symbols_to_check.add(pos["symbol"].upper())
    for r in rules:
        if r.get("symbol"):
            symbols_to_check.add(r["symbol"].upper())

    prices = fetch_multiple_prices(list(symbols_to_check), offline_dir=offline_dir)
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    alerts = []
    positions_updated = False
    symbols_to_flatten = []

    print("=" * 68)
    print(" 🔔 BINANCE CRYPTO REAL-TIME POSITION & PRICE ALERTS")
    print("=" * 68)

    if not positions:
        print("[i] No open positions in portfolio to monitor.")
    else:
        print(f"[*] Monitoring {len(positions)} open position(s):")
        for pos in positions:
            sym = pos["symbol"].upper()
            entry = float(pos["entry_price"])
            current_price = prices.get(sym) or pos.get("current_price", entry)
            stop = float(pos.get("stop_price", 0.0)) if pos.get("stop_price") else None
            target = float(pos.get("target_price", 0.0)) if pos.get("target_price") else None
            qty = float(pos["qty"])

            pos["current_price"] = current_price
            pnl = round((current_price - entry) * qty, 2)
            pnl_pct = round(((current_price - entry) / entry) * 100.0, 2)
            pos["pnl"] = pnl
            pos["pnl_pct"] = pnl_pct
            positions_updated = True

            # Evaluate Stop Loss
            if stop and current_price <= stop:
                alert_msg = (
                    f"🚨 [STOP LOSS TRIGGERED] {sym}: Current ${format_price(current_price)} <= Stop ${format_price(stop)} "
                    f"| Loss: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
                )
                alerts.append({
                    "type": "STOP_LOSS",
                    "symbol": sym,
                    "message": alert_msg,
                    "price": current_price,
                    "threshold": stop,
                    "pnl": pnl,
                })
                print(f"  {alert_msg}")
                if auto_flatten:
                    symbols_to_flatten.append((sym, current_price, "STOP_LOSS"))

            # Evaluate Take Profit
            elif target and current_price >= target:
                alert_msg = (
                    f"🎯 [TAKE PROFIT TRIGGERED] {sym}: Current ${format_price(current_price)} >= Target ${format_price(target)} "
                    f"| Profit: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
                )
                alerts.append({
                    "type": "TAKE_PROFIT",
                    "symbol": sym,
                    "message": alert_msg,
                    "price": current_price,
                    "threshold": target,
                    "pnl": pnl,
                })
                print(f"  {alert_msg}")
                if auto_flatten:
                    symbols_to_flatten.append((sym, current_price, "TAKE_PROFIT"))
            else:
                pnl_sign = "+" if pnl >= 0 else ""
                print(
                    f"  [OK] {sym}: ${format_price(current_price)} (Entry: ${format_price(entry)} | "
                    f"Stop: ${format_price(stop)} | Target: ${format_price(target)}) "
                    f"PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)"
                )

    # Check custom rules
    if rules:
        print("-" * 68)
        print(f"[*] Checking {len(rules)} custom price rule(s)...")
        rule_triggers, updated_state = evaluate_rules(rules, prices, state=alerts_doc, now_iso=now_iso)
        for trig in rule_triggers:
            alerts.append(trig)
            print(f"  {trig['message']}")
        if alerts_path:
            write_json(alerts_path, alerts_doc)

    # Auto-flatten positions if triggered
    if auto_flatten and symbols_to_flatten:
        print("-" * 68)
        history = load_json(history_path, []) if history_path else []
        for sym, trig_price, reason in symbols_to_flatten:
            portfolio, trade_rec = flatten_position_internal(sym, portfolio, history, trig_price, reason=reason)
            if trade_rec:
                history.append(trade_rec)
                print(f"  ⚡ [AUTO-FLATTEN EXECUTED] Closed {sym} @ ${format_price(trig_price)} USDT | Realized PnL: ${trade_rec['pnl']:+.2f}")
        if history_path:
            write_json(history_path, history)
        positions_updated = True

    # Persist updated portfolio
    if positions_updated:
        cash = float(portfolio.get("cash", 0.0))
        rem_pos = portfolio.get("positions", [])
        pos_val = sum(p["qty"] * p["current_price"] for p in rem_pos)
        portfolio["portfolio_value"] = round(cash + pos_val, 2)
        portfolio["as_of"] = now_iso
        write_json(portfolio_path, portfolio)

    # Persist live prices cache
    if prices_path:
        write_json(prices_path, {"as_of": now_iso, "prices": prices})

    print("=" * 68)
    return alerts


def rules_from_plan(plan):
    """Generates stop and target rules from trader/plan.json."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise ValueError("Invalid plan.json: missing symbol.")
    sym = plan["symbol"].upper()
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rules = []

    # Stop Loss rule
    stop_price = plan.get("stop")
    if stop_price:
        rules.append({
            "id": f"{sym.lower()}-plan-stop",
            "symbol": sym,
            "type": "stop",
            "price": float(stop_price),
            "note": plan.get("stop_detail", "Plan Stop Loss"),
            "created_at": now_iso,
        })

    # Target 1R rule
    target_1r = plan.get("target") or plan.get("target_1r")
    if target_1r:
        rules.append({
            "id": f"{sym.lower()}-plan-target",
            "symbol": sym,
            "type": "target",
            "price": float(target_1r),
            "note": "Plan Take Profit 1R",
            "created_at": now_iso,
        })

    # Optional Target 2R rule
    target_2r = plan.get("target_2r")
    if target_2r:
        rules.append({
            "id": f"{sym.lower()}-plan-target-2r",
            "symbol": sym,
            "type": "target",
            "price": float(target_2r),
            "note": "Plan Take Profit 2R",
            "created_at": now_iso,
        })

    return rules


def update_prices_cache(symbols, out_path):
    """Fetches real-time prices for symbols and writes them to out_path."""
    prices = fetch_multiple_prices(symbols)
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "as_of": now_iso,
        "source": "binance_public_rest",
        "prices": prices,
    }
    write_json(out_path, doc)
    print(f"[+] Updated {len(prices)} prices in {out_path}:")
    for s, p in prices.items():
        print(f"    {s}: ${format_price(p)} USDT")


def market_status():
    """Returns Binance 24/7 crypto market session info and funding cycle."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    curr_hour = now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0
    funding_hours = [0.0, 8.0, 16.0, 24.0]
    next_fh = next(h for h in funding_hours if h > curr_hour)
    hours_left = next_fh - curr_hour
    mins_left = int(hours_left * 60)

    hours_to_daily = 24.0 - curr_hour

    return {
        "market": "Binance 24/7 Spot & Futures",
        "status": "OPEN (24/7)",
        "now_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "next_funding_utc": f"{int(next_fh % 24):02d}:00 UTC",
        "next_funding_in_minutes": mins_left,
        "daily_candle_close_in_hours": round(hours_to_daily, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="trader-alerts: Binance Real-Time Position & Price Alerts")
    subparsers = parser.add_subparsers(dest="command")

    # Command: check
    check_parser = subparsers.add_parser("check", help="Check active positions and custom rules against live prices")
    check_parser.add_argument("--portfolio", default="trader/portfolio.json")
    check_parser.add_argument("--prices", default="trader/prices.json")
    check_parser.add_argument("--alerts", default="trader/alerts.json")
    check_parser.add_argument("--history", default="trader/trade_history.json")
    check_parser.add_argument("--auto-flatten", action="store_true", help="Auto-close positions hitting stop or target")
    check_parser.add_argument("--offline", help="Directory with offline fixture files for quotes")

    # Command: quote
    quote_parser = subparsers.add_parser("quote", help="Fetch live price quote for symbols")
    quote_parser.add_argument("symbols", nargs="*", default=[], help="Crypto symbols e.g. SOLUSDT BTCUSDT")
    quote_parser.add_argument("--holdings", action="store_true", help="Quote all symbols in portfolio and plan")
    quote_parser.add_argument("--portfolio", default="trader/portfolio.json")
    quote_parser.add_argument("--plan", default="trader/plan.json")
    quote_parser.add_argument("--write", action="store_true", help="Write to trader/prices.json")
    quote_parser.add_argument("--out", default="trader/prices.json")

    # Command: add
    add_parser = subparsers.add_parser("add", help="Add price, stop, target or volatility alerts")
    add_parser.add_argument("--from-plan", action="store_true", help="Import stop and target from trader/plan.json")
    add_parser.add_argument("--plan", default="trader/plan.json")
    add_parser.add_argument("--symbol", help="Crypto pair e.g. BTCUSDT, DOGEUSDT")
    add_parser.add_argument("--stop", type=float, help="Stop loss alert price")
    add_parser.add_argument("--target", type=float, help="Take profit alert price")
    add_parser.add_argument("--above", type=float, help="Alert when price >= threshold")
    add_parser.add_argument("--below", type=float, help="Alert when price <= threshold")
    add_parser.add_argument("--pct", type=float, help="Percentage move volatility alert")
    add_parser.add_argument("--note", default="", help="Note or reason for alert")
    add_parser.add_argument("--rearm", action="store_true", help="Rearm rule when price crosses back")
    add_parser.add_argument("--out", default="trader/alerts.json")

    # Command: list
    list_parser = subparsers.add_parser("list", help="List active positions, PnL, and configured alert rules")
    list_parser.add_argument("--portfolio", default="trader/portfolio.json")
    list_parser.add_argument("--alerts", default="trader/alerts.json")

    # Command: reset
    reset_parser = subparsers.add_parser("reset", help="Reset triggered alerts so they can fire again")
    reset_parser.add_argument("rule_id", nargs="?", default="all", help="Specific alert rule ID or 'all'")
    reset_parser.add_argument("--alerts", default="trader/alerts.json")

    # Command: clock
    clock_parser = subparsers.add_parser("clock", help="Display Binance 24/7 market status and funding schedule")

    args = parser.parse_args()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Resolve paths
    def resolve_path(p):
        if not p:
            return p
        return p if os.path.isabs(p) else os.path.join(base_dir, p)

    if args.command == "clock":
        st = market_status()
        print("=" * 65)
        print(" BINANCE 24/7 CRYPTO MARKET CLOCK")
        print("=" * 65)
        print(f" Market Status:      {st['status']}")
        print(f" Current UTC Time:   {st['now_utc']}")
        print(f" Next 8h Funding:    {st['next_funding_utc']} (in {st['next_funding_in_minutes']} mins)")
        print(f" Daily Candle Reset: 00:00 UTC (in {st['daily_candle_close_in_hours']} hours)")
        print("=" * 65)
        return

    if args.command == "quote":
        syms = [s.strip().upper() for s in args.symbols if s.strip()]
        if args.holdings:
            port = load_json(resolve_path(args.portfolio), {})
            for p in port.get("positions", []):
                syms.append(p["symbol"].strip().upper())
            plan = load_json(resolve_path(args.plan), {})
            if plan and plan.get("symbol"):
                syms.append(plan["symbol"].strip().upper())
            syms = sorted(list(set(syms)))

        if not syms:
            print("[-] No symbols provided to quote. Specify symbols or use --holdings.")
            sys.exit(1)

        out_path = resolve_path(args.out)
        if args.write:
            update_prices_cache(syms, out_path)
        else:
            prices = fetch_multiple_prices(syms)
            print(f"[*] Fetched {len(prices)} Binance live price(s):")
            for s, p in prices.items():
                print(f"    {s}: ${format_price(p)} USDT")
        return

    if args.command == "add":
        out_path = resolve_path(args.out)
        alerts_doc = load_json(out_path, {"rules": [], "rung": {}})
        rules = list(alerts_doc.get("rules", []))
        added_count = 0

        if args.from_plan:
            plan_path = resolve_path(args.plan)
            plan = load_json(plan_path)
            if not plan:
                print(f"[-] Cannot load plan file from {plan_path}")
                sys.exit(1)
            plan_rules = rules_from_plan(plan)
            for pr in plan_rules:
                # Replace existing rule with same ID if present
                rules = [r for r in rules if r.get("id") != pr["id"]]
                rules.append(pr)
                added_count += 1
                print(f"[+] Added rule from plan: {pr['id']} ({pr['type']} @ ${format_price(pr['price'])})")
        else:
            if not args.symbol:
                print("[-] --symbol is required when not adding --from-plan.")
                sys.exit(1)
            sym = args.symbol.strip().upper()
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if args.stop is not None:
                rid = f"{sym.lower()}-stop"
                rules = [r for r in rules if r.get("id") != rid]
                rules.append({"id": rid, "symbol": sym, "type": "stop", "price": args.stop, "note": args.note or "Stop Loss", "rearm": args.rearm, "created_at": now_iso})
                added_count += 1
                print(f"[+] Added stop rule: {rid} ({sym} <= ${format_price(args.stop)})")

            if args.target is not None:
                rid = f"{sym.lower()}-target"
                rules = [r for r in rules if r.get("id") != rid]
                rules.append({"id": rid, "symbol": sym, "type": "target", "price": args.target, "note": args.note or "Take Profit", "rearm": args.rearm, "created_at": now_iso})
                added_count += 1
                print(f"[+] Added target rule: {rid} ({sym} >= ${format_price(args.target)})")

            if args.above is not None:
                rid = f"{sym.lower()}-above"
                rules = [r for r in rules if r.get("id") != rid]
                rules.append({"id": rid, "symbol": sym, "type": "above", "price": args.above, "note": args.note or "Price Above", "rearm": args.rearm, "created_at": now_iso})
                added_count += 1
                print(f"[+] Added above rule: {rid} ({sym} >= ${format_price(args.above)})")

            if args.below is not None:
                rid = f"{sym.lower()}-below"
                rules = [r for r in rules if r.get("id") != rid]
                rules.append({"id": rid, "symbol": sym, "type": "below", "price": args.below, "note": args.note or "Price Below", "rearm": args.rearm, "created_at": now_iso})
                added_count += 1
                print(f"[+] Added below rule: {rid} ({sym} <= ${format_price(args.below)})")

            if args.pct is not None:
                rid = f"{sym.lower()}-pct-{int(args.pct)}"
                rules = [r for r in rules if r.get("id") != rid]
                rules.append({"id": rid, "symbol": sym, "type": "pct_move", "pct": args.pct, "note": args.note or "Volatility Alert", "rearm": args.rearm, "created_at": now_iso})
                added_count += 1
                print(f"[+] Added pct move rule: {rid} ({sym} move >= {args.pct}%)")

        alerts_doc["rules"] = rules
        write_json(out_path, alerts_doc)
        print(f"[+] Total {added_count} alert rule(s) saved to {out_path}.")
        return

    if args.command == "reset":
        alerts_path = resolve_path(args.alerts)
        alerts_doc = load_json(alerts_path, {"rules": [], "rung": {}})
        target = args.rule_id
        rung = alerts_doc.get("rung", {})
        if target == "all":
            cleared = len(rung)
            alerts_doc["rung"] = {}
            print(f"[+] Reset all ({cleared}) triggered alerts.")
        else:
            if target in rung:
                del rung[target]
                print(f"[+] Reset triggered alert '{target}'.")
            else:
                print(f"[i] Alert '{target}' was not in triggered/rung state.")
        write_json(alerts_path, alerts_doc)
        return

    if args.command == "list" or not args.command:
        port_path = resolve_path(getattr(args, "portfolio", "trader/portfolio.json"))
        alerts_path = resolve_path(getattr(args, "alerts", "trader/alerts.json"))

        portfolio = load_json(port_path, {"cash": 100.0, "portfolio_value": 100.0, "positions": []})
        alerts_doc = load_json(alerts_path, {"rules": [], "rung": {}})
        positions = portfolio.get("positions", [])
        rules = alerts_doc.get("rules", [])
        rung = alerts_doc.get("rung", {})

        print("=" * 65)
        print(" ACTIVE BINANCE CRYPTO POSITIONS")
        print("=" * 65)
        if not positions:
            print(" No open positions (Account is 100% Cash / Flat).")
        for p in positions:
            pnl_sign = "+" if p.get("pnl", 0) >= 0 else ""
            print(f" Symbol: {p['symbol']} | Qty: {p['qty']}")
            print(f"   Entry:   ${format_price(p['entry_price'])}")
            print(f"   Current: ${format_price(p.get('current_price', p['entry_price']))}")
            print(f"   Stop:    ${format_price(p.get('stop_price'))}")
            print(f"   Target:  ${format_price(p.get('target_price'))}")
            print(f"   PnL:     {pnl_sign}${p.get('pnl', 0):.2f} ({pnl_sign}{p.get('pnl_pct', 0):.2f}%)")
            print("-" * 65)

        print("=" * 65)
        print(f" CONFIGURED ALERT RULES ({len(rules)})")
        print("=" * 65)
        if not rules:
            print(" No custom alert rules configured.")
        for r in rules:
            rid = r.get("id")
            status_str = f"🔔 TRIGGERED ({rung[rid]})" if rid in rung else "⏳ WATCHING"
            thresh = f"${format_price(r.get('price'))}" if r.get("price") is not None else f"{r.get('pct')}%"
            print(f" [{status_str}] {rid} | {r.get('symbol')} {r.get('type')} {thresh}")
            if r.get("note"):
                print(f"    Note: {r['note']}")
        print("=" * 65)
        return

    if args.command == "check":
        port_path = resolve_path(args.portfolio)
        prices_path = resolve_path(args.prices)
        alerts_path = resolve_path(args.alerts)
        history_path = resolve_path(args.history)
        offline_dir = resolve_path(args.offline) if args.offline else None

        alerts = check_positions(
            portfolio_path=port_path,
            prices_path=prices_path,
            auto_flatten=args.auto_flatten,
            history_path=history_path,
            alerts_path=alerts_path,
            offline_dir=offline_dir,
        )
        # Clean exit code 0 so automated pipelines and office_app proceed without interruption
        sys.exit(0)


if __name__ == "__main__":
    main()
