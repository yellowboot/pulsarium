#!/usr/bin/env python3
"""Build one shared industry snapshot from permanently cached SEC XBRL frames.

Historical frames and issuer classifications are retained. Recent reporting
windows and the ticker directory have a refresh TTL. Visitor requests
never trigger SEC requests. No market-price API or portfolio data is used.
"""
import argparse
import base64
import calendar
import csv
import datetime as dt
import gzip
import io
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
import sys
import sec_financial_signals
from sec_read_cache import ReadCache, FRAME_CACHE, INSIDER_CACHE, INSIDER_SELECT, insider_row

REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax",
           "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
           "RevenuesNetOfInterestExpense"]
CFO = ["NetCashProvidedByUsedInOperatingActivities",
       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
LABELS = {
    "semiconductors": "Semiconductors", "software": "Software",
    "hardware": "Technology hardware", "pharmaceuticals": "Pharmaceuticals",
    "healthcare": "Healthcare", "chemicals": "Chemicals", "automotive": "Automotive",
    "transportation": "Transportation", "utilities": "Utilities", "energy": "Energy",
    "banks": "Banking & finance", "insurance": "Insurance", "real-estate": "Real estate",
    "retail": "Retail", "consumer-staples": "Consumer staples",
    "consumer-products": "Consumer products", "media-telecom": "Media & telecom",
    "industrials": "Industrials", "business-services": "Business services",
}
MIN_SAMPLE = 10
LOW_REVENUE_BASE = 1_000_000  # Prior-year quarterly revenue, USD; context only.
REFRESH_HOURS = 6
MAX_REPORT_AGE = 200


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def industry(sic):
    try:
        s = int(sic)
    except (ValueError, TypeError):
        return None
    if s == 3674: return "semiconductors"
    if 7370 <= s <= 7379: return "software"
    if 3570 <= s <= 3579 or 3650 <= s <= 3699: return "hardware"
    if 2830 <= s <= 2839: return "pharmaceuticals"
    if 3840 <= s <= 3859 or 8000 <= s <= 8099 or s in (6324, 8731): return "healthcare"
    if 2840 <= s <= 2844 or 2000 <= s <= 2199: return "consumer-staples"
    if 2800 <= s <= 2899: return "chemicals"
    if 3710 <= s <= 3719 or s in (3751,): return "automotive"
    if (1200 <= s <= 1399 or 2900 <= s <= 2999 or s in (4922, 4923, 5171, 5172, 6792)):
        return "energy"
    if 4900 <= s <= 4999: return "utilities"
    if 4000 <= s <= 4799: return "transportation"
    if 6000 <= s <= 6299: return "banks"
    if 6300 <= s <= 6499: return "insurance"
    if 6500 <= s <= 6599 or s == 6798: return "real-estate"
    if 5200 <= s <= 5999: return "retail"
    if (2700 <= s <= 2749 or 4800 <= s <= 4899 or 7310 <= s <= 7319 or 7800 <= s <= 7899):
        return "media-telecom"
    if 2200 <= s <= 2599 or 3100 <= s <= 3199 or 3900 <= s <= 3999: return "consumer-products"
    if 1000 <= s <= 1199 or 1400 <= s <= 1799 or 2600 <= s <= 3999: return "industrials"
    if 5000 <= s <= 5199 or 7000 <= s <= 8999: return "business-services"
    return None


def reporting_window(today):
    return today.year, (today.month - 1) // 3 + 1, today.year - 1


def quarter_frames(today, count=13):
    at = today.year * 4 + (today.month - 1) // 3
    return [f"CY{n // 4}Q{n % 4 + 1}" for n in range(at - count + 1, at + 1)]


def closed_quarter(today):
    at = today.year * 4 + (today.month - 1) // 3 - 1
    return f"CY{at // 4}Q{at % 4 + 1}"


def pack(data):
    return base64.b64encode(gzip.compress(json.dumps(data, separators=(",", ":"), allow_nan=False).encode(), mtime=0)).decode()


def unpack(value):
    return json.loads(gzip.decompress(base64.b64decode(value)))


def listing_symbol(symbol):
    return str(symbol or "").strip().upper().replace("$", "-P").replace("^", "-P").replace("/", "-").replace(".", "-")


def listing_kind(row):
    """Use exchange security descriptions and flags, not issuer-name guesses."""
    name = " ".join(row.get("Security Name", "").lower().split())
    if row.get("Test Issue") == "Y": return "test"
    if row.get("ETF") == "Y" or row.get("NextShares") == "Y": return "fund"
    if re.search(r"\b(?:etf|etn)\b|exchange.traded (?:fund|note)|closed.end fund", name): return "fund"
    if re.search(r"\bpreferred\b|\bpreference\b", name): return "preferred"
    if re.search(r"\bwarrants?\b", name): return "warrant"
    if re.search(r"\bright[s]? to\b| - rights\b", name): return "rights"
    if "$" in row.get("ACT Symbol", ""):
        return "debt" if re.search(r"\b(?:notes?|debentures?|bonds?)\b", name) else "preferred"
    if re.search(r"\bcommon\b|ordinary shares?|american depositary (?:shares|receipts)|voting shares|registered shares|(?:shares|units) of beneficial interest|(?:limited partner|limited partnership|limited liability company) interests|units of limited partnership|\bllc class [a-z] units\b", name):
        return "equity"
    if re.search(r"\b(?:notes?|debentures?|bonds?)\b", name): return "debt"
    if re.search(r"\bunits?\b", name): return "units"
    # Many NYSE common securities have only the issuer name, e.g. Visa Inc.
    # Funds and blank-check issuers are also excluded by the SIC universe.
    return "equity"


def parse_listings(content):
    rows = csv.DictReader(io.StringIO(content), delimiter="|")
    if not {"Security Name", "ETF"}.issubset(rows.fieldnames or []):
        raise ValueError("Exchange listing directory has unexpected columns")
    result = {}
    for row in rows:
        if not row.get("Security Name"): continue  # Includes file timestamp footer.
        kind = listing_kind(row)
        # CQS uses a case-sensitive 'p' preferred suffix (BCpC). Uppercasing
        # that alias would collide with the unrelated common ticker BCPC.
        for field in ("Symbol", "ACT Symbol", "NASDAQ Symbol"):
            symbol = listing_symbol(row.get(field))
            if symbol: result[symbol] = kind
    return result


def load_listing_directory(store, today, agent):
    rows = store.call("sec_market_sync_state?id=eq.listing_types&select=payload")
    cached = rows[0]["payload"] if rows else None
    if cached and cached.get("date") == today.isoformat() and cached.get("symbols"):
        return cached["symbols"]
    symbols = {}
    for file in ("nasdaqlisted.txt", "otherlisted.txt"):
        req = urllib.request.Request("https://www.nasdaqtrader.com/dynamic/SymDir/" + file,
                                     headers={"User-Agent": agent})
        with urllib.request.urlopen(req, timeout=30) as response:
            parsed = parse_listings(response.read().decode("utf-8-sig"))
        if len(parsed) < 500: raise RuntimeError("Exchange listing directory is incomplete; keep previous snapshot")
        symbols.update(parsed)
    store.upsert("sec_market_sync_state", "id", {"id": "listing_types",
                 "payload": {"date": today.isoformat(), "symbols": symbols}, "updated_at": stamp()})
    print(f"Cached free exchange security metadata: {len(symbols)} symbols, 2 metadata requests.", flush=True)
    return symbols


def listed_issuers(directory, listings):
    result = {}
    for values in directory["data"]:
        row = dict(zip(directory["fields"], values))
        if row.get("exchange") not in ("Nasdaq", "NYSE", "NYSE American", "Cboe", "NYSE Arca"): continue
        if listings.get(listing_symbol(row["ticker"])) != "equity": continue
        result.setdefault(int(row["cik"]), {"symbol": row["ticker"], "name": row["name"], "exchange": row["exchange"]})
    return result


class SEC:
    def __init__(self):
        contact = os.environ["SEC_CONTACT"]
        if "@" not in contact: raise ValueError("SEC contact email required")
        self.agent, self.last, self.requests = "Pulsarium Sector Analytics " + contact, 0, 0

    def get(self, url):
        if urllib.parse.urlparse(url).hostname not in ("www.sec.gov", "data.sec.gov"):
            raise ValueError("SEC host required")
        for attempt in range(4):
            time.sleep(max(0, self.last + 0.3 - time.monotonic()))
            self.last = time.monotonic()
            self.requests += 1
            req = urllib.request.Request(url, headers={"User-Agent": self.agent, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=45) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 404: return None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3: raise
                time.sleep(2 ** (attempt + 1))
            except (TimeoutError, urllib.error.URLError):
                if attempt == 3: raise
                time.sleep(2 ** (attempt + 1))


class Store:
    def __init__(self):
        self.cache = ReadCache()
        self.url = os.environ["SUPABASE_URL"].rstrip("/") + "/functions/v1/sec-ingest/rest/v1/"
        key = os.environ["SEC_INGEST_TOKEN"]
        self.headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def call(self, path, method="GET", body=None):
        headers = dict(self.headers)
        if method == "POST": headers["Prefer"] = "resolution=merge-duplicates"
        req = urllib.request.Request(self.url + path, method=method, headers=headers,
            data=json.dumps(body, allow_nan=False).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
            return json.loads(data) if data else None

    def rows(self, path):
        result = []
        for offset in range(0, 100000, 500):
            page = self.call(path + f"&limit=500&offset={offset}")
            result.extend(page)
            if len(page) < 500: return result
        raise RuntimeError("Unexpected cache size")

    def upsert(self, table, key, rows):
        if not isinstance(rows, list): rows = [rows]
        for start in range(0, len(rows), 100):
            self.call(f"{table}?on_conflict={key}", "POST", rows[start:start + 100])
            if table == "sec_market_frames":
                self.cache.put(FRAME_CACHE, rows[start:start + 100])

    def cached_frames(self, prefix):
        return self.cache.rows(self.call, self.rows, "sec_market_frames", f"&id=like.{prefix}/*",
                               FRAME_CACHE, "id,payload_gzip,fetched_at")


def load_frame(sec, store, concept, frame, live_frame, now=None, unit="USD"):
    key = f"us-gaap/{concept}/{unit}/{frame}"
    cached = ([store.frame_cache[key]] if key in store.frame_cache else []) if hasattr(store, "frame_cache") else store.call("sec_market_frames?id=eq." + key + "&select=payload_gzip,fetched_at")
    now = now or dt.datetime.now(dt.timezone.utc)
    if cached:
        fetched = dt.datetime.fromisoformat(cached[0]["fetched_at"].replace("Z", "+00:00"))
        window = frame.removesuffix("I")
        live = window in live_frame if isinstance(live_frame, (set, list, tuple)) else window == live_frame
        if not live or (now - fetched).total_seconds() < REFRESH_HOURS * 3600:
            return unpack(cached[0]["payload_gzip"])
    data = sec.get("https://data.sec.gov/api/xbrl/frames/" + key + ".json") or {"data": []}
    if cached:
        # Keep every downloaded version. New filings and corrections append
        # facts; absent issuers in a later response retain their saved history.
        old = unpack(cached[0]["payload_gzip"])
        seen = {json.dumps(r, sort_keys=True) for r in old.get("data", [])}
        data = {**data, "data": old.get("data", []) + [r for r in data.get("data", []) if json.dumps(r, sort_keys=True) not in seen]}
    record = {"id": key, "payload_gzip": pack(data), "fetched_at": now.isoformat()}
    store.upsert("sec_market_frames", "id", record)
    if hasattr(store, "frame_cache"): store.frame_cache[key] = record
    print(f"Cached {concept} {frame}: {len(data.get('data', []))} facts", flush=True)
    return data


def fact_index(doc):
    return {int(r["cik"]): r for r in doc.get("data", []) if isinstance(r.get("val"), (int, float)) and math.isfinite(r["val"])}


def duration(row):
    try: return (dt.date.fromisoformat(row["end"]) - dt.date.fromisoformat(row["start"])).days + 1
    except (KeyError, ValueError, TypeError): return 0


def matching(a, b):
    try:
        return (abs((dt.date.fromisoformat(a["end"]) - dt.date.fromisoformat(b["end"])).days) <= 7
                and abs(duration(a) - duration(b)) <= 7)
    except (ValueError, KeyError): return False


def fourth_quarter(frames, concept, year):
    """Derive Q4 from an annual fact and three contiguous quarters.

    Most US issuers file an annual 10-K rather than a standalone Q4 10-Q.
    Annual revenue must never be compared with three-month revenue.
    """
    target = frames[concept, f"CY{year}Q4"]
    for cik, annual in frames[concept, f"CY{year}"].items():
        if cik in target or not 350 <= duration(annual) <= 380: continue
        quarters = [frames[concept, f"CY{year}Q{q}"].get(cik) for q in (1, 2, 3)]
        if any(not q or not 80 <= duration(q) <= 100 for q in quarters): continue
        expected = dt.date.fromisoformat(annual["start"])
        valid = True
        for q in quarters:
            if abs((dt.date.fromisoformat(q["start"]) - expected).days) > 7:
                valid = False
                break
            expected = dt.date.fromisoformat(q["end"]) + dt.timedelta(days=1)
        if not valid: continue
        derived = {**annual, "start": expected.isoformat(), "val": annual["val"] - sum(q["val"] for q in quarters)}
        if 80 <= duration(derived) <= 100: target[cik] = derived


def revenue_pair(cik, frames, current, previous):
    for concept in REVENUE:
        a, b = frames[concept, current].get(cik), frames[concept, previous].get(cik)
        if not a or not b or a["val"] <= 0 or b["val"] <= 0: continue
        gap = (dt.date.fromisoformat(a["end"]) - dt.date.fromisoformat(b["end"])).days
        if 80 <= duration(a) <= 100 and 80 <= duration(b) <= 100 and 350 <= gap <= 380 and abs(duration(a) - duration(b)) <= 7:
            return a, b
    return None


def company_metrics(cik, meta, frames, current, previous, annual):
    pair = revenue_pair(cik, frames, current, previous)
    sector = industry(meta.get("sic"))
    if not pair or not sector: return None
    a, b = pair
    margin = None
    op, prev_op = frames["OperatingIncomeLoss", current].get(cik), frames["OperatingIncomeLoss", previous].get(cik)
    if op and prev_op and matching(a, op) and matching(b, prev_op):
        margin = 100 * (op["val"] / a["val"] - prev_op["val"] / b["val"])
    cash = None
    if sector not in ("banks", "insurance"):
        for cfo in CFO:
            c = frames[cfo, annual].get(cik)
            if not c or not 350 <= duration(c) <= 380: continue
            for capex in CAPEX:
                k = frames[capex, annual].get(cik)
                if k and k["val"] >= 0 and 350 <= duration(k) <= 380 and matching(c, k):
                    cash = c["val"] - k["val"]
                    break
            if cash is not None: break
    accession = a.get("accn", "")
    source = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html"
              if re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) else None)
    return {"cik": str(cik), "symbol": meta["symbol"], "name": meta["name"], "sector": sector,
            "revenue_yoy": 100 * (a["val"] / b["val"] - 1), "margin_change": margin,
            "revenue_current": a["val"], "revenue_previous": b["val"],
            "revenue_previous_end": b["end"], "low_revenue_base": b["val"] < LOW_REVENUE_BASE,
            "fcf": cash, "period_end": a["end"], "source": source}


def fiscal_quarters(rows):
    """Recover missing quarters from reported periods of one accounting concept."""
    # Accession sequence orders revisions within one issuer. Company-facts
    # supplements contain comparative facts filed later than the old frame.
    rows = sorted(rows, key=lambda r: (r.get("accn", "")[11:13], r.get("accn", "")[-6:]))
    chosen = {}
    for row in rows:
        if 70 <= duration(row) <= 125:
            chosen[row["end"]] = row
    # Comparative standalone quarters can be absent while their YTD values
    # are reported under this concept. Only exact shared boundaries permit
    # subtraction; separately reported quarters always take precedence.
    periods = sorted({(r["start"], r["end"]): r for r in rows if 70 <= duration(r) <= 380}.values(),
                     key=lambda r: (r.get("accn", "")[11:13], r.get("accn", "")[-6:]))
    derived = {}
    for total in periods:
        if duration(total) <= 125: continue
        for part in periods:
            if total["start"] == part["start"] and part["end"] < total["end"]:
                a, b = (dt.date.fromisoformat(part["end"]) + dt.timedelta(days=1)).isoformat(), total["end"]
            elif total["end"] == part["end"] and total["start"] < part["start"]:
                a, b = total["start"], (dt.date.fromisoformat(part["start"]) - dt.timedelta(days=1)).isoformat()
            else: continue
            quarter = {**total, "start": a, "end": b, "val": total["val"] - part["val"], "derived": True}
            if 70 <= duration(quarter) <= 125 and b not in chosen:
                derived[b] = quarter
    chosen.update(derived)
    annuals = {(r["start"], r["end"]): r for r in rows if 350 <= duration(r) <= 380}
    for annual in annuals.values():
        if any(abs((dt.date.fromisoformat(q["end"]) - dt.date.fromisoformat(annual["end"])).days) <= 7 for q in chosen.values()): continue
        cursor, parts = dt.date.fromisoformat(annual["start"]), []
        for _ in range(3):
            candidates = [q for q in chosen.values() if abs((dt.date.fromisoformat(q["start"]) - cursor).days) <= 7
                          and q["end"] < annual["end"]]
            if not candidates: break
            q = min(candidates, key=lambda r: abs((dt.date.fromisoformat(r["start"]) - cursor).days))
            parts.append(q)
            cursor = dt.date.fromisoformat(q["end"]) + dt.timedelta(days=1)
        derived = {**annual, "start": cursor.isoformat(), "val": annual["val"] - math.fsum(q["val"] for q in parts), "derived": True}
        if len(parts) == 3 and 70 <= duration(derived) <= 125:
            chosen[derived["end"]] = derived
    return sorted(chosen.values(), key=lambda r: (r["end"], r["start"]))


def consecutive_quarters(rows, end, count=4):
    at = next((i for i, r in enumerate(rows) if r["end"] == end), None)
    if at is None or at < count - 1: return None
    run = rows[at - count + 1:at + 1]
    if any(abs((dt.date.fromisoformat(b["start"]) - dt.date.fromisoformat(a["end"])).days - 1) > 7 for a, b in zip(run, run[1:])):
        return None
    if not 350 <= (dt.date.fromisoformat(run[-1]["end"]) - dt.date.fromisoformat(run[0]["start"])).days + 1 <= 380:
        return None
    return run


def latest_revenue(cik, pools, today):
    candidates = []
    for priority, concept in enumerate(REVENUE):
        rows = fiscal_quarters(pools.get(concept, {}).get(cik, []))
        for a in reversed(rows):
            found = False
            age = (today - dt.date.fromisoformat(a["end"])).days
            if not 0 <= age <= MAX_REPORT_AGE: continue
            current = consecutive_quarters(rows, a["end"])
            if not current or any(q["val"] <= 0 for q in current): continue
            for b in reversed(rows):
                gap = (dt.date.fromisoformat(a["end"]) - dt.date.fromisoformat(b["end"])).days
                if not 350 <= gap <= 380 or abs(duration(a) - duration(b)) > 7: continue
                previous = consecutive_quarters(rows, b["end"])
                if not previous or any(q["val"] <= 0 for q in previous): continue
                if any(abs(duration(x) - duration(y)) > 7 for x, y in zip(current, previous)): continue
                candidates.append((a["end"], -priority, current, previous))
                found = True
                break
            if found: break
    return max(candidates, key=lambda r: (r[0], r[1]))[2:] if candidates else None


def reporting_frame(end):
    date = dt.date.fromisoformat(end)
    periods = [(dt.date(y, q * 3, calendar.monthrange(y, q * 3)[1]), f"CY{y}Q{q}")
               for y in (date.year - 1, date.year, date.year + 1) for q in (1, 2, 3, 4)]
    return min(periods, key=lambda p: abs((date - p[0]).days))[1]


def refresh_windows(today):
    # Fiscal periods map to their closest calendar period. At the year turn,
    # annual filings still belong to last year's frame, not the empty future
    # calendar year. Keep 120 days of overlap after its latest possible end.
    quarter = reporting_frame(today.isoformat())
    at = int(quarter[2:6]) * 4 + int(quarter[7:]) - 1
    annual = min((today.year - 1, today.year), key=lambda y: abs((today - dt.date(y, 12, 31)).days))
    live = {quarter, f"CY{(at - 1) // 4}Q{(at - 1) % 4 + 1}", f"CY{annual}"}
    if (today - dt.date(annual, 6, 30)).days <= 120: live.add(f"CY{annual - 1}")
    return live


def latest_company_metrics(cik, meta, pools, today, pair=None):
    pair = pair or latest_revenue(cik, pools, today)
    sector = industry(meta.get("sic"))
    if not pair or not sector: return None
    current, previous = pair
    a, b = current[-1], previous[-1]
    revenue, prior = math.fsum(q["val"] for q in current), math.fsum(q["val"] for q in previous)
    margin = None
    operating = fiscal_quarters(pools.get("OperatingIncomeLoss", {}).get(cik, []))
    ops, prev_ops = consecutive_quarters(operating, a["end"]), consecutive_quarters(operating, b["end"])
    if ops and prev_ops and all(matching(x, y) for x, y in zip(current + previous, ops + prev_ops)):
        margin = 100 * (math.fsum(q["val"] for q in ops) / revenue - math.fsum(q["val"] for q in prev_ops) / prior)
    cash, cash_end = None, None
    if sector not in ("banks", "insurance"):
        options = []
        for cfo in CFO:
            annuals = {(r.get("start"), r.get("end")): r for r in pools.get(cfo, {}).get(cik, [])}
            for c in annuals.values():
                if not 350 <= duration(c) <= 380 or not 0 <= (today - dt.date.fromisoformat(c["end"])).days <= 480: continue
                for capex in CAPEX:
                    capex_rows = {(r.get("start"), r.get("end")): r for r in pools.get(capex, {}).get(cik, [])}
                    for k in capex_rows.values():
                        if k["val"] >= 0 and matching(c, k): options.append((c["end"], c["val"] - k["val"]))
        if options: cash_end, cash = max(options, key=lambda r: r[0])
    accession = a.get("accn", "")
    source = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html"
              if re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) else None)
    return {"cik": str(cik), "symbol": meta["symbol"], "name": meta["name"], "sector": sector,
            "revenue_yoy": 100 * (revenue / prior - 1), "margin_change": margin,
            "revenue_current": revenue, "revenue_previous": prior,
            "quarter_revenue_current": a["val"], "quarter_revenue_previous": b["val"],
            "quarter_period_start": a["start"], "quarter_previous_start": b["start"],
            "quarter_revenue_yoy": 100 * (a["val"] / b["val"] - 1),
            "revenue_previous_end": b["end"], "low_revenue_base": b["val"] < LOW_REVENUE_BASE,
            "fcf": cash, "fcf_period_end": cash_end, "period_start": current[0]["start"],
            "period_end": a["end"], "reporting_frame": reporting_frame(a["end"]), "source": source}


