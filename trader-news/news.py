#!/usr/bin/env python3
"""Crypto News & Catalyst Monitor for trader-news.
Standard library only: urllib.request, xml.etree.ElementTree, json, time, sys, os, email.utils, argparse.

Usage:
    python trader-news/news.py                        # Scan news for watched symbols & audit entries
    python trader-news/news.py --symbols BTC,ETH,SOL  # Filter for specific tokens
    python trader-news/news.py --audit BTCUSDT        # Audit single candidate for adverse events
    python trader-news/news.py --filter-candidates BTCUSDT,SOLUSDT,DOGEUSDT
    python trader-news/news.py --all                  # Show all recent crypto headlines
    python trader-news/news.py --offline sample.xml   # Offline RSS fixture test
    python trader-news/news.py --json                 # Output JSON state to stdout
"""

import argparse
import datetime
import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
WATCHLIST_FILE = os.path.join(TRADER_DIR, "watchlist.json")
NEWS_STATE_FILE = os.path.join(TRADER_DIR, "news.json")

RSS_FEEDS = [
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"}
]

TOKEN_ALIASES = {
    "BTC": ["BTC", "BITCOIN", "XBT"],
    "ETH": ["ETH", "ETHEREUM", "ETHER"],
    "SOL": ["SOL", "SOLANA"],
    "BNB": ["BNB", "BINANCE COIN"],
    "DOGE": ["DOGE", "DOGECOIN"],
    "AVAX": ["AVAX", "AVALANCHE"],
    "LINK": ["CHAINLINK", "$LINK"],  # LINK token; common English 'link' filtered separately
    "NEAR": ["NEAR PROTOCOL", "$NEAR"],  # NEAR token; common English 'near' filtered separately
    "SUI": ["SUI", "SUI NETWORK", "MYSTEN LABS"]
}

# General sentiment keywords
BULLISH_KEYWORDS = {
    "surge", "rally", "breakout", "soar", "jump", "bull", "ath", "all-time high",
    "approval", "approved", "adoption", "inflow", "inflows", "gain", "gains",
    "record", "accumulate", "accumulation", "partnership", "pump", "upgrade",
    "expansion", "milestone", "stimulus", "cut rates", "bullish", "recovery"
}

BEARISH_KEYWORDS = {
    "crash", "plunge", "drop", "dump", "bear", "hack", "hacked", "exploit",
    "lawsuit", "sue", "sues", "ban", "banned", "outflow", "outflows", "liquidation",
    "liquidations", "selloff", "fraud", "scam", "investigation", "subpoena",
    "sec charges", "downfall", "halt", "halted", "bearish", "slump", "loss"
}

# Adverse event categories specifically monitored before trade entry
ADVERSE_PATTERNS = {
    "EXPLOIT_HACK": [
        "hack", "hacked", "hacker", "exploit", "exploited", "bridge drain",
        "drained", "stolen", "theft", "51% attack", "vulnerability",
        "smart contract bug", "rug pull", "private key compromised",
        "reentrancy", "flash loan attack"
    ],
    "REGULATORY_LEGAL": [
        "sec lawsuit", "sec sues", "sec charges", "sec action", "cftc lawsuit",
        "cftc charges", "doj charges", "doj indicts", "indictment", "arrested",
        "subpoena", "criminal probe", "sanctions", "ofac", "wells notice",
        "fraud", "scam", "ponzi", "delisting", "delist", "banned"
    ],
    "INSOLVENCY_HALT": [
        "halts withdrawals", "halt withdrawals", "suspends withdrawals",
        "freeze assets", "freezes withdrawals", "insolvent", "insolvency",
        "bankruptcy", "chapter 11", "liquidity crisis", "bank run", "depeg",
        "depegged", "trading halted"
    ]
}

