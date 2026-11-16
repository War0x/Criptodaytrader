#!/usr/bin/env python3
"""
Comprehensive Test Suite for Binance Crypto Day Trader Swarm
Covers:
1. Binance Public API Connectivity (24hr ticker, klines)
2. Watchlist generation and scanner output
3. Entry triggers and Exit bracket calculations (ATR stop, 1.5R TP1, 2.5R TP2)
4. Time Machine 10,000 Monte Carlo bootstrap simulation outputs
5. Position sizing and Binance min_notional calculations ($5.00 min, $50.00 max, crypto precision)
6. Risk manager checks (PASS and BLOCK audits for all constraints)
7. Paper broker execution and portfolio balance updates (BUY fill, cash deduction, flatten PnL)
8. Virtual Office server initialization and HTTP REST API endpoints
"""

import http.client
import importlib
import json
import os
import sys
import threading
import time
import unittest
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Dynamically import hyphenated modules
scan_mod = importlib.import_module("trader-scanner.scan")
news_mod = importlib.import_module("trader-news.news")
whale_mod = importlib.import_module("trader-whale.whale")
analyst_mod = importlib.import_module("trader-analyst.analyst")
entry_mod = importlib.import_module("trader-entry.entry")
exit_mod = importlib.import_module("trader-exit.exit")
tm_mod = importlib.import_module("trader-timemachine.timemachine")
size_mod = importlib.import_module("trader-budget.size")
risk_mod = importlib.import_module("trader-risk.check")
broker_mod = importlib.import_module("trader-broker.broker")
alerts_mod = importlib.import_module("trader-alerts.alerts")
office_mod = importlib.import_module("office_app")


class TestBinancePublicAPI(unittest.TestCase):
    """1. Binance Public REST API Connectivity Tests."""

    def test_binance_24hr_ticker(self):
        """Verify 24hr ticker public endpoint returns valid price and volume data."""
        url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceSwarmVerifier/1.0"})
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data.get("symbol"), "BTCUSDT")
            self.assertIn("lastPrice", data)
            self.assertIn("quoteVolume", data)
            self.assertIn("highPrice", data)
            self.assertIn("lowPrice", data)
            last_price = float(data["lastPrice"])
            self.assertGreater(last_price, 0.0)

    def test_binance_klines(self):
        """Verify klines public endpoint returns OHLCV candlestick data."""
        url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=10"
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceSwarmVerifier/1.0"})
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            self.assertEqual(resp.status, 200)
            klines = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(len(klines), 10)
            first_candle = klines[0]
            # [open_time, open, high, low, close, volume, close_time, quote_asset_volume, trades, ...]
            self.assertGreaterEqual(len(first_candle), 6)
            self.assertGreater(float(first_candle[4]), 0.0)  # close price


class TestScannerAndWatchlist(unittest.TestCase):
    """2. Watchlist generation and scanner output tests."""

    def test_analyze_breakout_scoring(self):
        """Verify scoring logic awards high breakout score to near-high tokens."""
        mock_item = {
            "symbol": "SOLUSDT",
            "lastPrice": "219.5",
            "highPrice": "220.0",
            "lowPrice": "205.0",
            "priceChangePercent": "5.5",
            "quoteVolume": "950000000.0",
        }
        cand = scan_mod.analyze_candidate(mock_item)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["symbol"], "SOLUSDT")
        self.assertEqual(cand["setup"], "BREAKOUT_24H_HIGH")
        self.assertGreaterEqual(cand["score"], 50.0)
        self.assertLessEqual(cand["dist_from_high_pct"], 1.0)

    def test_watchlist_structure(self):
        """Verify scanner generates valid watchlist payload with hits and metadata."""
        res = scan_mod.scan_market(symbols=["BTCUSDT", "ETHUSDT"])
        self.assertIn("hits", res)
        self.assertIn("created_at", res)
        self.assertEqual(res.get("exchange"), "binance")
        self.assertGreater(len(res["hits"]), 0)
        hit = res["hits"][0]
        self.assertIn("symbol", hit)
        self.assertIn("price", hit)
        self.assertIn("score", hit)
        self.assertIn("setup", hit)


