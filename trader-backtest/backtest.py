# trader-backtest/backtest.py
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int


@dataclass
class BacktestConfig:
    symbol: str = "BTCUSDT"
    interval: str = "5m"
    initial_capital: float = 100.0
    max_risk_usd: float = 2.50
    breakout_periods: int = 20          # Ruptura de 20 velas (100 min en 5m)
    require_24h_high: bool = False      # Activar True para exigir ruptura simultánea 24h (288 velas)
    atr_period: int = 14
    atr_stop_mult: float = 1.5
    rr_ratio: float = 1.5
    commission_rate: float = 0.001      # 0.10% estándar (0.00075 con BNB)
    slippage_rate: float = 0.0005       # 0.05% por lado
    min_notional: float = 5.0


@dataclass
class SimulatedTrade:
    entry_time_ms: int
    exit_time_ms: int
    entry_price: float
    exit_price: float
    stop_price: float
    take_profit: float
    qty: float
    gross_pnl: float
    commission_paid: float
    slippage_cost: float
    net_pnl: float
    r_multiple: float
    exit_reason: str


def fetch_binance_klines(
    symbol: str,
    interval: str = "5m",
    days: int = 14,
    cache_path: Optional[Path] = None,
    timeout: float = 10.0,
) -> List[Candle]:
    """Descarga velas históricas de Binance /api/v3/klines con paginación."""
    if cache_path and cache_path.exists():
        return load_candles_from_file(cache_path)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400 * 1000)
    cursor_ms = start_ms
    candles: List[Candle] = []

    print(f"[backtest] Descargando {days} días de velas {interval} para {symbol} de Binance API...")
    while cursor_ms < end_ms:
        params = urllib.parse.urlencode(
            {
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": cursor_ms,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        url = f"https://api.binance.com/api/v3/klines?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Criptodaytrader-Backtest/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        if not raw:
            break

        for row in raw:
            candles.append(
                Candle(
                    open_time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time_ms=int(row[6]),
                )
            )

        last_open = int(raw[-1][0])
        if last_open <= cursor_ms or len(raw) < 1000:
            break
        cursor_ms = last_open + 1
        time.sleep(0.1)

    if cache_path and candles:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps([asdict(c) for c in candles]),
            encoding="utf-8",
        )
        print(f"[backtest] Guardadas {len(candles)} velas en caché: {cache_path}")
    return candles


def load_candles_from_file(path: Path) -> List[Candle]:
    """Carga velas desde JSON o CSV."""
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Candle(**item) for item in raw]

    candles: List[Candle] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader):
            ot = int(row.get("open_time_ms", idx * 300_000))
            candles.append(
                Candle(
                    open_time_ms=ot,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                    close_time_ms=int(row.get("close_time_ms", ot + 299_999)),
                )
            )
    return candles


def compute_wilder_atr(candles: List[Candle], period: int = 14) -> List[float]:
    """Calcula ATR usando suavizado exponencial de Wilder sin sesgo futuro."""
    n = len(candles)
    atrs = [0.0] * n
    if n <= period:
        return atrs

    trs: List[float] = [0.0] * n
    trs[0] = candles[0].high - candles[0].low
    for i in range(1, n):
        c = candles[i]
        prev_close = candles[i - 1].close
        trs[i] = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )

    first_atr = sum(trs[1 : period + 1]) / period
    atrs[period] = first_atr
    for i in range(period + 1, n):
        atrs[i] = ((atrs[i - 1] * (period - 1)) + trs[i]) / period
    return atrs