def supplement_large_issuers(sec, store, listed, pools, eligible, today):
    # Frames contain one fact per issuer/window. Comparative concepts and
    # 12/16-week calendars may need full facts. Bootstrap only significant
    # incomplete issuers; the daily index job then appends unseen filings.
    candidates = []
    for cik in listed:
        recent = [r for concept in REVENUE for r in pools[concept].get(cik, [])
                  if 70 <= duration(r) <= 125 and r["val"] >= 5_000_000_000
                  and 0 <= (today - dt.date.fromisoformat(r["end"])).days <= MAX_REPORT_AGE]
        pair = eligible.get(cik)
        if recent and (not pair or max(r["end"] for r in recent) > pair[0][-1]["end"]):
            candidates.append((max(r["val"] for r in recent), cik))
    states = store.call("sec_market_sync_state?id=eq.sector:history-targets&select=payload")
    targets = {int(r["cik"]): r for r in states[0]["payload"].get("companies", [])} if states else {}
    selected = {cik for _, cik in sorted(candidates, reverse=True)[:50]} | (set(targets) & set(listed))
    docs = sec_financial_signals.histories(store, unpack) if selected else {}
    for cik in sorted(selected):
        key = f"companyfacts/{cik:010d}"
        if cik in docs: doc = docs[cik]
        else:
            doc = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
            if not doc: continue
            store.upsert("sec_market_frames", "id", {"id": key, "payload_gzip": pack({"doc": doc, "through": "", "pending": []}), "fetched_at": stamp()})
            docs[cik] = doc
        targets[cik] = {"cik": str(cik), "symbol": listed[cik]["symbol"]}
        for concept in REVENUE + ["OperatingIncomeLoss"] + CFO + CAPEX:
            for r in doc.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {}).get("USD", []):
                if isinstance(r.get("val"), (int, float)) and math.isfinite(r["val"]):
                    pools[concept][cik].append({**r, "cik": cik})
        pair = latest_revenue(cik, pools, today)
        if pair: eligible[cik] = pair
    if targets:
        store.upsert("sec_market_sync_state", "id", {"id": "sector:history-targets", "payload": {"companies": list(targets.values())}, "updated_at": stamp()})
    print(f"Significant incomplete issuers checked: {min(len(candidates), 50)}; permanent supplementary histories: {len(targets)}", flush=True)


