#!/usr/bin/env python3
"""Binance Crypto Day Trader - Time Machine (trader-timemachine).
Runs 10,000 Monte Carlo bootstrap paths simulating the next 48 candles (4 hours of day trading).
Computes empirical probabilities of reaching Target vs Stop first, expected drawdown, and win rate.
Standard library only (urllib.request, json, time, datetime, sys, os, math, random).
"""

import argparse
import datetime
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
PLAN_PATH = os.path.join(TRADER_DIR, "plan.json")
ENTRY_PATH = os.path.join(TRADER_DIR, "entry.json")
TIMEMACHINE_PATH = os.path.join(TRADER_DIR, "timemachine.json")

BINANCE_API = "https://api.binance.com/api/v3"
USER_AGENT = "BinanceCryptoTraderSwarm/1.0"


def get_price_precision(price: float) -> int:
    """Determine decimal precision based on price magnitude."""
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


def fetch_historical_klines(symbol: str, interval: str = "5m", limit: int = 1000):
    """Fetch 500-1000 historical 5m bars from Binance."""
    url = f"{BINANCE_API}/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception:
        return None


def build_ratio_pool(klines):
    """
    Builds bootstrap pool of relative price movements:
    (high_ratio, low_ratio, close_ratio) relative to previous bar's close.
    """
    if not klines or len(klines) < 20:
        # Fallback synthetic empirical distribution typical for crypto 5m bars (volatility ~0.3%)
        pool = []
        for _ in range(500):
            drift = random.gauss(0.0001, 0.003)
            spread = abs(random.gauss(0.002, 0.0015))
            c_r = 1.0 + drift
            h_r = max(1.0, c_r) + spread
            l_r = min(1.0, c_r) - spread
            pool.append((h_r, l_r, c_r))
        return pool

    pool = []
    for i in range(1, len(klines)):
        prev_close = float(klines[i - 1][4])
        if prev_close <= 0:
            continue
        h = float(klines[i][2])
        l = float(klines[i][3])
        c = float(klines[i][4])

        h_ratio = h / prev_close
        l_ratio = l / prev_close
        c_ratio = c / prev_close

        # Filter out corrupted ticks / extreme flash crashes (>15% in a single 5m bar)
        if 0.85 <= l_ratio and h_ratio <= 1.15:
            pool.append((h_ratio, l_ratio, c_ratio))

    if not pool:
        pool = [(1.002, 0.998, 1.000) for _ in range(100)]
    return pool