def run_backtest(candles: List[Candle], cfg: BacktestConfig) -> Dict[str, Any]:
    if len(candles) < max(cfg.breakout_periods, cfg.atr_period, 288 if cfg.require_24h_high else 1) + 5:
        raise ValueError("Insuficientes velas para ejecutar el backtest")

    atrs = compute_wilder_atr(candles, cfg.atr_period)
    warmup = max(cfg.breakout_periods, cfg.atr_period, 288 if cfg.require_24h_high else 0)

    cash = cfg.initial_capital
    peak_equity = cfg.initial_capital
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0

    trades: List[SimulatedTrade] = []
    open_pos: Optional[Dict[str, Any]] = None
    pending_signal = False
    pending_atr = 0.0

    for i in range(warmup, len(candles)):
        bar = candles[i]

        # 1. Ejecutar entrada pendiente al Open de la vela actual (sin lookahead)
        if pending_signal and open_pos is None:
            raw_entry = bar.open
            entry_exec = raw_entry * (1.0 + cfg.slippage_rate)
            stop_dist = max(pending_atr * cfg.atr_stop_mult, entry_exec * 0.003)
            stop_price = entry_exec - stop_dist
            take_profit = entry_exec + stop_dist * cfg.rr_ratio

            effective_cash = cash / (1.0 + cfg.commission_rate)
            qty_by_risk = cfg.max_risk_usd / stop_dist
            qty_by_cash = effective_cash / entry_exec
            qty = min(qty_by_risk, qty_by_cash)

            if qty * entry_exec >= cfg.min_notional and qty > 0:
                notional = qty * entry_exec
                entry_comm = notional * cfg.commission_rate
                cash -= (notional + entry_comm)
                open_pos = {
                    "entry_time_ms": bar.open_time_ms,
                    "raw_entry": raw_entry,
                    "entry_price": entry_exec,
                    "stop_price": stop_price,
                    "take_profit": take_profit,
                    "qty": qty,
                    "entry_comm": entry_comm,
                    "entry_slip": (entry_exec - raw_entry) * qty,
                    "initial_risk": stop_dist * qty,
                }
            pending_signal = False

        # 2. Evaluar salida intra-vela (conservador: SL primero si ambos tocan)
        if open_pos is not None:
            exit_reason: Optional[str] = None
            raw_exit: float = 0.0

            if bar.low <= open_pos["stop_price"]:
                exit_reason = "STOP_LOSS"
                raw_exit = min(bar.open, open_pos["stop_price"])
            elif bar.high >= open_pos["take_profit"]:
                exit_reason = "TAKE_PROFIT"
                raw_exit = max(bar.open, open_pos["take_profit"])

            if exit_reason is not None:
                exit_exec = raw_exit * (1.0 - cfg.slippage_rate)
                qty = open_pos["qty"]
                exit_notional = qty * exit_exec
                exit_comm = exit_notional * cfg.commission_rate
                cash += (exit_notional - exit_comm)

                gross_pnl = (exit_exec - open_pos["entry_price"]) * qty
                total_comm = open_pos["entry_comm"] + exit_comm
                slip_cost = open_pos["entry_slip"] + (raw_exit - exit_exec) * qty
                net_pnl = gross_pnl - total_comm
                r_mult = net_pnl / open_pos["initial_risk"] if open_pos["initial_risk"] > 0 else 0.0

                trades.append(
                    SimulatedTrade(
                        entry_time_ms=open_pos["entry_time_ms"],
                        exit_time_ms=bar.close_time_ms,
                        entry_price=open_pos["entry_price"],
                        exit_price=exit_exec,
                        stop_price=open_pos["stop_price"],
                        take_profit=open_pos["take_profit"],
                        qty=qty,
                        gross_pnl=gross_pnl,
                        commission_paid=total_comm,
                        slippage_cost=slip_cost,
                        net_pnl=net_pnl,
                        r_multiple=r_mult,
                        exit_reason=exit_reason,
                    )
                )
                open_pos = None

        # 3. Mark-to-Market para Drawdown real
        mtm_equity = cash + (open_pos["qty"] * bar.close if open_pos is not None else 0.0)
        if mtm_equity > peak_equity:
            peak_equity = mtm_equity
        dd_usd = peak_equity - mtm_equity
        dd_pct = (dd_usd / peak_equity) * 100.0 if peak_equity > 0 else 0.0
        if dd_usd > max_drawdown_usd:
            max_drawdown_usd = dd_usd
        if dd_pct > max_drawdown_pct:
            max_drawdown_pct = dd_pct

        # 4. Señal de ruptura al cierre de vela
        if open_pos is None and i < len(candles) - 1:
            lookback_slice = candles[i - cfg.breakout_periods : i]
            high_n = max(c.high for c in lookback_slice)
            breakout_ok = bar.close > high_n
            if cfg.require_24h_high and i >= 288:
                high_24h = max(c.high for c in candles[i - 288 : i])
                breakout_ok = breakout_ok and (bar.close > high_24h)

            if breakout_ok and atrs[i] > 0:
                pending_signal = True
                pending_atr = atrs[i]

    # Cerrar remanente
    if open_pos is not None:
        last_bar = candles[-1]
        exit_exec = last_bar.close * (1.0 - cfg.slippage_rate)
        qty = open_pos["qty"]
        exit_notional = qty * exit_exec
        exit_comm = exit_notional * cfg.commission_rate
        cash += (exit_notional - exit_comm)
        gross_pnl = (exit_exec - open_pos["entry_price"]) * qty
        total_comm = open_pos["entry_comm"] + exit_comm
        net_pnl = gross_pnl - total_comm
        r_mult = net_pnl / open_pos["initial_risk"] if open_pos["initial_risk"] > 0 else 0.0
        trades.append(
            SimulatedTrade(
                entry_time_ms=open_pos["entry_time_ms"],
                exit_time_ms=last_bar.close_time_ms,
                entry_price=open_pos["entry_price"],
                exit_price=exit_exec,
                stop_price=open_pos["stop_price"],
                take_profit=open_pos["take_profit"],
                qty=qty,
                gross_pnl=gross_pnl,
                commission_paid=total_comm,
                slippage_cost=open_pos["entry_slip"] + (last_bar.close - exit_exec) * qty,
                net_pnl=net_pnl,
                r_multiple=r_mult,
                exit_reason="EOD_FORCE_CLOSE",
            )
        )

    # 5. Métricas cuantitativas
    total_trades = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))

    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
    net_profit = cash - cfg.initial_capital
    expectancy_usd = (net_profit / total_trades) if total_trades > 0 else 0.0
    expectancy_r = (sum(t.r_multiple for t in trades) / total_trades) if total_trades > 0 else 0.0

    return {
        "symbol": cfg.symbol,
        "interval": cfg.interval,
        "candles_evaluated": len(candles),
        "initial_capital": round(cfg.initial_capital, 2),
        "final_equity": round(cash, 4),
        "total_trades": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else "INF",
        "max_drawdown_usd": round(max_drawdown_usd, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "net_profit_usd": round(net_profit, 4),
        "return_pct": round((net_profit / cfg.initial_capital) * 100.0, 2),
        "expectancy_usd": round(expectancy_usd, 4),
        "expectancy_r": round(expectancy_r, 4),
        "total_commissions_usd": round(sum(t.commission_paid for t in trades), 4),
        "total_slippage_usd": round(sum(t.slippage_cost for t in trades), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtester Cuantitativo Intradía 5m")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--max-risk", type=float, default=2.50)
    parser.add_argument("--breakout-periods", type=int, default=20)
    parser.add_argument("--require-24h", action="store_true")
    parser.add_argument("--bnb-fee", action="store_true", help="Usa comision 0.075 pct (BNB)")
    parser.add_argument("--file", type=Path, default=None, help="Archivo local CSV/JSON de velas")
    parser.add_argument("--cache", type=Path, default=None, help="Ruta de caché JSON")
    args = parser.parse_args()

    candles = (
        load_candles_from_file(args.file)
        if args.file
        else fetch_binance_klines(args.symbol, "5m", args.days, cache_path=args.cache)
    )
    cfg = BacktestConfig(
        symbol=args.symbol,
        initial_capital=args.capital,
        max_risk_usd=args.max_risk,
        breakout_periods=args.breakout_periods,
        require_24h_high=args.require_24h,
        commission_rate=0.00075 if args.bnb_fee else 0.001,
    )
    metrics = run_backtest(candles, cfg)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
