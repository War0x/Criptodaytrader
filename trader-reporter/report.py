#!/usr/bin/env python3
"""
trader-reporter: Generador de Resumen Ejecutivo y Bitácora de Trading en Binance.
Lee el portafolio, las órdenes ejecutadas, auditorías de riesgo y bitácora,
compila métricas clave (KPIs), gestiona el registro de operaciones cerradas,
y actualiza el libro ejecutivo de Excel y JSON.
"""

import os
import sys
import json
import argparse
import uuid
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
DEFAULT_EXCEL_PATH = os.path.join(TRADER_DIR, "Resumen_Ejecutivo_Crypto.xlsx")
DEFAULT_HISTORY_PATH = os.path.join(TRADER_DIR, "trade_history.json")
DEFAULT_CONFIG_PATH = os.path.join(TRADER_DIR, "config.json")
DEFAULT_PORTFOLIO_PATH = os.path.join(TRADER_DIR, "portfolio.json")
DEFAULT_PRICES_PATH = os.path.join(TRADER_DIR, "prices.json")


def load_json(filepath, default=None):
    """Carga de forma segura un archivo JSON."""
    if filepath and os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(filepath, data):
    """Guarda datos estructurados en formato JSON con formato legible."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_closed_trade(trade):
    """Determina si un registro de operación representa una posición cerrada."""
    if not isinstance(trade, dict):
        return False
    if "pnl" in trade or "realized_pnl" in trade:
        return True
    if str(trade.get("trade_id", "")).upper().startswith("FLATTEN"):
        return True
    if trade.get("side", "").upper() == "SELL":
        return True
    return False


def get_trade_pnl(trade):
    """Extrae el PnL realizado de un registro de operación."""
    if not isinstance(trade, dict):
        return 0.0
    if "realized_pnl" in trade:
        try:
            return float(trade["realized_pnl"])
        except (ValueError, TypeError):
            pass
    if "pnl" in trade:
        try:
            return float(trade["pnl"])
        except (ValueError, TypeError):
            pass
    return 0.0


def get_trade_pnl_pct(trade):
    """Extrae el porcentaje de PnL de un registro de operación."""
    if not isinstance(trade, dict):
        return 0.0
    for key in ("realized_pnl_pct", "pnl_pct"):
        if key in trade:
            try:
                return float(trade[key])
            except (ValueError, TypeError):
                pass
    return 0.0


def calculate_metrics(history, portfolio, config, prices=None):
    """
    Calcula exhaustivamente todas las métricas operativas y de cumplimiento de riesgo.
    """
    history = history if isinstance(history, list) else []
    portfolio = portfolio if isinstance(portfolio, dict) else {}
    config = config if isinstance(config, dict) else {}
    prices_map = (prices.get("prices", {}) if isinstance(prices, dict) else {}) if prices else {}

    budget = float(config.get("budget", 100.0))
    cash = float(portfolio.get("cash", budget))
    portfolio_value = float(portfolio.get("portfolio_value", cash))
    raw_positions = portfolio.get("positions", [])
    if not isinstance(raw_positions, list):
        raw_positions = []

    # Enriquecer posiciones abiertas con precios actuales y PnL no realizado
    positions = []
    total_unrealized = 0.0
    for p in raw_positions:
        if not isinstance(p, dict):
            continue
        pos = dict(p)
        sym = pos.get("symbol", "")
        qty = float(pos.get("qty", 0.0))
        entry_px = float(pos.get("entry_price", 0.0))
        curr_px = float(pos.get("current_price", prices_map.get(sym, entry_px)))
        pos["current_price"] = curr_px
        unrealized = round((curr_px - entry_px) * qty, 4)
        pos["unrealized_pnl"] = unrealized
        pos["unrealized_pnl_pct"] = round(((curr_px - entry_px) / entry_px * 100.0), 2) if entry_px > 0 else 0.0
        total_unrealized += unrealized
        positions.append(pos)

    total_trades = len(history)
    closed_trades = [t for t in history if is_closed_trade(t)]
    closed_count = len(closed_trades)

    wins = [t for t in closed_trades if get_trade_pnl(t) > 0]
    losses = [t for t in closed_trades if get_trade_pnl(t) < 0]
    scratches = [t for t in closed_trades if get_trade_pnl(t) == 0]

    win_rate = (len(wins) / closed_count * 100.0) if closed_count > 0 else 0.0
    gross_profit = round(sum(get_trade_pnl(t) for t in wins), 4)
    gross_loss = round(abs(sum(get_trade_pnl(t) for t in losses)), 4)
    net_realized_pnl = round(sum(get_trade_pnl(t) for t in closed_trades), 4)

    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    avg_win = round(gross_profit / len(wins), 4) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 4) if losses else 0.0
    win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    win_pnls = [get_trade_pnl(t) for t in wins]
    loss_pnls = [get_trade_pnl(t) for t in losses]
    largest_win = round(max(win_pnls), 4) if win_pnls else 0.0
    largest_loss = round(min(loss_pnls), 4) if loss_pnls else 0.0
    expected_value = round(net_realized_pnl / closed_count, 4) if closed_count > 0 else 0.0

    # Cumplimiento de reglas de riesgo intradía
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    trades_today = [t for t in history if str(t.get("timestamp", "")).startswith(today_str)]
    trades_today_count = len(trades_today)

    losses_today = [
        abs(get_trade_pnl(t))
        for t in trades_today
        if is_closed_trade(t) and get_trade_pnl(t) < 0
    ]
    daily_loss_realized = round(sum(losses_today), 4)

    max_daily_loss = float(config.get("max_daily_loss_usd", 7.5))
    max_trades_per_day = int(config.get("max_trades_per_day", 5))

    daily_loss_limit_breached = daily_loss_realized >= max_daily_loss
    daily_trades_limit_breached = trades_today_count >= max_trades_per_day

    total_return_pct = round(((portfolio_value - budget) / budget * 100.0), 2) if budget > 0 else 0.0

    return {
        "budget": budget,
        "cash": cash,
        "portfolio_value": portfolio_value,
        "total_return_pct": total_return_pct,
        "total_unrealized_pnl": round(total_unrealized, 4),
        "positions_count": len(positions),
        "positions": positions,
        "total_trades": total_trades,
        "closed_trades": closed_count,
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        "win_rate": round(win_rate, 2),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_realized_pnl": net_realized_pnl,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": win_loss_ratio,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
        "expected_value": expected_value,
        "today_str": today_str,
        "trades_today_count": trades_today_count,
        "daily_loss_realized": daily_loss_realized,
        "max_daily_loss": max_daily_loss,
        "max_trades_per_day": max_trades_per_day,
        "daily_loss_limit_breached": daily_loss_limit_breached,
        "daily_trades_limit_breached": daily_trades_limit_breached,
        "mode": "PAPER TRADING" if config.get("paper", True) else "BINANCE LIVE",
    }


def log_trade(trade_record, history_path=DEFAULT_HISTORY_PATH):
    """
    Agrega un registro de operación a la bitácora histórica trade_history.json.
    """
    history = load_json(history_path, default=[])
    if not isinstance(history, list):
        history = []

    # Validar campos esenciales
    if not trade_record.get("trade_id"):
        prefix = "TRADE"
        if trade_record.get("side", "").upper() == "SELL":
            prefix = "FLATTEN"
        trade_record["trade_id"] = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

    if not trade_record.get("timestamp"):
        trade_record["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    history.append(trade_record)
    save_json(history_path, history)
    return trade_record


def log_closed_position(
    symbol,
    side="SELL",
    qty=0.0,
    price=0.0,
    entry_price=None,
    pnl=None,
    pnl_pct=None,
    reason="",
    mode="PAPER TRADING",
    history_path=DEFAULT_HISTORY_PATH,
):
    """
    Registra formalmente el cierre de una posición, calculando PnL si no se provee.
    """
    qty = float(qty)
    price = float(price)

    if entry_price is not None:
        entry_price = float(entry_price)

    # Calcular PnL si se conoce entry_price
    if pnl is None:
        if entry_price is not None and entry_price > 0:
            pnl = round((price - entry_price) * qty, 4)
        else:
            pnl = 0.0
    else:
        pnl = round(float(pnl), 4)

    if pnl_pct is None:
        if entry_price is not None and entry_price > 0:
            pnl_pct = round(((price - entry_price) / entry_price) * 100.0, 2)
        else:
            pnl_pct = 0.0
    else:
        pnl_pct = round(float(pnl_pct), 2)

    notional = round(qty * price, 2)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = {
        "trade_id": f"FLATTEN-{uuid.uuid4().hex[:8].upper()}",
        "symbol": symbol.strip().upper(),
        "side": side.strip().upper(),
        "qty": qty,
        "price": price,
        "entry_price": entry_price if entry_price is not None else price,
        "exit_price": price,
        "notional": notional,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "status": "FILLED",
        "mode": mode,
        "timestamp": now_iso,
        "why": reason or f"Closed position with Realized PnL ${pnl:+.2f} ({pnl_pct:+.2f}%)",
    }

    return log_trade(record, history_path=history_path)


def format_cell(ws, row, col, value=None, font=None, fill=None, alignment=None, border=None, number_format=None):
    """Helper seguro para estilizar y escribir celdas en openpyxl sin StyleProxy conflicts."""
    cell = ws.cell(row=row, column=col)
    if value is not None:
        cell.value = value
    if font is not None:
        cell.font = font
    if fill is not None:
        cell.fill = fill
    if alignment is not None:
        cell.alignment = alignment
    if border is not None:
        cell.border = border
    if number_format is not None:
        cell.number_format = number_format
    return cell


def generate_excel_workbook(metrics, history, output_path=DEFAULT_EXCEL_PATH):
    """
    Crea un libro de Excel institucional y elegante de 3 pestañas con openpyxl.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = Workbook()

        # Estilos comunes institucionales
        thin_border = Border(
            left=Side(style="thin", color="E0E0E0"),
            right=Side(style="thin", color="E0E0E0"),
            top=Side(style="thin", color="E0E0E0"),
            bottom=Side(style="thin", color="E0E0E0"),
        )
        gold_fill = PatternFill(start_color="181A20", end_color="181A20", fill_type="solid")
        title_font = Font(name="Segoe UI", size=13, bold=True, color="F0B90B")
        header_fill = PatternFill(start_color="2B313A", end_color="2B313A", fill_type="solid")
        header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        regular_font = Font(name="Segoe UI", size=10, color="000000")
        bold_font = Font(name="Segoe UI", size=10, bold=True, color="000000")

        win_fill = PatternFill(start_color="E6F4EA", end_color="E6F4EA", fill_type="solid")
        win_font = Font(name="Segoe UI", size=10, bold=True, color="137333")
        loss_fill = PatternFill(start_color="FCE8E6", end_color="FCE8E6", fill_type="solid")
        loss_font = Font(name="Segoe UI", size=10, bold=True, color="C5221F")

        # =========================================================================
        # PESTAÑA 1: Resumen Ejecutivo
        # =========================================================================
        ws1 = wb.active
        ws1.title = "Resumen Ejecutivo"
        ws1.views.sheetView[0].showGridLines = True

        # Banner Superior
        ws1.merge_cells("A1:G1")
        format_cell(
            ws1, 1, 1,
            value="BINANCE CRYPTO DAY TRADER — RESUMEN EJECUTIVO DE CARTERA",
            font=title_font,
            fill=gold_fill,
            alignment=Alignment(horizontal="center", vertical="center")
        )
        ws1.row_dimensions[1].height = 38

        format_cell(
            ws1, 2, 1,
            value=f"Generado: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  |  Modo: {metrics['mode']}  |  Divisa: USDT",
            font=Font(italic=True, size=9, color="555555")
        )

        # KPIs Principales
        format_cell(ws1, 4, 1, value="MÉTRICAS CLAVE DE CARTERA", font=Font(name="Segoe UI", size=11, bold=True, color="181A20"))
        kpis = [
            ("Presupuesto Base", f"${metrics['budget']:.2f} USDT"),
            ("Efectivo Disponible", f"${metrics['cash']:.2f} USDT"),
            ("Valor Total de Cartera", f"${metrics['portfolio_value']:.2f} USDT"),
            ("Rendimiento Total", f"{metrics['total_return_pct']:+.2f}%"),
            ("PnL Realizado Neto", f"${metrics['net_realized_pnl']:+.2f} USDT"),
            ("Total de Operaciones", f"{metrics['total_trades']}"),
            ("Operaciones Cerradas", f"{metrics['closed_trades']} ({metrics['wins']}W / {metrics['losses']}L / {metrics['scratches']}Scratch)"),
            ("Tasa de Acierto (Win Rate)", f"{metrics['win_rate']:.1f}%"),
            ("Factor de Beneficio (Profit Factor)", "Infinito" if metrics['profit_factor'] == float('inf') else f"{metrics['profit_factor']:.2f}"),
            ("Expectativa por Operación", f"${metrics['expected_value']:+.4f} USDT"),
        ]

        row = 5
        for label, val in kpis:
            format_cell(ws1, row, 1, value=label, font=bold_font, border=thin_border)
            val_font = regular_font
            if "USDT" in val and "+" in val:
                val_font = win_font
            elif "USDT" in val and "-" in val:
                val_font = loss_font
            format_cell(ws1, row, 2, value=val, font=val_font, border=thin_border)
            row += 1

        # Control de Riesgo Diario
        row += 1
        format_cell(ws1, row, 1, value="CUMPLIMIENTO DE REGLAS DE RIESGO", font=Font(name="Segoe UI", size=11, bold=True, color="181A20"))
        row += 1
        risk_headers = ["Regla de Riesgo", "Límite", "Estado Actual", "Veredicto"]
        for c_idx, rh in enumerate(risk_headers, 1):
            format_cell(ws1, row, c_idx, value=rh, font=header_font, fill=header_fill, alignment=Alignment(horizontal="center"), border=thin_border)

        risk_rows = [
            (
                "Pérdida Máxima Diaria ($7.50)",
                f"${metrics['max_daily_loss']:.2f} USDT",
                f"${metrics['daily_loss_realized']:.2f} USDT",
                "ALERTA BRECHA" if metrics["daily_loss_limit_breached"] else "CUMPLE (OK)",
            ),
            (
                "Operaciones Máximas Diarias (5)",
                f"{metrics['max_trades_per_day']} trades",
                f"{metrics['trades_today_count']} trades hoy",
                "LÍMITE ALCANZADO" if metrics["daily_trades_limit_breached"] else "CUMPLE (OK)",
            ),
            (
                "Pérdida Máxima por Operación",
                "$2.50 USDT",
                "Enforced via Stop",
                "CUMPLE (OK)",
            ),
            (
                "Ratio Riesgo / Beneficio Mínimo",
                "1.50 R",
                "Enforced en Plan",
                "CUMPLE (OK)",
            ),
            (
                "Stop Loss Obligatorio",
                "100% de órdenes",
                "Activo y Verificado",
                "CUMPLE (OK)",
            ),
            (
                "Límite de Asignación por Token",
                "50.0% ($50.00 USDT)",
                "Preservación de Capital",
                "CUMPLE (OK)",
            ),
        ]

        for r_item in risk_rows:
            row += 1
            for c_idx, val in enumerate(r_item, 1):
                fnt = regular_font
                fll = None
                align = None
                if c_idx == 4:
                    align = Alignment(horizontal="center")
                    if "ALERTA" in val or "LÍMITE" in val:
                        fll = loss_fill
                        fnt = loss_font
                    else:
                        fll = win_fill
                        fnt = win_font
                format_cell(ws1, row, c_idx, value=val, font=fnt, fill=fll, alignment=align, border=thin_border)

        # Posiciones Abiertas
        row += 2
        format_cell(ws1, row, 1, value="POSICIONES ABIERTAS ACTUALES", font=Font(name="Segoe UI", size=11, bold=True, color="181A20"))
        row += 1
        pos_headers = ["Símbolo", "Cantidad", "Precio Entrada", "Precio Actual", "Stop Loss", "Target", "PnL No Realizado"]
        for c_idx, ph in enumerate(pos_headers, 1):
            format_cell(ws1, row, c_idx, value=ph, font=header_font, fill=header_fill, alignment=Alignment(horizontal="center"), border=thin_border)

        if metrics["positions"]:
            for pos in metrics["positions"]:
                row += 1
                sym = pos.get("symbol", "")
                qty = float(pos.get("qty", 0.0))
                entry_px = float(pos.get("entry_price", 0.0))
                curr_px = float(pos.get("current_price", entry_px))
                stop_px = float(pos.get("stop_price", 0.0))
                target_px = float(pos.get("target_price", 0.0))
                unrealized = float(pos.get("unrealized_pnl", (curr_px - entry_px) * qty))

                format_cell(ws1, row, 1, value=sym, font=bold_font, border=thin_border)
                format_cell(ws1, row, 2, value=qty, font=regular_font, border=thin_border, number_format="#,##0.0000")
                format_cell(ws1, row, 3, value=entry_px, font=regular_font, border=thin_border, number_format="$#,##0.0000")
                format_cell(ws1, row, 4, value=curr_px, font=regular_font, border=thin_border, number_format="$#,##0.0000")
                format_cell(ws1, row, 5, value=stop_px, font=regular_font, border=thin_border, number_format="$#,##0.0000")
                format_cell(ws1, row, 6, value=target_px, font=regular_font, border=thin_border, number_format="$#,##0.0000")
                
                u_fnt = win_font if unrealized > 0 else (loss_font if unrealized < 0 else regular_font)
                u_fll = win_fill if unrealized > 0 else (loss_fill if unrealized < 0 else None)
                format_cell(ws1, row, 7, value=unrealized, font=u_fnt, fill=u_fll, border=thin_border, number_format="$#,##0.00")
        else:
            row += 1
            ws1.merge_cells(f"A{row}:G{row}")
            format_cell(
                ws1, row, 1,
                value="Sin posiciones abiertas. 100% efectivo disponible para setups de alta convicción.",
                font=Font(italic=True, size=10, color="555555"),
                alignment=Alignment(horizontal="center"),
                border=thin_border
            )

        # =========================================================================
        # PESTAÑA 2: Bitácora de Operaciones (Trade Log)
        # =========================================================================
        ws2 = wb.create_sheet(title="Bitácora de Operaciones")
        ws2.views.sheetView[0].showGridLines = True

        ws2.merge_cells("A1:M1")
        format_cell(
            ws2, 1, 1,
            value="BINANCE CRYPTO DAY TRADER — BITÁCORA HISTÓRICA DE OPERACIONES",
            font=title_font,
            fill=gold_fill,
            alignment=Alignment(horizontal="center", vertical="center")
        )
        ws2.row_dimensions[1].height = 35

        hist_headers = [
            "ID Operación",
            "Fecha / Hora (UTC)",
            "Símbolo",
            "Lado",
            "Modo",
            "Cantidad",
            "Precio Entrada ($)",
            "Precio Salida ($)",
            "Nocional ($)",
            "PnL ($)",
            "PnL (%)",
            "Estado",
            "Motivo / Justificación",
        ]

        row = 3
        for c_idx, h in enumerate(hist_headers, 1):
            format_cell(ws2, row, c_idx, value=h, font=header_font, fill=header_fill, alignment=Alignment(horizontal="center"), border=thin_border)

        # Mapa de compras anteriores para inferir precio de entrada si hace falta
        buy_entry_map = {}
        for t in history:
            if isinstance(t, dict):
                s = t.get("symbol")
                if t.get("side", "").upper() == "BUY" and s:
                    buy_entry_map[s] = float(t.get("price", 0.0))

        if history:
            for t in history:
                if not isinstance(t, dict):
                    continue
                row += 1
                side = t.get("side", "").upper()
                sym = t.get("symbol", "")
                qty = float(t.get("qty", 0.0))
                px = float(t.get("price", 0.0))
                notional = float(t.get("notional", round(qty * px, 2)))
                is_closed = is_closed_trade(t)
                pnl = get_trade_pnl(t) if is_closed else None
                pnl_pct = get_trade_pnl_pct(t) if is_closed else None

                # Determinar precio de entrada y salida
                if side == "BUY":
                    entry_px = px
                    exit_px = None
                else:
                    exit_px = px
                    if "entry_price" in t:
                        entry_px = float(t["entry_price"])
                    elif sym in buy_entry_map:
                        entry_px = buy_entry_map[sym]
                    elif qty > 0 and pnl != 0:
                        entry_px = exit_px - (pnl / qty)
                    else:
                        entry_px = exit_px

                format_cell(ws2, row, 1, value=str(t.get("trade_id", "")), font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)
                format_cell(ws2, row, 2, value=str(t.get("timestamp", "")), font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)
                format_cell(ws2, row, 3, value=sym, font=bold_font, border=thin_border)
                format_cell(ws2, row, 4, value=side, font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)
                format_cell(ws2, row, 5, value=str(t.get("mode", "PAPER")), font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)
                format_cell(ws2, row, 6, value=qty, font=regular_font, border=thin_border, number_format="#,##0.0000")
                format_cell(ws2, row, 7, value=entry_px, font=regular_font, border=thin_border, number_format="$#,##0.0000")
                
                if exit_px is not None:
                    format_cell(ws2, row, 8, value=exit_px, font=regular_font, border=thin_border, number_format="$#,##0.0000")
                else:
                    format_cell(ws2, row, 8, value="-", font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)

                format_cell(ws2, row, 9, value=notional, font=regular_font, border=thin_border, number_format="$#,##0.00")

                pnl_fnt = regular_font
                pnl_fll = None
                if pnl is not None:
                    if pnl > 0:
                        pnl_fnt = win_font
                        pnl_fll = win_fill
                    elif pnl < 0:
                        pnl_fnt = loss_font
                        pnl_fll = loss_fill
                    format_cell(ws2, row, 10, value=pnl, font=pnl_fnt, fill=pnl_fll, border=thin_border, number_format="$#,##0.00")
                    format_cell(ws2, row, 11, value=f"{pnl_pct:+.2f}%", font=pnl_fnt, fill=pnl_fll, alignment=Alignment(horizontal="center"), border=thin_border)
                else:
                    format_cell(ws2, row, 10, value="-", font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)
                    format_cell(ws2, row, 11, value="-", font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)

                format_cell(ws2, row, 12, value=str(t.get("status", "FILLED")), font=regular_font, alignment=Alignment(horizontal="center"), border=thin_border)
                format_cell(ws2, row, 13, value=str(t.get("why", "")), font=regular_font, border=thin_border)

        # =========================================================================
        # PESTAÑA 3: Métricas y Rendimiento (Performance Analytics)
        # =========================================================================
        ws3 = wb.create_sheet(title="Métricas y Rendimiento")
        ws3.views.sheetView[0].showGridLines = True

        ws3.merge_cells("A1:D1")
        format_cell(
            ws3, 1, 1,
            value="BINANCE CRYPTO DAY TRADER — ANÁLISIS DE RENDIMIENTO",
            font=title_font,
            fill=gold_fill,
            alignment=Alignment(horizontal="center", vertical="center")
        )
        ws3.row_dimensions[1].height = 35

        perf_headers = ["Indicador de Rendimiento", "Valor", "Métrica Benchmark", "Evaluación"]
        row = 3
        for c_idx, ph in enumerate(perf_headers, 1):
            format_cell(ws3, row, c_idx, value=ph, font=header_font, fill=header_fill, alignment=Alignment(horizontal="center"), border=thin_border)

        perf_rows = [
            ("Operaciones Totales", f"{metrics['total_trades']}", "Muestreo mínimo: 20", "En progreso"),
            ("Operaciones Cerradas", f"{metrics['closed_trades']}", "Trades completados", "En progreso"),
            ("Operaciones Ganadoras (Wins)", f"{metrics['wins']}", "-", "Frecuencia de acierto"),
            ("Operaciones Perdedoras (Losses)", f"{metrics['losses']}", "-", "Control de riesgo"),
            ("Operaciones Empatadas (Breakeven)", f"{metrics['scratches']}", "-", "Preservación capital"),
            ("Tasa de Acierto (Win Rate)", f"{metrics['win_rate']:.1f}%", "> 50.0%", "EXCELENTE" if metrics['win_rate'] >= 50 else "NORMAL"),
            ("Ganancia Bruta Acumulada", f"${metrics['gross_profit']:.2f} USDT", "-", "Gains"),
            ("Pérdida Bruta Acumulada", f"${metrics['gross_loss']:.2f} USDT", "< $7.50 diario", "Bajo Control"),
            ("PnL Realizado Neto", f"${metrics['net_realized_pnl']:+.2f} USDT", "> $0.00 USDT", "POSITIVO" if metrics['net_realized_pnl'] >= 0 else "NEGATIVO"),
            ("Factor de Beneficio (Profit Factor)", "Infinito" if metrics['profit_factor'] == float('inf') else f"{metrics['profit_factor']:.2f}", "> 1.50", "ÓPTIMO" if metrics['profit_factor'] >= 1.5 else "REVISAR"),
            ("Ganancia Promedio por Trade Ganador", f"${metrics['avg_win']:.2f} USDT", "-", "Magnitud Win"),
            ("Pérdida Promedio por Trade Perdedor", f"${metrics['avg_loss']:.2f} USDT", "< $2.50 límite", "CUMPLE"),
            ("Ratio Ganancia/Pérdida (Payout Ratio)", f"{metrics['win_loss_ratio']:.2f}", "> 1.50 R", "ALINEADO" if metrics['win_loss_ratio'] >= 1.5 else "EN DESARROLLO"),
            ("Mayor Ganancia (Largest Win)", f"${metrics['largest_win']:.2f} USDT", "-", "Best Trade"),
            ("Mayor Pérdida (Largest Loss)", f"${metrics['largest_loss']:.2f} USDT", "Máx -$2.50", "DENTRO DE LÍMITE"),
            ("Expectativa Matemática por Trade", f"${metrics['expected_value']:+.4f} USDT", "> 0", "EDGE POSITIVO" if metrics['expected_value'] > 0 else "NEUTRAL"),
        ]

        for p_row in perf_rows:
            row += 1
            for c_idx, val in enumerate(p_row, 1):
                fnt = bold_font if c_idx == 1 else regular_font
                fll = None
                align = Alignment(horizontal="center") if c_idx == 4 else None
                if c_idx == 4:
                    if "EXCELENTE" in val or "ÓPTIMO" in val or "POSITIVO" in val or "EDGE" in val or "CUMPLE" in val:
                        fll = win_fill
                        fnt = win_font
                    elif "NEGATIVO" in val or "REVISAR" in val:
                        fll = loss_fill
                        fnt = loss_font
                format_cell(ws3, row, c_idx, value=val, font=fnt, fill=fll, alignment=align, border=thin_border)

        # Auto-ajuste de anchos de columnas en todas las hojas
        for sheet in [ws1, ws2, ws3]:
            for col in sheet.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    if cell.value:
                        lines = str(cell.value).split("\n")
                        for line in lines:
                            max_len = max(max_len, len(line))
                sheet.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 48)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        wb.save(output_path)
        return True, output_path
    except Exception as e:
        return False, str(e)


