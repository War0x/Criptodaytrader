#!/usr/bin/env python3
"""Unit tests for trader-alerts/alerts.py. Standard library only."""

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

alerts_mod = importlib.import_module("trader-alerts.alerts")


class TestCryptoAlertsEngine(unittest.TestCase):
    """Unit tests for Binance real-time price & position alerts."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.portfolio_path = os.path.join(self.test_dir, "portfolio.json")
        self.prices_path = os.path.join(self.test_dir, "prices.json")
        self.alerts_path = os.path.join(self.test_dir, "alerts.json")
        self.history_path = os.path.join(self.test_dir, "trade_history.json")
        self.offline_dir = os.path.join(self.test_dir, "fixtures")
        os.makedirs(self.offline_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_fixture(self, symbol, price):
        path = os.path.join(self.offline_dir, f"{symbol.upper()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"symbol": symbol.upper(), "price": price}, f)

    def test_format_price(self):
        """Verify dynamic precision preserving sub-dollar and micro-penny prices."""
        self.assertEqual(alerts_mod.format_price(68954.123), "68954.12")
        self.assertEqual(alerts_mod.format_price(19.4567), "19.4567")
        self.assertEqual(alerts_mod.format_price(0.06544), "0.065440")
        self.assertEqual(alerts_mod.format_price(0.00001234), "0.00001234")
        self.assertEqual(alerts_mod.format_price(None), "N/A")

    def test_rules_from_plan(self):
        """Verify rules generated from plan.json contain stop, target 1R and target 2R."""
        plan = {
            "symbol": "BTCUSDT",
            "entry": 69000.0,
            "stop": 68800.0,
            "stop_detail": "1.5x ATR dynamic stop",
            "target": 69300.0,
            "target_1r": 69300.0,
            "target_2r": 69500.0,
        }
        rules = alerts_mod.rules_from_plan(plan)
        self.assertEqual(len(rules), 3)
        rule_types = {r["type"] for r in rules}
        self.assertIn("stop", rule_types)
        self.assertIn("target", rule_types)

        stop_rule = next(r for r in rules if r["type"] == "stop")
        self.assertEqual(stop_rule["price"], 68800.0)
        self.assertEqual(stop_rule["symbol"], "BTCUSDT")

    def test_evaluate_rules_stop_and_target(self):
        """Verify rule evaluation triggers stop and target thresholds."""
        rules = [
            {"id": "sol-stop", "symbol": "SOLUSDT", "type": "stop", "price": 195.0},
            {"id": "sol-target", "symbol": "SOLUSDT", "type": "target", "price": 210.0},
        ]
        state = {"rung": {}}

        # Price within range
        trigs, state = alerts_mod.evaluate_rules(rules, {"SOLUSDT": 200.0}, state=state)
        self.assertEqual(len(trigs), 0)

        # Price hits stop
        trigs, state = alerts_mod.evaluate_rules(rules, {"SOLUSDT": 194.0}, state=state)
        self.assertEqual(len(trigs), 1)
        self.assertEqual(trigs[0]["id"], "sol-stop")
        self.assertIn("sol-stop", state["rung"])

        # Second check: should not ring again (already rung)
        trigs_again, state = alerts_mod.evaluate_rules(rules, {"SOLUSDT": 193.0}, state=state)
        self.assertEqual(len(trigs_again), 0)

    def test_evaluate_rules_pct_move(self):
        """Verify volatility percentage move alerts."""
        rules = [
            {"id": "doge-vol", "symbol": "DOGEUSDT", "type": "pct_move", "pct": 5.0, "ref": 0.10}
        ]
        state = {"rung": {}}
        # Price moved from 0.10 to 0.106 (+6%)
        trigs, state = alerts_mod.evaluate_rules(rules, {"DOGEUSDT": 0.106}, state=state)
        self.assertEqual(len(trigs), 1)
        self.assertEqual(trigs[0]["id"], "doge-vol")
        self.assertIn("VOLATILITY ALERT", trigs[0]["message"])

    def test_check_positions_stop_loss(self):
        """Verify position check triggers stop-loss alert when price dips below stop."""
        portfolio = {
            "cash": 50.0,
            "portfolio_value": 100.0,
            "positions": [
                {
                    "symbol": "SOLUSDT",
                    "qty": 0.25,
                    "entry_price": 200.0,
                    "stop_price": 195.0,
                    "target_price": 210.0,
                    "current_price": 200.0,
                }
            ],
        }
        alerts_mod.write_json(self.portfolio_path, portfolio)
        self._write_fixture("SOLUSDT", 194.0)

        alerts = alerts_mod.check_positions(
            self.portfolio_path,
            prices_path=self.prices_path,
            auto_flatten=False,
            offline_dir=self.offline_dir,
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["type"], "STOP_LOSS")
        self.assertEqual(alerts[0]["symbol"], "SOLUSDT")

    def test_check_positions_take_profit(self):
        """Verify position check triggers take-profit alert when price hits target."""
        portfolio = {
            "cash": 50.0,
            "portfolio_value": 100.0,
            "positions": [
                {
                    "symbol": "SOLUSDT",
                    "qty": 0.25,
                    "entry_price": 200.0,
                    "stop_price": 195.0,
                    "target_price": 210.0,
                    "current_price": 200.0,
                }
            ],
        }
        alerts_mod.write_json(self.portfolio_path, portfolio)
        self._write_fixture("SOLUSDT", 212.0)

        alerts = alerts_mod.check_positions(
            self.portfolio_path,
            prices_path=self.prices_path,
            auto_flatten=False,
            offline_dir=self.offline_dir,
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["type"], "TAKE_PROFIT")
        self.assertEqual(alerts[0]["symbol"], "SOLUSDT")

    def test_auto_flatten_execution(self):
        """Verify auto-flatten automatically closes the position and updates cash & history."""
        portfolio = {
            "cash": 50.0,
            "portfolio_value": 100.0,
            "positions": [
                {
                    "symbol": "SOLUSDT",
                    "qty": 0.25,
                    "entry_price": 200.0,
                    "stop_price": 195.0,
                    "target_price": 210.0,
                    "current_price": 200.0,
                }
            ],
        }
        alerts_mod.write_json(self.portfolio_path, portfolio)
        alerts_mod.write_json(self.history_path, [])
        self._write_fixture("SOLUSDT", 192.0)  # Stop triggered

        alerts = alerts_mod.check_positions(
            self.portfolio_path,
            prices_path=self.prices_path,
            auto_flatten=True,
            history_path=self.history_path,
            offline_dir=self.offline_dir,
        )
        self.assertEqual(len(alerts), 1)

        # Inspect updated portfolio
        updated_port = alerts_mod.load_json(self.portfolio_path)
        self.assertEqual(len(updated_port["positions"]), 0)  # Position was flattened
        # Cash = 50 + (0.25 * 192 = 48) = 98.0
        self.assertEqual(updated_port["cash"], 98.0)
        self.assertEqual(updated_port["portfolio_value"], 98.0)

        # Inspect updated trade_history
        history = alerts_mod.load_json(self.history_path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["symbol"], "SOLUSDT")
        self.assertEqual(history[0]["side"], "SELL")
        self.assertEqual(history[0]["price"], 192.0)
        self.assertEqual(history[0]["pnl"], -2.0)

    def test_market_status(self):
        """Verify market status reports 24/7 Binance crypto session and funding time."""
        st = alerts_mod.market_status()
        self.assertIn("OPEN", st["status"])
        self.assertIn("UTC", st["now_utc"])
        self.assertIn("next_funding_utc", st)
        self.assertGreaterEqual(st["next_funding_in_minutes"], 0)


if __name__ == "__main__":
    unittest.main()
