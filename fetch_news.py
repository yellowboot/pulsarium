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
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from html import unescape

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
# For automatic updates via GitHub Actions, the key must live ONLY in the
# repo's encrypted GitHub Secrets (Settings → Secrets and variables →
# Actions), never in code or workflow logs — details and instructions on
# setting the secret without showing it to anyone are in README.md.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = "deepseek-chat"  # DeepSeek-V3 — cheap, plenty for classification
LLM_BATCH_SIZE = 15  # how many news items to send per API request

# Public financial news RSS feeds (no subscription, no headline-level paywall)
FEEDS = [
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "MarketWatch",   "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news_25.rss"},
    {"name": "CNBC",          "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"name": "Nasdaq",        "url": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks"},
    {"name": "OilPrice.com",  "url": "https://oilprice.com/rss/main"},
    {"name": "Mining.com",    "url": "https://www.mining.com/feed"},
    {"name": "Defense One",   "url": "https://www.defenseone.com/rss/all/"},
    {"name": "FiercePharma",  "url": "https://www.fiercepharma.com/rss/xml"},
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
    {"ticker": "AMZN", "sector": "Big Tech",               "names": ["Amazon"]},
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
    {"ticker": "PARA", "sector": "Media / Entertainment",    "names": ["Paramount Global"]},
    {"ticker": "WBD",  "sector": "Media / Entertainment",    "names": ["Warner Bros Discovery", "Warner Bros. Discovery"]},

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
    {"ticker": "GOLD", "sector": "Metals & Mining",       "names": ["Barrick Gold"]},
    {"ticker": "LUG",  "sector": "Metals & Mining",       "names": ["Lundin Gold"]},

    # ---- Defense / Space ----
    {"ticker": "RHM",  "sector": "Defense",       "names": ["Rheinmetall"]},
    {"ticker": "LMT",  "sector": "Defense",       "names": ["Lockheed Martin"]},
    {"ticker": "BA",   "sector": "Defense",       "names": ["Boeing"]},
    {"ticker": "NOC",  "sector": "Defense",       "names": ["Northrop Grumman"]},
    {"ticker": "RTX",  "sector": "Defense",       "names": ["RTX", "Raytheon"]},
    {"ticker": "LHX",  "sector": "Defense",       "names": ["L3Harris"]},
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
    {"ticker": "APO",  "sector": "Banking & Finance",        "names": ["Apollo Global Management"]},
    {"ticker": "V",    "sector": "Payments / Fintech",       "names": ["Visa"]},
    {"ticker": "MA",   "sector": "Payments / Fintech",       "names": ["Mastercard"]},
    {"ticker": "PYPL", "sector": "Payments / Fintech",       "names": ["PayPal"]},
    {"ticker": "HOOD", "sector": "Payments / Fintech",       "names": ["Robinhood"]},

    # ---- Healthcare / pharma / biotech ----
    {"ticker": "PFE",  "sector": "Healthcare",        "names": ["Pfizer"]},
    {"ticker": "JNJ",  "sector": "Healthcare",        "names": ["Johnson & Johnson"]},
    {"ticker": "LLY",  "sector": "Healthcare",        "names": ["Eli Lilly"]},
    {"ticker": "MRK",  "sector": "Healthcare",        "names": ["Merck"]},
    {"ticker": "UNH",  "sector": "Healthcare",        "names": ["UnitedHealth"]},
    {"ticker": "MRNA", "sector": "Healthcare",        "names": ["Moderna"]},
    {"ticker": "GILD", "sector": "Healthcare",        "names": ["Gilead Sciences", "Gilead"]},
    {"ticker": "BSX",  "sector": "Healthcare",        "names": ["Boston Scientific"]},
    {"ticker": "RVMD", "sector": "Healthcare",        "names": ["Revolution Medicines"]},
    {"ticker": "AZN",  "sector": "Healthcare",        "names": ["AstraZeneca"]},
    {"ticker": "NVS",  "sector": "Healthcare",        "names": ["Novartis"]},
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
    {"ticker": "TCOM", "sector": "Airlines / Travel", "names": ["Trip.com"]},
    {"ticker": "AAL",  "sector": "Airlines / Travel", "names": ["American Airlines"]},

    # ---- Real estate / homebuilders ----
    {"ticker": "LEN",  "sector": "Real Estate / Homebuilders", "names": ["Lennar"]},

    # ---- Crypto ----
    {"ticker": "BTC",  "sector": "Cryptocurrencies",           "names": ["Bitcoin"]},
    {"ticker": "ETH",  "sector": "Cryptocurrencies",           "names": ["Ethereum"]},
    {"ticker": "COIN", "sector": "Cryptocurrencies",           "names": ["Coinbase"]},
    {"ticker": "MSTR", "sector": "Cryptocurrencies",           "names": ["MicroStrategy", "Strategy"]},
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

