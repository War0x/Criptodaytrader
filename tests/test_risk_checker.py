#!/usr/bin/env python3
"""Comprehensive Unit Tests for trader-risk/check.py

Audits all 10 risk and compliance rules:
  1. Format & Asset Validation (USDT symbol, BUY/SELL, positive qty, positive entry)
  2. Minimum Notional (>= $5.00 USDT)
  3. Position Cap (<= 50% budget)
  4. Available Cash (<= remaining cash in portfolio)
  5. Mandatory Stop Loss (required, stop < entry for BUY, stop > entry for SELL)
  6. Maximum Loss Per Trade (<= $2.50 USDT)
  7. Minimum Reward:Risk Ratio (>= 1.5 R and correct profit direction)
  8. Cumulative Daily Loss Limit (<= $7.50 USDT)
  9. Maximum Daily Trade Limit (<= 5 trades/day)
  10. Binance Orderbook Spread & Slippage (<= 0.2% with offline fallback)
  11. SHA-256 Fingerprint & State Reporting
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import importlib
risk_mod = importlib.import_module("trader-risk.check")


class TestRiskChecker10Rules(unittest.TestCase):
    def setUp(self):
        self.config = {
            "budget": 100.0,
            "currency": "USDT",
            "exchange": "binance",
            "paper": True,
            "max_loss_per_trade_usd": 2.5,
            "max_daily_loss_usd": 7.5,
            "max_trades_per_day": 5,
            "min_reward_risk": 1.5,
            "require_stop": True,
            "max_position_pct": 50.0,
            "min_notional_usdt": 5.0,
            "slippage_tolerance_pct": 0.2,
        }
        self.portfolio = {
            "cash": 100.0,
            "portfolio_value": 100.0,
            "currency": "USDT",
            "positions": [],
        }

    # 1. Format & Asset Validation
    def test_rule1_format_missing_symbol(self):
        order = {"symbol": "", "side": "BUY", "qty": 1.0, "entry_price": 10.0, "stop_loss_stop_price": 9.0, "take_profit_limit_price": 12.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Missing trading symbol" in r for r in res["results"][0]["reasons"]))

    def test_rule1_format_non_usdt_pair(self):
        order = {"symbol": "BTCBUSD", "side": "BUY", "qty": 0.001, "entry_price": 10000.0, "stop_loss_stop_price": 9000.0, "take_profit_limit_price": 12000.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("not a supported USDT spot pair" in r for r in res["results"][0]["reasons"]))

    def test_rule1_format_invalid_side(self):
        order = {"symbol": "BTCUSDT", "side": "HOLD", "qty": 0.001, "entry_price": 10000.0, "stop_loss_stop_price": 9000.0, "take_profit_limit_price": 12000.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Invalid side" in r for r in res["results"][0]["reasons"]))

    def test_rule1_format_zero_quantity(self):
        order = {"symbol": "BTCUSDT", "side": "BUY", "qty": 0.0, "entry_price": 10000.0, "stop_loss_stop_price": 9000.0, "take_profit_limit_price": 12000.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Quantity must be greater than zero" in r for r in res["results"][0]["reasons"]))

    def test_rule1_format_zero_entry_price(self):
        order = {"symbol": "BTCUSDT", "side": "BUY", "qty": 0.001, "entry_price": 0.0, "stop_loss_stop_price": 9000.0, "take_profit_limit_price": 12000.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Entry price must be specified" in r for r in res["results"][0]["reasons"]))

    # 2. Minimum Notional
    def test_rule2_min_notional_blocked(self):
        # 0.01 * $200 = $2.00 (< $5.00 min notional)
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.01, "entry_price": 200.0, "stop_loss_stop_price": 190.0, "take_profit_limit_price": 220.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("below Binance minimum" in r for r in res["results"][0]["reasons"]))

    def test_rule2_min_notional_pass_boundary(self):
        # 0.025 * $200 = $5.00 (== $5.00 min notional)
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.025, "entry_price": 200.0, "stop_loss_stop_price": 190.0, "take_profit_limit_price": 220.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "PASS")

    # 3. Position Cap (<= 50%)
    def test_rule3_position_cap_blocked(self):
        # 0.3 * $200 = $60.00 (> $50.00 max position cap)
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.3, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 210.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Position Cap" in r for r in res["results"][0]["reasons"]))

    # 4. Cash Available
    def test_rule4_insufficient_cash(self):
        low_cash_port = {"cash": 10.0, "portfolio_value": 100.0}
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 190.0, "take_profit_limit_price": 220.0}
        # Notional = $20.00, Cash = $10.00
        res = risk_mod.audit_orders([order], self.config, low_cash_port, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Insufficient Funds" in r for r in res["results"][0]["reasons"]))

    def test_rule4_multi_order_cash_depletion(self):
        port = {"cash": 45.0, "portfolio_value": 100.0}
        order1 = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.15, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 210.0}  # $30
        order2 = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.10, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 210.0}  # $20 (30 + 20 = 50 > 45)
        res = risk_mod.audit_orders([order1, order2], self.config, port, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertEqual(res["results"][1]["verdict"], "BLOCK")
        self.assertTrue(any("Insufficient Funds" in r for r in res["results"][1]["reasons"]))

    # 5. Mandatory Stop Loss
    def test_rule5_missing_stop_loss(self):
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": None, "take_profit_limit_price": 220.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Mandatory Stop" in r for r in res["results"][0]["reasons"]))

    def test_rule5_buy_stop_above_entry(self):
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 205.0, "take_profit_limit_price": 220.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("strictly below entry" in r for r in res["results"][0]["reasons"]))

    def test_rule5_sell_stop_below_entry(self):
        order = {"symbol": "SOLUSDT", "side": "SELL", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 180.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("strictly above entry" in r for r in res["results"][0]["reasons"]))

    # 6. Maximum Loss Per Trade (<= $2.50)
    def test_rule6_max_loss_exceeded(self):
        # 0.2 SOL * ($200 - $185) = $3.00 loss risk (> $2.50)
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.2, "entry_price": 200.0, "stop_loss_stop_price": 185.0, "take_profit_limit_price": 230.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Max Loss Per Trade" in r for r in res["results"][0]["reasons"]))

    # 7. Minimum Reward:Risk (>= 1.5 R) & Direction
    def test_rule7_missing_target(self):
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": None}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Missing take profit" in r for r in res["results"][0]["reasons"]))

    def test_rule7_buy_target_below_entry(self):
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 190.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("strictly above entry" in r for r in res["results"][0]["reasons"]))

    def test_rule7_low_rr_ratio(self):
        # Risk = 5, Reward = 5 -> R:R = 1.0 < 1.5
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 205.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Reward/Risk" in r for r in res["results"][0]["reasons"]))

    # 8. Cumulative Daily Loss Limit (<= $7.50)
    def test_rule8_daily_loss_limit_exceeded(self):
        today_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT10:00:00Z")
        history = [
            {"timestamp": today_iso, "max_loss": 2.50},
            {"timestamp": today_iso, "max_loss": 2.50},
            {"timestamp": today_iso, "max_loss": 1.50},  # Total $6.50
        ]
        # Trade adds $1.50 loss risk -> 6.50 + 1.50 = 8.00 (> $7.50 cap)
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.15, "entry_price": 200.0, "stop_loss_stop_price": 190.0, "take_profit_limit_price": 220.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, history, check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Daily Loss Limit" in r for r in res["results"][0]["reasons"]))

    # 9. Maximum Daily Trade Limit (<= 5)
    def test_rule9_daily_trade_cap_exceeded(self):
        today_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT10:00:00Z")
        history = [{"timestamp": today_iso, "max_loss": 0.50} for _ in range(5)]
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.05, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 210.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, history, check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Daily Trade Cap" in r for r in res["results"][0]["reasons"]))

    # 10. Spread & Orderbook Check
    @patch.object(risk_mod, "fetch_binance_spread")
    def test_rule10_spread_too_wide(self, mock_spread):
        mock_spread.return_value = (0.35, 199.5, 200.2)  # 0.35% > 0.20% tolerance
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 210.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=True)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Spread Too Wide" in r for r in res["results"][0]["reasons"]))

    @patch.object(risk_mod, "fetch_binance_spread")
    def test_rule10_spread_within_tolerance(self, mock_spread):
        mock_spread.return_value = (0.05, 199.95, 200.05)
        order = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry_price": 200.0, "stop_loss_stop_price": 195.0, "take_profit_limit_price": 210.0}
        res = risk_mod.audit_orders([order], self.config, self.portfolio, [], check_network=True)
        self.assertEqual(res["overall"], "PASS")

    # 11. Robust Input Formats (dict vs list, alternative keys)
    def test_single_order_dict_input(self):
        order_dict = {"symbol": "SOLUSDT", "side": "BUY", "qty": 0.1, "entry": 200.0, "stop": 195.0, "target": 210.0}
        res = risk_mod.audit_orders(order_dict, self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "PASS")
        self.assertEqual(len(res["results"]), 1)
        self.assertIsNotNone(res["results"][0]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
