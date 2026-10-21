"""Shared SEC Form 4 history and a public, read-only insider radar.

No quote provider or LLM is used. Completed daily EDGAR indexes discover
filings across SEC-listed US stocks. Each accession is downloaded once;
failed downloads remain retryable. The initial window is seven calendar
days, then history is retained permanently. Runs three times daily with a scoped worker credential:
SUPABASE_URL, SEC_INGEST_TOKEN, SEC_CONTACT.

For a credential-free local verification use --local-state FILE. This keeps
the same durable checkpoints in SQLite and writes a preview snapshot.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import datetime as dt
import gzip
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

UTC = dt.timezone.utc
WINDOW_DAYS = 7
MIN_PURCHASE = 100_000
SEC_ROOT = "https://www.sec.gov/Archives/"


def stamp():
    return dt.datetime.now(UTC).isoformat()


def number(value):
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def flag(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def index_filings(text, listed_ciks):
    """The index repeats joint issuer/owner entries; accession is the key."""
    found = {}
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or parts[2] not in ("4", "4/A"):
            continue
        cik, _, form, date, path = parts
        match = re.fullmatch(r"edgar/data/\d+/(\d{10}-\d{2}-\d{6})\.txt", path)
        if not match or not cik.isdigit() or int(cik) not in listed_ciks:
            continue
        try:
            filed = dt.datetime.strptime(date, "%Y%m%d" if "-" not in date else "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
        accession = match[1]
        found.setdefault(accession, dict(accession=accession, filed_date=filed,
            form_type=form, source_path=path, status="pending", attempts=0,
            payload=None, updated_at=stamp()))
    return list(found.values())


def parse_filing(text, accession, filed_date, source_path):
    match = re.search(r"<ownershipDocument\b.*?</ownershipDocument\s*>", text, re.S | re.I)
    if not match:
        raise ValueError("Ownership XML missing")
    root = ET.fromstring(match[0])
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]

    def value(parent, path):
        child = parent.find(path)
        return (child.text or "").strip() if child is not None else ""

    owners = []
    for owner in root.findall("reportingOwner"):
        rel = owner.find("reportingOwnerRelationship")
        owners.append(dict(cik=value(owner, "reportingOwnerId/rptOwnerCik").lstrip("0") or "0",
            name=value(owner, "reportingOwnerId/rptOwnerName"),
            title=value(rel, "officerTitle") if rel is not None else "",
            officer=flag(value(rel, "isOfficer")) if rel is not None else False,
            director=flag(value(rel, "isDirector")) if rel is not None else False))
    notes = {x.attrib.get("id", ""): " ".join("".join(x.itertext()).split())
             for x in root.findall("footnotes/footnote")}
    all_notes = " ".join(notes.values())
    purchases, sales, direct_rows = [], [], []
    planned = flag(value(root, "aff10b5One")) or bool(re.search(r"10b5[\s-]*1", all_notes, re.I))
    for position, trans in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        code = value(trans, "transactionCoding/transactionCode")
        disposition = value(trans, "transactionAmounts/transactionAcquiredDisposedCode/value")
        direct = value(trans, "ownershipNature/directOrIndirectOwnership/value") == "D"
        security = value(trans, "securityTitle/value")
        shares = number(value(trans, "transactionAmounts/transactionShares/value"))
        shares_after = number(value(trans, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
        date = value(trans, "transactionDate/value")
        swap = flag(value(trans, "transactionCoding/equitySwapInvolved"))
        if direct:
            direct_rows.append(dict(row=position, code=code, disposition=disposition,
                security=security, shares=shares, shares_after=shares_after, date=date, swap=swap))
        if (code, disposition) not in (("P", "A"), ("S", "D")):
            continue
        if swap:
            continue
        price = number(value(trans, "transactionAmounts/transactionPricePerShare/value"))
        if not shares or not price:
            continue
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            continue
        # Currency and private placement footnotes apply conservatively to
        # the whole filing. Their rows remain in history, outside USD ranks.
        currency = "USD"
        for currency_code, pattern in (("CAD", r"Canadian dollars|\bCAD\b|C\$"),
                              ("EUR", r"\bEUR\b|\beuros?\b"),
                              ("GBP", r"\bGBP\b|pounds sterling"),
                              ("AUD", r"Australian dollars|\bAUD\b"),
                              ("JPY", r"\bJPY\b|Japanese yen"),
                              ("CHF", r"Swiss francs|\bCHF\b"),
                              ("BRL", r"\bBRL\b|Brazilian reais|\breais\b|R\$"),
                              ("CNY", r"\bCNY\b|\bRMB\b|renminbi|Chinese yuan"),
                              ("HKD", r"\bHKD\b|Hong Kong dollars|HK\$"),
                              ("ILS", r"\bILS\b|Israeli shekels|\bNIS\b"),
                              ("SEK", r"\bSEK\b|Swedish kronor"),
                              ("NOK", r"\bNOK\b|Norwegian kroner"),
                              ("DKK", r"\bDKK\b|Danish kroner"),
                              ("INR", r"\bINR\b|Indian rupees"),
                              ("MXN", r"\bMXN\b|Mexican pesos"),
                              ("ZAR", r"\bZAR\b|South African rand"),
                              ("KRW", r"\bKRW\b|Korean won"),
                              ("TWD", r"\bTWD\b|Taiwan dollars|NT\$"),
                              ("NZD", r"\bNZD\b|New Zealand dollars")):
            if re.search(pattern, all_notes, re.I):
                currency = currency_code
                break
        private = bool(re.search(r"private placement|private purchase|privately negotiated|subscription agreement", all_notes, re.I))
        target = purchases if code == "P" else sales
        target.append(dict(row=position, date=date, shares=shares, price=price,
            amount=round(shares * price, 2), currency=currency,
            security=security, direct=direct,
            planned=planned, private=private,
            shares_after=shares_after))
    # The percentage describes one security class in one filing. Other
    # acquisitions/disposals, absent balances and inconsistent row balances
    # make that denominator ambiguous, so no percentage is published.
    for security in {s["security"] for s in sales if s["direct"]}:
        rows = sorted((r for r in direct_rows if r["security"] == security), key=lambda r: (r["date"], r["row"]))
        ranked_rows = [s for s in sales if s["direct"] and s["security"] == security]
        if not rows or len(rows) != len(ranked_rows) or any(r["code"] != "S" or r["disposition"] != "D" or
                r["swap"] or not r["shares"] or r["shares_after"] is None for r in rows):
            continue
        final = rows[-1]["shares_after"]
        repeated_final = all(math.isclose(r["shares_after"], final) for r in rows)
        sequential = all(math.isclose(a["shares_after"], b["shares_after"] + b["shares"])
                         for a, b in zip(rows, rows[1:]))
        if not (repeated_final or sequential):
            continue
        sold = sum(r["shares"] for r in rows)
        percent = round(100 * sold / (sold + final), 1)
        for sale in ranked_rows:
            sale["holding_reduction_pct"] = percent
    return dict(schema_version=2, accession=accession, filed_date=filed_date,
        form=value(root, "documentType"), original_date=value(root, "dateOfOriginalSubmission"),
        issuer_cik=value(root, "issuer/issuerCik").lstrip("0") or "0",
        symbol=value(root, "issuer/issuerTradingSymbol").upper(),
        name=value(root, "issuer/issuerName"), owners=owners, purchases=purchases, sales=sales,
        notes=notes,
        ownership_xml_gzip=base64.b64encode(gzip.compress(match[0].encode("utf-8"), mtime=0)).decode("ascii"),
        source=SEC_ROOT + source_path)


def snapshot(records, state, today):
    earliest = (today - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    eligible = []
    amendments = [r["payload"] for r in records if r.get("payload") and r["payload"].get("form") == "4/A"]
    for record in records:
        filing = record.get("payload")
        if not filing or filing.get("form") != "4" or not filing.get("symbol"):
            continue
        # Reclassify retained footnotes at calculation time: improving the
        # signal never requires re-downloading accession history. Code P
        # also covers automatic reinvestments and IPO allocation programs.
        notes = " ".join(filing.get("notes", {}).values())
        routine = re.search(r"reinvest\w*.{0,60}dividends?|dividends?.{0,60}reinvest\w*|"
            r"initial public offering|directed share program|employee stock purchase|payroll deductions?", notes, re.I)
        if routine:
            continue
        # Multiple owners and indirect ownership can describe one economic
        # purchase several times. First release ranks single-owner direct
        # purchases by officers/directors only, with a disclosed methodology.
        owners = filing.get("owners", [])
        if len(owners) != 1 or not (owners[0]["officer"] or owners[0]["director"]):
            continue
        owner = owners[0]
        for purchase in filing["purchases"]:
            if not (earliest <= purchase["date"] <= today.isoformat()):
                continue
            if not purchase["direct"] or purchase["planned"] or purchase["private"] or purchase["currency"] != "USD":
                continue
            # Preserve original and amendment in history. Suppress affected
            # originals when the correction cannot be safely reconciled.
            amended = any(a["issuer_cik"] == filing["issuer_cik"] and
                any(o["cik"] == owner["cik"] for o in a["owners"]) and
                (a.get("original_date") == filing["filed_date"] or
                 any(p["date"] == purchase["date"] and p["security"] == purchase["security"] for p in a["purchases"]))
                for a in amendments)
            if amended:
                continue
            eligible.append(dict(**purchase, accession=filing["accession"], issuer=filing["issuer_cik"],
                symbol=filing["symbol"], name=filing["name"], owner=owner["cik"],
                person=owner["name"], title=owner["title"] or "Director",
                filed=filing["filed_date"], source=filing["source"]))
    groups = {}
    for row in eligible:
        key = row["issuer"]
        group = groups.setdefault(key, dict(symbol=row["symbol"], name=row["name"],
            amount=0, owners={}, start=row["date"], end=row["date"], disclosed=row["filed"], sources={}))
        group["amount"] += row["amount"]
        group["owners"][row["owner"]] = dict(name=row["person"], title=row["title"])
        group["start"] = min(group["start"], row["date"])
        group["end"] = max(group["end"], row["date"])
        group["disclosed"] = max(group["disclosed"], row["filed"])
        group["sources"][row["accession"]] = dict(url=row["source"], filed=row["filed"])
    cards = []
    for g in groups.values():
        g["amount"] = round(g["amount"], 2)
        g["buyers"] = len(g["owners"])
        g["people"] = list(g.pop("owners").values())
        g["sources"] = list(g["sources"].values())
        cards.append(g)
    clusters = sorted((g for g in cards if g["buyers"] >= 2 and g["amount"] >= MIN_PURCHASE),
                      key=lambda g: (-g["buyers"], -g["amount"], g["symbol"]))[:5]
    large = sorted((g for g in cards if g["amount"] >= MIN_PURCHASE),
                   key=lambda g: (-g["amount"], g["symbol"]))[:5]
    relevant = [r for r in records if r["filed_date"] >= earliest]
    done = sum(r["status"] == "parsed" for r in relevant)
    return dict(version=1, updated_at=stamp(), window_days=WINDOW_DAYS,
        history_from=state["history_from"], through=state.get("through"),
        coverage=dict(processed=done, discovered=len(relevant),
                      complete=done == len(relevant) and bool(state.get("through"))),
        clusters=clusters, large=large, qualifying_companies=len(cards),
        sales=sale_snapshot(records, state, today),
        methodology="Direct, priced purchases (Form 4 code P) by one officer or director per filing. "
                    "USD only; identified trading plans, dividend reinvestments, IPO programs, "
                    "employee purchase plans, private placements and unresolved amendments are excluded. "
                    "Ranked by buyers and reported purchase value; no current price is used.")


def sale_snapshot(records, state, today):
    earliest = (today - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    history_from = state.get("sales_history_from", today.isoformat())
    amendments = [r["payload"] for r in records if r.get("payload") and r["payload"].get("form") == "4/A"]
    groups = {}
    for record in records:
        filing = record.get("payload")
        if record["filed_date"] < history_from or not filing or filing.get("form") != "4" or not filing.get("symbol"):
            continue
        owners = filing.get("owners", [])
        if len(owners) != 1 or not (owners[0]["officer"] or owners[0]["director"]):
            continue
        owner = owners[0]
        for sale in filing.get("sales", []):
            if not (earliest <= sale["date"] <= today.isoformat()) or not sale["direct"] or sale["private"] or sale["currency"] != "USD":
                continue
            amended = any(a["issuer_cik"] == filing["issuer_cik"] and
                any(o["cik"] == owner["cik"] for o in a["owners"]) and
                (a.get("original_date") == filing["filed_date"] or
                 any(t["date"] == sale["date"] and t["security"] == sale["security"]
                     for t in a.get("sales", []) + a.get("purchases", []))) for a in amendments)
            if amended:
                continue
            group = groups.setdefault(filing["issuer_cik"], dict(symbol=filing["symbol"], name=filing["name"],
                amount=0, planned_amount=0, owners={}, start=sale["date"], end=sale["date"],
                disclosed=filing["filed_date"], sources={}, holding_reduction=None))
            group["amount"] += sale["amount"]
            if sale["planned"]:
                group["planned_amount"] += sale["amount"]
            group["owners"][owner["cik"]] = dict(name=owner["name"], title=owner["title"] or "Director")
            group["start"] = min(group["start"], sale["date"])
            group["end"] = max(group["end"], sale["date"])
            group["disclosed"] = max(group["disclosed"], filing["filed_date"])
            group["sources"][filing["accession"]] = dict(url=filing["source"], filed=filing["filed_date"])
            percent = sale.get("holding_reduction_pct")
            if percent is not None and (group["holding_reduction"] is None or percent > group["holding_reduction"]["percent"]):
                group["holding_reduction"] = dict(percent=percent, person=owner["name"], security=sale["security"],
                    accession=filing["accession"])
    cards = []
    for group in groups.values():
        group["amount"] = round(group["amount"], 2)
        group["planned_amount"] = round(group["planned_amount"], 2)
        group["sellers"] = len(group["owners"])
        group["people"] = list(group.pop("owners").values())
        group["sources"] = list(group["sources"].values())
        cards.append(group)
    relevant = [r for r in records if r["filed_date"] >= max(earliest, history_from)]
    done = sum(r["status"] == "parsed" and (r.get("payload") or {}).get("schema_version", 1) >= 2 for r in relevant)
    return dict(history_from=history_from,
        coverage=dict(processed=done, discovered=len(relevant),
            complete=done == len(relevant) and (state.get("through") or "") >= history_from),
        clusters=sorted((g for g in cards if g["sellers"] >= 2 and g["amount"] >= MIN_PURCHASE),
                        key=lambda g: (-g["sellers"], -g["amount"], g["symbol"]))[:5],
        large=sorted((g for g in cards if g["amount"] >= MIN_PURCHASE),
                     key=lambda g: (-g["amount"], g["symbol"]))[:5],
        qualifying_companies=len(cards),
        methodology="Direct, priced sales (Form 4 code S) by one officer or director per filing. "
            "USD only; private transactions and unresolved amendments are excluded. "
            "Identified 10b5-1 plans are included and marked. "
            "Holding percentages describe one security class in one filing, using sold shares and the reported "
            "remaining direct balance; ambiguous balances or other transactions omit the percentage. "
            "Sales history starts with the displayed collection date.")


class SEC:
    def __init__(self, contact):
        if not contact or "@" not in contact:
            raise ValueError("SEC_CONTACT must contain a contact email")
        self.agent = "Pulsarium Insider Radar " + contact
        self.lock, self.next_request = threading.Lock(), 0.0

    def get(self, url):
        if urllib.parse.urlparse(url).hostname not in ("www.sec.gov", "data.sec.gov"):
            raise ValueError("SEC host required")
        with self.lock:
            delay = max(0, self.next_request - time.monotonic())
            time.sleep(delay)
            self.next_request = time.monotonic() + 0.3  # <= 3.34 requests/s globally
        req = urllib.request.Request(url, headers={"User-Agent": self.agent, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")


class LocalStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("create table if not exists state(id text primary key, payload text)")
        self.db.execute("create table if not exists filings(accession text primary key, payload text)")
        self.path = Path(path)

    def state(self):
        row = self.db.execute("select payload from state where id='global'").fetchone()
        return json.loads(row[0]) if row else None

    def save_state(self, state):
        self.db.execute("insert or replace into state values('global',?)", (json.dumps(state),))
        self.db.commit()

    def records(self, since=None):
        return [json.loads(r[0]) for r in self.db.execute("select payload from filings")]

    def discover(self, rows):
        self.db.executemany("insert or ignore into filings values(?,?)", [(r["accession"], json.dumps(r)) for r in rows])
        self.db.commit()

    def save(self, rows):
        self.db.executemany("insert or replace into filings values(?,?)", [(r["accession"], json.dumps(r)) for r in rows])
        self.db.commit()

    def publish(self, payload):
        self.path.with_suffix(".snapshot.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


class SupabaseStore:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/") + "/functions/v1/sec-ingest/rest/v1/"
        key = os.environ["SEC_INGEST_TOKEN"]
        self.headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def call(self, path, method="GET", body=None, prefer=None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(self.url + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=45) as response:
            data = response.read()
            return json.loads(data) if data else None

    def state(self):
        rows = self.call("sec_insider_sync_state?id=eq.global&select=payload")
        return rows[0]["payload"] if rows else None

    def save_state(self, state):
        self.call("sec_insider_sync_state?on_conflict=id", "POST", {"id": "global", "payload": state}, "resolution=merge-duplicates")

    def records(self, since=None):
        rows = []
        for offset in range(0, 1_000_000, 1000):
            recent = f"&or=(filed_date.gte.{since},status.neq.parsed)" if since else ""
            page = self.call(f"sec_insider_filings?select=*&order=accession&limit=1000&offset={offset}{recent}")
            rows.extend(page)
            if len(page) < 1000:
                return rows
        raise RuntimeError("Unexpected history size")

    def discover(self, rows):
        for offset in range(0, len(rows), 250):
            self.call("sec_insider_filings?on_conflict=accession", "POST", rows[offset:offset + 250], "resolution=ignore-duplicates")

    def save(self, rows):
        if rows:
            self.call("sec_insider_filings?on_conflict=accession", "POST", rows, "resolution=merge-duplicates")

    def publish(self, payload):
        self.call("sec_insider_radar?on_conflict=id", "POST", {"id": "global", "payload": payload,
            "updated_at": payload["updated_at"]}, "resolution=merge-duplicates")


def run(sec, store, max_filings, today):
    state = store.state() or dict(history_from=(today - dt.timedelta(days=WINDOW_DAYS)).isoformat(),
        initialized_at=stamp(), indexes=[], through=None)
    if "sales_history_from" not in state:
        state["sales_history_from"] = (dt.date.fromisoformat(state["through"]) + dt.timedelta(days=1)).isoformat() \
            if state.get("through") else state["history_from"]
        state["sales_enabled_at"] = stamp()
        store.save_state(state)
    # A daily index is fetched once after publication. New index files are
    # catalogs of new accessions, never a re-download of filing history.
    tickers = json.loads(sec.get("https://www.sec.gov/files/company_tickers.json"))
    listed = {int(r["cik_str"]) for r in tickers.values()}
    start = dt.date.fromisoformat(state["history_from"])
    end = today - dt.timedelta(days=1)
    day = start
    indexed = set(state.get("indexes", []))
    while day <= end:
        key = day.isoformat()
        if day.weekday() < 5 and key not in indexed:
            url = SEC_ROOT + f"edgar/daily-index/{day.year}/QTR{(day.month - 1) // 3 + 1}/master.{day:%Y%m%d}.idx"
            try:
                text = sec.get(url)
            except urllib.error.HTTPError as error:
                if error.code in (403, 404):
                    # Holidays/missing indexes are retried. A denied request
                    # never advances a cursor or becomes a completed day.
                    print(f"Index {key} unavailable (HTTP {error.code}); retaining checkpoint", flush=True)
                    day += dt.timedelta(days=1)
                    continue
                raise
            if not re.search(r"CIK\|Company ?Name\|Form ?Type\|Date ?Filed\|File ?Name", text, re.I):
                raise ValueError("Unexpected SEC index format")
            rows = index_filings(text, listed)
            store.discover(rows)
            indexed.add(key)
            state["indexes"] = sorted(indexed)
            state["through"] = max(indexed)
            store.save_state(state)
            print(f"Discovered {len(rows)} unique listed-company filings for {key}", flush=True)
        day += dt.timedelta(days=1)
    recent_start = (today - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    records = store.records(since=recent_start)
    def retry_due(record):
        if record["status"] == "parsed":
            return False
        if record["status"] != "failed":
            return True
        attempted = dt.datetime.fromisoformat(record["updated_at"])
        cooldown = min(24, 2 ** min(record.get("attempts", 1), 5)) * 3600
        return (dt.datetime.now(UTC) - attempted).total_seconds() >= cooldown

    pending = sorted((r for r in records if retry_due(r)),
                     key=lambda r: (r["filed_date"], r["accession"]), reverse=True)[:max_filings]
    print(f"Processing {len(pending)} filings; history contains {len(records)}", flush=True)

    def download(record):
        row = dict(record)
        row["attempts"] = row.get("attempts", 0) + 1
        try:
            text = sec.get(SEC_ROOT + row["source_path"])
            row["payload"] = parse_filing(text, row["accession"], row["filed_date"], row["source_path"])
            row["status"] = "parsed"
            row["error"] = None
        except Exception as error:
            row["status"] = "failed"
            row["error"] = type(error).__name__ + ": " + str(error)[:200]
        row["updated_at"] = stamp()
        return row

    processed, outage = 0, False
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0, len(pending), 50):
            batch = list(pool.map(download, pending[offset:offset + 50]))
            store.save(batch)
            processed += len(batch)
            print(f"Saved {processed}/{len(pending)}", flush=True)
            if processed == 50 or processed % 500 == 0:
                store.publish(snapshot(store.records(since=recent_start), state, today))
            if sum(r["status"] == "failed" for r in batch) >= max(10, len(batch) // 2):
                outage = True
                break
    state["last_run_at"] = stamp()
    store.save_state(state)
    payload = snapshot(store.records(since=recent_start), state, today)
    store.publish(payload)
    print(json.dumps({"coverage": payload["coverage"], "clusters": len(payload["clusters"]),
        "large": len(payload["large"]), "sales": len(payload["sales"]["large"]),
        "sales_coverage": payload["sales"]["coverage"], "through": payload["through"]}), flush=True)
    if outage:
        raise RuntimeError("SEC download failures; saved progress and retained retryable filings")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-state")
    parser.add_argument("--max-filings", type=int, default=5000)
    args = parser.parse_args()
    store = LocalStore(args.local_state) if args.local_state else SupabaseStore()
    run(SEC(os.environ.get("SEC_CONTACT", "")), store, max(0, min(args.max_filings, 10000)), dt.datetime.now(UTC).date())