def classify_batch_with_llm(batch_items):
    """Sends a batch of news items to the DeepSeek API (OpenAI-compatible
    /chat/completions endpoint) and asks it to honestly (with real context
    understanding) determine sentiment, importance, and content type.

    batch_items: a list of {"title": ..., "description": ...}
    Returns a list of {"sentiment": ..., "importance": ..., "content_type": ...}
    in the same order, or None on any error (network, rate limits,
    unexpected format) — the caller then just keeps the heuristic values.
    """
    if not DEEPSEEK_API_KEY:
        return None

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
        "item above IN THE SAME ORDER:\n"
        '{"results": [{"content_type": "market_signal", "sentiment": "positive", "importance": 42}, ...]}'
    )

    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  [!] Error calling the DeepSeek API: {e}")
        return None

    try:
        text = raw["choices"][0]["message"]["content"].strip()
        # in case the model wrapped the JSON in ```json ... ``` anyway
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        parsed = json.loads(text)
        results = parsed.get("results") if isinstance(parsed, dict) else parsed
        if isinstance(results, list) and len(results) == len(batch_items):
            return results
        print("  [!] LLM returned an unexpected response format — using the heuristic for this batch")
        return None
    except Exception as e:
        print(f"  [!] Failed to parse the DeepSeek API response: {e}")
        return None


def classify_items_with_llm(items):
    """Runs items through classify_batch_with_llm in batches, updating
    sentiment/importance/content_type in place. News items the LLM didn't
    answer for (a whole batch dropped due to network/rate limits) keep
    their heuristic values."""
    classified = 0
    for start in range(0, len(items), LLM_BATCH_SIZE):
        batch = items[start:start + LLM_BATCH_SIZE]
        batch_input = [{"title": it["title"], "description": it["description"]} for it in batch]
        result = classify_batch_with_llm(batch_input)
        if result is None:
            continue  # this batch stays on the heuristic

        for item, res in zip(batch, result):
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
        classified += len(batch)

    print(f"  Classified via DeepSeek: {classified}/{len(items)} news items "
          f"(the rest use the local heuristic)")


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
    """
    found = {}
    for entry in COMPANY_MAP:
        ticker = entry["ticker"]
        matched = False

        if len(ticker) >= 3 and re.search(r"\b" + re.escape(ticker) + r"\b", text):
            matched = True

        if not matched:
            for name in entry["names"]:
                if re.search(r"\b" + re.escape(name) + r"\b", text, flags=re.IGNORECASE):
                    matched = True
                    break

        if matched:
            found[ticker] = entry["sector"]
    return [{"ticker": t, "sector": s} for t, s in found.items()]


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

        if is_advice_column(title):
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

    if DEEPSEEK_API_KEY:
        print(f"\nAPI key found — refining sentiment and importance via DeepSeek ({DEEPSEEK_MODEL})...")
        classify_items_with_llm(deduped)
    else:
        print("\nDEEPSEEK_API_KEY not set — using the local keyword heuristic.")
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