class TestWhaleSentimentAndFunding(unittest.TestCase):
    """2b. Whale and funding rate tracking tests."""

    def test_whale_heavy_long_detection(self):
        res = whale_mod.analyze_whale_metrics(
            symbol="BTCUSDT",
            funding_rate=0.000040,
            mark_price=96500.0,
            long_pct=68.0,
            short_pct=32.0,
            ls_ratio=2.12
        )
        self.assertEqual(res["sentiment"], "WHALE_HEAVY_LONG")
        self.assertEqual(res["trade_bias"], "STRONG_LONG")
        self.assertGreater(res["annualized_rate_pct"], 4.0)

    def test_whale_negative_funding_squeeze(self):
        res = whale_mod.analyze_whale_metrics(
            symbol="DOGEUSDT",
            funding_rate=-0.000020,
            mark_price=0.39,
            long_pct=55.0,
            short_pct=45.0,
            ls_ratio=1.22
        )
        self.assertEqual(res["sentiment"], "NEGATIVE_FUNDING_SQUEEZE")
        self.assertEqual(res["trade_bias"], "STRONG_LONG")
        self.assertLess(res["annualized_rate_pct"], 0.0)

    def test_whale_tracking_execution(self):
        fixture_file = os.path.join(BASE_DIR, "tests", "fixtures", "whale_data.json")
        payload = whale_mod.track_whales(offline_file=fixture_file)
        self.assertIn("metrics", payload)
        self.assertGreaterEqual(payload["tracked_symbols"], 9)


class TestEntryAndExitPlanning(unittest.TestCase):
    """3. Entry triggers and Exit bracket calculations."""

    def test_entry_trigger_evaluation(self):
        """Verify entry trigger logic detects valid breakout closure above resistance."""
        result = entry_mod.evaluate_breakout(symbol="DOGEUSDT", interval="5m", force_trigger=True)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["symbol"], "DOGEUSDT")
        self.assertGreater(result["price"], 0.0)
        self.assertGreater(result["resistance_5m"], 0.0)
        self.assertIn("reason", result)

    def test_exit_bracket_math(self):
        """Verify exit planner generates stop, TP1 (>=1.5R), TP2 (>=2.5R) and OCO bracket."""
        plan = exit_mod.plan_exit(symbol="SOLUSDT", entry_price=200.0, stop_mode="auto")
        self.assertEqual(plan["symbol"], "SOLUSDT")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["entry"], 200.0)
        self.assertLess(plan["stop"], plan["entry"])
        self.assertGreater(plan["target_1r"], plan["entry"])
        self.assertGreater(plan["target_2r"], plan["target_1r"])

        # Mathematical R:R verification
        r_risk = plan["reward_risk"]
        r_risk_2r = plan["reward_risk_2r"]
        self.assertGreaterEqual(r_risk, 1.50)
        self.assertGreaterEqual(r_risk_2r, 2.50)
        self.assertIn("bracket", plan)
        self.assertEqual(plan["bracket"]["order_type"], "BRACKET_OCO")


class TestTimeMachineSimulation(unittest.TestCase):
    """4. Time Machine 10,000 simulation outputs."""

    def test_monte_carlo_bootstrap_engine(self):
        """Verify Monte Carlo bootstrap generates win rates, drawdowns, and quantile bands."""
        res_doc = tm_mod.run_monte_carlo(
            symbol="SOLUSDT",
            entry=200.0,
            stop=195.0,
            target=208.0,
            replays=1000,  # Fast run for unit test
            horizon_bars=24,
            seed=42,
        )
        self.assertIn("results", res_doc)
        self.assertIn("bands", res_doc)
        results = res_doc["results"]
        bands = res_doc["bands"]
        self.assertIn("p_target", results)
        self.assertIn("p_stop", results)
        self.assertIn("win_rate", results)
        self.assertIn("expected_drawdown_pct", results)
        self.assertIn("expected_value_r", results)
        self.assertIn("verdict", results)
        self.assertTrue(len(bands) > 0)
        total_p = results["p_target"] + results["p_stop"] + results.get("p_neither", 0.0)
        self.assertAlmostEqual(total_p, 1.0, places=2)


