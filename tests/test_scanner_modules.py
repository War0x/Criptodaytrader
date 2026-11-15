#!/usr/bin/env python3
"""
Unit and integration tests for scanner, news, whale, and analyst modules.
Standard library unittest.
"""

import os
import sys
import unittest
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from importlib import import_module

scan = import_module("trader-scanner.scan")
news = import_module("trader-news.news")
whale = import_module("trader-whale.whale")
analyst = import_module("trader-analyst.analyst")


class TestScanner(unittest.TestCase):
    def test_analyze_breakout_candidate(self):
        item = {
            "symbol": "SOLUSDT",
            "lastPrice": "218.0",
            "priceChangePercent": "6.5",
            "highPrice": "220.0",
            "lowPrice": "200.0",
            "quoteVolume": "850000000.0"
        }
        res = scan.analyze_candidate(item)
        self.assertIsNotNone(res)
        self.assertEqual(res["symbol"], "SOLUSDT")
        self.assertGreater(res["score"], 60.0)
        self.assertEqual(res["setup"], "BREAKOUT_24H_HIGH")
        self.assertLessEqual(res["dist_from_high_pct"], 1.5)

    def test_range_compression_candidate(self):
        item = {
            "symbol": "BTCUSDT",
            "lastPrice": "99500.0",
            "priceChangePercent": "0.8",
            "highPrice": "100000.0",
            "lowPrice": "97500.0",
            "quoteVolume": "1500000000.0"
        }
        res = scan.analyze_candidate(item)
        self.assertIsNotNone(res)
        self.assertIn(res["setup"], ["RANGE_COMPRESSION", "BREAKOUT_24H_HIGH", "RANGE_BOUND"])

    def test_scan_market_execution(self):
        res = scan.scan_market(symbols=["BTCUSDT", "ETHUSDT"])
        self.assertIn("hits", res)
        self.assertGreater(len(res["hits"]), 0)


class TestNews(unittest.TestCase):
    def test_sentiment_classification(self):
        bull_text = "Bitcoin rallies to record surge with massive ETF inflows approved"
        sentiment, score = news.classify_text_sentiment(bull_text)
        self.assertEqual(sentiment, "BULLISH")
        self.assertGreater(score, 0)

        bear_text = "Crypto plunge as exchange hacked and sec sues founder for fraud"
        sentiment, score = news.classify_text_sentiment(bear_text)
        self.assertEqual(sentiment, "BEARISH")
        self.assertLess(score, 0)

    def test_symbol_matching(self):
        text = "Solana ecosystem expands while Dogecoin and Ether experience high activity"
        symbols = news.match_symbols(text)
        self.assertIn("SOL", symbols)
        self.assertIn("DOGE", symbols)
        self.assertIn("ETH", symbols)

    def test_no_false_positive_link_and_near(self):
        text = "Analysts see no direct link between Nvidia and bitcoin trading near support"
        symbols = news.match_symbols(text)
        self.assertIn("BTC", symbols)
        self.assertNotIn("LINK", symbols)
        self.assertNotIn("NEAR", symbols)

    def test_detect_adverse_events(self):
        exploit_text = "Major Solana DeFi bridge hacked and drained of 50M in exploit"
        flags = news.detect_adverse_events(exploit_text)
        self.assertTrue(any(f["category"] == "EXPLOIT_HACK" for f in flags))

        sec_text = "SEC charges founder with fraud and issues subpoena"
        flags = news.detect_adverse_events(sec_text)
        self.assertTrue(any(f["category"] == "REGULATORY_LEGAL" for f in flags))

        halt_text = "Exchange halts withdrawals and files for chapter 11 bankruptcy"
        flags = news.detect_adverse_events(halt_text)
        self.assertTrue(any(f["category"] == "INSOLVENCY_HALT" for f in flags))

    def test_audit_candidate_blocks_exploit(self):
        mock_articles = [
            {
                "title": "Solana lending protocol exploited in reentrancy attack",
                "source": "CoinDesk",
                "symbols": ["SOL"],
                "score": -2,
                "adverse_flags": [{"category": "EXPLOIT_HACK", "severity": "CRITICAL"}]
            }
        ]
        audit = news.audit_candidate_news("SOLUSDT", articles=mock_articles)
        self.assertFalse(audit["allow_entry"])
        self.assertEqual(audit["action"], "BLOCK_ENTRY")
        self.assertEqual(audit["risk_level"], "CRITICAL")
        self.assertIn("EXPLOIT_HACK", audit["reason"])

    def test_audit_candidate_allows_clean(self):
        mock_articles = [
            {
                "title": "Ethereum staking rewards surge as network activity expands",
                "source": "Cointelegraph",
                "symbols": ["ETH"],
                "score": 3,
                "adverse_flags": []
            }
        ]
        audit = news.audit_candidate_news("ETHUSDT", articles=mock_articles)
        self.assertTrue(audit["allow_entry"])
        self.assertEqual(audit["action"], "ALLOW_ENTRY")
        self.assertEqual(audit["risk_level"], "LOW")
        self.assertEqual(audit["sentiment"], "BULLISH")



