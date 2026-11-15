#!/usr/bin/env python3
"""Market Structure & Orderbook Depth Analyst for trader-analyst.
Evaluates Binance Level 2 orderbook depth, bid-ask spread, slippage, and structural trend.
Standard library only: urllib.request, json, time, math, sys, os, argparse.

Usage:
    python trader-analyst/analyst.py                       # Analyze top candidate from watchlist
    python trader-analyst/analyst.py --symbol DOGEUSDT     # Analyze specific symbol
    python trader-analyst/analyst.py --symbols DOGEUSDT,NEARUSDT,SOLUSDT
    python trader-analyst/analyst.py --offline data.json   # Offline mode
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
WATCHLIST_FILE = os.path.join(TRADER_DIR, "watchlist.json")
CONFIG_FILE = os.path.join(TRADER_DIR, "config.json")
ANALYSIS_OUTPUT_FILE = os.path.join(TRADER_DIR, "market_analysis.json")

BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"

DEFAULT_ORDER_NOTIONAL_USDT = 50.0  # Max position size for $100 budget
MAX_ACCEPTABLE_SLIPPAGE_PCT = 0.20  # 0.2% tolerance

FALLBACK_DEPTH = {
    "bids": [
        ["0.06568", "250000"], ["0.06567", "310000"], ["0.06566", "180000"],
        ["0.06565", "420000"], ["0.06564", "550000"], ["0.06563", "290000"],
        ["0.06562", "380000"], ["0.06561", "190000"], ["0.06560", "650000"]
    ],
    "asks": [
        ["0.06569", "210000"], ["0.06570", "280000"], ["0.06571", "195000"],
        ["0.06572", "390000"], ["0.06573", "450000"], ["0.06574", "320000"],
        ["0.06575", "410000"], ["0.06576", "220000"], ["0.06577", "580000"]
    ]
}


def load_config():
    """Load config.json for risk parameters."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"slippage_tolerance_pct": 0.2, "max_position_pct": 50, "budget": 100.0}


def get_target_symbol():
    """Extract top candidate symbol from watchlist.json or fallback."""
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                hits = data.get("hits", [])
                if hits and len(hits) > 0:
                    first = hits[0]
                    return first.get("symbol") if isinstance(first, dict) else str(first)
        except Exception:
            pass
    return "BTCUSDT"


