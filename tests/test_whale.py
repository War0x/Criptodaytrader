#!/usr/bin/env python3
"""Unit tests for trader-whale (Whale & Funding Rate Tracker).
Tests signal classification, telemetry enrichment, offline mode, and CLI options.
Standard library unittest only.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from importlib import import_module
whale = import_module("trader-whale.whale")


class TestWhaleSignals(unittest.TestCase):
    def test_heavy_long_signal(self):
        """Top trader positions >= 66% and ratio >= 1.9 trigger WHALE_HEAVY_LONG."""
        res = whale.analyze_whale_metrics(
            symbol="BTCUSDT",
            funding_rate=0.000040,
            mark_price=96000.0,
            long_pct=68.5,
            short_pct=31.5,
            ls_ratio=2.17
        )
        self.assertEqual(res["sentiment"], "WHALE_HEAVY_LONG")
        self.assertEqual(res["trade_bias"], "STRONG_LONG")
        self.assertIn("Whales heavily clustered", res["signal"])

    def test_heavy_short_signal(self):
        """Top traders short >= 62% and ratio <= 0.65 trigger WHALE_HEAVY_SHORT."""
        res = whale.analyze_whale_metrics(
            symbol="ETHUSDT",
            funding_rate=0.000010,
            mark_price=3100.0,
            long_pct=35.0,
            short_pct=65.0,
            ls_ratio=0.54
        )
        self.assertEqual(res["sentiment"], "WHALE_HEAVY_SHORT")
        self.assertEqual(res["trade_bias"], "STRONG_SHORT")
        self.assertIn("overhead sell resistance", res["signal"])

    def test_negative_funding_squeeze(self):
        """Negative funding rate (< -0.00001) triggers NEGATIVE_FUNDING_SQUEEZE."""
        res = whale.analyze_whale_metrics(
            symbol="DOGEUSDT",
            funding_rate=-0.000030,
            mark_price=0.38,
            long_pct=52.0,
            short_pct=48.0,
            ls_ratio=1.08
        )
        self.assertEqual(res["sentiment"], "NEGATIVE_FUNDING_SQUEEZE")
        self.assertEqual(res["trade_bias"], "STRONG_LONG")
        self.assertLess(res["annualized_rate_pct"], 0.0)

    def test_overheated_funding(self):
        """Annualized funding >= 25% APR triggers OVERHEATED_LONG_FUNDING."""
        # 0.00025 * 3 * 365 * 100 = 27.375% APR
        res = whale.analyze_whale_metrics(
            symbol="SOLUSDT",
            funding_rate=0.000250,
            mark_price=220.0,
            long_pct=60.0,
            short_pct=40.0,
            ls_ratio=1.50
        )
        self.assertEqual(res["sentiment"], "OVERHEATED_LONG_FUNDING")
        self.assertEqual(res["trade_bias"], "NEUTRAL_TO_SHORT")
        self.assertGreaterEqual(res["annualized_rate_pct"], 25.0)

    def test_moderate_long_bias(self):
        """Long pct >= 55% triggers MODERATE_LONG_BIAS."""
        res = whale.analyze_whale_metrics(
            symbol="AVAXUSDT",
            funding_rate=0.000030,
            mark_price=35.0,
            long_pct=58.0,
            short_pct=42.0,
            ls_ratio=1.38
        )
        self.assertEqual(res["sentiment"], "MODERATE_LONG_BIAS")
        self.assertEqual(res["trade_bias"], "LONG")

    def test_neutral_flow(self):
        """Balanced positions trigger NEUTRAL_FLOW."""
        res = whale.analyze_whale_metrics(
            symbol="BNBUSDT",
            funding_rate=0.000010,
            mark_price=640.0,
            long_pct=51.0,
            short_pct=49.0,
            ls_ratio=1.04
        )
        self.assertEqual(res["sentiment"], "NEUTRAL_FLOW")
        self.assertEqual(res["trade_bias"], "NEUTRAL")


class TestWhaleTelemetryEnrichment(unittest.TestCase):
    def test_enrichment_fields(self):
        """Ensure open interest in USDT and next funding countdown are computed."""
        res = whale.analyze_whale_metrics(
            symbol="BTCUSDT",
            funding_rate=0.000040,
            mark_price=100000.0,
            long_pct=70.0,
            short_pct=30.0,
            ls_ratio=2.33,
            top_account_long_pct=75.0,
            top_account_short_pct=25.0,
            top_account_ls_ratio=3.0,
            open_interest=50000.0,
            next_funding_time_ms=1794614400000
        )
        self.assertEqual(res["top_account_long_pct"], 75.0)
        self.assertEqual(res["top_account_ls_ratio"], 3.0)
        self.assertEqual(res["open_interest"], 50000.0)
        self.assertEqual(res["open_interest_usdt"], 5000000000.0)
        self.assertIn("next_funding_utc", res)
        self.assertIn("funding_countdown_min", res)


class TestWhaleOfflineAndCLI(unittest.TestCase):
    def test_offline_fixture_load(self):
        fixture_file = os.path.join(BASE_DIR, "tests", "fixtures", "whale_data.json")
        self.assertTrue(os.path.exists(fixture_file))

        payload = whale.track_whales(offline_file=fixture_file)
        self.assertIn("metrics", payload)
        self.assertGreaterEqual(len(payload["metrics"]), 9)
        self.assertIn("BTCUSDT", payload["metrics"])
        self.assertIn("SOLUSDT", payload["metrics"])
        self.assertIn("DOGEUSDT", payload["metrics"])

    def test_save_whale_data(self):
        fixture_file = os.path.join(BASE_DIR, "tests", "fixtures", "whale_data.json")
        with open(fixture_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            whale.save_whale_data(data, filepath=tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["tracked_symbols"], data["tracked_symbols"])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_cli_subprocess_offline_json(self):
        fixture_file = os.path.join(BASE_DIR, "tests", "fixtures", "whale_data.json")
        script = os.path.join(BASE_DIR, "trader-whale", "whale.py")
        cmd = [sys.executable, script, "--offline", fixture_file, "--json", "--no-save"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertIn("metrics", payload)
        self.assertEqual(payload["tracked_symbols"], 9)


if __name__ == "__main__":
    unittest.main()