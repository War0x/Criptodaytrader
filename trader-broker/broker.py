#!/usr/bin/env python3
"""Binance Crypto Order Broker & Paper Engine for trader-broker. Standard library only.

Default Mode: Paper Trading (100% capital safe, zero risk).
Fetches live Binance market price, simulates instant fill, updates trader/portfolio.json,
and logs the execution to trader/trade_history.json.

Live/Testnet Mode:
If config has valid binance_api_key & binance_api_secret and paper=false (or use_testnet=true),
executes signed HMAC-SHA256 Binance REST API v3 orders.
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BINANCE_PROD_URL = "https://api.binance.com"
BINANCE_TESTNET_URL = "https://testnet.binance.vision"


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


def get_binance_market_price(symbol, timeout=4.0):
    """Fetch real-time ticker price from Binance public REST API."""
    sym = symbol.strip().upper()
    url = f"{BINANCE_PROD_URL}/api/v3/ticker/price?symbol={sym}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoBroker/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return float(data.get("price"))
    except Exception as e:
        # Fallback or offline
        return None


def sign_binance_request(params, api_secret):
    """Generates HMAC-SHA256 signature and urlencoded query string for Binance API requests."""
    query_str = urllib.parse.urlencode(params)
    signature = hmac.new(api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return query_str, signature


def get_binance_account_info(api_key, api_secret, testnet=False, timeout=10.0):
    """Fetches Binance spot account balances and permissions using signed HMAC-SHA256."""
    base_url = BINANCE_TESTNET_URL if testnet else BINANCE_PROD_URL
    endpoint = f"{base_url}/api/v3/account"
    timestamp = int(time.time() * 1000)
    params = {"timestamp": str(timestamp), "recvWindow": "5000"}
    query_str, signature = sign_binance_request(params, api_secret)
    full_url = f"{endpoint}?{query_str}&signature={signature}"

    req = urllib.request.Request(
        full_url,
        headers={"X-MBX-APIKEY": api_key, "User-Agent": "BinanceCryptoBroker/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            raise RuntimeError(f"Binance Account API Error {e.code}: {err.get('msg', 'Unknown')} (code: {err.get('code')})") from e
        except Exception:
            raise RuntimeError(f"Binance HTTP {e.code}: {e.reason}") from e


def execute_binance_live_order(symbol, side, qty, order_type, price=None, api_key="", api_secret="", testnet=False):
    """Executes a signed REST order on Binance Live or Spot Testnet using HMAC-SHA256."""
    base_url = BINANCE_TESTNET_URL if testnet else BINANCE_PROD_URL
    endpoint = f"{base_url}/api/v3/order"

    timestamp = int(time.time() * 1000)
    params = {
        "symbol": symbol.strip().upper(),
        "side": side.strip().upper(),
        "type": order_type.strip().upper(),
        "quantity": str(qty),
        "timestamp": str(timestamp),
        "recvWindow": "5000",
    }

    if order_type.strip().upper() == "LIMIT":
        if not price:
            raise ValueError("LIMIT order requires price")
        params["price"] = str(price)
        params["timeInForce"] = "GTC"

    query_str, signature = sign_binance_request(params, api_secret)
    full_url = f"{endpoint}?{query_str}&signature={signature}"

    req = urllib.request.Request(
        full_url,
        data=b"",
        headers={
            "X-MBX-APIKEY": api_key,
            "User-Agent": "BinanceCryptoBroker/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_payload = json.loads(e.read().decode("utf-8"))
            raise RuntimeError(f"Binance Order API Error {e.code}: {err_payload.get('msg', 'Unknown')} (code: {err_payload.get('code')})") from e
        except Exception:
            raise RuntimeError(f"Binance HTTP {e.code}: {e.reason}") from e


def execute_paper_order(order, portfolio, live_price=None):
    """Simulates instant fill of a bracket order at current market price."""
    symbol = order["symbol"].strip().upper()
    side = order["side"].strip().upper()
    qty = float(order.get("qty", order.get("qty_float", 0.0)))
    stop_price = float(order.get("stop_loss_stop_price", 0.0)) if order.get("stop_loss_stop_price") else None
    target_price = float(order.get("take_profit_limit_price", 0.0)) if order.get("take_profit_limit_price") else None
    
    fill_price = live_price if live_price else float(order.get("entry_price", order.get("limit_price", 0.0)))
    if fill_price <= 0:
        fill_price = 1.0

    notional = round(qty * fill_price, 2)
    trade_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Update portfolio
    positions = list(portfolio.get("positions", []))
    cash = float(portfolio.get("cash", 100.0))

    if side == "BUY":
        if cash < notional:
            raise ValueError(f"Insufficient cash ${cash:.2f} to paper buy ${notional:.2f} of {symbol}")
        cash = round(cash - notional, 2)

        # Check if already holding this symbol
        existing = next((p for p in positions if p["symbol"] == symbol), None)
        if existing:
            tot_qty = existing["qty"] + qty
            avg_entry = round(((existing["qty"] * existing["entry_price"]) + notional) / tot_qty, 4)
            existing["qty"] = tot_qty
            existing["entry_price"] = avg_entry
            existing["current_price"] = fill_price
            existing["stop_price"] = stop_price
            existing["target_price"] = target_price
            existing["pnl"] = round((fill_price - avg_entry) * tot_qty, 2)
            existing["pnl_pct"] = round(((fill_price - avg_entry) / avg_entry) * 100.0, 2)
        else:
            positions.append({
                "symbol": symbol,
                "qty": qty,
                "entry_price": fill_price,
                "current_price": fill_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "opened_at": now_iso,
            })
    elif side == "SELL":
        existing = next((p for p in positions if p["symbol"] == symbol), None)
        if not existing or existing["qty"] < qty - 1e-6:
            raise ValueError(f"Cannot sell {qty} {symbol}: holding {existing['qty'] if existing else 0}")
        cash = round(cash + notional, 2)
        existing["qty"] = round(existing["qty"] - qty, 6)
        if existing["qty"] <= 1e-6:
            positions = [p for p in positions if p["symbol"] != symbol]

    # Recalculate total portfolio value
    positions_value = sum(p["qty"] * p.get("current_price", p["entry_price"]) for p in positions)
    portfolio_value = round(cash + positions_value, 2)

    updated_portfolio = {
        "cash": cash,
        "portfolio_value": portfolio_value,
        "currency": "USDT",
        "as_of": now_iso,
        "mode": "PAPER TRADING",
        "exchange": "binance",
        "positions": positions,
    }

    trade_record = {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": fill_price,
        "notional": notional,
        "stop_price": stop_price,
        "target_price": target_price,
        "status": "FILLED",
        "mode": "PAPER",
        "timestamp": now_iso,
        "why": order.get("why", "Paper order execution"),
    }

    return updated_portfolio, trade_record


def flatten_position(symbol, portfolio, trade_history, live_price=None, dry_run=False):
    """Closes an open position at current market price."""
    sym = symbol.strip().upper()
    positions = portfolio.get("positions", [])
    pos = next((p for p in positions if p["symbol"] == sym), None)
    if not pos:
        return None, f"No open position found for {sym} to flatten."

    qty = float(pos["qty"])
    if live_price is None:
        live_price = get_binance_market_price(sym) or float(pos.get("current_price", pos["entry_price"]))
    entry_price = float(pos["entry_price"])
    notional = round(qty * live_price, 2)
    pnl = round((live_price - entry_price) * qty, 2)
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
        "trade_id": f"FLATTEN-{uuid.uuid4().hex[:8].upper()}",
        "symbol": sym,
        "side": "SELL",
        "qty": qty,
        "price": live_price,
        "notional": notional,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "status": "FILLED",
        "mode": portfolio.get("mode", "PAPER"),
        "timestamp": now_iso,
        "why": f"Flatten/Close position: Realized PnL ${pnl:+.2f} ({pnl_pct:+.2f}%)",
    }

    return updated_portfolio, trade_record


def show_status(portfolio, history, cfg=None):
    print("=" * 65)
    print(" BINANCE CRYPTO BROKER - PORTFOLIO STATUS")
    print("=" * 65)
    cash = portfolio.get("cash", 100.0)
    total_val = portfolio.get("portfolio_value", cash)
    mode = portfolio.get("mode", "PAPER TRADING")
    print(f" Account Mode:      {mode}")
    print(f" Cash (USDT):       ${cash:.2f}")
    print(f" Portfolio Value:   ${total_val:.2f} USDT")
    print(f" As of:             {portfolio.get('as_of', 'N/A')}")
    print("-" * 65)
    positions = portfolio.get("positions", [])
    if not positions:
        print(" Active Positions:   None (Flat / In Cash)")
    else:
        print(f" Active Positions:   ({len(positions)})")
        for p in positions:
            pnl_sign = "+" if p.get("pnl", 0) >= 0 else ""
            print(
                f"  * {p['symbol']}: {p['qty']} coins @ avg ${p['entry_price']:.4f} "
                f"(Current: ${p.get('current_price', p['entry_price']):.4f} | "
                f"Stop: ${p.get('stop_price', 0):.4f} | Target: ${p.get('target_price', 0):.4f}) "
                f"PnL: {pnl_sign}${p.get('pnl', 0):.2f} ({pnl_sign}{p.get('pnl_pct', 0):.2f}%)"
            )
    print("-" * 65)
    trades = history if isinstance(history, list) else []
    print(f" Total Trades:      {len(trades)}")
    if trades:
        last = trades[-1]
        print(f" Last Execution:    {last.get('side')} {last.get('qty')} {last.get('symbol')} @ ${last.get('price')} on {last.get('timestamp')}")
    if cfg and not cfg.get("paper", True) and cfg.get("binance_api_key") and cfg.get("binance_api_secret"):
        print("-" * 65)
        print(" Live Binance API Connected:")
        try:
            info = get_binance_account_info(
                cfg["binance_api_key"],
                cfg["binance_api_secret"],
                testnet=cfg.get("use_testnet", False),
                timeout=4.0
            )
            balances = [b for b in info.get("balances", []) if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0]
            print(f"  Account Type: {info.get('accountType', 'SPOT')} | Maker/Taker: {info.get('makerCommission')}/{info.get('takerCommission')}")
            print(f"  Non-Zero Balances ({len(balances)}):")
            for b in balances[:5]:
                print(f"    - {b['asset']}: free={b['free']} locked={b['locked']}")
        except Exception as e:
            print(f"  Live query notice: {e}")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="trader-broker: Binance Crypto Execution Engine")
    subparsers = parser.add_subparsers(dest="command")

    # Command: execute
    exec_parser = subparsers.add_parser("execute", help="Execute proposed orders")
    exec_parser.add_argument("--orders", default="trader/orders.json", help="Path to orders.json")
    exec_parser.add_argument("--config", default="trader/config.json", help="Path to config.json")
    exec_parser.add_argument("--portfolio", default="trader/portfolio.json", help="Path to portfolio.json")
    exec_parser.add_argument("--history", default="trader/trade_history.json", help="Path to trade_history.json")
    exec_parser.add_argument("--risk", default="trader/risk.json", help="Path to risk audit file")
    exec_parser.add_argument("--force", action="store_true", help="Bypass risk check verification")
    exec_parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files")

    # Command: flatten / close
    close_parser = subparsers.add_parser("flatten", help="Flatten / Close an open position")
    close_parser.add_argument("--symbol", required=True, help="Coin symbol to close (e.g. SOLUSDT)")
    close_parser.add_argument("--portfolio", default="trader/portfolio.json")
    close_parser.add_argument("--history", default="trader/trade_history.json")
    close_parser.add_argument("--dry-run", action="store_true")

    # Command: status
    status_parser = subparsers.add_parser("status", help="Show portfolio balance & positions")
    status_parser.add_argument("--portfolio", default="trader/portfolio.json")
    status_parser.add_argument("--history", default="trader/trade_history.json")
    status_parser.add_argument("--config", default="trader/config.json")

    # Command: verify
    verify_parser = subparsers.add_parser("verify", help="Self-test Binance connectivity, live price fetch, and HMAC-SHA256 signing")

    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.command == "verify":
        print("=" * 65)
        print(" TRADER-BROKER SELF-VERIFICATION SUITE")
        print("=" * 65)
        # 1. HMAC signing test
        test_p = {"symbol": "BTCUSDT", "side": "BUY", "timestamp": "1600000000000"}
        q, sig = sign_binance_request(test_p, "test_binance_secret")
        expected_sig = "9ac09c2b09e8c3e247f4223de8bad412f280e607dba423e2e5b0e7f737a9516f"
        assert sig == expected_sig, f"HMAC mismatch: {sig} != {expected_sig}"
        print("[+] 1. HMAC-SHA256 Signature Generator: PASS (valid signature verified)")

        # 2. Live Binance ticker check
        t0 = time.time()
        btc_price = get_binance_market_price("BTCUSDT", timeout=5.0)
        dt = (time.time() - t0) * 1000
        if btc_price:
            print(f"[+] 2. Live Binance Ticker API: PASS (BTCUSDT: ${btc_price:,.2f} in {dt:.0f}ms)")
        else:
            print("[-] 2. Live Binance Ticker API: Offline or Unreachable")

        # 3. Portfolio & History file check
        port_path = os.path.join(base_dir, "trader/portfolio.json")
        hist_path = os.path.join(base_dir, "trader/trade_history.json")
        port = load_json(port_path, {})
        hist = load_json(hist_path, [])
        print(f"[+] 3. Portfolio Integrity: Cash=${port.get('cash', 0):.2f} USDT, Positions={len(port.get('positions', []))}, Trade History={len(hist)} records")
        print("=" * 65)
        return

    if args.command == "status" or not args.command:
        port_path = os.path.join(base_dir, getattr(args, "portfolio", "trader/portfolio.json"))
        hist_path = os.path.join(base_dir, getattr(args, "history", "trader/trade_history.json"))
        cfg_path = os.path.join(base_dir, getattr(args, "config", "trader/config.json"))
        portfolio = load_json(port_path, {"cash": 100.0, "portfolio_value": 100.0, "positions": []})
        history = load_json(hist_path, [])
        cfg = load_json(cfg_path, {})
        show_status(portfolio, history, cfg=cfg)
        return
        history = load_json(hist_path, [])
        show_status(portfolio, history)
        return

    if args.command == "flatten":
        port_path = os.path.join(base_dir, args.portfolio)
        hist_path = os.path.join(base_dir, args.history)
        portfolio = load_json(port_path, {"cash": 100.0, "positions": []})
        history = load_json(hist_path, [])
        up_port, trade_rec = flatten_position(args.symbol, portfolio, history, dry_run=args.dry_run)
        if not up_port:
            print(f"[-] {trade_rec}")
            sys.exit(1)
        if not args.dry_run:
            history.append(trade_rec)
            write_json(port_path, up_port)
            write_json(hist_path, history)
        print(f"[+] Successfully closed position {args.symbol} at ${trade_rec['price']:.4f} USDT | Realized PnL: ${trade_rec['pnl']:+.2f} ({trade_rec['pnl_pct']:+.2f}%)")
        return

    if args.command == "execute":
        orders_path = os.path.join(base_dir, args.orders)
        cfg_path = os.path.join(base_dir, args.config)
        port_path = os.path.join(base_dir, args.portfolio)
        hist_path = os.path.join(base_dir, args.history)
        risk_path = os.path.join(base_dir, args.risk)

        orders_doc = load_json(orders_path, {})
        cfg = load_json(cfg_path, {"paper": True})
        portfolio = load_json(port_path, {"cash": 100.0, "portfolio_value": 100.0, "positions": []})
        history = load_json(hist_path, [])
        risk_doc = load_json(risk_path)

        orders = orders_doc.get("orders", []) if isinstance(orders_doc, dict) else orders_doc
        if not orders:
            print("[-] No orders found in orders.json to execute.")
            sys.exit(1)

        # Check risk audit unless --force
        if not args.force:
            if not risk_doc or risk_doc.get("overall") != "PASS":
                print("[-] Risk Check Block: Cannot execute orders without a passing risk audit in risk.json. Run trader-risk/check.py or use --force.")
                sys.exit(1)

        is_paper = cfg.get("paper", True)
        api_key = cfg.get("binance_api_key", "").strip()
        api_secret = cfg.get("binance_api_secret", "").strip()
        use_testnet = cfg.get("use_testnet", False)

        for order in orders:
            symbol = order["symbol"]
            qty = float(order.get("qty", order.get("qty_float", 0.0)))
            side = order.get("side", "BUY")

            print(f"[*] Processing order: {side} {qty} {symbol}...")

            if is_paper or not api_key or not api_secret:
                live_price = get_binance_market_price(symbol)
                print(f"    Mode: PAPER TRADING (Safe simulation)")
                if live_price:
                    print(f"    Fetched live Binance price: ${live_price:.4f} USDT")
                else:
                    live_price = float(order.get("entry_price", order.get("limit_price", 100.0)))
                    print(f"    Using planned price: ${live_price:.4f} USDT")

                portfolio, trade_rec = execute_paper_order(order, portfolio, live_price=live_price)
                history.append(trade_rec)
                print(f"[+] FILLED: {trade_rec['trade_id']} | {side} {qty} {symbol} @ ${trade_rec['price']:.4f} | Notional: ${trade_rec['notional']:.2f} USDT")
            else:
                # Live / Testnet Binance API execution
                endpoint_desc = "Binance Spot Testnet" if use_testnet else "Binance Live Exchange"
                print(f"    Mode: {endpoint_desc}")
                resp = execute_binance_live_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    order_type=order.get("type", "MARKET"),
                    price=order.get("limit_price"),
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=use_testnet,
                )
                print(f"[+] Live Order Placed on Binance! Order ID: {resp.get('orderId')}")
                trade_record = {
                    "trade_id": str(resp.get("orderId")),
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": float(resp.get("price", 0.0)) or float(order.get("entry_price", 0.0)),
                    "notional": float(resp.get("cummulativeQuoteQty", 0.0)),
                    "status": resp.get("status", "SUBMITTED"),
                    "mode": "TESTNET" if use_testnet else "LIVE",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "raw_response": resp,
                }
                history.append(trade_record)

        if not args.dry_run:
            write_json(port_path, portfolio)
            write_json(hist_path, history)
            # Update orders status to executed
            orders_doc["status"] = "executed"
            orders_doc["executed_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            write_json(orders_path, orders_doc)
            print("[+] Portfolio and Trade History updated successfully.")


if __name__ == "__main__":
    main()
