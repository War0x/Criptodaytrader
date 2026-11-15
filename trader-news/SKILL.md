---
name: trader-news
description: Crypto breaking news and market catalyst monitor with automated sentiment analysis and pre-entry adverse event auditing.
---

# Trader-News: Crypto News & Catalyst Monitor

Watches real-time cryptocurrency RSS feeds (Cointelegraph, CoinDesk, Decrypt) and evaluates breaking headlines against watched crypto pairs (BTC, ETH, SOL, DOGE, AVAX, LINK, NEAR, SUI, BNB).

## Usage

```bash
# Monitor news and generate pre-entry adverse risk audits for watchlist
python trader-news/news.py

# Filter for specific tokens
python trader-news/news.py --symbols BTC,ETH,SOL

# Pre-entry audit for a single trade candidate (returns exit code 0 on ALLOW, 1 on BLOCK)
python trader-news/news.py --audit BTCUSDT

# Audit and filter multiple candidates
python trader-news/news.py --filter-candidates BTCUSDT,SOLUSDT,ETHUSDT

# View raw JSON payload
python trader-news/news.py --json

# Offline mode
python trader-news/news.py --offline tests/fixtures/sample_feed.xml
```

## Features
- **Automated Sentiment Classification**: `BULLISH`, `BEARISH`, `NEUTRAL` scoring.
- **Pre-Entry Adverse Event Auditing**: Identifies and blocks entries triggered by:
  - `EXPLOIT_HACK`: Smart contract exploits, bridge drains, 51% attacks, private key theft.
  - `REGULATORY_LEGAL`: SEC/CFTC/DOJ lawsuits, indictments, subpoenas, delistings, bans.
  - `INSOLVENCY_HALT`: Withdrawal suspensions, bankruptcies, liquidity runs, depegging.
- **Context-Aware Token Matching**: Eliminates false positives on common English words (e.g. "link" -> Chainlink, "near" -> Near Protocol).
- **Multi-Feed RSS Aggregation**: Cointelegraph, CoinDesk, Decrypt with automatic fallback.
- **State Persistence**: Saves full article feed, sentiment summary, and candidate clearance audits to `trader/news.json`.