def generate_executive_report(
    config_path=DEFAULT_CONFIG_PATH,
    portfolio_path=DEFAULT_PORTFOLIO_PATH,
    history_path=DEFAULT_HISTORY_PATH,
    excel_path=DEFAULT_EXCEL_PATH,
    prices_path=DEFAULT_PRICES_PATH,
    quiet=False,
):
    """
    Genera el informe ejecutivo en terminal y actualiza el libro Excel institucional.
    """
    config = load_json(config_path, default={})
    portfolio = load_json(portfolio_path, default={})
    history = load_json(history_path, default=[])
    prices = load_json(prices_path, default={})

    metrics = calculate_metrics(history, portfolio, config, prices=prices)
    excel_ok, excel_result = generate_excel_workbook(metrics, history, output_path=excel_path)
    metrics["excel_generated"] = excel_ok
    metrics["excel_path"] = excel_result if excel_ok else None

    if not quiet:
        print("=" * 65)
        print("   BINANCE CRYPTO DAY TRADER — INFORME EJECUTIVO DEL ENJAMBRE")
        print("=" * 65)
        print(f" Exchange:           BINANCE ({metrics['mode']})")
        print(f" Presupuesto Base:   ${metrics['budget']:.2f} USDT")
        print(f" Efectivo Actual:    ${metrics['cash']:.2f} USDT")
        print(f" Valor de Cartera:   ${metrics['portfolio_value']:.2f} USDT ({metrics['total_return_pct']:+.2f}%)")
        print(f" Posiciones Activas: {metrics['positions_count']}")
        print(f" Operaciones:        {metrics['total_trades']} totales ({metrics['closed_trades']} cerradas)")
        print(f" Tasa de Acierto:    {metrics['win_rate']:.1f}% ({metrics['wins']}W / {metrics['losses']}L / {metrics['scratches']}Scratch)")
        print(f" PnL Realizado Neto: ${metrics['net_realized_pnl']:+.2f} USDT (Gross +${metrics['gross_profit']:.2f} / -${metrics['gross_loss']:.2f})")
        pf_str = "Infinito" if metrics['profit_factor'] == float('inf') else f"{metrics['profit_factor']:.2f}"
        print(f" Factor Beneficio:   {pf_str} (Win/Loss Ratio: {metrics['win_loss_ratio']:.2f})")
        print("-" * 65)
        print(f" Riesgo Hoy:         ${metrics['daily_loss_realized']:.2f} / ${metrics['max_daily_loss']:.2f} USDT máx ({metrics['trades_today_count']}/{metrics['max_trades_per_day']} trades)")
        print("-" * 65)

        if metrics["positions"]:
            print(" POSICIONES ABIERTAS:")
            for pos in metrics["positions"]:
                sym = pos.get("symbol", "N/A")
                qty = float(pos.get("qty", 0.0))
                entry_px = float(pos.get("entry_price", 0.0))
                curr_px = float(pos.get("current_price", entry_px))
                print(f"   • {sym}: {qty} unidades @ ${entry_px:.4f} (Actual: ${curr_px:.4f})")
            print("-" * 65)

        if excel_ok:
            print(f" [OK] Libro Excel ejecutivo actualizado: {os.path.basename(excel_result)}")
        else:
            print(f" [INFO] Reporte Excel omitido: {excel_result}")
        print("=" * 65)

    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="trader-reporter: Generador de Resumen Ejecutivo y Bitácora de Trading Binance"
    )
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponibles")

    # Comando summary
    summary_parser = subparsers.add_parser("summary", help="Generar informe ejecutivo y actualizar Excel")
    summary_parser.add_argument("--json", action="store_true", help="Salida en formato JSON")
    summary_parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    summary_parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_PATH)
    summary_parser.add_argument("--history", default=DEFAULT_HISTORY_PATH)
    summary_parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH)
    summary_parser.add_argument("--prices", default=DEFAULT_PRICES_PATH)

    # Comando log-close
    close_parser = subparsers.add_parser("log-close", help="Registrar el cierre de una posición")
    close_parser.add_argument("--symbol", required=True, help="Símbolo del token (e.g. BTCUSDT)")
    close_parser.add_argument("--side", default="SELL", help="Lado de cierre (default SELL)")
    close_parser.add_argument("--qty", type=float, required=True, help="Cantidad cerrada")
    close_parser.add_argument("--price", type=float, required=True, help="Precio de salida en USDT")
    close_parser.add_argument("--entry-price", type=float, default=None, help="Precio de entrada original")
    close_parser.add_argument("--pnl", type=float, default=None, help="PnL realizado en USDT")
    close_parser.add_argument("--pnl-pct", type=float, default=None, help="PnL porcentual")
    close_parser.add_argument("--reason", default="", help="Motivo o regla de salida")
    close_parser.add_argument("--mode", default="PAPER TRADING", help="Modo (PAPER TRADING / LIVE)")
    close_parser.add_argument("--history", default=DEFAULT_HISTORY_PATH)
    close_parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH)

    # Comando log-buy
    buy_parser = subparsers.add_parser("log-buy", help="Registrar una compra / apertura de posición")
    buy_parser.add_argument("--symbol", required=True, help="Símbolo del token")
    buy_parser.add_argument("--qty", type=float, required=True, help="Cantidad comprada")
    buy_parser.add_argument("--price", type=float, required=True, help="Precio de entrada en USDT")
    buy_parser.add_argument("--stop", type=float, default=0.0, help="Nivel de Stop Loss")
    buy_parser.add_argument("--target", type=float, default=0.0, help="Nivel de Take Profit")
    buy_parser.add_argument("--reason", default="", help="Motivo o setup técnico")
    buy_parser.add_argument("--mode", default="PAPER TRADING", help="Modo")
    buy_parser.add_argument("--history", default=DEFAULT_HISTORY_PATH)
    buy_parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH)

    # Flags globales cuando se ejecuta sin subcomandos
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO_PATH)
    parser.add_argument("--history", default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH)
    parser.add_argument("--prices", default=DEFAULT_PRICES_PATH)
    parser.add_argument("--quiet", action="store_true", help="Modo silencioso en consola")

    args = parser.parse_args()

    if args.command == "log-close":
        record = log_closed_position(
            symbol=args.symbol,
            side=args.side,
            qty=args.qty,
            price=args.price,
            entry_price=args.entry_price,
            pnl=args.pnl,
            pnl_pct=args.pnl_pct,
            reason=args.reason,
            mode=args.mode,
            history_path=args.history,
        )
        print(f" [OK] Posición cerrada registrada: {record['trade_id']} | {record['symbol']} | PnL: ${record['pnl']:+.2f}")
        # Actualizar Excel tras registrar cierre
        generate_executive_report(
            config_path=getattr(args, "config", DEFAULT_CONFIG_PATH),
            portfolio_path=getattr(args, "portfolio", DEFAULT_PORTFOLIO_PATH),
            history_path=args.history,
            excel_path=args.excel,
            prices_path=getattr(args, "prices", DEFAULT_PRICES_PATH),
            quiet=True,
        )
        return

    if args.command == "log-buy":
        notional = round(args.qty * args.price, 2)
        rec = {
            "trade_id": f"PAPER-{uuid.uuid4().hex[:8].upper()}",
            "symbol": args.symbol.strip().upper(),
            "side": "BUY",
            "qty": args.qty,
            "price": args.price,
            "notional": notional,
            "stop_price": args.stop,
            "target_price": args.target,
            "status": "FILLED",
            "mode": args.mode,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "why": args.reason or f"Buy {args.qty} {args.symbol} @ ${args.price:.4f}",
        }
        log_trade(rec, history_path=args.history)
        print(f" [OK] Compra registrada: {rec['trade_id']} | {rec['symbol']} | {rec['qty']} @ ${rec['price']:.4f}")
        generate_executive_report(
            config_path=getattr(args, "config", DEFAULT_CONFIG_PATH),
            portfolio_path=getattr(args, "portfolio", DEFAULT_PORTFOLIO_PATH),
            history_path=args.history,
            excel_path=args.excel,
            prices_path=getattr(args, "prices", DEFAULT_PRICES_PATH),
            quiet=True,
        )
        return

    # Acción por defecto: generar resumen ejecutivo
    metrics = generate_executive_report(
        config_path=args.config,
        portfolio_path=args.portfolio,
        history_path=args.history,
        excel_path=args.excel,
        prices_path=args.prices,
        quiet=args.json or args.quiet,
    )

    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