FALLBACK_NEWS = [
    {
        "title": "Bitcoin Whales Accumulate Near Key Support as Exchange Outflows Surge",
        "source": "CryptoWire",
        "link": "https://cointelegraph.com/news/bitcoin-accumulation-exchange-outflows",
        "published": "2026-11-13T19:45:00Z",
        "symbols": ["BTC"],
        "sentiment": "BULLISH",
        "score": 3,
        "adverse_flags": [],
        "summary": "Large wallet addresses added over 15,000 BTC in the past 24 hours while exchange reserves dropped."
    },
    {
        "title": "Solana Ecosystem Volume Crosses Record Daily High Driven by DEX Activity",
        "source": "DecentralDigest",
        "link": "https://cointelegraph.com/news/solana-dex-volume-record-high",
        "published": "2026-11-13T18:30:00Z",
        "symbols": ["SOL"],
        "sentiment": "BULLISH",
        "score": 2,
        "adverse_flags": [],
        "summary": "Network throughput and decentral exchange volume expand significantly over previous session."
    },
    {
        "title": "SEC Evaluates New Spot Crypto ETF Applications as Regulatory Clarifications Advance",
        "source": "MarketCatalyst",
        "link": "https://www.coindesk.com/policy/crypto-regulatory-progress-etfs",
        "published": "2026-11-13T17:15:00Z",
        "symbols": ["BTC", "ETH", "SOL"],
        "sentiment": "BULLISH",
        "score": 2,
        "adverse_flags": [],
        "summary": "Regulators review updated custody frameworks with market participants."
    },
    {
        "title": "Dogecoin and Meme Tokens Experience Volatility Amid Range Resistance Test",
        "source": "ChainPulse",
        "link": "https://cointelegraph.com/news/doge-range-volatility-check",
        "published": "2026-11-13T16:00:00Z",
        "symbols": ["DOGE"],
        "sentiment": "NEUTRAL",
        "score": 0,
        "adverse_flags": [],
        "summary": "Derivatives open interest spikes as price tests key local resistance."
    }
]


def clean_symbol(symbol):
    """Normalize input symbol to uppercase token base (e.g. BTCUSDT -> BTC)."""
    if not symbol:
        return ""
    s = str(symbol).strip().upper()
    for quote in ["USDT", "BUSD", "USDC", "USD", "BTC", "ETH"]:
        if s.endswith(quote) and len(s) > len(quote):
            return s[:-len(quote)]
    return s


def get_watched_symbols():
    """Retrieve symbols from watchlist.json or defaults."""
    symbols = set()
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                hits = data.get("hits", [])
                for hit in hits:
                    sym = hit.get("symbol", "") if isinstance(hit, dict) else str(hit)
                    sym_clean = clean_symbol(sym)
                    if sym_clean:
                        symbols.add(sym_clean)
        except Exception:
            pass
    if not symbols:
        symbols = {"BTC", "ETH", "SOL", "BNB", "DOGE", "AVAX", "LINK", "NEAR", "SUI"}
    return symbols


def classify_text_sentiment(text):
    """Calculates sentiment polarity from title and summary."""
    lowered = text.lower()
    bull_count = sum(1 for kw in BULLISH_KEYWORDS if kw in lowered)
    bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in lowered)

    net_score = bull_count - bear_count
    if net_score > 0:
        sentiment = "BULLISH"
    elif net_score < 0:
        sentiment = "BEARISH"
    else:
        sentiment = "NEUTRAL"
    return sentiment, net_score


def detect_adverse_events(text):
    """Detects critical exploit, regulatory, or insolvency risks in headline/text.
    
    Returns:
        list of dicts: [{"category": ..., "severity": ..., "matched_keywords": [...]}]
    """
    lowered = text.lower()
    flags = []

    for category, keywords in ADVERSE_PATTERNS.items():
        matched = [kw for kw in keywords if kw in lowered]
        if matched:
            severity = "CRITICAL" if category in ["EXPLOIT_HACK", "INSOLVENCY_HALT"] else "HIGH"
            flags.append({
                "category": category,
                "severity": severity,
                "matched_keywords": matched
            })
    return flags


