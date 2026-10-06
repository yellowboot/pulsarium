"""Market movers for the news dashboard: the day's biggest gainers, losers
and most traded shares among the US-listed companies the site follows.

Prices are Marketstack end-of-day closes (the cabinet's provider; its paid
plan allows showing them to users), read once per trading day: about 600
symbols in batches of 100, so a refresh costs ~6 requests of the plan's
10,000 a month. The result is kept in data/movers.json and only re-read when
a newer session should exist, at most every few hours while it doesn't.

Without MARKETSTACK_API_KEY (local runs) the stored file is used as it is.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import fetch_news

EOD_URL = "https://api.marketstack.com/v1/eod"
USER_AGENT = "Pulsarium/1.0 (+https://pulsarium.finance)"
STORE = "data/movers.json"
BATCH = 100              # symbols per Marketstack request
SHOWN = 8                # rows per list
RETRY_HOURS = 3          # wait this long before asking again for a session not there yet
# A bigger one-day move among these large companies is nearly always a data
# slip (a share-ratio change the closes don't reflect), not a market move:
# América Movil showed -98% on 2026-10-02.
MAX_MOVE_PCT = 50
READY_UTC = (22, 30)     # a US session's closes are read after this time (UTC)

# Not US-listed shares: market-wide entries, coins, and listings abroad that
# the site tracks by name. In the hand-made part of the list, OTC foreign
# shares and ADRs (five letters ending in F or Y) are left out too; the SEC
# part (entries with a "label") is all Nasdaq/NYSE listings.
NOT_SHARES = {"BTC", "ETH", "XMR", "OPEC", "FED", "UST10Y", "RHM", "GLEN", "LUG"}
NOT_SHARE_SECTORS = {"ETFs / Indices", "Bonds / Macro"}


def tradable(entry: dict) -> bool:
    ticker = entry["ticker"]
    otc = "label" not in entry and re.fullmatch(r"[A-Z]{4}[FY]", ticker) is not None
    return (entry["sector"] not in NOT_SHARE_SECTORS
            and ticker not in NOT_SHARES
            and re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", ticker) is not None
            and not otc)


def companies() -> dict:
    """ticker -> {name, slug} for every share the site follows."""
    found = {}
    for entry in fetch_news.COMPANY_MAP:
        if tradable(entry):
            name = entry.get("label") or (entry["names"][0] if entry["names"] else entry["ticker"])
            found[entry["ticker"]] = {"name": name, "slug": re.sub(r"[^a-z0-9]+", "-", entry["ticker"].lower()).strip("-")}
    return found


def expected_session(now: datetime) -> str:
    """The latest US weekday whose closes should be readable by now."""
    day = now.date()
    if (now.hour, now.minute) < READY_UTC:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def read_closes(key: str, symbols: list, since: str) -> dict:
    """symbol -> its rows since `since`, newest first."""
    rows = {}
    for start in range(0, len(symbols), BATCH):
        query = urllib.parse.urlencode({
            "access_key": key, "symbols": ",".join(symbols[start:start + BATCH]),
            "date_from": since, "limit": 1000,
        })
        # Python's default "Python-urllib" agent gets a bare 403 in front of the API
        request = urllib.request.Request(f"{EOD_URL}?{query}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        for row in payload.get("data") or []:
            rows.setdefault(row.get("symbol"), []).append(row)
    for symbol in rows:
        rows[symbol].sort(key=lambda row: str(row.get("date")), reverse=True)
    return rows


def build(rows: dict, names: dict) -> dict:
    """Gainers, losers and most traded on the newest session most symbols share."""
    latest = {}
    for symbol, history in rows.items():
        if history:
            latest[symbol] = str(history[0].get("date"))[:10]
    if not latest:
        return {}
    dates = sorted(latest.values())
    session = max(set(dates), key=lambda d: (dates.count(d), d))
    moves = []
    for symbol, history in rows.items():
        if symbol not in names or latest.get(symbol) != session or len(history) < 2:
            continue
        close = history[0].get("close")
        # the change from split-adjusted closes where Marketstack has them
        now_adj = history[0].get("adj_close") or close
        then_adj = history[1].get("adj_close") or history[1].get("close")
        if not close or not now_adj or not then_adj or close <= 0 or now_adj <= 0 or then_adj <= 0:
            continue
        change = (float(now_adj) / float(then_adj) - 1) * 100
        if abs(change) > MAX_MOVE_PCT:
            continue
        moves.append({
            "ticker": symbol, "name": names[symbol]["name"], "slug": names[symbol]["slug"],
            "close": round(float(close), 2),
            "change_pct": round(change, 2),
            "volume": int(history[0].get("volume") or 0),
        })
    gainers = sorted((m for m in moves if m["change_pct"] > 0), key=lambda m: -m["change_pct"])
    losers = sorted((m for m in moves if m["change_pct"] < 0), key=lambda m: m["change_pct"])
    active = sorted(moves, key=lambda m: -m["volume"])
    return {"session": session, "count": len(moves),
            "gainers": gainers[:SHOWN], "losers": losers[:SHOWN], "active": active[:SHOWN]}


def load(path: str = STORE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh(path: str = STORE, now: datetime | None = None) -> dict:
    """The stored movers, re-read from Marketstack when a newer session is due."""
    now = now or datetime.now(timezone.utc)
    stored = load(path)
    key = os.environ.get("MARKETSTACK_API_KEY", "").strip()
    due = expected_session(now)
    if not key or stored.get("session", "") >= due:
        return stored
    attempted = stored.get("attempted_at")
    if attempted and now - datetime.fromisoformat(attempted) < timedelta(hours=RETRY_HOURS):
        return stored

    names = companies()
    since = (now.date() - timedelta(days=10)).isoformat()
    # A failed read isn't recorded as an attempt: the next run asks again.
    try:
        fresh = build(read_closes(key, sorted(names), since), names)
    except urllib.error.HTTPError as error:
        try:
            problem = json.load(error).get("error") or {}
        except ValueError:
            problem = {}
        print(f"Market movers: Marketstack answered HTTP {error.code} "
              f"{problem.get('code', '')}: {problem.get('message', '')}; keeping the stored ones")
        return stored
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"Market movers: Marketstack read failed ({type(error).__name__}); keeping the stored ones")
        return stored
    result = fresh if fresh.get("session", "") > stored.get("session", "") else stored
    result = {**result, "attempted_at": now.isoformat()}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"Market movers: session {result.get('session', '—')}, {result.get('count', 0)} shares")
    return result


def public(movers: dict) -> dict:
    """What the dashboard shows (no bookkeeping fields)."""
    return {k: movers[k] for k in ("session", "count", "gainers", "losers", "active") if k in movers}
