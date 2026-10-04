#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock market news aggregator.
Pulls headlines from public RSS feeds, does a simple mechanical sentiment
and ticker-mention analysis, and saves it all to news_data.js, which is
loaded by news_dashboard.html via <script src>.

Run:
    python3 fetch_news.py

Only needs the Python standard library (no pip install required).
"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from html import unescape

from companies_sec import SEC_COMPANIES

# On some Windows systems the console defaults to something other than
# UTF-8 (e.g. cp1252), and the script's output is full of non-ASCII text.
# Without this, print() crashes with UnicodeEncodeError on the very first
# line. reconfigure() is available on Python 3.7+; silently do nothing on
# older versions.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

# --- Optional LLM classification (DeepSeek API) -----------------------------
# If DEEPSEEK_API_KEY is set, DeepSeek determines each news item's sentiment
# and importance (it understands actual meaning: "sanctions lifted" is
# positive, "costs rise" is negative, even if individual words suggest the
# opposite). If there's no key or the request fails, falls back to the
# local keyword heuristic (detect_sentiment / calc_importance below), as
# before.
#
# DeepSeek was chosen as one of the cheapest APIs with quality that's more
# than sufficient for this task (sentiment/importance classification, not
# creative writing). The API is OpenAI-compatible (the /chat/completions
# endpoint); get a key at platform.deepseek.com.
#
# THE KEY IS NEVER STORED IN THIS FILE AND NEVER COMMITTED TO THE REPO —
# environment variable only. For a local run:
#   macOS/Linux:   export DEEPSEEK_API_KEY="sk-..."
#   Windows (cmd): set DEEPSEEK_API_KEY=sk-...
# For automatic updates via GitHub Actions, use an encrypted repository
# secret named DEEPSEEK_API_KEY. Never commit the key or print it in logs.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = "deepseek-chat"  # DeepSeek-V3 — cheap, plenty for classification
LLM_BATCH_SIZE = 15  # how many news items to send per API request
# Fallback when DeepSeek doesn't answer: the same prompt goes to Google's
# Gemini API. Key from the GEMINI_API_KEY secret (same rules as above);
# the model from the GEMINI_MODEL setting, else a cheap fast one.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-lite-latest"
# A manual run can try one provider on the whole feed ("DeepSeek" or
# "Gemini"), e.g. to check the fallback works; empty or "auto" = normal runs.
LLM_ONLY = os.environ.get("LLM_ONLY", "").strip()
LLM_ONLY = "" if LLM_ONLY.lower() == "auto" else LLM_ONLY
LLM_TIMEOUT = 60  # seconds per request in total; DeepSeek can be slow under load
# all model calls in a run stop after this many seconds, so a slow provider
# never holds up the feed: what's left keeps its earlier labels this run
LLM_RUN_BUDGET = 6 * 60
LLM_DEADLINE = time.monotonic() + LLM_RUN_BUDGET
# Bump whenever the classification prompt changes: items labelled under an
# older version are sent again (and keep their old labels if that fails).
LLM_PROMPT_VERSION = 2