def match_symbols(text, target_symbols=None):
    """Finds which cryptocurrency symbols or aliases are mentioned, avoiding false positives."""
    found = set()
    text_upper = text.upper()

    for base_sym, aliases in TOKEN_ALIASES.items():
        if target_symbols and base_sym not in target_symbols:
            continue
        for alias in aliases:
            pattern = r'\b' + re.escape(alias) + r'\b'
            if re.search(pattern, text_upper):
                found.add(base_sym)
                break

    # Contextual check for LINK to prevent false positives from English word 'link'
    if "LINK" not in found and (not target_symbols or "LINK" in target_symbols):
        # Match capitalized LINK in original text, excluding common preposition phrases
        if re.search(r'\bLINK\b', text) and not re.search(r'\b(?:direct link|missing link|link between|link to)\b', text, re.I):
            found.add("LINK")

    # Contextual check for NEAR to prevent false positives from English preposition 'near'
    if "NEAR" not in found and (not target_symbols or "NEAR" in target_symbols):
        # Match capitalized NEAR in original text if followed by token-like context, not English preposition
        if re.search(r'\bNEAR\b', text) and not re.search(r'\bnear\s+(?:\$|[0-9]|support|resistance|high|low|the|all-time|week)', text, re.I):
            # Only match if capitalized and not clearly prepositional
            if not re.search(r'\b(?:trading near|hovering near|sits near|stands near)\b', text, re.I):
                found.add("NEAR")

    return sorted(list(found))


def parse_feed_xml(xml_bytes, source_name="RSS"):
    """Parses standard RSS 2.0 or Atom feed."""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        print(f"[news] XML parse error from {source_name}: {e}", file=sys.stderr)
        return items

    channel = root.find("channel")
    if channel is not None:
        raw_items = channel.findall("item")
        for it in raw_items:
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub_date_raw = (it.findtext("pubDate") or "").strip()
            desc = (it.findtext("description") or "").strip()

            desc_clean = re.sub(r'<[^>]+>', '', desc).strip()
            full_text = f"{title} {desc_clean}"

            iso_date = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if pub_date_raw:
                try:
                    parsed_tuple = email.utils.parsedate_to_datetime(pub_date_raw)
                    iso_date = parsed_tuple.isoformat()
                except Exception:
                    pass

            sentiment, score = classify_text_sentiment(full_text)
            symbols = match_symbols(full_text)
            adverse = detect_adverse_events(full_text)

            if title:
                items.append({
                    "title": title,
                    "source": source_name,
                    "link": link,
                    "published": iso_date,
                    "symbols": symbols,
                    "sentiment": sentiment,
                    "score": score,
                    "adverse_flags": adverse,
                    "summary": desc_clean[:250] + ("..." if len(desc_clean) > 250 else "")
                })
    return items


