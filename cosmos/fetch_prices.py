"""Daily adjusted closes for the cosmos backtest, from Marketstack v2.

The universe is the US-listed shares the site follows (the same list
market_movers.py prices every day, about 630 symbols).

One request per symbol: limit=1000 covers up to four years of sessions, so
three years of history cost one request per symbol however Marketstack counts
multi-symbol calls (some code here counts a 100-symbol call as one request,
some as 100). A full first run is about 630 requests; a re-run asks only for
symbols not saved yet (raw answers are kept in cosmos/data/raw/).

A one-day move above 50% among these large companies is nearly always a
data slip (a share-ratio change the closes don't reflect), so a symbol with
one is left out of prices.csv and named in the log.

Usage:
  MARKETSTACK_API_KEY=... python cosmos/fetch_prices.py [--years 3] [--max-requests 700] [--refresh]

Writes cosmos/data/prices.csv (date,symbol,adj_close,volume) and
cosmos/data/universe.csv (symbol,sector,name). Both stay out of git: the
repository is public and raw prices must not be republished.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fetch_news      # noqa: E402  (ticker -> sector map)
import market_movers   # noqa: E402  (the followed universe)

EOD_URL = "https://api.marketstack.com/v2/eod"
DATA = HERE / "data"
RAW = DATA / "raw"
PRICES = DATA / "prices.csv"
UNIVERSE = DATA / "universe.csv"
MAX_MOVE = 0.50
PAUSE = 0.3  # seconds between requests: Marketstack refuses bursts ("maximum rate limitation")


def eod_rows(data) -> list[dict]:
    """Price rows from any nesting Marketstack uses (list, list of lists, {"eod": [...]})."""
    if isinstance(data, dict):
        if "symbol" in data:
            return [data]
        return [row for value in data.values() for row in eod_rows(value)]
    if isinstance(data, list):
        return [row for value in data for row in eod_rows(value)]
    return []


def fetch_symbol(key: str, symbol: str, since: str, tries: int = 3) -> list[dict]:
    query = urllib.parse.urlencode({"access_key": key, "symbols": symbol, "date_from": since, "limit": 1000})
    request = urllib.request.Request(f"{EOD_URL}?{query}", headers={"User-Agent": market_movers.USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503) and attempt + 1 < tries:
                time.sleep(2 ** attempt * 2)
                continue
            raise
        if isinstance(payload, dict) and payload.get("error"):
            message = json.dumps(payload["error"])[:300]
            if "rate" in message.lower() and attempt + 1 < tries:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"Marketstack, {symbol}: {message}")
        total = (payload.get("pagination") or {}).get("total") if isinstance(payload, dict) else None
        if total and int(total) > 1000:
            print(f"  {symbol}: {total} rows, only the newest 1000 kept", flush=True)
        return eod_rows(payload.get("data") if isinstance(payload, dict) else payload)
    return []


def clean_series(rows: list[dict]) -> tuple[list[tuple[str, float, float | None]], str | None]:
    """(date, adj_close, volume) oldest first, or a reason to leave the symbol out."""
    series = {}
    for row in rows:
        close = row.get("adj_close") or row.get("close")
        day = str(row.get("date", ""))[:10]
        if close and day:
            series[day] = (float(close), float(row["volume"]) if row.get("volume") else None)
    out = [(d, c, v) for d, (c, v) in sorted(series.items())]
    for (d0, c0, _), (d1, c1, _) in zip(out, out[1:]):
        if c0 > 0 and abs(c1 / c0 - 1) > MAX_MOVE:
            return out, f"{c1 / c0 - 1:+.0%} on {d1}"
    return out, None


def assemble(raw_dir: Path = RAW, prices: Path = PRICES) -> dict:
    """prices.csv from the saved raw answers; symbols with a data slip are left out."""
    written, dropped = 0, {}
    prices.parent.mkdir(parents=True, exist_ok=True)
    with open(prices, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["date", "symbol", "adj_close", "volume"])
        for path in sorted(raw_dir.glob("*.json")):
            symbol = path.stem
            series, slip = clean_series(json.loads(path.read_text()))
            if slip:
                dropped[symbol] = slip
                continue
            if not series:
                dropped[symbol] = "no rows"
                continue
            for day, close, volume in series:
                out.writerow([day, symbol, f"{close:.6f}", "" if volume is None else f"{volume:.0f}"])
            written += 1
    for symbol, why in dropped.items():
        print(f"  left out {symbol}: {why}", flush=True)
    return {"symbols": written, "dropped": dropped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download daily adjusted closes for the cosmos backtest.")
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--max-requests", type=int, default=700, help="stop after this many API requests")
    ap.add_argument("--refresh", action="store_true", help="ask again for symbols already saved")
    args = ap.parse_args(argv)
    key = os.environ.get("MARKETSTACK_API_KEY")
    if not key:
        print("MARKETSTACK_API_KEY is not set", file=sys.stderr)
        return 2
    names = market_movers.companies()
    sector = {entry["ticker"]: entry["sector"] for entry in fetch_news.COMPANY_MAP}
    DATA.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["symbol", "sector", "name"])
        for symbol in sorted(names):
            out.writerow([symbol, sector.get(symbol, ""), names[symbol]["name"]])
    since = (date.today() - timedelta(days=round(365.25 * args.years))).isoformat()
    asked = 0
    for symbol in sorted(names):
        path = RAW / f"{symbol}.json"
        if path.exists() and not args.refresh:
            continue
        if asked >= args.max_requests:
            print(f"Request cap {args.max_requests} reached; run again to continue", flush=True)
            break
        rows = fetch_symbol(key, symbol, since)
        asked += 1
        path.write_text(json.dumps(rows))
        if asked == 1 and not rows:
            print(f"First answer for {symbol} held no rows; check the key and plan before spending more requests", file=sys.stderr)
            return 1
        time.sleep(PAUSE)
    summary = assemble()
    print(f"Requests used: {asked}. Symbols in prices.csv: {summary['symbols']}, left out: {len(summary['dropped'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
