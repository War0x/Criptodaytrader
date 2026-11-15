#!/usr/bin/env python3
"""Tests for trader-entry, trader-exit, and trader-timemachine modules."""

import json
import os
import subprocess
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
ENTRY_PY = os.path.join(BASE_DIR, "trader-entry", "entry.py")
EXIT_PY = os.path.join(BASE_DIR, "trader-exit", "exit.py")
TIMEMACHINE_PY = os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py")


class TestStrategistModules(unittest.TestCase):

    def test_01_entry_execution(self):
        """Test trader-entry generates valid entry.json."""
        cmd = [sys.executable, ENTRY_PY, "check", "--symbol", "DOGEUSDT", "--force", "--write"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"entry.py failed: {result.stderr}")

        entry_path = os.path.join(TRADER_DIR, "entry.json")
        self.assertTrue(os.path.exists(entry_path))

        with open(entry_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("created_by"), "trader-entry")
        self.assertEqual(data.get("symbol"), "DOGEUSDT")
        self.assertEqual(data.get("status"), "triggered")
        self.assertGreater(data.get("price", 0), 0)
        self.assertGreater(data.get("volume", 0), 0)
        self.assertIn("reason", data)

    def test_02_exit_planning(self):
        """Test trader-exit generates valid plan.json with R:R >= 1.5."""
        cmd = [sys.executable, EXIT_PY, "plan", "--write"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"exit.py failed: {result.stderr}")

        plan_path = os.path.join(TRADER_DIR, "plan.json")
        self.assertTrue(os.path.exists(plan_path))

        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)

        self.assertEqual(plan.get("created_by"), "trader-exit")
        self.assertEqual(plan.get("status"), "ready")
        self.assertGreater(plan.get("entry", 0), 0)
        self.assertGreater(plan.get("stop", 0), 0)
        self.assertLess(plan.get("stop"), plan.get("entry"))
        self.assertGreater(plan.get("target_1r"), plan.get("entry"))
        self.assertGreater(plan.get("target_2r"), plan.get("target_1r"))
        self.assertGreaterEqual(plan.get("reward_risk", 0), 1.5)
        self.assertGreaterEqual(plan.get("reward_risk_2r", 0), 2.5)
        self.assertIn("bracket", plan)

    def test_03_timemachine_simulation(self):
        """Test trader-timemachine runs 10,000 paths and computes metrics."""
        cmd = [sys.executable, TIMEMACHINE_PY, "run", "--replays", "1000", "--write"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"timemachine.py failed: {result.stderr}")

        tm_path = os.path.join(TRADER_DIR, "timemachine.json")
        self.assertTrue(os.path.exists(tm_path))

        with open(tm_path, "r", encoding="utf-8") as f:
            tm = json.load(f)

        self.assertEqual(tm.get("created_by"), "trader-timemachine")
        self.assertIn("results", tm)
        res = tm["results"]
        self.assertIn("p_target", res)
        self.assertIn("p_stop", res)
        self.assertIn("win_rate", res)
        self.assertIn("expected_drawdown_pct", res)
        self.assertIn("verdict", res)
        self.assertIn("bands", tm)

    def test_04_full_pipeline_compatibility(self):
        """Test full pipeline: entry -> exit -> timemachine -> size -> risk pass."""
        size_py = os.path.join(BASE_DIR, "trader-budget", "size.py")
        risk_py = os.path.join(BASE_DIR, "trader-risk", "check.py")

        # Run size.py
        res_size = subprocess.run([sys.executable, size_py, "--write"], capture_output=True, text=True)
        self.assertEqual(res_size.returncode, 0, f"size.py failed: {res_size.stderr}")

        # Run risk check.py
        res_risk = subprocess.run([sys.executable, risk_py], capture_output=True, text=True)
        self.assertEqual(res_risk.returncode, 0, f"risk check.py failed: {res_risk.stderr}")
        self.assertIn("[PASS]", res_risk.stdout)


if __name__ == "__main__":
    unittest.main()