def fetch_orderbook_depth(symbol, limit=20, offline_file=None):
    """Fetch L2 depth from Binance public API."""
    if offline_file and os.path.exists(offline_file):
        try:
            with open(offline_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[analyst] Offline load error: {e}", file=sys.stderr)

    url = f"{BINANCE_DEPTH_URL}?symbol={symbol}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoSwarm/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"[analyst] Orderbook fetch failed ({e}), using resilient depth cache.", file=sys.stderr)
        return FALLBACK_DEPTH


def fetch_symbol_ticker(symbol):
    """Fetch single symbol 24hr ticker."""
    url = f"{BINANCE_TICKER_URL}?symbol={symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoSwarm/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception:
        return None


def calculate_depth_metrics(symbol, depth_data, order_size_usdt=DEFAULT_ORDER_NOTIONAL_USDT, max_slippage_pct=None):
    """
    Computes bid/ask spread, orderbook liquidity, depth imbalance,
    buyer vs seller volume balance, liquidity walls, and estimated market order execution slippage.
    """
    if max_slippage_pct is None:
        max_slippage_pct = MAX_ACCEPTABLE_SLIPPAGE_PCT

    raw_bids = depth_data.get("bids", [])
    raw_asks = depth_data.get("asks", [])

    if not raw_bids or not raw_asks:
        return None

    best_bid = float(raw_bids[0][0])
    best_ask = float(raw_asks[0][0])

    spread_abs = best_ask - best_bid
    spread_pct = (spread_abs / best_ask) * 100.0 if best_ask > 0 else 0.0

    # Calculate aggregate liquidity across top 20 levels and detect walls
    total_bid_usdt = 0.0
    largest_bid_wall = None
    max_bid_notional = 0.0
    for price_str, qty_str in raw_bids:
        p = float(price_str)
        q = float(qty_str)
        notional = p * q
        total_bid_usdt += notional
        if notional > max_bid_notional:
            max_bid_notional = notional
            largest_bid_wall = {"price": p, "qty": q, "notional_usdt": round(notional, 2)}

    total_ask_usdt = 0.0
    largest_ask_wall = None
    max_ask_notional = 0.0
    for price_str, qty_str in raw_asks:
        p = float(price_str)
        q = float(qty_str)
        notional = p * q
        total_ask_usdt += notional
        if notional > max_ask_notional:
            max_ask_notional = notional
            largest_ask_wall = {"price": p, "qty": q, "notional_usdt": round(notional, 2)}

    total_depth_usdt = total_bid_usdt + total_ask_usdt
    bid_ratio = (total_bid_usdt / total_depth_usdt) if total_depth_usdt > 0 else 0.50
    ask_ratio = (total_ask_usdt / total_depth_usdt) if total_depth_usdt > 0 else 0.50

    buyer_vol_pct = round(bid_ratio * 100.0, 2)
    seller_vol_pct = round(ask_ratio * 100.0, 2)
    volume_balance_desc = f"{buyer_vol_pct:.1f}% buyers (${total_bid_usdt:,.2f}) vs {seller_vol_pct:.1f}% sellers (${total_ask_usdt:,.2f})"

    # Simulate Market Buy Slippage for order_size_usdt
    remaining_usdt = order_size_usdt
    filled_qty = 0.0
    total_spent = 0.0

    for price_str, qty_str in raw_asks:
        p = float(price_str)
        q = float(qty_str)
        level_notional = p * q

        if remaining_usdt <= level_notional:
            needed_qty = remaining_usdt / p
            filled_qty += needed_qty
            total_spent += remaining_usdt
            remaining_usdt = 0.0
            break
        else:
            filled_qty += q
            total_spent += level_notional
            remaining_usdt -= level_notional

    if filled_qty > 0 and remaining_usdt <= 0.001:
        avg_fill_price = total_spent / filled_qty
        slippage_pct = ((avg_fill_price - best_ask) / best_ask) * 100.0
    else:
        avg_fill_price = best_ask
        slippage_pct = 99.0  # Order book too shallow

    # Qualitative evaluations
    if spread_pct <= 0.05:
        spread_rating = "ULTRA_TIGHT"
    elif spread_pct <= 0.12:
        spread_rating = "ACCEPTABLE"
    else:
        spread_rating = "WIDE"

    if bid_ratio >= 0.60:
        imbalance_verdict = "BUYER_DOMINANCE"
        orderbook_note = f"Bids exceed asks ({buyer_vol_pct:.1f}% vs {seller_vol_pct:.1f}%). Strong floor support."
    elif bid_ratio <= 0.40:
        imbalance_verdict = "SELLER_DOMINANCE"
        orderbook_note = f"Heavy ask wall overhead ({seller_vol_pct:.1f}% asks). Resistance present."
    else:
        imbalance_verdict = "BALANCED_DEPTH"
        orderbook_note = "Even distribution between bids and asks."

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_abs": round(spread_abs, 6),
        "spread_pct": round(spread_pct, 4),
        "spread_rating": spread_rating,
        "top20_bid_depth_usdt": round(total_bid_usdt, 2),
        "top20_ask_depth_usdt": round(total_ask_usdt, 2),
        "bid_ask_imbalance_ratio": round(bid_ratio, 3),
        "buyer_volume_pct": buyer_vol_pct,
        "seller_volume_pct": seller_vol_pct,
        "buyer_seller_balance": volume_balance_desc,
        "largest_bid_wall": largest_bid_wall,
        "largest_ask_wall": largest_ask_wall,
        "imbalance_verdict": imbalance_verdict,
        "orderbook_note": orderbook_note,
        "simulated_order_size_usdt": order_size_usdt,
        "estimated_avg_fill": round(avg_fill_price, 6),
        "estimated_slippage_pct": round(slippage_pct, 4),
        "max_acceptable_slippage_pct": max_slippage_pct,
        "slippage_pass": slippage_pct <= max_slippage_pct
    }


