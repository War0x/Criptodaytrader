#!/usr/bin/env python3
"""
Unit and Integration Tests for trader-reporter (report.py)
Covers:
1. Metric calculations (win rate, profit factor, risk compliance, drawdown)
2. Closed trade detection and PnL extraction
3. Logging trades and closed positions to JSON history
4. Multi-sheet Excel workbook generation with openpyxl
5. CLI execution and JSON output
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import importlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

report_mod = importlib.import_module("trader-reporter.report")


class TestReporterMetrics(unittest.TestCase):
    """Test suite for metric calculations in report.py."""

    def setUp(self):
        self.config = {
            "budget": 100.0,
            "currency": "USDT",
            "paper": True,
            "max_loss_per_trade_usd": 2.5,
            "max_daily_loss_usd": 7.5,
            "max_trades_per_day": 5,
        }
        self.portfolio = {
            "cash": 95.0,
            "portfolio_value": 102.5,
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "qty": 0.0005,
                    "entry_price": 95000.0,
                    "current_price": 96000.0,
                    "stop_price": 94000.0,
                    "target_price": 97000.0,
                }
            ],
        }

    def test_empty_history_metrics(self):
        """Verify metrics on empty trade history."""
        metrics = report_mod.calculate_metrics([], self.portfolio, self.config)
        self.assertEqual(metrics["total_trades"], 0)
        self.assertEqual(metrics["closed_trades"], 0)
        self.assertEqual(metrics["win_rate"], 0.0)
        self.assertEqual(metrics["net_realized_pnl"], 0.0)
        self.assertEqual(metrics["profit_factor"], 0.0)
        self.assertEqual(metrics["positions_count"], 1)
        self.assertEqual(metrics["daily_loss_limit_breached"], False)

    def test_winning_and_losing_metrics(self):
        """Verify win rate, profit factor, and PnL calculations with mock trades."""
        history = [
            # Buy 1
            {
                "trade_id": "TRADE-1",
                "symbol": "SOLUSDT",
                "side": "BUY",
                "qty": 0.5,
                "price": 200.0,
                "timestamp": "2026-11-13T10:00:00Z",
            },
            # Sell 1 (Win +$2.50)
            {
                "trade_id": "FLATTEN-1",
                "symbol": "SOLUSDT",
                "side": "SELL",
                "qty": 0.5,
                "price": 205.0,
                "pnl": 2.50,
                "pnl_pct": 2.50,
                "timestamp": "2026-11-13T10:15:00Z",
            },
            # Buy 2
            {
                "trade_id": "TRADE-2",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "qty": 0.02,
                "price": 3000.0,
                "timestamp": "2026-11-13T11:00:00Z",
            },
            # Sell 2 (Loss -$1.00)
            {
                "trade_id": "FLATTEN-2",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "qty": 0.02,
                "price": 2950.0,
                "pnl": -1.00,
                "pnl_pct": -1.67,
                "timestamp": "2026-11-13T11:30:00Z",
            },
            # Sell 3 (Breakeven $0.00)
            {
                "trade_id": "FLATTEN-3",
                "symbol": "DOGEUSDT",
                "side": "SELL",
                "qty": 100.0,
                "price": 0.10,
                "pnl": 0.00,
                "pnl_pct": 0.00,
                "timestamp": "2026-11-13T12:00:00Z",
            },
        ]

        metrics = report_mod.calculate_metrics(history, self.portfolio, self.config)
        self.assertEqual(metrics["total_trades"], 5)
        self.assertEqual(metrics["closed_trades"], 3)
        self.assertEqual(metrics["wins"], 1)
        self.assertEqual(metrics["losses"], 1)
        self.assertEqual(metrics["scratches"], 1)

        # Win rate: 1 win out of 3 closed = 33.33%
        self.assertAlmostEqual(metrics["win_rate"], 33.33, places=1)
        self.assertAlmostEqual(metrics["gross_profit"], 2.50)
        self.assertAlmostEqual(metrics["gross_loss"], 1.00)
        self.assertAlmostEqual(metrics["net_realized_pnl"], 1.50)
        self.assertAlmostEqual(metrics["profit_factor"], 2.50)
        self.assertAlmostEqual(metrics["largest_win"], 2.50)
        self.assertAlmostEqual(metrics["largest_loss"], -1.00)

    def test_daily_risk_limits_breach(self):
        """Verify breach detection when daily loss or max trades are exceeded."""
        today_iso = report_mod.datetime.now(report_mod.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 5 trades today totaling $8.00 in losses (limit is $7.50, max trades is 5)
        history = [
            {"trade_id": f"FLATTEN-{i}", "symbol": "BTCUSDT", "side": "SELL", "pnl": -1.60, "timestamp": today_iso}
            for i in range(5)
        ]
        metrics = report_mod.calculate_metrics(history, self.portfolio, self.config)
        self.assertEqual(metrics["trades_today_count"], 5)
        self.assertAlmostEqual(metrics["daily_loss_realized"], 8.00)
        self.assertTrue(metrics["daily_loss_limit_breached"])
        self.assertTrue(metrics["daily_trades_limit_breached"])


class TestReporterLoggingAndExcel(unittest.TestCase):
    """Test suite for trade logging and Excel report generation."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.history_path = os.path.join(self.test_dir, "test_history.json")
        self.excel_path = os.path.join(self.test_dir, "test_report.xlsx")
        self.config_path = os.path.join(self.test_dir, "test_config.json")
        self.portfolio_path = os.path.join(self.test_dir, "test_portfolio.json")

        report_mod.save_json(self.config_path, {"budget": 100.0, "paper": True})
        report_mod.save_json(self.portfolio_path, {"cash": 100.0, "portfolio_value": 100.0, "positions": []})
        report_mod.save_json(self.history_path, [])

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_log_closed_position(self):
        """Verify logging a closed position writes correctly to history file."""
        rec = report_mod.log_closed_position(
            symbol="BTCUSDT",
            qty=0.001,
            price=96000.0,
            entry_price=95000.0,
            reason="Hit TP1",
            history_path=self.history_path,
        )
        self.assertIn("trade_id", rec)
        self.assertEqual(rec["symbol"], "BTCUSDT")
        self.assertEqual(rec["side"], "SELL")
        self.assertAlmostEqual(rec["pnl"], 1.00)
        self.assertAlmostEqual(rec["pnl_pct"], 1.05, places=2)

        # Check file was updated
        data = report_mod.load_json(self.history_path)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["trade_id"], rec["trade_id"])

    def test_excel_generation(self):
        """Verify generation of institutional Excel report with openpyxl."""
        history = [
            {
                "trade_id": "PAPER-100",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "qty": 0.05,
                "price": 3100.0,
                "notional": 155.0,
                "timestamp": "2026-11-13T12:00:00Z",
                "why": "Breakout",
            },
            {
                "trade_id": "FLATTEN-101",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "qty": 0.05,
                "price": 3150.0,
                "notional": 157.5,
                "pnl": 2.50,
                "pnl_pct": 1.61,
                "timestamp": "2026-11-13T12:30:00Z",
                "why": "Take Profit reached",
            },
        ]
        metrics = report_mod.calculate_metrics(history, {}, {"budget": 100.0})
        ok, res = report_mod.generate_excel_workbook(metrics, history, output_path=self.excel_path)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.excel_path))

        # Inspect generated workbook with openpyxl
        from openpyxl import load_workbook

        wb = load_workbook(self.excel_path)
        self.assertIn("Resumen Ejecutivo", wb.sheetnames)
        self.assertIn("Bitácora de Operaciones", wb.sheetnames)
        self.assertIn("Métricas y Rendimiento", wb.sheetnames)

        ws1 = wb["Resumen Ejecutivo"]
        self.assertEqual(ws1["A1"].value, "BINANCE CRYPTO DAY TRADER — RESUMEN EJECUTIVO DE CARTERA")

        ws2 = wb["Bitácora de Operaciones"]
        self.assertEqual(ws2["C4"].value, "ETHUSDT")  # First row of trade data
        self.assertEqual(ws2["D4"].value, "BUY")
        self.assertEqual(ws2["D5"].value, "SELL")

    def test_generate_executive_report_quiet(self):
        """Verify generate_executive_report executes cleanly and returns KPIs."""
        res = report_mod.generate_executive_report(
            config_path=self.config_path,
            portfolio_path=self.portfolio_path,
            history_path=self.history_path,
            excel_path=self.excel_path,
            quiet=True,
        )
        self.assertIn("budget", res)
        self.assertIn("win_rate", res)
        self.assertTrue(res["excel_generated"])
        self.assertTrue(os.path.exists(self.excel_path))


if __name__ == "__main__":
    unittest.main()
