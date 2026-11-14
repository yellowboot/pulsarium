"""Market movers for the news dashboard: the day's biggest gainers, losers
and most traded shares among the US-listed companies the site follows.

Prices are end-of-day closes read once per trading day from Yahoo Finance
(no paid API key required). The result is kept in data/movers.json and only
re-read when a newer session should exist, at most every few hours while it
doesn't.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import fetch_news

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
STORE = "data/movers.json"
SHOWN = 8                # rows per list
RETRY_HOURS = 3          # wait this long before asking again for a session not there yet
MAX_MOVE_PCT = 50
READY_UTC = (22, 30)     # a US session's closes are read after this time (UTC)

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


def _fetch_symbol_closes(symbol: str) -> tuple[str, list[dict]]:
    yh_sym = symbol.replace(".", "-")
    for host in ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"):
        url = f"{host}/v8/finance/chart/{urllib.parse.quote(yh_sym)}?range=10d&interval=1d"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = json.load(response)
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                continue
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            history = []
            for ts, close, vol in zip(timestamps, closes, volumes):
                if isinstance(close, (int, float)) and math.isfinite(close) and close > 0:
                    day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
                    history.append({
                        "symbol": symbol,
                        "date": day,
                        "close": float(close),
                        "adj_close": float(close),
                        "volume": int(vol) if isinstance(vol, (int, float)) and vol >= 0 else 0,
                    })
            history.sort(key=lambda r: r["date"], reverse=True)
            return symbol, history
        except Exception:
            continue
    return symbol, []


def read_closes(symbols: list, since: str = "") -> dict:
    """symbol -> its recent daily rows, newest first."""
    rows = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, history in pool.map(_fetch_symbol_closes, symbols):
            if history:
                rows[sym] = [r for r in history if not since or r["date"] >= since]
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
        now_adj = history[0].get("adj_close") or close
        then_adj = history[1].get("adj_close") or history[1].get("close")
        if not close or not now_adj or not then_adj or close <= 0 or now_adj <= 0 or then_adj <= 0:
            continue
        change = (float(now_adj) / float(then_adj) - 1) * 100
        if abs(change) > MAX_MOVE_PCT:
            continue
        slug = names[symbol]["slug"]
        moves.append({
            "ticker": symbol, "name": names[symbol]["name"], "slug": slug,
            "page": os.path.exists(os.path.join("news", slug, "index.html")),
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
    """The stored movers, re-read when a newer session is due."""
    now = now or datetime.now(timezone.utc)
    stored = load(path)
    due = expected_session(now)
    if stored.get("session", "") >= due:
        return stored
    attempted = stored.get("attempted_at")
    if attempted and now - datetime.fromisoformat(attempted) < timedelta(hours=RETRY_HOURS):
        return stored

    names = companies()
    since = (now.date() - timedelta(days=10)).isoformat()
    try:
        fresh = build(read_closes(sorted(names), since), names)
    except Exception as error:
        print(f"Market movers: read failed ({type(error).__name__}); keeping the stored ones")
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