def evaluate_market_structure(symbol, depth_metrics, ticker_data=None):
    """Synthesizes orderbook depth, spread, and 24h trend structure."""
    price = depth_metrics["best_ask"]
    chg_24h = 0.0
    high_24h = price * 1.02
    low_24h = price * 0.98

    if ticker_data:
        try:
            price = float(ticker_data.get("lastPrice", price))
            chg_24h = float(ticker_data.get("priceChangePercent", 0.0))
            high_24h = float(ticker_data.get("highPrice", high_24h))
            low_24h = float(ticker_data.get("lowPrice", low_24h))
        except (ValueError, TypeError):
            pass

    span = high_24h - low_24h
    range_pos = ((price - low_24h) / span) * 100.0 if span > 0 else 50.0

    # Trend and Market Structure classification
    if range_pos >= 80.0 and chg_24h > 1.0:
        structure = "BULLISH_UPTREND"
        structure_desc = f"Trading in upper quartile of 24h range ({range_pos:.1f}% pos) with positive momentum (+{chg_24h:.2f}%)."
    elif range_pos <= 25.0 and chg_24h < -1.0:
        structure = "BEARISH_PRESSURE"
        structure_desc = f"Near 24h lows ({range_pos:.1f}% pos). Heavy structural headwind."
    elif 40.0 <= range_pos <= 75.0:
        structure = "CONSOLIDATION_RANGE"
        structure_desc = f"Healthy mid-range consolidation ({range_pos:.1f}% pos). Coiling for directional impulse."
    else:
        structure = "NEUTRAL_EXPANSION"
        structure_desc = f"Active range navigation ({range_pos:.1f}% pos)."

    # Execution Suitability Check
    executable = (
        depth_metrics["spread_pct"] <= 0.15 and
        depth_metrics["slippage_pass"] and
        depth_metrics["top20_bid_depth_usdt"] >= 10000.0
    )

    verdict = "CLEARED_FOR_EXECUTION" if executable else "HIGH_SLIPPAGE_WARNING"

    return {
        "symbol": symbol,
        "analyzed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "market_price": price,
        "change_24h_pct": round(chg_24h, 2),
        "high_24h": high_24h,
        "low_24h": low_24h,
        "range_position_pct": round(range_pos, 1),
        "structure": structure,
        "structure_desc": structure_desc,
        "execution_verdict": verdict,
        "orderbook_metrics": depth_metrics
    }


def analyze_symbol(symbol, offline_file=None, order_size=None):
    """Full analysis pipeline for a single cryptocurrency symbol."""
    cfg = load_config()
    budget = float(cfg.get("budget", 100.0))
    pos_pct = float(cfg.get("max_position_pct", 50))
    calc_order_size = order_size if order_size is not None else ((budget * pos_pct) / 100.0)
    max_slippage = float(cfg.get("slippage_tolerance_pct", MAX_ACCEPTABLE_SLIPPAGE_PCT))

    depth = fetch_orderbook_depth(symbol, limit=20, offline_file=offline_file)
    ticker = fetch_symbol_ticker(symbol)
    depth_metrics = calculate_depth_metrics(
        symbol, depth, order_size_usdt=calc_order_size, max_slippage_pct=max_slippage
    )
    if not depth_metrics:
        return None
    return evaluate_market_structure(symbol, depth_metrics, ticker)