def insider_totals(filings, state, companies, today):
    result = defaultdict(lambda: {"purchases": 0, "sales": 0, "planned_sales": 0, "purchase_issuers": set(), "sale_issuers": set()})
    start = (today - dt.timedelta(days=29)).isoformat()
    amendments = [f for f in filings if f.get("form") == "4/A"]
    seen = set()
    for f in filings:
        if f.get("form") != "4": continue
        meta = companies.get(int(f.get("issuer_cik") or 0))
        if not meta or not industry(meta.get("sic")): continue
        owners = f.get("owners") or []
        if len(owners) != 1 or not (owners[0].get("officer") or owners[0].get("director")): continue
        owner, sector = owners[0], industry(meta["sic"])
        routine = re.search(r"reinvest\w*.{0,60}dividends?|dividends?.{0,60}reinvest\w*|initial public offering|directed share program|employee stock purchase|payroll deductions?", " ".join((f.get("notes") or {}).values()), re.I)
        for mode in ("purchases", "sales"):
            if mode == "purchases" and routine: continue
            if mode == "sales" and f["filed_date"] < state.get("sales_history_from", today.isoformat()): continue
            for t in f.get(mode) or []:
                if not start <= t.get("date", "") <= today.isoformat(): continue
                if not t.get("direct") or t.get("private") or t.get("currency") != "USD": continue
                if mode == "purchases" and t.get("planned"): continue
                if any(a.get("issuer_cik") == f["issuer_cik"] and
                       any(o.get("cik") == owner["cik"] for o in a.get("owners") or []) and
                       (a.get("original_date") == f["filed_date"] or any(x.get("date") == t["date"] and x.get("security") == t.get("security")
                        for x in (a.get("purchases") or []) + (a.get("sales") or []))) for a in amendments): continue
                key = (f["issuer_cik"], owner["cik"], mode, t["date"], t.get("security"), t.get("shares"), t.get("price"))
                if key in seen: continue
                seen.add(key)
                amount = t.get("amount", 0)
                if not isinstance(amount, (float, int)) or not math.isfinite(amount) or amount <= 0: continue
                r = result[sector]
                r[mode] += amount
                r["purchase_issuers" if mode == "purchases" else "sale_issuers"].add(f["issuer_cik"])
                if mode == "sales" and t.get("planned"): r["planned_sales"] += amount
    return {k: {**{x: round(v[x], 2) for x in ("purchases", "sales", "planned_sales")},
                "purchase_companies": len(v["purchase_issuers"]), "sale_companies": len(v["sale_issuers"])} for k, v in result.items()}