class TestPositionSizingAndMinNotional(unittest.TestCase):
    """5. Position sizing and Binance min_notional calculations."""

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
            "max_position_pct": 50,
            "min_notional_usdt": 5.0,
            "slippage_tolerance_pct": 0.2,
        }
        self.portfolio = {
            "cash": 100.0,
            "portfolio_value": 100.0,
            "currency": "USDT",
            "positions": [],
        }

    def test_position_sizing_position_cap(self):
        """Verify position sizing does not exceed 50% ($50) position cap."""
        plan = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry": 70000.0,
            "stop": 69950.0,
            "target_1r": 70100.0,
            "reward_risk": 2.0,
        }
        result = size_mod.size_trade(plan, self.config, self.portfolio)
        self.assertIsNone(result.get("refusal"))
        order = result["order"]
        self.assertLessEqual(order["notional"], 50.0)
        self.assertGreaterEqual(order["notional"], 5.0)

    def test_position_sizing_max_loss_constraint(self):
        """Verify position sizing respects $2.50 maximum loss limit."""
        plan = {
            "symbol": "SOLUSDT",
            "side": "BUY",
            "entry": 200.0,
            "stop": 190.0,  # Risk $10/coin (5%). Max loss $2.50 -> max 0.25 SOL ($50 notional)
            "target_1r": 215.0,
            "reward_risk": 1.5,
        }
        result = size_mod.size_trade(plan, self.config, self.portfolio)
        self.assertIsNone(result.get("refusal"))
        order = result["order"]
        self.assertLessEqual(order["max_loss"], 2.51)

    def test_position_sizing_min_notional_rejection(self):
        """Verify order below $5.00 min notional is refused."""
        plan = {
            "symbol": "ETHUSDT",
            "side": "BUY",
            "entry": 3000.0,
            "stop": 1000.0,  # Risk $2000/coin. Max loss $2.50 -> 0.00125 ETH = $3.75 (< $5.00 min notional)
            "target_1r": 6000.0,
            "reward_risk": 1.5,
        }
        result = size_mod.size_trade(plan, self.config, self.portfolio)
        self.assertIsNotNone(result.get("refusal"))
        self.assertIn("below Binance minimum requirement", result["refusal"])

    def test_precision_rounding_subdollar_crypto(self):
        """Verify sub-dollar pairs like DOGE retain sufficient precision without R:R collapse."""
        plan = {
            "symbol": "DOGEUSDT",
            "side": "BUY",
            "entry": 0.06547,
            "stop": 0.06530,
            "target_1r": 0.06573,
            "reward_risk": 1.53,
        }
        result = size_mod.size_trade(plan, self.config, self.portfolio)
        self.assertIsNone(result.get("refusal"))
        order = result["order"]
        entry = order["entry_price"]
        stop = order["stop_loss_stop_price"]
        target = order["take_profit_limit_price"]
        calc_rr = round(abs(target - entry) / abs(entry - stop), 2)
        # Sizing must preserve at least 1.50 R:R for risk auditor
        self.assertGreaterEqual(calc_rr, 1.50)

    def test_explicit_step_size_and_lot_filtering(self):
        """Verify custom stepSize is respected in lot sizing calculations."""
        plan = {
            "symbol": "SOLUSDT",
            "side": "BUY",
            "entry": 200.0,
            "stop": 195.0,
            "target_1r": 210.0,
            "reward_risk": 2.0,
            "step_size": 0.05,
        }
        result = size_mod.size_trade(plan, self.config, self.portfolio, step_size=0.05)
        self.assertIsNone(result.get("refusal"))
        order = result["order"]
        self.assertEqual(order["step_size"], 0.05)
        self.assertTrue(round(order["qty_float"] / 0.05, 5).is_integer())
        self.assertLessEqual(order["notional"], 50.0)
        self.assertGreaterEqual(order["notional"], 5.0)
        self.assertLessEqual(order["max_loss"], 2.50)