class TestWhale(unittest.TestCase):
    def test_whale_metrics_heavy_long(self):
        res = whale.analyze_whale_metrics(
            symbol="BTCUSDT",
            funding_rate=0.000045,
            mark_price=96000.0,
            long_pct=72.0,
            short_pct=28.0,
            ls_ratio=2.57
        )
        self.assertEqual(res["sentiment"], "WHALE_HEAVY_LONG")
        self.assertGreater(res["annualized_rate_pct"], 4.0)

    def test_whale_metrics_negative_funding_squeeze(self):
        res = whale.analyze_whale_metrics(
            symbol="DOGEUSDT",
            funding_rate=-0.000025,
            mark_price=0.38,
            long_pct=52.0,
            short_pct=48.0,
            ls_ratio=1.08
        )
        self.assertEqual(res["sentiment"], "NEGATIVE_FUNDING_SQUEEZE")
        self.assertLess(res["annualized_rate_pct"], 0.0)


class TestAnalyst(unittest.TestCase):
    def test_depth_metrics_and_slippage(self):
        mock_depth = {
            "bids": [["100.0", "10"], ["99.9", "20"]],
            "asks": [["100.01", "10"], ["100.02", "20"]]
        }
        res = analyst.calculate_depth_metrics("TESTUSDT", mock_depth, order_size_usdt=50.0)
        self.assertIsNotNone(res)
        self.assertEqual(res["best_bid"], 100.0)
        self.assertEqual(res["best_ask"], 100.01)
        self.assertLess(res["spread_pct"], 0.02)
        self.assertTrue(res["slippage_pass"])

    def test_buyer_seller_volume_balance_and_walls(self):
        mock_depth = {
            "bids": [["100.0", "5"], ["99.0", "50"]],   # total bid: 500 + 4950 = 5450
            "asks": [["100.1", "10"], ["101.0", "15"]]   # total ask: 1001 + 1515 = 2516
        }
        res = analyst.calculate_depth_metrics("TESTUSDT", mock_depth, order_size_usdt=50.0)
        self.assertGreater(res["buyer_volume_pct"], 65.0)
        self.assertLess(res["seller_volume_pct"], 35.0)
        self.assertIn("buyers", res["buyer_seller_balance"])
        self.assertEqual(res["largest_bid_wall"]["price"], 99.0)
        self.assertEqual(res["largest_ask_wall"]["price"], 101.0)
        self.assertEqual(res["imbalance_verdict"], "BUYER_DOMINANCE")

    def test_slippage_exceeded(self):
        # Shallow ask book: only $10 notional at best ask, next is 10% higher
        mock_depth = {
            "bids": [["100.0", "10"]],
            "asks": [["100.0", "0.1"], ["110.0", "5.0"]]
        }
        res = analyst.calculate_depth_metrics("TESTUSDT", mock_depth, order_size_usdt=50.0, max_slippage_pct=0.20)
        self.assertFalse(res["slippage_pass"])
        self.assertGreater(res["estimated_slippage_pct"], 1.0)

    def test_market_structure_evaluation(self):
        mock_depth = {
            "bids": [["100.0", "500"]],
            "asks": [["100.01", "500"]]
        }
        dm = analyst.calculate_depth_metrics("TESTUSDT", mock_depth, order_size_usdt=50.0)
        ticker = {
            "lastPrice": "100.0",
            "priceChangePercent": "4.5",
            "highPrice": "101.0",
            "lowPrice": "94.0"
        }
        analysis = analyst.evaluate_market_structure("TESTUSDT", dm, ticker)
        self.assertEqual(analysis["structure"], "BULLISH_UPTREND")
        self.assertEqual(analysis["execution_verdict"], "CLEARED_FOR_EXECUTION")


if __name__ == "__main__":
    unittest.main()