def median(values):
    return round(statistics.median(values), 2) if values else None


def reporting_coverage(rows, today):
    target = closed_quarter(today)
    latest = [r for r in rows if r.get("reporting_frame", "") >= target]
    total = math.fsum(r["revenue_previous"] for r in rows)
    ends = sorted(r["period_end"] for r in rows if r.get("period_end"))
    cash_ends = sorted(r["fcf_period_end"] for r in rows if r.get("fcf_period_end"))
    return {"reporting_target": target.replace("CY", "")[4:] + " " + target[2:6],
            "latest_report_count": len(latest),
            "latest_report_revenue_pct": round(100 * math.fsum(r["revenue_previous"] for r in latest) / total, 1) if total else None,
            "period_end_min": ends[0] if ends else None, "period_end_max": ends[-1] if ends else None,
            "cash_flow_end_min": cash_ends[0] if cash_ends else None, "cash_flow_end_max": cash_ends[-1] if cash_ends else None}


def aggregate(metrics, insider, coverage, period, annual, state, today):
    grouped = defaultdict(list)
    for m in metrics: grouped[m["sector"]].append(m)
    sectors = []
    for key, rows in grouped.items():
        revenue = median([r["revenue_yoy"] for r in rows])
        revenue_current = math.fsum(r["revenue_current"] for r in rows)
        revenue_previous = math.fsum(r["revenue_previous"] for r in rows)
        total_growth = 100 * (revenue_current / revenue_previous - 1)
        margins = [r["margin_change"] for r in rows if r["margin_change"] is not None]
        margin = median(margins)
        direction = "limited"
        if len(rows) >= MIN_SAMPLE:
            if len(margins) >= MIN_SAMPLE:
                direction = "growth" if total_growth > 0 and margin >= 0 else "pressure" if total_growth < 0 and margin <= 0 else "mixed"
            else: direction = "mixed"
        cash = [r["fcf"] for r in rows if r["fcf"] is not None]
        examples = sorted(rows, key=lambda r: (-r.get("quarter_revenue_current", r["revenue_current"]), r["symbol"], r["cik"]))[:6]
        companies = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k not in ("sector", "fcf")} for r in examples]
        for company in companies:
            if "quarter_revenue_current" in company:
                company["ttm_revenue_current"] = company["revenue_current"]
                company["ttm_revenue_yoy"] = company["revenue_yoy"]
                for field in ("current", "previous", "yoy"):
                    company["revenue_" + field] = company.pop("quarter_revenue_" + field)
        reporting = reporting_coverage(rows, today)
        sectors.append({**reporting, "id": key, "name": LABELS[key], "direction": direction,
            "company_count": len(rows), "revenue_yoy": revenue, "margin_change": margin, "margin_count": len(margins),
            "revenue_total_yoy": round(total_growth, 2), "revenue_current": revenue_current, "revenue_previous": revenue_previous,
            "growing_count": sum(r["revenue_yoy"] > 0 for r in rows), "declining_count": sum(r["revenue_yoy"] < 0 for r in rows),
            "flat_count": sum(r["revenue_yoy"] == 0 for r in rows), "fcf_count": len(cash), "fcf_positive_count": sum(c > 0 for c in cash),
            "fcf_positive_pct": round(100 * sum(c > 0 for c in cash) / len(cash), 1) if cash else None,
            "insiders": insider.get(key), "companies": companies})
    sectors.sort(key=lambda s: (-s["revenue_total_yoy"], s["name"]))
    target = (today - dt.timedelta(days=29)).isoformat()
    since = max(target, state.get("history_from") or today.isoformat())
    sales_since = max(target, state.get("sales_history_from") or today.isoformat())
    live = any("quarter_revenue_current" in m for m in metrics)
    return {"version": 2 if live else 1, "revenue_basis": "ttm" if live else "quarterly", "company_ranking": "revenue", "updated_at": stamp(), "period": period, "cash_flow_period": annual,
        "coverage": {**coverage, **reporting_coverage(metrics, today), "included": len(metrics), "sector_count": len(sectors)}, "sectors": sectors,
        "insider_window": {"target_days": 30, "purchase_since": since, "sale_since": sales_since,
                           "through": state.get("through"), "partial": since > target or sales_since > target,
                           "coverage_complete": bool(state.get("through")) and state["through"] >= sales_since},
        "methodology": {"universe": "SEC issuers with exchange-listed common equity, common partnership units or ADRs and comparable USD US-GAAP quarterly revenue. Security types use the official Nasdaq Trader listing directories for US exchanges. Funds, ETFs/ETNs, preferred shares, debt securities, warrants, rights, packaged units and test issues are excluded. Indices, options and futures are outside this issuer universe. Operating REITs and partnerships are retained. Fund and blank-check SIC codes are outside the mapped industries. One issuer per CIK; multiple common share classes count once. Industries follow SEC SIC codes.",
            "reporting_period": "Each company uses its latest available comparable report, with no fixed waiting period after quarter end. Revenue covers four contiguous fiscal quarters (TTM), compared with the previous four. Latest quarter ends must be within 200 days of the snapshot date. Coverage of the most recently completed calendar quarter is based on the closest calendar quarter to each fiscal period end. The covered share uses prior-year TTM revenue as its denominator. Snapshot refreshed is the computation time; individual reporting dates and cash-flow dates are shown separately.",
            "revenue": "Headline growth is the change in summed TTM revenue of the same issuers in both periods: (current total / prior-year total - 1) × 100. This is equivalent to weighting company growth by prior-year revenue. The median gives each company equal weight; breadth is the share with growing revenue. Totals describe the covered business sample, not stock-market returns or market-capitalization weights. Fiscal-quarter durations must be 70–125 days (including 12/16-week calendars) and corresponding quarters differ by at most 7 days; each TTM span and year-over-year end-date gap must be 350–380 days. Missing quarters are never filled with zeros. Each company pair uses the same revenue concept. Missing standalone quarters can be derived from differences between reported periods of that same concept sharing an exact start or end. Where Q4 is not reported separately, it can also be derived from an annual fact minus three contiguous quarters of the same concept.",
            "company_values": "Up to six largest issuers in each industry, ranked by current quarterly revenue in descending order. Amounts are in USD, with the year-over-year percentage change shown alongside. Growing and declining companies are included. Low revenue base means prior-year quarterly revenue below $1 million; this contextual flag does not exclude the issuer or identify its market capitalization.",
            "margin": "Median change in four-quarter operating income divided by TTM revenue, in percentage points. Operating income and revenue must cover matching periods. Each metric shows its own sample size.",
            "cash_flow": "Positive annual operating cash flow minus reported capital expenditure, among companies with both values. Annual periods must be 350–380 days and match. Banking and insurance are excluded. Each issuer uses its latest available matching annual figures, up to 480 days old. Actual annual period-end ranges are shown. This metric is annual, separate from TTM revenue.",
            "direction": "Growth: positive total revenue change and non-negative median operating margin change. Pressure: negative total revenue change and non-positive median operating margin change. At least 10 companies are required for both metrics. Other combinations are mixed; smaller revenue samples are limited.",
            "insiders": "Available Form 4 history within the last 30 days, for classified issuers in the covered universe. Direct priced USD transactions by a single officer or director. Purchases exclude identified plans and routine programs; sales include identified plans, shown separately. Private transactions and unresolved amendments are excluded. Purchases and sales may have different collection start dates. Values describe disclosures, not trading recommendations.",
            "cache": "Historical SEC frames are downloaded once and retained. Recent fiscal reporting windows refresh after six hours, with three scheduled runs per day. Quarter and annual windows follow their closest calendar periods; an annual overlap of 120 days allows year-end and off-calendar filings to accumulate. Future empty frames are not periodically requested before their reporting window. Significant incomplete issuers (at least $5 billion of quarterly revenue, up to 50) receive a one-time company-facts supplement; the scheduled filing-index job appends unseen reports to those permanent histories. Incoming fact versions append to saved history; older frames are retained without periodic re-download. New reporting windows and issuer classifications are added. Free SEC ticker and exchange security-type directories are cached daily. All visitors read this shared snapshot."}}