def run_monte_carlo(symbol: str = None, entry: float = None, stop: float = None, target: float = None,
                     side: str = None, replays: int = 10000, horizon_bars: int = 48, seed: int = 42):
    """
    Runs 10,000 bootstrap simulations of 48 candles (4 hours of day trading).
    Calculates empirical probability of Target vs Stop, expected drawdown, and win rate.
    """
    reward_risk = 1.5
    resolved_side = (side or "BUY").upper()

    # 1. Resolve parameters from plan.json if not provided
    if os.path.exists(PLAN_PATH):
        try:
            with open(PLAN_PATH, "r", encoding="utf-8") as f:
                p = json.load(f)
                plan_sym = p.get("symbol")
                # If symbol not specified or matches plan's symbol, take plan parameters
                if symbol is None or (plan_sym and symbol.upper() == plan_sym.upper()):
                    symbol = symbol or plan_sym or "DOGEUSDT"
                    if entry is None and p.get("entry"):
                        entry = float(p.get("entry"))
                    if stop is None and p.get("stop"):
                        stop = float(p.get("stop"))
                    if target is None:
                        target = float(p.get("target_1r") or p.get("target") or 0.0) or None
                    if p.get("reward_risk"):
                        reward_risk = float(p.get("reward_risk"))
                    if side is None and p.get("side"):
                        resolved_side = str(p.get("side")).upper()
        except Exception:
            pass

    symbol = (symbol or "DOGEUSDT").upper()

    # 2. Fetch historical klines
    random.seed(seed)
    klines = fetch_historical_klines(symbol, interval="5m", limit=1000)
    pool = build_ratio_pool(klines)

    # If entry still missing, get from latest candle close
    if entry is None or entry <= 0:
        if klines and len(klines) > 0:
            try:
                entry = float(klines[-1][4])
            except Exception:
                entry = 80.0
        else:
            entry = 80.0

    # Sanitize stop and target boundaries
    if resolved_side == "SELL":
        if stop is None or stop <= entry:
            stop = entry * 1.01
        if target is None or target >= entry or target <= 0:
            target = entry - abs(stop - entry) * reward_risk
        if abs(stop - entry) > 1e-8:
            reward_risk = abs(entry - target) / abs(stop - entry)
    else:
        # Default BUY (LONG)
        if stop is None or stop <= 0 or stop >= entry:
            stop = entry * 0.99
        if target is None or target <= entry:
            target = entry + abs(entry - stop) * reward_risk
        if abs(entry - stop) > 1e-8:
            reward_risk = abs(target - entry) / abs(entry - stop)

    # 3. Simulate paths
    target_hits = 0
    stop_hits = 0
    neither_hits = 0
    drawdowns = []
    final_prices = []
    exit_bars = []

    # Sample trajectory points for 5 percentile bands at milestones
    # Milestones: steps [6, 12, 18, 24, 30, 36, 42, 48] (30m, 1h, 1.5h, 2h, 2.5h, 3h, 3.5h, 4h)
    milestones = [6, 12, 18, 24, 30, 36, 42, 48]
    milestone_prices = {m: [] for m in milestones}

    pool_len = len(pool)
    for _ in range(replays):
        price = entry
        path_max_dd = 0.0
        outcome = "neither"
        hit_bar = horizon_bars

        for step in range(1, horizon_bars + 1):
            if outcome == "neither":
                h_r, l_r, c_r = pool[random.randrange(pool_len)]
                b_high = price * h_r
                b_low = price * l_r
                b_close = price * c_r

                if resolved_side == "SELL":
                    # For SHORT: drawdown when price spikes high
                    dd = (b_high - entry) / entry
                    if dd > path_max_dd:
                        path_max_dd = dd

                    # Conservative check: stop checked before target
                    if b_high >= stop:
                        outcome = "stop"
                        hit_bar = step
                        price = stop
                    elif b_low <= target:
                        outcome = "target"
                        hit_bar = step
                        price = target
                    else:
                        price = b_close
                else:
                    # For BUY (LONG): drawdown when price drops low
                    dd = (entry - b_low) / entry
                    if dd > path_max_dd:
                        path_max_dd = dd

                    # Conservative check: stop checked before target
                    if b_low <= stop:
                        outcome = "stop"
                        hit_bar = step
                        price = stop
                    elif b_high >= target:
                        outcome = "target"
                        hit_bar = step
                        price = target
                    else:
                        price = b_close

            if step in milestone_prices:
                milestone_prices[step].append(price)

        drawdowns.append(max(0.0, path_max_dd))
        final_prices.append(price)
        exit_bars.append(hit_bar)

        if outcome == "target":
            target_hits += 1
        elif outcome == "stop":
            stop_hits += 1
        else:
            neither_hits += 1

    # 4. Compute statistics
    p_target = target_hits / replays
    p_stop = stop_hits / replays
    p_neither = neither_hits / replays
    resolved = target_hits + stop_hits
    win_rate = (target_hits / resolved) if resolved > 0 else (target_hits / replays)

    avg_drawdown = (sum(drawdowns) / len(drawdowns)) * 100.0 if drawdowns else 0.0
    max_drawdown = (max(drawdowns) * 100.0) if drawdowns else 0.0
    avg_exit_bar = sum(exit_bars) / len(exit_bars) if exit_bars else horizon_bars

    final_prices.sort()
    p5_price = final_prices[int(0.05 * replays)]
    p25_price = final_prices[int(0.25 * replays)]
    median_price = final_prices[int(0.50 * replays)]
    p75_price = final_prices[int(0.75 * replays)]
    p95_price = final_prices[int(0.95 * replays)]

    # Expected value in terms of R (Risk units)
    # Win = +reward_risk R, Loss = -1.0 R
    ev_r = round((p_target * reward_risk) - (p_stop * 1.0), 3)

    # Profit factor
    loss_val = max(p_stop * 1.0, 1e-4)
    profit_factor = round((p_target * reward_risk) / loss_val, 2)

    prec = get_price_precision(entry)

    # Fan chart bands across milestones
    bands = {}
    for m in milestones:
        prices_at_m = sorted(milestone_prices[m]) if milestone_prices[m] else [entry] * replays
        n_m = len(prices_at_m)
        idx_5 = min(int(0.05 * n_m), n_m - 1)
        idx_25 = min(int(0.25 * n_m), n_m - 1)
        idx_50 = min(int(0.50 * n_m), n_m - 1)
        idx_75 = min(int(0.75 * n_m), n_m - 1)
        idx_95 = min(int(0.95 * n_m), n_m - 1)
        bands[f"step_{m}_bars"] = {
            "time_minutes": m * 5,
            "p5": round(prices_at_m[idx_5], prec),
            "p25": round(prices_at_m[idx_25], prec),
            "median": round(prices_at_m[idx_50], prec),
            "p75": round(prices_at_m[idx_75], prec),
            "p95": round(prices_at_m[idx_95], prec),
        }

    # Verdict and recommendation
    if win_rate >= 0.50 and ev_r > 0:
        verdict = f"FAVORABLE: High statistical edge ({win_rate*100:.1f}% win rate, EV: +{ev_r:.2f}R, PF: {profit_factor})"
        recommendation = "PROCEED_TO_SIZING"
    elif ev_r > 0:
        verdict = f"MODERATE: Positive expected value (+{ev_r:.2f}R, {win_rate*100:.1f}% win rate, PF: {profit_factor})"
        recommendation = "PROCEED_TO_SIZING"
    else:
        verdict = f"CAUTION: Unfavorable risk-reward profile (EV: {ev_r:.2f}R, {win_rate*100:.1f}% win rate)"
        recommendation = "REVIEW_BRACKET"

    now_dt = datetime.datetime.now(datetime.timezone.utc)
    iso_time = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    result = {
        "created_by": "trader-timemachine",
        "created_at": iso_time,
        "symbol": symbol.upper(),
        "side": resolved_side,
        "simulations": replays,
        "horizon_bars": horizon_bars,
        "horizon_hours": round(horizon_bars * 5 / 60, 1),
        "candles_analyzed": len(pool),
        "plan": {
            "entry": round(entry, prec),
            "stop": round(stop, prec),
            "target": round(target, prec),
            "reward_risk": round(reward_risk, 2)
        },
        "results": {
            "p_target": round(p_target, 4),
            "p_stop": round(p_stop, 4),
            "p_neither": round(p_neither, 4),
            "win_rate": round(win_rate, 4),
            "win_rate_pct": round(win_rate * 100, 2),
            "expected_drawdown_pct": round(avg_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "expected_value_r": ev_r,
            "profit_factor": profit_factor,
            "avg_bars_to_exit": round(avg_exit_bar, 1),
            "median_final_price": round(median_price, prec),
            "p5_final_price": round(p5_price, prec),
            "p95_final_price": round(p95_price, prec),
            "target_hits": target_hits,
            "stop_hits": stop_hits,
            "neither_hits": neither_hits,
            "verdict": verdict,
            "recommendation": recommendation
        },
        "bands": bands
    }

    return result


def print_ascii_chart(result):
    """Print ASCII fan chart and summary statistics."""
    res = result["results"]
    plan = result["plan"]
    side = result.get("side", "BUY")
    p_win = res["p_target"]
    p_loss = res["p_stop"]

    bar_len = 40
    win_chars = int(p_win * bar_len)
    loss_chars = int(p_loss * bar_len)
    flat_chars = max(0, bar_len - win_chars - loss_chars)

    distribution_bar = ("█" * win_chars) + ("░" * flat_chars) + ("▓" * loss_chars)

    prec = get_price_precision(plan['entry'])
    print("\n" + "=" * 66)
    print(f" BINANCE TIME MACHINE: {result['simulations']:,} MONTE CARLO REPLAYS")
    print(f" Asset: {result['symbol']} ({side}) | Horizon: {result['horizon_hours']}h ({result['horizon_bars']} x 5m Bars)")
    print("=" * 66)
    print(f" Plan: Entry ${plan['entry']:.{prec}f} | Stop ${plan['stop']:.{prec}f} | Target ${plan['target']:.{prec}f} (R:R {plan['reward_risk']}x)")
    print("-" * 66)
    print(f" Target Win Prob (P_target):   {res['p_target']*100:6.2f}%  ({res['target_hits']:,} paths)")
    print(f" Stop-Loss Prob (P_stop):       {res['p_stop']*100:6.2f}%  ({res['stop_hits']:,} paths)")
    print(f" Timeout / Open (P_neither):    {res['p_neither']*100:6.2f}%  ({res['neither_hits']:,} paths)")
    print(f" Resolved Win Rate:             {res['win_rate_pct']:6.2f}%")
    print(f" Profit Factor:                 {res.get('profit_factor', 1.0):6.2f}")
    print(f" Expected Drawdown:             {res['expected_drawdown_pct']:6.2f}% (Worst: {res['max_drawdown_pct']:.2f}%)")
    print(f" Expected Value (EV):           {res['expected_value_r']:+6.3f} R")
    print(f" Terminal Price (P5 - P95):     ${res['p5_final_price']:.{prec}f} -> ${res['p95_final_price']:.{prec}f} (Median: ${res['median_final_price']:.{prec}f})")
    print("-" * 66)
    print(" Outcome Distribution (█=Target | ░=Open | ▓=Stop):")
    print(f" [{distribution_bar}]")
    print("-" * 66)
    bands = result.get("bands", {})
    if bands:
        print(" Fan Quantiles across Horizon:")
        print("  Horizon   |    P5 (Worst)   |     Median      |    P95 (Best)")
        print("  ----------+-----------------+-----------------+-----------------")
        for key in ["step_6_bars", "step_12_bars", "step_24_bars", "step_48_bars"]:
            if key in bands:
                b = bands[key]
                t_str = f"{b['time_minutes']}m ({b['time_minutes']//60}h)" if b['time_minutes'] >= 60 else f"{b['time_minutes']}m"
                print(f"  {t_str:9s} | ${b['p5']:14.{prec}f} | ${b['median']:14.{prec}f} | ${b['p95']:14.{prec}f}")
        print("-" * 66)
    print(f" Verdict:        {res['verdict']}")
    print(f" Recommendation: {res.get('recommendation', 'N/A')}")
    print("=" * 66 + "\n")


def save_timemachine(data: dict, path: str = TIMEMACHINE_PATH):
    """Save results to trader/timemachine.json."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser(description="Binance Crypto Time Machine")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "chart"], help="Action")
    parser.add_argument("--symbol", "-s", type=str, default=None, help="Crypto pair")
    parser.add_argument("--side", type=str, default=None, choices=["BUY", "SELL"], help="Trade side")
    parser.add_argument("--entry", "-e", type=float, default=None, help="Entry price")
    parser.add_argument("--stop", type=float, default=None, help="Stop price")
    parser.add_argument("--target", "-t", type=float, default=None, help="Target price")
    parser.add_argument("--replays", "-n", type=int, default=10000, help="Replay count (default: 10000)")
    parser.add_argument("--horizon", type=int, default=48, help="Horizon in 5m bars (default: 48 = 4h)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--write", "-w", action="store_true", default=True, help="Save to trader/timemachine.json")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    # If chart command called without custom simulation overrides, display existing file if present
    if args.command == "chart" and args.symbol is None and args.entry is None and os.path.exists(TIMEMACHINE_PATH):
        try:
            with open(TIMEMACHINE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print_ascii_chart(data)
            sys.exit(0)
        except Exception:
            pass

    data = run_monte_carlo(
        symbol=args.symbol,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        side=args.side,
        replays=args.replays,
        horizon_bars=args.horizon,
        seed=args.seed
    )

    if args.write:
        save_timemachine(data)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_ascii_chart(data)
        if args.write:
            print(f" Results saved to: {TIMEMACHINE_PATH}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