class TestRiskManagerAudits(unittest.TestCase):
    """6. Risk manager checks (PASS and BLOCK verification)."""

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
            "max_position_pct": 50,
            "min_notional_usdt": 5.0,
            "slippage_tolerance_pct": 0.2,
        }
        self.portfolio = {
            "cash": 100.0,
            "portfolio_value": 100.0,
            "currency": "USDT",
            "positions": [],
        }

    def test_risk_pass_clean_order(self):
        """Verify compliant order receives PASS verdict."""
        orders = [{
            "symbol": "SOLUSDT",
            "side": "BUY",
            "qty": "0.24",
            "qty_float": 0.24,
            "notional": 48.0,
            "entry_price": 200.0,
            "stop_loss_stop_price": 195.0,
            "take_profit_limit_price": 208.0,  # 8 / 5 = 1.6 R
            "risk_per_unit": 5.0,
            "reward_risk": 1.6,
            "max_loss": 1.20,
        }]
        res = risk_mod.audit_orders(orders, self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "PASS")
        self.assertEqual(res["pass_count"], 1)
        self.assertEqual(res["block_count"], 0)

    def test_risk_block_missing_stop(self):
        """Verify order without stop loss is blocked."""
        orders = [{
            "symbol": "SOLUSDT",
            "side": "BUY",
            "qty": "0.24",
            "notional": 48.0,
            "entry_price": 200.0,
            "stop_loss_stop_price": None,
            "take_profit_limit_price": 210.0,
            "max_loss": 1.20,
        }]
        res = risk_mod.audit_orders(orders, self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertIn("Mandatory Stop", res["results"][0]["reasons"][0])

    def test_risk_block_reward_risk_too_low(self):
        """Verify order with R:R < 1.50 is blocked."""
        orders = [{
            "symbol": "SOLUSDT",
            "side": "BUY",
            "qty": "0.24",
            "notional": 48.0,
            "entry_price": 200.0,
            "stop_loss_stop_price": 190.0,  # Risk $10
            "take_profit_limit_price": 210.0,  # Reward $10 -> 1.0 R < 1.5 R
            "max_loss": 2.40,
        }]
        res = risk_mod.audit_orders(orders, self.config, self.portfolio, [], check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Reward/Risk" in r for r in res["results"][0]["reasons"]))

    def test_risk_block_exceed_daily_trades(self):
        """Verify 6th trade of the day is blocked (max 5)."""
        today_utc = time.strftime("%Y-%m-%d", time.gmtime())
        history = [
            {"timestamp": f"{today_utc}T10:00:00Z", "max_loss": 0.5}
            for _ in range(5)
        ]
        orders = [{
            "symbol": "SOLUSDT",
            "side": "BUY",
            "qty": "0.1",
            "notional": 20.0,
            "entry_price": 200.0,
            "stop_loss_stop_price": 195.0,
            "take_profit_limit_price": 210.0,
            "max_loss": 0.5,
            "reward_risk": 2.0,
        }]
        res = risk_mod.audit_orders(orders, self.config, self.portfolio, history, check_network=False)
        self.assertEqual(res["overall"], "BLOCK")
        self.assertTrue(any("Daily Trade Cap" in r for r in res["results"][0]["reasons"]))


class TestBrokerExecutionAndPortfolio(unittest.TestCase):
    """7. Paper broker execution and portfolio balance updates."""

    def test_paper_execution_and_flatten(self):
        """Verify paper order fills instantly, updates cash & positions, and flattens correctly."""
        initial_portfolio = {
            "cash": 100.0,
            "portfolio_value": 100.0,
            "currency": "USDT",
            "positions": [],
        }
        # Use synthetic test pair so live API doesn't alter expected unit test prices
        order = {
            "symbol": "SYNTHUSDT",
            "side": "BUY",
            "qty": 0.25,
            "entry_price": 200.0,
            "stop_loss_stop_price": 195.0,
            "take_profit_limit_price": 210.0,
            "why": "Unit test paper execution",
        }

        # 1. Execute BUY
        updated_port, trade_record = broker_mod.execute_paper_order(order, initial_portfolio, live_price=200.0)
        self.assertEqual(trade_record["status"], "FILLED")
        self.assertEqual(updated_port["cash"], 50.0)  # $100 - (0.25 * 200 = $50)
        self.assertEqual(len(updated_port["positions"]), 1)
        self.assertEqual(updated_port["positions"][0]["symbol"], "SYNTHUSDT")
        self.assertEqual(updated_port["positions"][0]["qty"], 0.25)
        self.assertEqual(updated_port["portfolio_value"], 100.0)

        # 2. Update current_price for profit simulation ($208.0)
        updated_port["positions"][0]["current_price"] = 208.0

        # 3. Flatten position
        flattened_port, flat_record = broker_mod.flatten_position("SYNTHUSDT", updated_port, [], dry_run=False)
        self.assertEqual(flat_record["status"], "FILLED")
        self.assertEqual(len(flattened_port["positions"]), 0)
        # Cash = $50 + (0.25 * 208 = $52) = $102
        self.assertEqual(flattened_port["cash"], 102.0)
        self.assertEqual(flattened_port["portfolio_value"], 102.0)
        self.assertEqual(flat_record["pnl"], 2.0)

    def test_hmac_sha256_signing(self):
        """Verify HMAC-SHA256 signature generator for signed Binance REST requests."""
        test_params = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": "0.001",
            "price": "65000",
            "timestamp": "1600000000000",
            "recvWindow": "5000",
        }
        test_secret = "test_binance_secret_key"
        query_str, signature = broker_mod.sign_binance_request(test_params, test_secret)
        
        # Verify query string format and non-empty SHA256 hex signature (64 chars)
        self.assertIn("symbol=BTCUSDT", query_str)
        self.assertIn("side=BUY", query_str)
        self.assertEqual(len(signature), 64)
        
        # Verify deterministic match
        import hmac, hashlib
        expected_sig = hmac.new(test_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected_sig)


class TestVirtualOfficeServer(unittest.TestCase):
    """8. Virtual Office server initialization and HTTP REST APIs."""

    @classmethod
    def setUpClass(cls):
        """Start a test HTTP server on port 8779 in a background thread."""
        cls.port = 8779
        cls.server = office_mod.HTTPServer(("127.0.0.1", cls.port), office_mod.BinanceOfficeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        """Shutdown test HTTP server."""
        cls.server.shutdown()
        cls.server.server_close()

    def test_office_root_html(self):
        """Verify HTTP 200 on / and delivery of Binance themed HTML."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        html_body = resp.read().decode("utf-8")
        self.assertIn("BINANCE CRYPTO DAY TRADER", html_body)
        self.assertIn("#0b0e14", html_body)
        conn.close()

    def test_office_api_status(self):
        """Verify HTTP 200 on /api/status returning swarm state."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data.get("exchange"), "binance")
        self.assertIn("budget", data)
        self.assertIn("cash", data)
        self.assertIn("connected", data)
        self.assertIn("tickers", data)
        conn.close()

    def test_office_api_tickers(self):
        """Verify HTTP 200 on /api/tickers returning live ticker list."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/tickers")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertIn("tickers", data)
        conn.close()


if __name__ == "__main__":
    unittest.main()