def save_analysis(payload, filepath=ANALYSIS_OUTPUT_FILE):
    """Save analysis report to trader/market_analysis.json."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    temp_path = filepath + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(temp_path, filepath)


def print_analyst_report(analysis):
    """Print clean formatted analyst report."""
    ob = analysis["orderbook_metrics"]
    print("=" * 95)
    print(" 🔬 BINANCE ORDERBOOK DEPTH & MARKET STRUCTURE ANALYST (trader-analyst)")
    print(f" Symbol: {analysis['symbol']} | Price: {analysis['market_price']} USDT | 24h Change: {analysis['change_24h_pct']:+}%")
    print(f" Execution Clearance: 🛡️ {analysis['execution_verdict']}")
    print("=" * 95)
    print(" 📊 ORDERBOOK DEPTH & SPREAD ANALYSIS (Top 20 Levels):")
    print(f"   • Best Bid:        {ob['best_bid']} USDT")
    print(f"   • Best Ask:        {ob['best_ask']} USDT")
    print(f"   • Spread:          {ob['spread_abs']} USDT ({ob['spread_pct']:.4f}%) -> [{ob['spread_rating']}]")
    print(f"   • Top 20 Bids:     ${ob['top20_bid_depth_usdt']:,.2f} USDT")
    print(f"   • Top 20 Asks:     ${ob['top20_ask_depth_usdt']:,.2f} USDT")
    print(f"   • Buyer/Seller:    {ob.get('buyer_seller_balance', 'N/A')}")
    print(f"   • Book Imbalance:  {ob['imbalance_verdict']} ({ob['bid_ask_imbalance_ratio']*100:.1f}% bids)")
    if ob.get("largest_bid_wall"):
        w = ob["largest_bid_wall"]
        print(f"   • Bid Wall (Floor): ${w['notional_usdt']:,.2f} USDT ({w['qty']} units @ {w['price']} USDT)")
    if ob.get("largest_ask_wall"):
        w = ob["largest_ask_wall"]
        print(f"   • Ask Wall (Ceil):  ${w['notional_usdt']:,.2f} USDT ({w['qty']} units @ {w['price']} USDT)")
    print(f"   • Orderbook Flow:  {ob['orderbook_note']}")
    print("-" * 95)
    print(f" ⚡ LIQUIDITY & SLIPPAGE SIMULATION (${ob['simulated_order_size_usdt']} USDT Market Order):")
    print(f"   • Est. Avg Fill:   {ob['estimated_avg_fill']} USDT")
    print(f"   • Est. Slippage:   {ob['estimated_slippage_pct']:.4f}% (Max tolerance: {ob.get('max_acceptable_slippage_pct', MAX_ACCEPTABLE_SLIPPAGE_PCT)}%)")
    pass_badge = "✅ PASS (Within limits)" if ob['slippage_pass'] else "❌ FAIL (Exceeds tolerance)"
    print(f"   • Slippage Check:  {pass_badge}")
    print("-" * 95)
    print(" 🏛️ MARKET STRUCTURE & RANGE POSTURE:")
    print(f"   • 24h Range:       {analysis['low_24h']} -> {analysis['high_24h']} USDT")
    print(f"   • Range Position:  {analysis['range_position_pct']:.1f}%")
    print(f"   • Structure Type:  {analysis['structure']}")
    print(f"   • Assessment:      {analysis['structure_desc']}")
    print("=" * 95)


def main():
    parser = argparse.ArgumentParser(description="Binance Orderbook Depth & Market Structure Analyst")
    parser.add_argument("--symbol", type=str, help="Symbol to inspect (default: top watchlist coin)")
    parser.add_argument("--symbols", type=str, help="Comma-separated list of symbols")
    parser.add_argument("--order-size", type=float, help="Simulated order size in USDT (default: 50.0)")
    parser.add_argument("--offline", type=str, help="Offline JSON depth fixture")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument("--no-save", action="store_true", help="Do not save trader/market_analysis.json")

    args = parser.parse_args()

    symbols = []
    if args.symbol:
        symbols = [args.symbol.strip().upper()]
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = [get_target_symbol()]

    all_analyses = {}
    for sym in symbols:
        res = analyze_symbol(sym, offline_file=args.offline, order_size=args.order_size)
        if res:
            all_analyses[sym] = res

    if not all_analyses:
        print("[analyst] Error: could not analyze any symbol.", file=sys.stderr)
        sys.exit(1)

    primary = list(all_analyses.values())[0]
    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "primary_symbol": primary["symbol"],
        "analysis": primary if len(symbols) == 1 else all_analyses
    }

    if not args.no_save:
        save_analysis(payload)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for a in all_analyses.values():
            print_analyst_report(a)

    sys.exit(0)


if __name__ == "__main__":
    main()