# Public financial news RSS feeds (no subscription, no headline-level paywall)
#
# Yahoo Finance: its general feed (finance.yahoo.com/news/rssindex and its
# aliases) froze on 2026-09-23 and mixes in items from as far back as 2024,
# so every one of them fell outside the 80 newest and Yahoo vanished from
# the dashboard. The per-symbol headline feed stays current; asking it for
# the main indices plus the largest caps gives a general market stream.
# BioPharma Dive replaces FiercePharma, whose feed answers 403 to scripts
# and never delivered an item.
FEEDS = [
    {"name": "Yahoo Finance", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EDJI,%5EIXIC,AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA&region=US&lang=en-US"},
    {"name": "MarketWatch",   "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news_25.rss"},
    {"name": "CNBC",          "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"name": "Nasdaq",        "url": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"},
    {"name": "OilPrice.com",  "url": "https://oilprice.com/rss/main"},
    {"name": "Mining.com",    "url": "https://www.mining.com/feed"},
    {"name": "Defense One",   "url": "https://www.defenseone.com/rss/all/"},
    {"name": "BioPharma Dive", "url": "https://www.biopharmadive.com/feeds/news/"},
    {"name": "CoinDesk",      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Retail Dive",   "url": "https://www.retaildive.com/feeds/news/"},
]

# Maximum number of news items to show in the end.
# 7 general/niche feeds + 4 sector feeds (defense, pharma, crypto, retail) —
# sectors that used to be empty now get material too.
MAX_ITEMS = 80

# Ticker + sector + company name variants (so matching works not just on
# the ticker code but also on the company name in the text — e.g.
# "Rheinmetall" or "Nike"). The list covers the main market sectors of
# interest to a broad range of investors: tech, semiconductors, consumer
# goods, energy, metals, finance, healthcare, industrials, bonds/macro, etc.
COMPANY_MAP = [
    # ---- Semiconductors / AI infrastructure ----
    {"ticker": "NVDA", "sector": "Semiconductors",         "names": ["Nvidia"]},
    {"ticker": "AVGO", "sector": "Semiconductors",         "names": ["Broadcom"]},
    {"ticker": "MRVL", "sector": "Semiconductors",         "names": ["Marvell"]},
    {"ticker": "MU",   "sector": "Semiconductors",         "names": ["Micron"]},
    {"ticker": "ON",   "sector": "Semiconductors",         "names": ["ON Semiconductor", "Onsemi"]},
    {"ticker": "ALAB", "sector": "Semiconductors",         "names": ["Astera Labs"]},
    {"ticker": "AMAT", "sector": "Semiconductors",         "names": ["Applied Materials"]},
    {"ticker": "ASML", "sector": "Semiconductors",         "names": ["ASML"]},
    {"ticker": "INTC", "sector": "Semiconductors",         "names": ["Intel"]},
    {"ticker": "AMD",  "sector": "Semiconductors",         "names": ["AMD", "Advanced Micro Devices"]},
    {"ticker": "QCOM", "sector": "Semiconductors",         "names": ["Qualcomm"]},
    {"ticker": "TSM",  "sector": "Semiconductors",         "names": ["TSMC", "Taiwan Semiconductor"]},
    {"ticker": "ARM",  "sector": "Semiconductors",         "names": ["Arm Holdings"]},
    {"ticker": "SNDK", "sector": "Semiconductors",         "names": ["SanDisk"]},
    {"ticker": "TTMI", "sector": "Semiconductors",         "names": ["TTM Technologies"]},
    {"ticker": "EXTR", "sector": "Semiconductors",         "names": ["Extreme Networks"]},
    {"ticker": "SNPS", "sector": "Semiconductors",         "names": ["Synopsys"]},
    {"ticker": "HPQ",  "sector": "Semiconductors",         "names": ["HP Inc"]},
    {"ticker": "RGTI", "sector": "Semiconductors",         "names": ["Rigetti Computing", "Rigetti"]},

    # ---- AI infrastructure / "neocloud" (GPU rental, AI servers) —
    # split out from Software / Cloud because these are hardware/compute
    # capacity plays, not SaaS subscriptions; frequently in headlines
    # together as a group ("neocloud stocks") ----
    {"ticker": "CRWV", "sector": "AI Infrastructure",      "names": ["CoreWeave"]},
    {"ticker": "NBIS", "sector": "AI Infrastructure",      "names": ["Nebius"]},
    {"ticker": "SMCI", "sector": "AI Infrastructure",      "names": ["Super Micro Computer", "Super Micro", "Supermicro"]},
    {"ticker": "DELL", "sector": "AI Infrastructure",      "names": ["Dell Technologies", "Dell"]},
    {"ticker": "LITE", "sector": "AI Infrastructure",      "names": ["Lumentum Holdings", "Lumentum"]},
    {"ticker": "SNX",  "sector": "AI Infrastructure",      "names": ["TD Synnex"]},
    # Pure Storage renamed itself Everpure (Feb 2026), ticker PSTG → P
    {"ticker": "P",    "sector": "AI Infrastructure",      "names": ["Everpure", "Pure Storage"]},

    # ---- Software / AI / Cybersecurity / Cloud ----
    {"ticker": "CRWD", "sector": "Cybersecurity",      "names": ["CrowdStrike"]},
    {"ticker": "NET",  "sector": "Cybersecurity",      "names": ["Cloudflare"]},
    {"ticker": "PANW", "sector": "Cybersecurity",      "names": ["Palo Alto Networks"]},
    {"ticker": "FTNT", "sector": "Cybersecurity",      "names": ["Fortinet"]},
    {"ticker": "PLTR", "sector": "Software / AI Analytics",      "names": ["Palantir"]},
    {"ticker": "SOUN", "sector": "Software / AI Analytics",      "names": ["SoundHound"]},
    {"ticker": "CRM",  "sector": "Software / Cloud",             "names": ["Salesforce"]},
    {"ticker": "NOW",  "sector": "Software / Cloud",             "names": ["ServiceNow"]},
    {"ticker": "SNOW", "sector": "Software / Cloud",             "names": ["Snowflake"]},
    {"ticker": "ADBE", "sector": "Software / Cloud",             "names": ["Adobe"]},
    {"ticker": "ORCL", "sector": "Software / Cloud",             "names": ["Oracle"]},

    # ---- Big Tech ----
    {"ticker": "MSFT", "sector": "Big Tech",               "names": ["Microsoft"]},
    {"ticker": "GOOGL","sector": "Big Tech",               "names": ["Alphabet", "Google"]},
    {"ticker": "AAPL", "sector": "Big Tech",               "names": ["Apple"]},
    {"ticker": "AMZN", "sector": "Big Tech",               "names": ["Amazon", "AWS", "Amazon Web Services"]},
    {"ticker": "META", "sector": "Big Tech",               "names": ["Meta", "Facebook"]},
    {"ticker": "BABA", "sector": "Big Tech",               "names": ["Alibaba"]},

    # ---- Electric vehicles / Automotive ----
    {"ticker": "TSLA", "sector": "Automotive / EV",          "names": ["Tesla"]},
    {"ticker": "RIVN", "sector": "Automotive / EV",          "names": ["Rivian"]},
    {"ticker": "F",    "sector": "Automotive / EV",          "names": ["Ford"]},
    {"ticker": "GM",   "sector": "Automotive / EV",          "names": ["General Motors"]},
    {"ticker": "VOW3", "sector": "Automotive / EV",          "names": ["Volkswagen"]},
    {"ticker": "TM",   "sector": "Automotive / EV",          "names": ["Toyota"]},
    {"ticker": "BYDDY","sector": "Automotive / EV",          "names": ["BYD"]},
    {"ticker": "STLA", "sector": "Automotive / EV",          "names": ["Stellantis"]},
    {"ticker": "HYMTF","sector": "Automotive / EV",          "names": ["Hyundai"]},

    # ---- Consumer sector (real businesses: food, retail, brands) ----
    {"ticker": "NKE",  "sector": "Consumer Goods", "names": ["Nike"]},
    {"ticker": "KO",   "sector": "Consumer Goods", "names": ["Coca-Cola"]},
    {"ticker": "PEP",  "sector": "Consumer Goods", "names": ["PepsiCo"]},
    {"ticker": "MCD",  "sector": "Consumer Goods", "names": ["McDonald's", "McDonalds"]},
    {"ticker": "SBUX", "sector": "Consumer Goods", "names": ["Starbucks"]},
    {"ticker": "WMT",  "sector": "Consumer Goods", "names": ["Walmart"]},
    {"ticker": "COST", "sector": "Consumer Goods", "names": ["Costco"]},
    {"ticker": "PG",   "sector": "Consumer Goods", "names": ["Procter & Gamble"]},
    {"ticker": "LULU", "sector": "Consumer Goods", "names": ["Lululemon"]},
    {"ticker": "TGT",  "sector": "Consumer Goods", "names": ["Target Corp", "Target Corporation"]},
    {"ticker": "M",    "sector": "Consumer Goods", "names": ["Macy's", "Macys"]},
    {"ticker": "KSS",  "sector": "Consumer Goods", "names": ["Kohl's", "Kohls"]},
    {"ticker": "GAP",  "sector": "Consumer Goods", "names": ["Gap Inc"]},
    {"ticker": "DIS",  "sector": "Media / Entertainment",    "names": ["Disney"]},
    {"ticker": "NFLX", "sector": "Media / Entertainment",    "names": ["Netflix"]},
    {"ticker": "RDDT", "sector": "Media / Entertainment",    "names": ["Reddit"]},
    {"ticker": "NTDOY","sector": "Media / Entertainment",    "names": ["Nintendo"]},
    # Paramount Global merged with Skydance (Aug 2025), ticker PARA → PSKY
    {"ticker": "PSKY", "sector": "Media / Entertainment",    "names": ["Paramount Skydance", "Paramount"]},
    {"ticker": "WBD",  "sector": "Media / Entertainment",    "names": ["Warner Bros Discovery", "Warner Bros. Discovery", "Warner Bros"]},

    # ---- Energy (oil and gas) ----
    {"ticker": "XOM",  "sector": "Oil & Gas",            "names": ["ExxonMobil", "Exxon Mobil"]},
    {"ticker": "CVX",  "sector": "Oil & Gas",            "names": ["Chevron"]},
    {"ticker": "SHEL", "sector": "Oil & Gas",            "names": ["Shell"]},
    {"ticker": "BP",   "sector": "Oil & Gas",            "names": ["BP"]},
    {"ticker": "COP",  "sector": "Oil & Gas",            "names": ["ConocoPhillips"]},
    {"ticker": "OPEC", "sector": "Oil & Gas",            "names": ["OPEC", "OPEC+"]},
    {"ticker": "VIST", "sector": "Oil & Gas",            "names": ["Vista Energy"]},

    # ---- Metals and mining ----
    {"ticker": "RIO",  "sector": "Metals & Mining",       "names": ["Rio Tinto"]},
    {"ticker": "BHP",  "sector": "Metals & Mining",       "names": ["BHP"]},
    {"ticker": "FCX",  "sector": "Metals & Mining",       "names": ["Freeport-McMoRan"]},
    {"ticker": "NEM",  "sector": "Metals & Mining",       "names": ["Newmont"]},
    {"ticker": "AA",   "sector": "Metals & Mining",       "names": ["Alcoa"]},
    # Barrick Gold became Barrick Mining (May 2025), ticker GOLD → B; the old
    # ticker also matched "GOLD" written in capitals in commodity headlines
    {"ticker": "B",    "sector": "Metals & Mining",       "names": ["Barrick Mining", "Barrick Gold", "Barrick"]},
    {"ticker": "LUG",  "sector": "Metals & Mining",       "names": ["Lundin Gold"]},
    {"ticker": "GLEN", "sector": "Metals & Mining",       "names": ["Glencore"]},

    # ---- Defense / Space ----
    {"ticker": "RHM",  "sector": "Defense",       "names": ["Rheinmetall"]},
    {"ticker": "LMT",  "sector": "Defense",       "names": ["Lockheed Martin"]},
    {"ticker": "BA",   "sector": "Defense",       "names": ["Boeing"]},
    {"ticker": "NOC",  "sector": "Defense",       "names": ["Northrop Grumman"]},
    # "RTX PRO 5500", "RTX 5090" are Nvidia graphics cards, not RTX Corp
    {"ticker": "RTX",  "sector": "Defense",       "names": ["RTX", "Raytheon"],
     "exclude": r"\bRTX\s*(?:PRO\b|\d{3,4}\b)"},
    {"ticker": "LHX",  "sector": "Defense",       "names": ["L3Harris"]},
    # Paris listing; plain "AIR" is AAR Corp's US ticker
    {"ticker": "AIR.PA", "sector": "Defense",     "names": ["Airbus"]},
    {"ticker": "SPCX", "sector": "Space",                 "names": ["SpaceX"]},
    {"ticker": "LUNR", "sector": "Space",                 "names": ["Intuitive Machines"]},

    # ---- Finance / banks / payments ----
    {"ticker": "JPM",  "sector": "Banking & Finance",        "names": ["JPMorgan", "JP Morgan"]},
    {"ticker": "GS",   "sector": "Banking & Finance",        "names": ["Goldman Sachs"]},
    {"ticker": "BAC",  "sector": "Banking & Finance",        "names": ["Bank of America"]},
    {"ticker": "MS",   "sector": "Banking & Finance",        "names": ["Morgan Stanley"]},
    {"ticker": "SPGI", "sector": "Banking & Finance",        "names": ["S&P Global"]},
    {"ticker": "WFC",  "sector": "Banking & Finance",        "names": ["Wells Fargo"]},
    {"ticker": "BRK.B","sector": "Banking & Finance",        "names": ["Berkshire Hathaway"]},
    {"ticker": "MORN", "sector": "Banking & Finance",        "names": ["Morningstar"]},
    {"ticker": "EQH",  "sector": "Banking & Finance",        "names": ["Equitable Holdings"]},
    {"ticker": "APO",  "sector": "Banking & Finance",        "names": ["Apollo Global Management", "Apollo Global", "Apollo"]},
    {"ticker": "UBS",  "sector": "Banking & Finance",        "names": ["UBS"]},
    {"ticker": "V",    "sector": "Payments / Fintech",       "names": ["Visa"]},
    {"ticker": "MA",   "sector": "Payments / Fintech",       "names": ["Mastercard"]},
    {"ticker": "PYPL", "sector": "Payments / Fintech",       "names": ["PayPal"]},
    {"ticker": "HOOD", "sector": "Payments / Fintech",       "names": ["Robinhood"]},

    # ---- Healthcare / pharma / biotech ----
    {"ticker": "PFE",  "sector": "Healthcare",        "names": ["Pfizer"]},
    {"ticker": "JNJ",  "sector": "Healthcare",        "names": ["Johnson & Johnson"]},
    {"ticker": "LLY",  "sector": "Healthcare",        "names": ["Eli Lilly", "Lilly"]},
    {"ticker": "MRK",  "sector": "Healthcare",        "names": ["Merck"]},
    {"ticker": "UNH",  "sector": "Healthcare",        "names": ["UnitedHealth"]},
    {"ticker": "MRNA", "sector": "Healthcare",        "names": ["Moderna"]},
    {"ticker": "GILD", "sector": "Healthcare",        "names": ["Gilead Sciences", "Gilead"]},
    {"ticker": "BSX",  "sector": "Healthcare",        "names": ["Boston Scientific"]},
    {"ticker": "RVMD", "sector": "Healthcare",        "names": ["Revolution Medicines"]},
    {"ticker": "AZN",  "sector": "Healthcare",        "names": ["AstraZeneca"]},
    {"ticker": "NVS",  "sector": "Healthcare",        "names": ["Novartis"]},
    {"ticker": "NVO",  "sector": "Healthcare",        "names": ["Novo Nordisk", "Novo"]},
    {"ticker": "AMGN", "sector": "Healthcare",        "names": ["Amgen"]},
    {"ticker": "ZTS",  "sector": "Healthcare",        "names": ["Zoetis"]},

    # ---- Industrials / infrastructure ----
    {"ticker": "CAT",  "sector": "Industrials",         "names": ["Caterpillar"]},
    {"ticker": "HON",  "sector": "Industrials",         "names": ["Honeywell"]},
    {"ticker": "GE",   "sector": "Industrials",         "names": ["General Electric", "GE Aerospace"]},
    {"ticker": "BDRBF","sector": "Industrials",         "names": ["Bombardier"]},

    # ---- Telecom / utilities ----
    {"ticker": "T",    "sector": "Telecom",                "names": ["AT&T"]},
    {"ticker": "VZ",   "sector": "Telecom",                "names": ["Verizon"]},
    {"ticker": "NEE",  "sector": "Utilities",        "names": ["NextEra Energy"]},
    {"ticker": "BE",   "sector": "Utilities",        "names": ["Bloom Energy"]},

    # ---- Airlines / travel ----
    {"ticker": "DAL",  "sector": "Airlines / Travel", "names": ["Delta Air Lines"]},
    {"ticker": "UAL",  "sector": "Airlines / Travel", "names": ["United Airlines"]},
    {"ticker": "ABNB", "sector": "Airlines / Travel", "names": ["Airbnb"]},
    {"ticker": "UBER", "sector": "Airlines / Travel", "names": ["Uber"]},
    {"ticker": "LVS",  "sector": "Airlines / Travel", "names": ["Las Vegas Sands"]},
    {"ticker": "MGM",  "sector": "Airlines / Travel", "names": ["MGM Resorts"], "match_ticker": False},
    {"ticker": "TCOM", "sector": "Airlines / Travel", "names": ["Trip.com"]},
    {"ticker": "AAL",  "sector": "Airlines / Travel", "names": ["American Airlines"]},

    # ---- Real estate / homebuilders ----
    {"ticker": "LEN",  "sector": "Real Estate / Homebuilders", "names": ["Lennar"]},

    # ---- Crypto ----
    {"ticker": "BTC",  "sector": "Cryptocurrencies",           "names": ["Bitcoin"]},
    {"ticker": "ETH",  "sector": "Cryptocurrencies",           "names": ["Ethereum"]},
    {"ticker": "COIN", "sector": "Cryptocurrencies",           "names": ["Coinbase"]},
    # "Strategy" (its name since 2025) is an everyday word: it counts only
    # capitalised and next to bitcoin or its preferred shares (STRC, STRK,
    # STRF, STRD), as in "Strategy adds $370M of BTC"
    {"ticker": "MSTR", "sector": "Cryptocurrencies",           "names": ["MicroStrategy", "Strategy", "Michael Saylor"],
     "context": {"Strategy": r"(?i:\b(?:bitcoin|btc)\b)|\bSTR[CKFD]\b"}},
    {"ticker": "CRCL", "sector": "Cryptocurrencies",           "names": ["Circle Internet Group", "Circle Internet Financial"]},
    {"ticker": "XMR",  "sector": "Cryptocurrencies",           "names": ["Monero"]},

    # ---- Bonds / macro (not companies, but market terms) ----
    {"ticker": "UST10Y","sector": "Bonds / Macro",     "names": ["10-year Treasury", "Treasury yield", "Treasury yields", "U.S. Treasury"]},
    {"ticker": "TLT",   "sector": "Bonds / Macro",     "names": ["Treasury bond", "long-term Treasury bond"]},
    {"ticker": "FED",   "sector": "Bonds / Macro",     "names": ["Federal Reserve", "Fed rate", "interest rate decision"]},

    # ---- ETFs / indices ----
    {"ticker": "SPY",  "sector": "ETFs / Indices",          "names": ["S&P 500"]},
    {"ticker": "QQQ",  "sector": "ETFs / Indices",          "names": ["Nasdaq 100", "Nasdaq Composite"]},
    {"ticker": "DJI",  "sector": "ETFs / Indices",          "names": ["Dow Jones"]},
    {"ticker": "VWCE", "sector": "ETFs / Indices",          "names": ["VWCE", "FTSE All-World"]},
]

# The next 500 largest US listings, from SEC's ticker list (companies_sec.py);
# an entry above always wins over one there for the same ticker.
_CURATED_TICKERS = {entry["ticker"] for entry in COMPANY_MAP}
COMPANY_MAP += [entry for entry in SEC_COMPANIES if entry["ticker"] not in _CURATED_TICKERS]

# Keeping the old variable name for backward compatibility with code below
WATCHLIST = COMPANY_MAP

# Simple keyword list for mechanical sentiment scoring (no AI)
POSITIVE_WORDS = [
    "surge", "surges", "rally", "rallies", "jump", "jumps", "gain", "gains",
    "soar", "soars", "beat", "beats", "record high", "climbs", "climb",
    "upgrade", "upgraded", "boost", "boosts", "rebound", "outperform",
    "bullish", "rises", "rise", "advance", "advances", "profit growth",
]
NEGATIVE_WORDS = [
    "plunge", "plunges", "crash", "crashes", "fall", "falls", "falling",
    "drop", "drops", "slump", "slumps", "miss", "misses", "downgrade",
    "downgraded", "sell-off", "selloff", "recession", "bearish", "warns",
    "warning", "cut", "cuts", "layoffs", "decline", "declines", "tumbles",
    "tumble", "loss", "losses",
]

# "Loud" events that usually move the market harder than routine news —
# used for mechanical importance scoring (no AI).
HIGH_IMPACT_WORDS = [
    "acquisition", "acquires", "acquired", "merger", "merges", "takeover",
    "bankruptcy", "bankrupt", "files for chapter 11", "chapter 11",
    "resigns", "resignation", "steps down", "fired", "ousted",
    "investigation", "probe", "lawsuit", "sues", "fraud", "guilty",
    "settlement", "antitrust", "sanctions", "tariff", "tariffs",
    "recall", "recalls", "hack", "hacked", "breach", "data breach",
    "record high", "record low", "all-time high", "all-time low",
    "halted", "trading halt", "bailout", "default", "ipo", "spinoff",
    "spin-off", "stake", "buyback", "dividend hike", "profit warning",
    "guidance cut", "earnings beat", "earnings miss", "rate decision",
    "rate hike", "rate cut", "emergency meeting",
    # urgency markers and broad market crash/rally signals
    "breaking", "breaking news", "just in", "developing story",
    "sell-off", "selloff", "rout", "market rout", "tech rout",
    "wipes out", "wiped out", "erases", "erased", "worst day",
    "worst week", "biggest drop", "biggest decline", "billions wiped",
    "extends losses", "broad decline", "market-wide", "across the board",
    # mirror phrasings for a sharp RALLY — these were missing before,
    # so crashes got unfairly more weight than rallies
    "market rally", "broad rally", "tech rally", "best day", "best week",
    "biggest jump", "biggest gain", "biggest rally", "adds billions",
    "extends gains", "broad gain", "surges to record", "soars to record",
    "melt-up", "risk-on rally",
]

# Compiled regexes with WORD BOUNDARIES (\b) for all three lists. The old
# version used a plain substring check (`w in text`), which meant "again"
# falsely counted as the positive word "gain" (simply because the letters
# "gain" appear inside "again"), and "stake" falsely matched inside
# "stakeholder(s)". \b fixes both problems at once.
def _compile_word_patterns(words):
    return [re.compile(r"\b" + re.escape(w) + r"\b") for w in words]


POSITIVE_PATTERNS = _compile_word_patterns(POSITIVE_WORDS)
NEGATIVE_PATTERNS = _compile_word_patterns(NEGATIVE_WORDS)
HIGH_IMPACT_PATTERNS = _compile_word_patterns(HIGH_IMPACT_WORDS)

# Markers of macro/geopolitical news — sanctions, wars, elections, central
# bank policy, etc. Such news often has NO direct market "target" (a
# specific company/ticker) and shouldn't be presented as an investment
# signal when it's really just political background. If a news item does
# mention a specific ticker/company, it stays a "market signal"
# (content_type = market_signal) even if it also carries a geopolitical
# tone.
MACRO_KEYWORDS = [
    "sanction", "sanctions", "tariff", "tariffs", "embargo", "geopolitic",
    "war", "invasion", "ceasefire", "cease-fire", "treaty", "diplomatic",
    "diplomacy", "election", "referendum", "coup", "protest", "protests",
    "parliament", "president", "prime minister", "government",
    "european union", "united nations", "g7", "g20", "opec",
    "central bank", "trade deal", "trade war", "immigration", "border",
    "military", "troops", "nuclear", "missile", "patriarch",
]
MACRO_PATTERNS = _compile_word_patterns(MACRO_KEYWORDS)


def is_macro_context(text: str, tickers: list) -> bool:
    """True if the news item looks like macro/geopolitical background
    rather than news with a specific market "target". If the item has at
    least one recognized ticker/company, we treat it as a market signal
    even if it also touches on politics."""
    if tickers:
        return False
    lower = text.lower()
    return any(p.search(lower) for p in MACRO_PATTERNS)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

NS = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

# Signals of personal advice columns (MarketWatch "Retirement" / "Fix My
# Portfolio" / "The Moneyist" and similar) — these aren't market news,
# they're a breakdown of one reader's letter ("my grandmother will get
# such-and-such a pension", "I sold my..."), or a direct editorial reply
# to a reader letter. The old family-word list and personal-phrasing list
# were too narrow, and some of this junk slipped through.
#
# Key signal: real news headlines (wire services, market feeds) are almost
# ALWAYS impersonal and talk about companies/markets in the third person —
# they don't start with "I"/"We"/"My"/"Our". Personal columns, on the
# other hand, almost always do. `^(i|we|my|our)\b` isn't a substring
# check: `\b` on both sides prevents false matches on "IPO", "iPhone",
# "Inc", etc.
ADVICE_COLUMN_PATTERNS = [
    r"^['’\"]",                                    # headline starts with a quote mark
    r"^(i|i'm|i've|i'd|we|we're|we've|my|our)\b",  # first-person narration — "I retired...", "My husband...", "We sold..."
    r"\b(my|our) (wife|husband|brother|sister|mother|father|mom|dad|son|daughter|parents?|"
    r"grandmother|grandfather|grandma|grandpa|aunt|uncle|cousin|boyfriend|girlfriend|"
    r"fianc[eé]e?|spouse|partner|roommate|in-laws?|ex-wife|ex-husband)\b",
    r"\bi'?m \d{2}\b",                              # "I'm 67 with a pension"
    r"\bwe'?re \d{2}\b",
    r"\b(should i|should we)\b",
    r"\bmy (pension|retirement|401\(?k\)?|social security|inheritance|savings|nest egg|ira)\b",
    r"\b(reader|readers)\b[^.]{0,25}\b(ask|asks|asked|wrote|writes|question)\b",  # editorial replies to reader letters
    r"\b(the moneyist|fix my portfolio|retirement weekly|ask the fool|dear penny|money mailbag|ask the hammer)\b",  # known advice columns
]
ADVICE_COLUMN_RE = re.compile("|".join(ADVICE_COLUMN_PATTERNS), flags=re.IGNORECASE)


def is_advice_column(title: str) -> bool:
    """True if the headline looks like a personal advice column rather than news."""
    return bool(ADVICE_COLUMN_RE.search(title))


# Paid market-research press releases that Yahoo's headline feed carries
# ("Cluster Computing Global Market Report 2026: Capitalize on the surge
# to $85.27 billion by 2030") — industry sizing ads, not market news.
MARKET_REPORT_RE = re.compile(r"\bglobal market report\b|\bmarket report 20\d\d\b", flags=re.IGNORECASE)


def is_market_report_ad(title: str) -> bool:
    return bool(MARKET_REPORT_RE.search(title))


# Nasdaq's feed carries hundreds of machine-written posts a month (ETF
# Channel / BNK Invest): fund flows, option activity, moving-average
# crosses, dividend calendars, "movers" ticker lists. They hold no news,
# and each tags a handful of companies at once ("Notable ETF Outflow
# Detected - XLE, VLO, EOG, BKR"); about 960 of 2,580 Nasdaq items in the
# archive by 2 Oct 2026. Matched on Nasdaq only: the same words in another
# outlet ("bitcoin ETF inflows of $700 million") are real news.
TEMPLATED_POST_RE = re.compile("|".join([
    # ETF flows
    r"\b(?:Notable|Noteworthy) ETF (?:Outflows?|Inflows?)\b", r"\bBig ETF (?:Inflows|Outflows)\b",
    r"\bLarge (?:Inflows|Outflows) Detected\b", r"\bETF (?:Outflow|Inflow) Alert\b",
    r"\bExperiences Big (?:Inflow|Outflow)\b",
    # options
    r"\bOption Activity\b", r"\bOptions Begin Trading\b", r"\bOptions Now Available For\b", r"\bYieldBoost\b",
    r"\bPut And Call Options\b", r"\bImplied Volatility Surging for\b", r"\bOptions Traders (?:Betting|Know)\b",
    r"\bOptions Market Predicting\b",
    # technical levels
    r"\b(?:Key|Critical) Moving Average\b", r"\bTwo Hundred Day Moving Average\b", r"\b200-Day Moving Average\b",
    r"\b200 DMA\b", r"\bCritical Technical Indicator\b", r"\b(?:Now|Becomes|Getting Very) Oversold\b",
    r"\bOversold Conditions\b", r"\bEnters Oversold Territory\b", r"\bis Oversold\s*$",
    r"\bCrowded With (?:Sellers|Buyers)\b", r"\bwith Unusual Volume\b",
    # holdings, dividends, ranks and ticker lists
    r"\b13F Filers\b", r"\bDividend Run For\b", r"\bDaily Dividend Report\b", r"\bEx-Dividend Reminder\b",
    r"\bEx-Div Reminder\b", r"\bGoes Ex-Dividend Soon\b", r"\bCash Dividend On The Way From\b",
    r"\bDividend Yield Pushes Past\b", r"\bCross(?:es)? [\d.]+% Yield Mark\b", r"\bTop 10 [\w ]*Dividend Stock\b",
    r"\bTo The Top 10\b", r"\bInsider Buying Report\b", r"\bAnalyst Moves: [A-Z]",
    r"\bCrosses (?:Above|Below) Average Analyst Target\b", r"\bReaches Analyst Target Price\b",
    r"\bAchieves #\d+ Analyst Rank\b", r"\bMoves Up In Analyst Rankings\b", r"\bAnalyst Favorites:",
    r"\bRanks Among Analysts' Top\b", r"\bBroker Darlings of\b", r"\bGains Ahead For The Holdings of\b",
    r"\bNew Strong (?:Buy|Sell) Stocks for\b", r"\bSector (?:Leaders|Laggards):",
    r"\bMovers: [A-Z]{1,5}(?:\.[A-Z])?(?:, ?[A-Z]{1,5}(?:\.[A-Z])?)*\s*$",
]), flags=re.IGNORECASE)


def is_templated_post(title: str, source: str) -> bool:
    """True for Nasdaq's machine-written ticker-list posts (see above)."""
    return source == "Nasdaq" and bool(TEMPLATED_POST_RE.search(title))


def strip_html(raw_html: str) -> str:
    """Strips HTML tags and extra whitespace from text."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_pubdate(raw: str):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        # some feeds use ISO format
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None


def find_image(item: ET.Element) -> str:
    """Tries to find an image in the various possible spots in an RSS item."""
    media_content = item.find("media:content", NS)
    if media_content is not None and media_content.get("url"):
        return media_content.get("url")

    media_thumb = item.find("media:thumbnail", NS)
    if media_thumb is not None and media_thumb.get("url"):
        return media_thumb.get("url")

    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        enc_type = enclosure.get("type", "")
        if "image" in enc_type or enclosure.get("url", "").lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        ):
            return enclosure.get("url")

    # sometimes the image is hidden right in the description as <img src="...">
    desc = item.find("description")
    if desc is not None and desc.text:
        m = re.search(r'<img[^>]+src="([^"]+)"', desc.text)
        if m:
            return m.group(1)

    return ""


def detect_sentiment(text: str) -> str:
    lower = text.lower()
    pos = sum(1 for p in POSITIVE_PATTERNS if p.search(lower))
    neg = sum(1 for p in NEGATIVE_PATTERNS if p.search(lower))
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def calc_importance(title: str, description: str, tickers: list, pub_dt) -> float:
    """Mechanical "importance" score for a news item (0-100+, no AI).

    Components:
    - "loud" words in the title — the strongest signal (BREAKING, sell-off,
      rout, etc.), weighted by HOW MANY times the word appears, not just
      whether it's present — an article with "plunge... crash... sell-off...
      tumbles" back to back is clearly more alarming than one with a single
      such word
    - the same words in the body — also counted, but with less weight
    - sentiment strength (positive/negative) across the whole text, also
      weighted by frequency
    - number of tickers/companies mentioned
    - a freshness bonus (breaks ties between otherwise-similar news items)

    This is a HEURISTIC, not an editorial judgment of significance — it's
    good at catching obviously resonant news (crashes, mergers,
    bankruptcies, records), but it's no substitute for your own judgment
    of what actually matters.
    """
    title_lower = title.lower()
    desc_lower = description.lower()
    full_lower = f"{title_lower} {desc_lower}"

    def weighted_hits(patterns, text, cap_per_word=3):
        """Counts total occurrences of words from the list (by word
        boundary, not substring), capping each individual word's
        contribution — so one word repeated many times doesn't skew the
        count."""
        total = 0
        for p in patterns:
            c = len(p.findall(text))
            if c:
                total += min(c, cap_per_word)
        return total

    high_impact_title = weighted_hits(HIGH_IMPACT_PATTERNS, title_lower)
    high_impact_desc = weighted_hits(HIGH_IMPACT_PATTERNS, desc_lower)
    sentiment_hits = weighted_hits(POSITIVE_PATTERNS, full_lower) + weighted_hits(NEGATIVE_PATTERNS, full_lower)

    score = 0.0
    score += high_impact_title * 26   # a loud word in the title — the strongest signal
    score += high_impact_desc * 12    # same thing in the body — still important, but weaker
    score += sentiment_hits * 5       # sentiment intensity (frequency, not just presence)
    score += min(len(tickers), 4) * 4 # several companies mentioned at once

    # an explicit urgency marker — a separate, hefty bonus
    if "breaking" in title_lower:
        score += 25

    # freshness bonus: newer scores higher, decaying smoothly over 48 hours
    if pub_dt is not None:
        try:
            now = datetime.now(timezone.utc)
            pub_utc = pub_dt if pub_dt.tzinfo else pub_dt.replace(tzinfo=timezone.utc)
            hours_ago = max(0, (now - pub_utc).total_seconds() / 3600)
            freshness_bonus = max(0.0, 12 - hours_ago * 0.25)
            score += freshness_bonus
        except Exception:
            pass

    return round(score, 1)


# ---------------------------------------------------------------------------
# LLM CLASSIFICATION (optional, via the DeepSeek API)
# ---------------------------------------------------------------------------

def build_prompt(batch_items):
    """The classification request for a batch of {"title", "description"}:
    asks the model to honestly (with real context understanding) determine
    sentiment, importance, and content type. Provider-neutral — DeepSeek and
    the Gemini fallback get the same text."""
    numbered = "\n\n".join(
        f"{i + 1}. Title: {it['title']}\nDescription: {(it['description'] or '')[:300]}"
        for i, it in enumerate(batch_items)
    )
    prompt = (
        "You are a financial news classifier for an investor. For each news "
        "item below, determine three fields.\n\n"
        "1) content_type — \"market_signal\" or \"macro_context\":\n"
        "   - \"market_signal\" if the news has a specific market target — "
        "a company, sector, or asset class whose price could realistically "
        "move because of this news.\n"
        "   - \"macro_context\" if it's geopolitics/politics/macro background "
        "WITHOUT a direct link to a specific security — sanctions against an "
        "individual, diplomacy, elections, military action, decisions by "
        "international bodies, etc. Such news matters for understanding the "
        "bigger picture but is NOT a trading signal by itself.\n\n"
        "2) sentiment — \"positive\", \"negative\" or \"neutral\", STRICTLY "
        "from the standpoint of likely impact on the market/asset, NOT from "
        "a political, moral, or humanitarian judgment of the event. These "
        "are different axes: a news item can be tragic or controversial in "
        "substance while being neutral or even positive for a specific "
        "asset — and vice versa. If a news item is \"macro_context\" and has "
        "no clear one-sided market effect, honestly mark it \"neutral\" "
        "rather than trying to score its political significance.\n"
        "   Calibration examples:\n"
        "   - \"EU declines to sanction [a religious/political figure]\" → "
        "content_type=macro_context (no single stock price this directly "
        "acts on), sentiment=neutral (absence of escalation is not a strong "
        "signal up or down for any specific asset), NOT negative just "
        "because the underlying event may be morally contentious.\n"
        "   - \"EU imports record volumes of LNG from Russia\" → "
        "content_type=macro_context (no single ticker target in a broad "
        "trend), sentiment=neutral-to-positive for the energy market (more "
        "gas supply), not \"negative\" just because the topic involves "
        "Russia.\n"
        "   - \"Nvidia surges to record high\" → content_type=market_signal, "
        "sentiment=positive.\n"
        "   - \"CPI comes in hotter than expected\", \"Fed holds rates\", "
        "\"Payrolls beat estimates\" → content_type=market_signal: scheduled "
        "macro data and central-bank decisions move bonds, currencies and "
        "the broad indices directly. Sentiment follows the market reaction "
        "the item describes; if it describes none, judge the likely one "
        "(hotter inflation is negative for stocks and bonds).\n"
        "   - Opinion and promotional pieces — \"3 stocks to buy now\", "
        "\"Is X a buy?\", \"Prediction: X will soar\", a manager's top picks "
        "— are not news: sentiment=neutral unless the item reports an actual "
        "price move, importance 5-10.\n"
        "   - Previews of events that haven't happened yet — \"what to "
        "expect from X's earnings\", \"week ahead\" — are sentiment=neutral, "
        "importance 5-15.\n"
        "   - Mixed or two-sided items — \"stocks mixed\", \"X rises while Y "
        "falls\", a beat on one line and a miss on another — are "
        "sentiment=neutral unless one side clearly dominates.\n"
        "   - Analyst actions on one company: an upgrade or a raised price "
        "target is positive, a downgrade or a cut target is negative.\n"
        "   - \"Wheat rallies as Russia rejects Ukraine's peace proposal\" → "
        "sentiment=positive for the wheat market (the price of the asset in "
        "the headline is explicitly rising — \"rallies\"), even though the "
        "underlying cause (a conflict continuing) reads as grim news in "
        "general. Score the literal price direction stated for that "
        "specific asset, not how sympathetic the underlying cause is. The "
        "same rule applies in reverse: a price FALLING because of \"good\" "
        "geopolitical news (e.g. a ceasefire easing supply fears) is still "
        "negative for that asset's price, not positive.\n\n"
        "3) importance — a number from 0 to 100: how much this news item can "
        "realistically move the market or specific stocks. BE CONSERVATIVE — "
        "err toward lower scores. Most items in a normal day's feed should "
        "land under 40; scores above 60 must be rare (a handful per day at "
        "most), reserved for news that would actually lead the evening "
        "financial news, not routine trading-day moves. Calibration anchors:\n"
        "   - 5-15: routine — analyst rating tweak, small single-stock move, "
        "a regular earnings report with no surprise.\n"
        "   - 20-35: notable single-company move — a real earnings beat/miss, "
        "a sizeable single-stock rally or drop (5-15%).\n"
        "   - 40-55: sector-wide move, a major M&A deal, or a single company "
        "moving sharply (15%+) on real news.\n"
        "   - 60-75: market-wide move tied to a scheduled macro event (CPI, "
        "Fed rate decision) that visibly moved major indices, or a genuinely "
        "large-scale geopolitical development.\n"
        "   - 80-100: rare, historic-scale events only — a major bank's "
        "collapse, a market-wide crash, the start of a war, a systemic "
        "financial crisis. Do NOT use this range for a normal green/red "
        "trading day, even a strong one.\n"
        "   A headline like \"Dow rises on inflation data\" or \"Stocks open "
        "higher after CPI report\" describing an ordinary, expected market "
        "reaction is importance ~15-30, NOT 60+ — routine daily market "
        "commentary is not breaking news.\n\n"
        f"News items:\n{numbered}\n\n"
        "Respond with STRICTLY a JSON object, no prose outside the JSON, "
        "in this exact shape, with \"results\" containing one entry per news "
        "item above, in the same order, each with \"n\" set to that item's "
        "number:\n"
        '{"results": [{"n": 1, "content_type": "market_signal", "sentiment": "positive", "importance": 42}, ...]}'
    )

    return prompt


def within_timeout(call):
    """call() with a hard limit of LLM_TIMEOUT seconds in total. urlopen's
    own timeout only fires on silence, and a busy DeepSeek keeps sending
    blank keep-alive lines while it queues a request, which kept a run
    waiting for over twenty minutes. The worker is a daemon thread, so one
    left hanging doesn't hold up the end of the run."""
    box = {}
    def work():
        try:
            box["value"] = call()
        except BaseException as e:  # handed to the caller below
            box["error"] = e
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(LLM_TIMEOUT)
    if worker.is_alive():
        raise TimeoutError(f"no complete answer within {LLM_TIMEOUT} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def post_json(name, url, headers, body):
    """POSTs a JSON body and returns the parsed answer, or None. One retry
    for a slow or busy moment (timeout, rate limit, server error); a
    rejected key or an empty balance won't fix itself, so no retry for
    other errors. Keys travel only in headers and are never printed."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    for attempt in (1, 2):
        if time.monotonic() > LLM_DEADLINE:
            print(f"  [!] Time for model calls is up this run — not asking {name}")
            return None
        def fetch():
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                return json.loads(resp.read())
        try:
            return within_timeout(fetch)
        except urllib.error.HTTPError as e:
            print(f"  [!] Error calling the {name} API: HTTP {e.code}")
            if e.code != 429 and e.code < 500:
                return None
        except Exception as e:  # a model call must never stop the news update
            print(f"  [!] Error calling the {name} API: {e}")
        if attempt == 1:
            time.sleep(5)
    return None


def parse_results(name, text, count):
    """The model's {"results": [...]} as one entry per item, or None in the
    place of an item it skipped: matched by each entry's "n" (the item's
    number), else by order when the count fits. A model now and then drops
    or merges an item; the rest of its answer is still good. None when
    nothing usable came back."""
    try:
        # in case the model wrapped the JSON in ```json ... ``` anyway
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        results = parsed.get("results") if isinstance(parsed, dict) else parsed
    except Exception as e:
        print(f"  [!] {name}: unreadable answer ({e})")
        return None
    if not isinstance(results, list):
        print(f"  [!] {name}: no results list in the answer")
        return None
    numbered = {}
    for entry in results:
        if isinstance(entry, dict) and str(entry.get("n", "")).strip().isdigit():
            numbered[int(str(entry["n"]).strip())] = entry
    if numbered:
        matched = [numbered.get(i + 1) for i in range(count)]
    elif len(results) == count:
        matched = [entry if isinstance(entry, dict) else None for entry in results]
    else:
        matched = [None] * count
    got = sum(1 for entry in matched if entry)
    if got < count:
        print(f"  [!] {name} answered for {got} of {count} items ({len(results)} entries came back)")
    return matched if got else None


def ask_deepseek(prompt, count):
    """DeepSeek's OpenAI-compatible /chat/completions endpoint."""
    raw = post_json("DeepSeek", "https://api.deepseek.com/chat/completions",
                    {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, {
                        "model": DEEPSEEK_MODEL,
                        "max_tokens": 2000,
                        # the same headline should get the same label on every run
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}],
                    })
    try:
        return parse_results("DeepSeek", raw["choices"][0]["message"]["content"], count) if raw else None
    except (KeyError, IndexError, TypeError):
        print("  [!] DeepSeek returned no message")
        return None


def ask_gemini(prompt, count):
    """Google's Gemini API (generateContent), the fallback when DeepSeek
    doesn't answer."""
    raw = post_json("Gemini", f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                    {"x-goog-api-key": GEMINI_API_KEY}, {
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        # room for the reasoning tokens newer models spend
                        # before answering, so the JSON is never cut short
                        "generationConfig": {"temperature": 0, "maxOutputTokens": 8192,
                                             "responseMimeType": "application/json"},
                    })
    if not raw:
        return None
    try:
        text = "".join(part.get("text", "") for part in raw["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        text = ""
    if not text:
        candidate = (raw.get("candidates") or [{}])[0]
        reason = candidate.get("finishReason") or (raw.get("promptFeedback") or {}).get("blockReason") or "unknown"
        print(f"  [!] Gemini returned no text (reason: {reason})")
        return None
    return parse_results("Gemini", text, count)


LLM_PROVIDERS = {"DeepSeek": ask_deepseek, "Gemini": ask_gemini}


def active_providers():
    """Providers with a key, in fallback order; LLM_ONLY narrows it to one."""
    keys = {"DeepSeek": DEEPSEEK_API_KEY, "Gemini": GEMINI_API_KEY}
    return [name for name in LLM_PROVIDERS if keys[name] and LLM_ONLY in ("", name)]


def classify_batch_with_llm(batch_items, providers):
    """Asks each provider in `providers` (names from LLM_PROVIDERS) in turn
    until one answers. Returns (results, provider name) with one
    {"sentiment", "importance", "content_type"} or None per item in the same
    order, or (None, None) — the caller then keeps the earlier values."""
    prompt = build_prompt(batch_items)
    for name in providers:
        results = LLM_PROVIDERS[name](prompt, len(batch_items))
        if results is not None:
            return results, name
    return None, None


def label_key(item: dict) -> str:
    """The same headline across runs: its link, else the start of its title
    (as site_build.py keys the archive)."""
    return (item.get("link") or "").strip() or item["title"].lower()[:60]


def load_previous_labels(path: str = "news_data.js") -> dict:
    """The language-model labels from the last run's feed, by label_key.
    Most headlines stay in the feed for several runs; they keep these labels
    instead of being sent again, so a DeepSeek outage only touches headlines
    that are new in that run (and the archive behind the Mood Index isn't
    overwritten with keyword-rule labels)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (OSError, ValueError):
        return {}
    return {
        label_key(item): {field: item.get(field) for field in
                          ("sentiment", "importance", "content_type", "llm_classified", "llm_version", "llm_provider")}
        for item in data.get("items", []) if item.get("llm_classified")
    }


def classify_items_with_llm(items):
    """Labels items through classify_batch_with_llm in batches, updating
    sentiment/importance/content_type in place. Headlines labelled in the
    last run under the current prompt version keep those labels; the rest
    are sent. Items the LLM didn't answer for keep their last LLM labels if
    they have any, else the heuristic values."""
    global LLM_DEADLINE
    LLM_DEADLINE = time.monotonic() + LLM_RUN_BUDGET
    previous = load_previous_labels()
    pending = []
    for item in items:
        carried = previous.get(label_key(item))
        if carried:
            item.update(carried)
        if not carried or carried.get("llm_version") != LLM_PROMPT_VERSION or LLM_ONLY:
            pending.append(item)
    print(f"  Labels kept from the last run: {len(items) - len(pending)}; sending {len(pending)}")

    providers = active_providers()
    counts = {name: 0 for name in providers}
    failures_in_a_row = {name: 0 for name in providers}
    for start in range(0, len(pending), LLM_BATCH_SIZE):
        # a batch lost even after its retry means a provider is down right
        # now: the run stops asking it (DeepSeek's batches go straight to
        # Gemini), and with none left the next run picks the rest up
        for name in [n for n in providers if failures_in_a_row[n] >= 1]:
            print(f"  [!] {name} isn't answering — not asking it again this run")
            providers.remove(name)
        if not providers:
            break
        batch = pending[start:start + LLM_BATCH_SIZE]
        batch_input = [{"title": it["title"], "description": it["description"]} for it in batch]
        result, provider = classify_batch_with_llm(batch_input, providers)
        # every provider asked before the one that answered has failed
        for name in providers[:providers.index(provider) if provider else len(providers)]:
            failures_in_a_row[name] += 1
        if result is None:
            continue  # this batch keeps its earlier labels
        failures_in_a_row[provider] = 0

        for item, res in zip(batch, result):
            if not res:
                continue  # skipped by the model: keeps its earlier labels
            counts[provider] += 1
            sentiment = res.get("sentiment")
            importance = res.get("importance")
            content_type = res.get("content_type")
            if sentiment in ("positive", "negative", "neutral"):
                item["sentiment"] = sentiment
            if isinstance(importance, (int, float)):
                item["importance"] = round(float(importance), 1)
            if content_type in ("market_signal", "macro_context"):
                item["content_type"] = content_type
            item["llm_classified"] = True
            item["llm_version"] = LLM_PROMPT_VERSION
            item["llm_provider"] = provider

    labelled = sum(1 for item in items if item.get("llm_classified"))
    done = ", ".join(f"{name} {count}" for name, count in counts.items()) or "no provider"
    print(f"  Classified this run: {done} of {len(pending)}; "
          f"{labelled}/{len(items)} news items carry model labels (the rest use the local heuristic)")


def detect_watchlist_matches(text: str) -> list:
    """Looks in the text for both tickers and company names from
    COMPANY_MAP. Returns a list of unique {"ticker": ..., "sector": ...}.

    Rules to avoid false positives:
    1) Tickers 3+ characters long are matched case-sensitively (real text
       writes tickers in caps — NVDA, RIO, CAT). This keeps short tickers
       like "CAT" from being confused with the random word "cat" in
       ordinary text.
    2) Tickers 1-2 characters long (F, T, V, MA, GE, AA...) almost always
       coincide with regular words/letters/abbreviations — matching by the
       ticker itself is DISABLED for these, we only match by company name
       ("Ford", "AT&T", "Mastercard", etc).
    3) Company names are matched case-insensitively, since in headlines
       they can also appear at the start of a sentence.
    4) An entry with "match_ticker": False is found by name only — for a
       ticker that is also part of other names ("MGM" in "Amazon MGM
       Studios").
    5) An entry's "exclude" pattern is cut out of the text before matching
       it — for look-alikes such as Nvidia's "RTX PRO" cards vs RTX Corp.
    6) A name listed in an entry's "context" is a common word ("Strategy",
       MicroStrategy's name since 2025): it is matched case-sensitively and
       only when the text also matches its context pattern.
    Typographic apostrophes are made plain first: feeds write
    "McDonald’s", COMPANY_MAP writes "McDonald's".
    """
    text = text.replace("’", "'").replace("‘", "'")
    lower = text.lower()
    found = {}
    for entry, ticker_re, names, exclude in _compiled_company_map():
        ticker = entry["ticker"]
        # a plain substring check first: most companies aren't in a given headline
        if not ((ticker_re and ticker in text) or any(name in lower for name, _, _ in names)):
            continue
        entry_text = exclude.sub(" ", text) if exclude else text
        matched = bool(ticker_re and ticker_re.search(entry_text))
        if not matched:
            for _, name_re, context_re in names:
                if name_re.search(entry_text) and (context_re is None or context_re.search(entry_text)):
                    matched = True
                    break
        if matched:
            found[ticker] = entry["sector"]
    return [{"ticker": t, "sector": s} for t, s in found.items()]


_COMPILED_MAP = {"key": None, "entries": []}


def _compiled_company_map():
    """COMPANY_MAP with its patterns compiled once. Some 2,000 patterns
    overflow the re module's own cache, which then recompiled them for every
    headline; site_build.py re-tags a month of headlines on every run."""
    key = (id(COMPANY_MAP), len(COMPANY_MAP))
    if _COMPILED_MAP["key"] != key:
        entries = []
        for entry in COMPANY_MAP:
            ticker = entry["ticker"]
            ticker_re = (re.compile(r"\b" + re.escape(ticker) + r"\b")
                         if len(ticker) >= 3 and entry.get("match_ticker", True) else None)
            names = []
            for name in entry["names"]:
                context = entry.get("context", {}).get(name)
                names.append((name.lower(),
                              re.compile(r"\b" + re.escape(name) + r"\b", 0 if context else re.IGNORECASE),
                              re.compile(context) if context else None))
            exclude = re.compile(entry["exclude"]) if entry.get("exclude") else None
            entries.append((entry, ticker_re, names, exclude))
        _COMPILED_MAP.update(key=key, entries=entries)
    return _COMPILED_MAP["entries"]


# ---------------------------------------------------------------------------
# MAIN LOGIC
# ---------------------------------------------------------------------------

def parse_feed(feed_name: str, url: str) -> list:
    items_out = []
    try:
        raw = fetch_url(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[!] Failed to load {feed_name}: {e}")
        return items_out

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"[!] XML parsing error for {feed_name}: {e}")
        return items_out

    # RSS 2.0: channel/item ; Atom: entry
    channel = root.find("channel")
    entries = channel.findall("item") if channel is not None else root.findall("atom:entry", NS)

    for item in entries:
        title_el = item.find("title")
        title = strip_html(title_el.text) if title_el is not None and title_el.text else ""
        if not title:
            continue

        if is_advice_column(title) or is_market_report_ad(title) or is_templated_post(title, feed_name):
            continue

        link_el = item.find("link")
        link = ""
        if link_el is not None:
            link = link_el.text or link_el.get("href", "")

        desc_el = item.find("description")
        if desc_el is None:
            desc_el = item.find("content:encoded", NS)
        description = strip_html(desc_el.text) if desc_el is not None and desc_el.text else ""
        if len(description) > 240:
            description = description[:237].rsplit(" ", 1)[0] + "…"

        pub_el = item.find("pubDate")
        if pub_el is None:
            pub_el = item.find("atom:published", NS)
        pub_dt = parse_pubdate(pub_el.text) if pub_el is not None and pub_el.text else None

        image = find_image(item)
        full_text = f"{title} {description}"
        matches = detect_watchlist_matches(full_text)
        tickers = [m["ticker"] for m in matches]

        items_out.append({
            "title": title,
            "link": link.strip() if link else "",
            "description": description,
            "source": feed_name,
            "image": image,
            "published": pub_dt.isoformat() if pub_dt else None,
            "published_display": pub_dt.strftime("%d.%m %H:%M") if pub_dt else "—",
            "sentiment": detect_sentiment(full_text),
            "tickers": tickers,
            "sectors": sorted(set(m["sector"] for m in matches)),
            "importance": calc_importance(title, description, tickers, pub_dt),
            "content_type": "macro_context" if is_macro_context(full_text, tickers) else "market_signal",
            "llm_classified": False,
        })

    return items_out


def build_summary(all_items: list) -> dict:
    # Sentiment is computed ONLY over news with a specific market "target"
    # (content_type == market_signal). Macro/geopolitical news is
    # background, not an investment signal, and shouldn't skew the overall
    # market mood (otherwise a news item about sanctions against someone
    # could drag "Quick Analysis" into negative territory even though it
    # isn't really a signal about any specific stock).
    signal_items = [i for i in all_items if i.get("content_type", "market_signal") == "market_signal"]
    macro_items = [i for i in all_items if i.get("content_type") == "macro_context"]

    pos = sum(1 for i in signal_items if i["sentiment"] == "positive")
    neg = sum(1 for i in signal_items if i["sentiment"] == "negative")
    neu = sum(1 for i in signal_items if i["sentiment"] == "neutral")

    ticker_counts = {}
    for i in all_items:
        for t in i["tickers"]:
            ticker_counts[t] = ticker_counts.get(t, 0) + 1
    top_tickers = sorted(ticker_counts.items(), key=lambda x: -x[1])[:6]

    sector_counts = {}
    for i in all_items:
        for sec in i.get("sectors", []):
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

    source_counts = {}
    for i in all_items:
        source_counts[i["source"]] = source_counts.get(i["source"], 0) + 1

    return {
        "total": len(all_items),
        "signal_total": len(signal_items),
        "macro_total": len(macro_items),
        "positive": pos,
        "negative": neg,
        "neutral": neu,
        "top_tickers": [{"ticker": t, "count": c} for t, c in top_tickers],
        "sectors": sorted(sector_counts.keys()),
        "source_counts": source_counts,
    }


# ---------------------------------------------------------------------------
# MAJOR INDEX QUOTES
# ---------------------------------------------------------------------------

# An unofficial but widely-used-in-open-source endpoint for Yahoo Finance —
# no key needed, but it's an UNofficial API: Yahoo can change it at any
# time without notice. So this whole section is wrapped in try/except with
# a silent skip — if the response format changes or the endpoint becomes
# unavailable, the dashboard simply won't show the quotes block, and the
# rest of the script keeps working as usual.
INDEX_QUOTES = [
    # --- US indices ---
    {"symbol": "^GSPC", "name": "S&P 500"},
    {"symbol": "^DJI",  "name": "Dow Jones"},
    {"symbol": "^IXIC", "name": "Nasdaq Composite"},
    {"symbol": "^RUT",  "name": "Russell 2000"},

    # --- International indices (the site targets a global audience) ---
    {"symbol": "^FTSE",     "name": "FTSE 100 (UK)"},
    {"symbol": "^GDAXI",    "name": "DAX (Germany)"},
    {"symbol": "^STOXX50E", "name": "Euro Stoxx 50"},
    {"symbol": "^N225",     "name": "Nikkei 225 (Japan)"},
    {"symbol": "^HSI",      "name": "Hang Seng (Hong Kong)"},

    # --- Commodities, crypto, bonds ---
    {"symbol": "GC=F",    "name": "Gold (futures)"},
    {"symbol": "CL=F",    "name": "WTI Crude Oil (futures)"},
    {"symbol": "BTC-USD", "name": "Bitcoin"},
    {"symbol": "^TNX",    "name": "10-Year US Treasury Yield", "unit": "yield_pct", "scale": 0.1},

    # --- VIX last: it's the odd one out (inverted color scale on the
    # dashboard — see --vix-hot/--vix-cool in news_dashboard.html), keeping
    # it away from the other US indices avoids it being visually mistaken
    # for a regular green/red quote at a glance. ---
    {"symbol": "^VIX",  "name": "VIX (Fear Index)"},
]


def fetch_index_quote(symbol: str, scale: float = 1.0):
    """Returns {"price", "change_abs", "change_pct", "spark"} for an index
    ticker, or None on any error (network, unexpected response format,
    etc). "spark" is a short list of intraday prices for a sparkline chart
    on the dashboard (empty list if Yahoo didn't return any).

    scale: Yahoo stores bond yields (^TNX) scaled ×10 from the real rate
    (42.85 instead of 4.285%) — for such tickers we pass scale=0.1 to show
    the real value. This doesn't affect change_pct, since that's a ratio
    and doesn't depend on scale.
    """
    # range=1d used to be enough (~26 15-min points for a regular US
    # session), but it's unreliable for some symbols: ^RUT (Russell 2000)
    # returns a completely empty series with range=1d regardless of
    # interval, and every US index returns only 1-2 points in the first
    # hour or so of its own session (today's candles simply haven't
    # accumulated yet) — both show up as a broken-looking sparkline.
    # range=5d is reliable for every symbol tested and always has plenty
    # of history; we just keep the most recent points below.
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=15m"
    try:
        raw = fetch_url(url, timeout=10)
        data = json.loads(raw)
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None or prev_close in (None, 0):
            return None
        change_abs = price - prev_close
        change_pct = (change_abs / prev_close) * 100
        # Yahoo now often quotes ^TNX as the yield itself (5.3, not 53), and
        # scaling that gave "0.53%" on the dashboard (Oct 2026). A 10-year
        # yield above 20% isn't real, so only a value that high is ×10.
        if scale != 1.0 and abs(price) < 20:
            scale = 1.0

        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
        # Most recent ~20 candles, not an even spread across all 5 days —
        # early in a session this naturally blends in yesterday's last
        # few candles instead of showing a stub 1-2-point line.
        spark = [round(c * scale, 4) for c in closes if c is not None][-20:]

        return {
            "price": round(price * scale, 2),
            "change_abs": round(change_abs * scale, 2),
            "change_pct": round(change_pct, 2),
            "spark": spark,
        }
    except Exception as e:
        print(f"  [!] Failed to get a quote for {symbol}: {e}")
        return None


def fetch_all_indices() -> list:
    print("\nFetching major index quotes...")
    results = []
    for item in INDEX_QUOTES:
        quote = fetch_index_quote(item["symbol"], scale=item.get("scale", 1.0))
        if quote:
            results.append({
                "name": item["name"],
                "symbol": item["symbol"],
                "unit": item.get("unit", "index"),
                **quote,
            })
        else:
            print(f"  skipping {item['name']} — data unavailable")
    print(f"  Quotes fetched: {len(results)}/{len(INDEX_QUOTES)}")
    return results


def write_watchlist_candidates(items: list, path: str = "watchlist_candidates.txt") -> None:
    """Writes a short, human-readable list of market-signal headlines that
    didn't match any ticker/company in COMPANY_MAP — quick candidates to
    review for watchlist additions. Overwritten on every run (like
    news_data.js), so it always reflects only the latest fetch; check it
    whenever, no need to read every headline yourself."""
    candidates = [
        i for i in items
        if i.get("content_type", "market_signal") == "market_signal" and not i.get("tickers")
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("Watchlist candidates\n")
        f.write("=====================\n\n")
        f.write(
            "Market-signal headlines from the latest run that didn't match any\n"
            "ticker/company in COMPANY_MAP (fetch_news.py). If a name keeps\n"
            "showing up here across multiple runs, it's probably worth adding —\n"
            "just ask, or add it yourself as one line in COMPANY_MAP.\n\n"
        )
        f.write(f"Generated: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        f.write(f"{len(candidates)} of {len(items)} news items unmatched this run\n\n")
        if not candidates:
            f.write("(none this run)\n")
        for i in candidates:
            f.write(f"- [{i['source']}] {i['title']}\n")


def main():
    print("Fetching news...")
    all_items = []
    for feed in FEEDS:
        print(f"  → {feed['name']}")
        items = parse_feed(feed["name"], feed["url"])
        print(f"    got: {len(items)}")
        all_items.extend(items)

    # sort by date (newest first), items with no date go last
    all_items.sort(
        key=lambda i: i["published"] or "0000-00-00T00:00:00",
        reverse=True,
    )

    # simple dedup by the first words of the title
    seen = set()
    deduped = []
    for i in all_items:
        key = i["title"].lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(i)

    deduped = deduped[:MAX_ITEMS]

    if active_providers():
        print(f"\nRefining sentiment and importance via {' then '.join(active_providers())}...")
        classify_items_with_llm(deduped)
    else:
        print("\nNo DEEPSEEK_API_KEY or GEMINI_API_KEY set — using the local keyword heuristic.")
        print("(See README.md for details on enabling LLM classification.)")

    write_watchlist_candidates(deduped)

    summary = build_summary(deduped)
    indices = fetch_all_indices()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_display": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "summary": summary,
        "indices": indices,
        "items": deduped,
    }

    out_path = "news_data.js"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("// This file is auto-generated by fetch_news.py — do not edit by hand\n")
        f.write("const NEWS_DATA = ")
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"\nDone! News collected: {len(deduped)}")
    print(f"Data saved to {out_path}")
    print("Watchlist candidates (untagged headlines) saved to watchlist_candidates.txt")
    print("Open (or refresh) news_dashboard.html in your browser.")


if __name__ == "__main__":
    main()
