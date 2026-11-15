#!/usr/bin/env python3
"""Comprehensive Unit Tests for trader-timemachine."""

import json
import os
import subprocess
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
TIMEMACHINE_PY = os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import importlib
tm_mod = importlib.import_module("trader-timemachine.timemachine")


class TestTimeMachine(unittest.TestCase):

    def test_precision_calculation(self):
        self.assertEqual(tm_mod.get_price_precision(95000.0), 2)
        self.assertEqual(tm_mod.get_price_precision(250.5), 4)
        self.assertEqual(tm_mod.get_price_precision(0.0654), 5)
        self.assertEqual(tm_mod.get_price_precision(0.00045), 6)

    def test_fallback_ratio_pool(self):
        pool = tm_mod.build_ratio_pool(None)
        self.assertGreaterEqual(len(pool), 100)
        for h_r, l_r, c_r in pool:
            self.assertGreater(h_r, 0.0)
            self.assertGreater(l_r, 0.0)
            self.assertGreater(c_r, 0.0)
            self.assertGreaterEqual(h_r, l_r)

    def test_monte_carlo_buy_side(self):
        result = tm_mod.run_monte_carlo(
            symbol="SOLUSDT",
            entry=200.0,
            stop=195.0,
            target=210.0,
            side="BUY",
            replays=1000,
            horizon_bars=24,
            seed=42
        )
        self.assertEqual(result["created_by"], "trader-timemachine")
        self.assertEqual(result["symbol"], "SOLUSDT")
        self.assertEqual(result["side"], "BUY")
        self.assertIn("results", result)
        res = result["results"]
        self.assertIn("win_rate", res)
        self.assertIn("p_target", res)
        self.assertIn("p_stop", res)
        self.assertIn("p_neither", res)
        self.assertIn("profit_factor", res)
        self.assertIn("recommendation", res)
        self.assertAlmostEqual(res["p_target"] + res["p_stop"] + res["p_neither"], 1.0, places=3)

    def test_monte_carlo_sell_side(self):
        result = tm_mod.run_monte_carlo(
            symbol="ETHUSDT",
            entry=3000.0,
            stop=3050.0,
            target=2900.0,
            side="SELL",
            replays=1000,
            horizon_bars=24,
            seed=42
        )
        self.assertEqual(result["side"], "SELL")
        res = result["results"]
        self.assertGreaterEqual(res["win_rate"], 0.0)
        self.assertGreaterEqual(res["expected_drawdown_pct"], 0.0)

    def test_cli_execution_and_json_output(self):
        cmd = [sys.executable, TIMEMACHINE_PY, "run", "--replays", "500", "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertEqual(data.get("created_by"), "trader-timemachine")
        self.assertEqual(data.get("simulations"), 500)

    def test_chart_subcommand(self):
        cmd = [sys.executable, TIMEMACHINE_PY, "chart"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Error: {proc.stderr}")
        self.assertIn("BINANCE TIME MACHINE", proc.stdout)
        self.assertIn("Outcome Distribution", proc.stdout)


if __name__ == "__main__":
    unittest.main()