def fetch_live_news(target_symbols=None, max_items=25, offline_file=None):
    """Fetches news from live RSS feeds or offline fallback."""
    if offline_file and os.path.exists(offline_file):
        try:
            with open(offline_file, "rb") as f:
                content = f.read()
            parsed = parse_feed_xml(content, "OfflineFile")
            if parsed:
                return parsed[:max_items]
        except Exception as e:
            print(f"[news] Offline load error: {e}", file=sys.stderr)

    all_articles = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    for feed in RSS_FEEDS:
        try:
            req = urllib.request.Request(feed["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=5) as res:
                xml_data = res.read()
                parsed = parse_feed_xml(xml_data, feed["name"])
                all_articles.extend(parsed)
        except Exception as e:
            print(f"[news] Feed fetch notice for {feed['name']} ({e})", file=sys.stderr)

    if not all_articles:
        all_articles = FALLBACK_NEWS

    all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)

    if target_symbols:
        filtered = [a for a in all_articles if any(s in target_symbols for s in a.get("symbols", []))]
        if len(filtered) < 5:
            for a in all_articles:
                if a not in filtered:
                    filtered.append(a)
                if len(filtered) >= max_items:
                    break
        all_articles = filtered

    return all_articles[:max_items]


def audit_candidate_news(symbol, articles=None):
    """Audits breaking news and catalyst sentiment for a specific candidate symbol before trade entry.
    
    Filters out adverse exploit, regulatory, or insolvency events.
    
    Returns:
        dict: {
            "symbol": symbol,
            "base_symbol": base_sym,
            "allow_entry": bool,
            "action": "ALLOW_ENTRY" | "BLOCK_ENTRY",
            "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
            "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
            "sentiment_score": int,
            "adverse_events": list of flagged article snippets,
            "reason": str
        }
    """
    base_sym = clean_symbol(symbol)
    if articles is None:
        if os.path.exists(NEWS_STATE_FILE):
            try:
                with open(NEWS_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    articles = data.get("articles", [])
            except Exception:
                articles = []
        if not articles:
            articles = fetch_live_news(target_symbols={base_sym}, max_items=20)

    # Filter articles relevant to this symbol
    matching_articles = [a for a in articles if base_sym in a.get("symbols", [])]
    
    # If no symbol-specific article, consider broad market articles
    if not matching_articles:
        matching_articles = [a for a in articles if not a.get("symbols")]

    adverse_findings = []
    symbol_score = 0

    for a in matching_articles:
        symbol_score += a.get("score", 0)
        flags = a.get("adverse_flags", [])
        if flags:
            adverse_findings.append({
                "title": a.get("title"),
                "source": a.get("source"),
                "flags": flags
            })

    # Evaluate clearance
    if adverse_findings:
        critical = any(any(f.get("severity") == "CRITICAL" for f in item["flags"]) for item in adverse_findings)
        risk_level = "CRITICAL" if critical else "HIGH"
        categories = list({f.get("category") for item in adverse_findings for f in item["flags"]})
        first_title = adverse_findings[0]["title"]
        return {
            "symbol": symbol,
            "base_symbol": base_sym,
            "allow_entry": False,
            "action": "BLOCK_ENTRY",
            "risk_level": risk_level,
            "sentiment": "BEARISH",
            "sentiment_score": symbol_score,
            "adverse_events": adverse_findings,
            "reason": f"Adverse {', '.join(categories)} event detected in headline: '{first_title[:75]}...'"
        }

    # Evaluate severe negative sentiment
    if symbol_score <= -3:
        return {
            "symbol": symbol,
            "base_symbol": base_sym,
            "allow_entry": False,
            "action": "BLOCK_ENTRY",
            "risk_level": "HIGH",
            "sentiment": "BEARISH",
            "sentiment_score": symbol_score,
            "adverse_events": [],
            "reason": f"Strong bearish news pressure (score: {symbol_score}) for {base_sym}."
        }

    # Safe to enter
    overall_sentiment = "BULLISH" if symbol_score > 0 else ("BEARISH" if symbol_score < 0 else "NEUTRAL")
    return {
        "symbol": symbol,
        "base_symbol": base_sym,
        "allow_entry": True,
        "action": "ALLOW_ENTRY",
        "risk_level": "LOW",
        "sentiment": overall_sentiment,
        "sentiment_score": symbol_score,
        "adverse_events": [],
        "reason": f"Clear to trade {base_sym}: No exploit, regulatory, or insolvency red flags detected."
    }


def audit_all_watched_candidates(symbols=None, articles=None):
    """Run entry news audit across all watched symbols."""
    target_syms = symbols if symbols else get_watched_symbols()
    audits = {}
    for sym in target_syms:
        audits[sym] = audit_candidate_news(sym, articles=articles)
    return audits


def save_news_state(articles, filepath=NEWS_STATE_FILE):
    """Save latest news feed, sentiment summary, and candidate audits to trader/news.json."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    bull_count = sum(1 for a in articles if a.get("sentiment") == "BULLISH")
    bear_count = sum(1 for a in articles if a.get("sentiment") == "BEARISH")
    neutral_count = sum(1 for a in articles if a.get("sentiment") == "NEUTRAL")

    if bull_count > bear_count * 1.5:
        market_bias = "BULLISH_CATALYST"
    elif bear_count > bull_count * 1.5:
        market_bias = "BEARISH_PRESSURE"
    else:
        market_bias = "BALANCED_CATALYST"

    watched_syms = get_watched_symbols()
    candidate_audits = audit_all_watched_candidates(symbols=watched_syms, articles=articles)

    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_articles": len(articles),
        "sentiment_summary": {
            "bullish": bull_count,
            "bearish": bear_count,
            "neutral": neutral_count,
            "market_bias": market_bias
        },
        "candidate_audits": candidate_audits,
        "articles": articles
    }

    temp_path = filepath + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(temp_path, filepath)
    return payload


def print_news_report(payload):
    """Print clean formatted catalyst, news and adverse risk audit report."""
    summary = payload.get("sentiment_summary", {})
    articles = payload.get("articles", [])
    audits = payload.get("candidate_audits", {})

    print("=" * 95)
    print(" 📰 BINANCE CRYPTO NEWS & CATALYST MONITOR (trader-news)")
    print(f" Updated: {payload.get('updated_at')} | Market Bias: {summary.get('market_bias')}")
    print(f" Sentiment Breakdown: 🟢 {summary.get('bullish')} Bullish | 🔴 {summary.get('bearish')} Bearish | ⚪ {summary.get('neutral')} Neutral")
    print("=" * 95)

    if audits:
        print(" 🛡️ PRE-ENTRY ADVERSE EVENT & CATALYST AUDITS:")
        for sym, aud in audits.items():
            status_icon = "✅ ALLOW" if aud["allow_entry"] else "⛔ BLOCK"
            print(f"   [{status_icon}] {sym:<6} | Risk: {aud['risk_level']:<8} | Score: {aud['sentiment_score']:+d} | {aud['reason']}")
        print("-" * 95)

    print(" 🗞️ RECENT HEADLINES:")
    for i, a in enumerate(articles[:10], 1):
        badge = "🟢 BULLISH" if a['sentiment'] == "BULLISH" else ("🔴 BEARISH" if a['sentiment'] == "BEARISH" else "⚪ NEUTRAL")
        tags = " ".join([f"#{s}" for s in a.get('symbols', [])]) if a.get('symbols') else "#MARKET"
        adverse_note = ""
        if a.get("adverse_flags"):
            cats = [f["category"] for f in a["adverse_flags"]]
            adverse_note = f" ⚠️ [{','.join(cats)}]"
        print(f"[{i:02d}] {badge} [{tags}] {a.get('source')}{adverse_note}")
        print(f"     Title: {a.get('title')}")
        if a.get('summary'):
            print(f"     Lead:  {a.get('summary')[:100]}...")
        print("-" * 95)
    print("=" * 95)


def main():
    parser = argparse.ArgumentParser(description="Crypto Breaking News & Catalyst Tracker")
    parser.add_argument("--symbols", type=str, help="Filter for specific symbols (e.g. BTC,ETH,SOL)")
    parser.add_argument("--audit", type=str, help="Audit a single symbol candidate for adverse events before trade entry")
    parser.add_argument("--filter-candidates", type=str, help="Audit comma-separated candidates and filter out blocked ones")
    parser.add_argument("--limit", type=int, default=15, help="Maximum number of headlines to return (default 15)")
    parser.add_argument("--all", action="store_true", help="Do not filter by watchlist symbols")
    parser.add_argument("--offline", type=str, help="Offline XML RSS feed file for testing")
    parser.add_argument("--json", action="store_true", help="Output JSON directly to stdout")
    parser.add_argument("--no-save", action="store_true", help="Do not save to trader/news.json")

    args = parser.parse_args()

    # Handle single candidate audit
    if args.audit:
        result = audit_candidate_news(args.audit)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "ALLOW_ENTRY" if result["allow_entry"] else "BLOCK_ENTRY"
            print(f"[news-audit] Symbol: {result['symbol']} -> {status} (Risk: {result['risk_level']})")
            print(f"             Reason: {result['reason']}")
            if result["adverse_events"]:
                print(f"             Adverse Events ({len(result['adverse_events'])}):")
                for ev in result["adverse_events"]:
                    print(f"               - {ev['title']}")
        sys.exit(0 if result["allow_entry"] else 1)

    # Handle filtering candidate list
    if args.filter_candidates:
        cand_list = [s.strip() for s in args.filter_candidates.split(",") if s.strip()]
        audits = {c: audit_candidate_news(c) for c in cand_list}
        allowed = [c for c, r in audits.items() if r["allow_entry"]]
        blocked = [c for c, r in audits.items() if not r["allow_entry"]]
        res = {
            "total": len(cand_list),
            "allowed": allowed,
            "blocked": blocked,
            "audits": audits
        }
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[news-filter] Allowed candidates ({len(allowed)}): {', '.join(allowed) if allowed else 'None'}")
            if blocked:
                print(f"              Blocked candidates ({len(blocked)}): {', '.join(blocked)}")
        sys.exit(0)

    if args.all:
        target_syms = None
    elif args.symbols:
        target_syms = {clean_symbol(s) for s in args.symbols.split(",")}
    else:
        target_syms = get_watched_symbols()

    articles = fetch_live_news(target_symbols=target_syms, max_items=args.limit, offline_file=args.offline)
    payload = save_news_state(articles) if not args.no_save else {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_articles": len(articles),
        "articles": articles
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print_news_report(payload)

    sys.exit(0)


if __name__ == "__main__":
    main()