def run(max_companies):
    today = dt.date.today()
    sec, store = SEC(), Store()
    states = store.call("sec_market_sync_state?id=eq.directory&select=payload")
    cached = states[0]["payload"] if states else None
    if cached and cached["date"] == today.isoformat(): directory = cached["data"]
    else:
        directory = sec.get("https://www.sec.gov/files/company_tickers_exchange.json")
        if not directory or not directory.get("data"): raise RuntimeError("SEC ticker directory missing")
        store.upsert("sec_market_sync_state", "id", {"id": "directory", "payload": {"date": today.isoformat(), "data": directory}, "updated_at": stamp()})
    listed = listed_issuers(directory, load_listing_directory(store, today, sec.agent))
    # List versions, then transfer only changed bodies. Old windows remain
    # in the runner cache; Supabase still retains the complete source history.
    store.frame_cache = {r["id"]: r for r in store.cached_frames("us-gaap")}
    quarters = quarter_frames(today)
    live = refresh_windows(today)
    pools = defaultdict(lambda: defaultdict(list))
    for concept in REVENUE + ["OperatingIncomeLoss"] + CFO + CAPEX:
        windows = ([*quarters, *[f"CY{year}" for year in range(today.year - 3, today.year + 1)]]
                   if concept in REVENUE + ["OperatingIncomeLoss"] else [f"CY{year}" for year in range(today.year - 2, today.year + 1)])
        for frame in windows:
            doc = load_frame(sec, store, concept, frame, live)
            for row in doc.get("data", []):
                if isinstance(row.get("val"), (int, float)) and math.isfinite(row["val"]):
                    pools[concept][int(row["cik"])].append(row)
    eligible = {cik: latest_revenue(cik, pools, today) for cik in listed}
    eligible = {cik: pair for cik, pair in eligible.items() if pair}
    supplement_large_issuers(sec, store, listed, pools, eligible, today)
    if len(eligible) < 100: raise RuntimeError(f"Insufficient comparable TTM history: {len(eligible)}; keep previous snapshot")
    companies = {int(r["cik"]): r["payload"] for r in store.rows("sec_market_companies?select=cik,payload&order=cik")}
    missing = sorted((c for c in eligible if c not in companies), key=lambda c: -eligible[c][0][-1]["val"])
    pending = []
    for cik in missing[:max_companies]:
        try:
            sub = sec.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            if sub is None: continue
            meta = {**listed[cik], "sic": sub.get("sic"), "sic_description": sub.get("sicDescription"), "classified_at": stamp()}
            companies[cik] = meta
            pending.append({"cik": cik, "payload": meta, "updated_at": stamp()})
            if len(pending) >= 50:
                store.upsert("sec_market_companies", "cik", pending)
                pending.clear()
                print(f"Classified {len(companies)} issuers; eligible {len(eligible)}", flush=True)
        except (urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 403: raise
            # Saved classifications survive interruption. A failed issuer is
            # retried on the next run, without re-fetching successful history.
            print(f"Classification deferred for CIK {cik}: {type(exc).__name__}", flush=True)
    store.upsert("sec_market_companies", "cik", pending)
    metrics = [latest_company_metrics(c, {**companies[c], **listed[c]}, pools, today, eligible[c]) for c in eligible if c in companies]
    metrics = [m for m in metrics if m]
    if len(metrics) < 100: raise RuntimeError("Insufficient classified industry metrics; keep previous snapshot")
    start = (today - dt.timedelta(days=29)).isoformat()
    records = store.cache.rows(store.call, store.rows, "sec_insider_filings", f"&filed_date=gte.{start}",
                               INSIDER_CACHE, INSIDER_SELECT, key="accession", revision="updated_at",
                               batch=100, transform=insider_row)
    filings = [{**(r.get("payload") or {}), "accession": r["accession"],
                "filed_date": r["filed_date"], "status": r["status"]} for r in records]
    state_rows = store.call("sec_insider_sync_state?id=eq.global&select=payload")
    state = state_rows[0]["payload"] if state_rows else {}
    window_filings = [f for f in filings if f["filed_date"] >= max(start, state.get("sales_history_from", today.isoformat()))]
    payload = aggregate(metrics, insider_totals(filings, state, {c: companies[c] for c in eligible if c in companies}, today),
        {"eligible": len(eligible), "classified": sum(c in companies for c in eligible), "complete": all(c in companies for c in eligible)},
        "Latest reported", "Latest annual", state, today)
    payload["insider_window"]["coverage_complete"] = payload["insider_window"]["coverage_complete"] and all(f["status"] == "parsed" for f in window_filings)
    payload["insider_window"]["purchase_complete"] = bool(state.get("through")) and state["through"] >= payload["insider_window"]["purchase_since"] and all(f["status"] == "parsed" for f in filings)
    # Financial signals reuse this issuer filter and prepared public snapshot.
    # No new public table and no requests triggered by visitors are required.
    try:
        docs = sec_financial_signals.collect(sec, store, metrics, pools, today, sys.modules[__name__])
        signal_metrics = sec_financial_signals.latest_metrics(metrics, pools, docs, today, sys.modules[__name__])
        payload["financial_signals"] = sec_financial_signals.build(signal_metrics, pools, docs, today, fiscal_quarters, payload["updated_at"])
        print("Financial signal coverage: " + json.dumps(payload["financial_signals"]["coverage"]), flush=True)
        print("Financial signal counts: " + json.dumps(payload["financial_signals"]["counts"]), flush=True)
    except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
        # Keep the successful industry refresh and retain the last good
        # financial snapshot with its original freshness timestamp.
        prior = store.call("sec_sector_analytics?id=eq.global&select=payload")
        if prior and prior[0]["payload"].get("financial_signals"):
            payload["financial_signals"] = prior[0]["payload"]["financial_signals"]
        print(f"Financial signals deferred: {type(exc).__name__}; HTTP status {getattr(exc, 'code', 'unavailable')}", flush=True)
    store.upsert("sec_sector_analytics", "id", {"id": "global", "payload": payload, "updated_at": payload["updated_at"]})
    print(f"Published {len(metrics)} issuers across {len(payload['sectors'])} industries. Classified {payload['coverage']['classified']}/{len(eligible)}.", flush=True)
    print(f"SEC requests this run: {sec.requests}. Historical frames and successful classifications are retained.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-companies", type=int, default=1500)
    args = parser.parse_args()
    run(args.max_companies)
