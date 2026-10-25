"""Financial report signals, computed once alongside the industry snapshot.

Frames screen the whole eligible equity universe. Full permanent histories
verify cash-flow and share signals. Visitor reads never fetch SEC data.
"""
import calendar
import datetime as dt
import math
import re
from collections import defaultdict

MIN_REVENUE = 100_000_000
MIN_AMOUNT = 1_000_000
MAX_NEW_HISTORIES = 24
MAX_TRACKED_HISTORIES = 180
MAX_LIST = 24
BASIC = ["WeightedAverageNumberOfSharesOutstandingBasic",
         "WeightedAverageNumberOfSharesOutstandingBasicAndDiluted"]
CASH = "CashAndCashEquivalentsAtCarryingValue"
CFO = ["NetCashProvidedByUsedInOperatingActivities",
       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SHARE_EVENTS = {
    93410: [{"date": "2025-07-18", "text": "Shares issued for the Hess acquisition (July 2025).",
             "source": "https://www.sec.gov/Archives/edgar/data/93410/000095014225001949/0000950142-25-001949-index.html"}],
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def days(row):
    try:
        return (dt.date.fromisoformat(row["end"]) - dt.date.fromisoformat(row["start"])).days + 1
    except (ValueError, KeyError, TypeError):
        return 0


def facts(doc, concept, today, unit="USD"):
    return [r for r in doc.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {}).get(unit, [])
            if finite(r.get("val")) and r.get("form") in FORMS
            and r.get("end", "9999") <= today.isoformat() and r.get("filed", "9999") <= today.isoformat()]


def revision(row):
    return row.get("filed", ""), row.get("accn", "")[11:13], row.get("accn", "")[-6:]


def source(cik, row):
    accn = row.get("accn", "")
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accn):
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{accn}-index.html"


def evidence(code, label, current, previous=None, unit="USD", extra=None):
    result = {"code": code, "label": label, "unit": unit,
              "current": current["val"], "period_start": current.get("start"), "period_end": current["end"],
              "filed": current.get("filed"), "accession": current.get("accn"),
              "derived": bool(current.get("derived"))}
    if previous is not None:
        result.update(previous=previous["val"], previous_start=previous.get("start"), previous_end=previous["end"])
        if previous["val"] > 0:
            result["change_pct"] = round(100 * (current["val"] / previous["val"] - 1), 2)
    if extra:
        result.update(extra)
    return result


def quarter_run(rows, end, count):
    selected = sorted({r["end"]: r for r in rows}.values(), key=lambda r: r["end"])
    at = next((i for i, r in enumerate(selected) if r["end"] == end), -1)
    if at < count - 1:
        return None
    run = selected[at-count+1:at+1]
    if any(not 70 <= days(r) <= 125 for r in run):
        return None
    if any(abs((dt.date.fromisoformat(b["start"]) - dt.date.fromisoformat(a["end"])).days - 1) > 7
           for a, b in zip(run, run[1:])):
        return None
    if count == 4 and not 350 <= (dt.date.fromisoformat(run[-1]["end"])-dt.date.fromisoformat(run[0]["start"])).days+1 <= 380:
        return None
    return run


def year_pair(rows, end):
    latest = [r for r in rows if r["end"] == end and 70 <= days(r) <= 125]
    for current in sorted(latest, key=revision, reverse=True):
        previous = [r for r in rows if 350 <= (dt.date.fromisoformat(end)-dt.date.fromisoformat(r["end"])).days <= 380
                    and abs(days(r)-days(current)) <= 7]
        if previous:
            return current, max(previous, key=revision)
    return None


def report_share_pair(doc, end, today):
    """Both comparative basic-share figures must come from the same filing.

    Basic shares avoid anti-dilutive EPS denominator changes after a loss.
    Comparatives in the same filing incorporate stock/reverse-stock splits.
    """
    for concept in BASIC:
        rows = facts(doc, concept, today, "shares")
        for current in sorted((r for r in rows if r["end"] == end and 70 <= days(r) <= 125 and r["val"] > 0), key=revision, reverse=True):
            previous = [r for r in rows if r.get("accn") == current.get("accn") and r["val"] > 0
                        and 350 <= (dt.date.fromisoformat(end)-dt.date.fromisoformat(r["end"])).days <= 380
                        and abs(days(r)-days(current)) <= 7]
            if previous:
                return concept, current, max(previous, key=revision)
    return None


def instant_pair(doc, concept, end, today):
    rows = facts(doc, concept, today)
    current = [r for r in rows if r["end"] == end and r["val"] >= 0]
    if not current:
        return None
    current = max(current, key=revision)
    previous = [r for r in rows if r.get("accn") == current.get("accn") and r["val"] >= 0
                and 70 <= (dt.date.fromisoformat(end)-dt.date.fromisoformat(r["end"])).days <= 380]
    # The filing's comparative balance sheet can be last quarter or year-end.
    if previous:
        return current, max(previous, key=lambda r: r["end"])
    return None


def debt_pair(doc, end, today):
    for current_tag, long_tag in [("LongTermDebtCurrent", "LongTermDebtNoncurrent"),
                                  ("LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent")]:
        a, b = instant_pair(doc, current_tag, end, today), instant_pair(doc, long_tag, end, today)
        if a and b and a[0].get("accn") == b[0].get("accn") and a[1]["end"] == b[1]["end"]:
            return ({**a[0], "val": a[0]["val"]+b[0]["val"]}, {**a[1], "val": a[1]["val"]+b[1]["val"]})
    return None


def cash_quarters(doc, today, quarter_function):
    for concept in CFO:
        rows = facts(doc, concept, today)
        if rows:
            yield concept, quarter_function(sorted(rows, key=revision))


def base_row(meta):
    return {k: meta[k] for k in ("cik", "symbol", "name", "sector", "period_end", "revenue_current")}


def relative_change(meta, current, previous):
    current_ratio = 100 * current["val"] / meta["quarter_revenue_current"]
    previous_ratio = 100 * previous["val"] / meta["quarter_revenue_previous"]
    return {"swing_amount": current["val"] - previous["val"],
            "current_ratio_pct": round(current_ratio, 3), "previous_ratio_pct": round(previous_ratio, 3),
            "strength_pp": round(current_ratio - previous_ratio, 3)}


def share_context(cik, current, previous):
    # A dated, sourced event applies only while it falls within the periods
    # compared by the share screen. It expires as the comparison moves on.
    return [event for event in SHARE_EVENTS.get(int(cik), [])
            if previous["start"] < event["date"] <= current["end"]]


def turnaround_order(row):
    signal = row["signals"][0]
    return (0 if signal["code"] == "operating_turnaround" else 1,
            -signal["swing_amount"], -signal["strength_pp"], -row["revenue_current"], row["symbol"])


def operating_signals(meta, pools, quarter_function):
    rows = quarter_function(pools.get("OperatingIncomeLoss", {}).get(int(meta["cik"]), []))
    pair = year_pair(rows, meta["period_end"])
    if not pair:
        return [], False
    cur, prior = pair
    if meta.get("quarter_period_start") and cur["start"] != meta["quarter_period_start"]:
        return [], False
    if meta.get("quarter_previous_start") and prior["start"] != meta["quarter_previous_start"]:
        return [], False
    revenue = meta["quarter_revenue_current"]
    old_revenue = meta["quarter_revenue_previous"]
    signals = []
    if cur["val"] >= MIN_AMOUNT and prior["val"] <= -MIN_AMOUNT and cur["val"]/revenue >= .005 and prior["val"]/old_revenue <= -.005:
        run = quarter_run(rows, cur["end"], 3)
        label = "Operating loss turned into profit YoY"
        extra = relative_change(meta, cur, prior)
        if run and all(r["val"] >= MIN_AMOUNT for r in run[-2:]) and run[0]["val"] <= -MIN_AMOUNT:
            label = "Loss to operating profit · positive for two quarters"
            extra["quarters"] = [{"current": r["val"], "period_start": r["start"], "period_end": r["end"]} for r in run]
        signals.append(evidence("operating_turnaround", label, cur, prior, extra=extra))
    return signals, True


def full_signals(meta, doc, today, quarter_function):
    end = meta["period_end"]
    recovery, shares, stress = [], [], []
    covered = {"cash_flow": False, "shares": False, "stress": False}
    quarter_cash = None
    for concept, rows in cash_quarters(doc, today, quarter_function):
        run = quarter_run(rows, end, 2)
        if not run:
            continue
        if meta.get("quarter_period_start") and run[-1]["start"] != meta["quarter_period_start"]:
            continue
        covered["cash_flow"] = True
        quarter_cash = rows
        pairs = [year_pair(rows, r["end"]) for r in run]
        crossed_zero = all(pairs) and pairs[-1][1]["val"] <= -MIN_AMOUNT
        aligned = all(pairs) and (not meta.get("quarter_previous_start") or pairs[-1][1]["start"] == meta["quarter_previous_start"])
        if (meta["sector"] not in ("banks", "insurance") and crossed_zero and aligned
                and all(a["val"] >= MIN_AMOUNT and a["val"]-b["val"] >= max(MIN_AMOUNT, abs(b["val"])*.1) for a, b in pairs)):
            extra = relative_change(meta, pairs[-1][0], pairs[-1][1])
            extra["quarters"] = [evidence("cash_flow", "Operating cash flow", a, b) for a, b in pairs]
            recovery.append(evidence("cash_flow_improvement", "Operating cash flow turned positive YoY", pairs[-1][0], pairs[-1][1], extra=extra))
        break
    pair = report_share_pair(doc, end, today)
    if pair:
        concept, cur, prior = pair
        if meta.get("quarter_period_start") and cur["start"] != meta["quarter_period_start"]:
            pair = None
    if pair:
        covered["shares"] = True
        concept, cur, prior = pair
        change = 100*(cur["val"]/prior["val"]-1)
        if abs(change) >= 2:
            code = "share_reduction" if change < 0 else "share_increase"
            label = "Average basic shares fell year over year" if change < 0 else "Average basic shares rose year over year"
            shares.append(evidence(code, label, cur, prior, "shares", {"concept": concept}))
            for tag, code, label in [("PaymentsForRepurchaseOfCommonStock", "buyback_cash", "Cash paid for common-share buybacks"),
                                     ("ProceedsFromStockOptionsExercised", "option_cash", "Cash received from option exercises"),
                                     ("ProceedsFromStockIssuance", "issue_cash", "Cash received from share issuance")]:
                cash = [r for r in facts(doc, tag, today) if r.get("accn") == cur.get("accn") and r["end"] == end and 70 <= days(r) <= 380 and r["val"] >= MIN_AMOUNT]
                if cash:
                    shares.append(evidence(code, label, max(cash, key=days)))
            category = "buybacks" if change < 0 and any(s["code"] == "buyback_cash" for s in shares) else "share_reduction" if change < 0 else "dilution"
            shares[0]["category"] = category
            shares[0]["contexts"] = share_context(meta["cik"], cur, prior) if change > 0 else []
    if meta["sector"] not in ("banks", "insurance"):
        balance = instant_pair(doc, CASH, end, today)
        run = quarter_run(quarter_cash or [], end, 4)
        if balance and run:
            covered["stress"] = True
            cash, old_cash = balance
            total = math.fsum(r["val"] for r in run)
            annual_cash = {**run[-1], "start": run[0]["start"], "val": total, "derived": True}
            if total <= -MIN_AMOUNT:
                stress.append(evidence("negative_operating_cash", "Operating cash flow negative over 12 months", annual_cash))
            if old_cash["val"] > 0 and cash["val"] <= .8*old_cash["val"] and old_cash["val"]-cash["val"] >= 10_000_000:
                stress.append(evidence("cash_decline", "Cash reserves fell at least 20%", cash, old_cash))
            debt = debt_pair(doc, end, today)
            if debt and debt[1]["val"] > 0 and debt[0]["val"] >= 1.25*debt[1]["val"] and debt[0]["val"]-debt[1]["val"] >= 25_000_000:
                stress.append(evidence("debt_increase", "Reported debt rose at least 25%", *debt))
            # A negative operating cash flow is required. Cash or debt alone
            # also changes during acquisitions, dividends and investment.
            if not (len(stress) >= 2 and any(s["code"] == "negative_operating_cash" for s in stress)):
                stress = []
    return recovery, shares, stress, covered


def histories(store, unpack):
    if hasattr(store, "company_docs"):
        return store.company_docs
    result = {}
    # The same histories feed both the large-issuer supplement and signals.
    # Reuse unchanged bodies across runs and decoded docs within this run.
    for row in store.cached_frames("companyfacts"):
        saved = unpack(row["payload_gzip"])
        if saved.get("doc"):
            result[int(row["id"].split("/")[1])] = saved["doc"]
    store.company_docs = result
    return result


def latest_metrics(meta, pools, docs, today, api):
    """Use each issuer's newest revenue period, including newly added facts.

    A new report supersedes an older screen even when the new period lacks
    the comparable history needed to calculate a signal.
    """
    result = []
    for m in meta:
        cik = int(m["cik"])
        doc = docs.get(cik)
        if not doc:
            result.append(m)
            continue
        reported_ends = []
        for concept in api.REVENUE + ["OperatingIncomeLoss"]:
            rows = facts(doc, concept, today)
            pools[concept][cik].extend(rows)
            if concept in api.REVENUE:
                reported_ends.extend(r["end"] for r in rows if 70 <= days(r) <= 380)
        pair = api.latest_revenue(cik, pools, today)
        if not pair:
            continue
        current, previous = pair
        a, b = current[-1], previous[-1]
        if reported_ends and max(reported_ends) > a["end"]:
            continue
        result.append({**m, "period_end": a["end"],
                       "revenue_current": math.fsum(r["val"] for r in current),
                       "quarter_revenue_current": a["val"], "quarter_revenue_previous": b["val"],
                       "quarter_period_start": a["start"], "quarter_previous_start": b["start"],
                       "source": source(cik, a)})
    return result


def screen(meta, pools, quarter_function):
    recovery, covered = operating_signals(meta, pools, quarter_function)
    end, cik = meta["period_end"], int(meta["cik"])
    shares = year_pair(pools.get(BASIC[0], {}).get(cik, []), end)
    share_score = abs(100*(shares[0]["val"]/shares[1]["val"]-1)) if shares and shares[1]["val"] > 0 else 0
    cash = sorted({r["end"]: r for r in pools.get(CASH, {}).get(cik, []) if r["end"] <= end and r["val"] >= 0}.values(), key=lambda r: r["end"])
    cash_drop = len(cash) >= 2 and cash[-1]["end"] == end and cash[-2]["val"] > 0 and cash[-1]["val"] <= .8*cash[-2]["val"]
    ops = quarter_function(pools.get("OperatingIncomeLoss", {}).get(cik, []))
    op = next((r for r in reversed(ops) if r["end"] == end), None)
    stress = bool(cash_drop and op and op["val"] < -MIN_AMOUNT and meta["sector"] not in ("banks", "insurance"))
    return bool(recovery), share_score >= 2, stress


def collect(sec, store, meta, pools, today, api):
    """Sixteen shared initial frame reads, then only recent-window refreshes.

    At most 24 previously unseen full histories are bootstrapped per run.
    Existing histories update via the shared daily filing-index workflow.
    """
    docs = histories(store, api.unpack)
    print(f'Financial signals: loaded {len(docs)} cached full histories.', flush=True)
    live = api.refresh_windows(today)
    for tag, unit, instant in [(BASIC[0], "shares", False), (CASH, "USD", True)]:
        for window in api.quarter_frames(today, 9):
            year, quarter = int(window[2:6]), int(window[7:])
            end = dt.date(year, quarter*3, calendar.monthrange(year, quarter*3)[1])
            if end > today+dt.timedelta(days=45):
                continue
            frame = window+"I" if instant else window
            data = api.load_frame(sec, store, tag, frame, live, unit=unit)
            for row in data.get("data", []):
                if finite(row.get("val")):
                    pools[tag][int(row["cik"])].append(row)
    candidates = [[], [], [], []]
    qualified = [m for m in meta if m["revenue_current"] >= MIN_REVENUE]
    for m in sorted(qualified, key=lambda m: (-m["revenue_current"], m["symbol"])):
        if int(m["cik"]) in docs:
            continue
        for i, included in enumerate(screen(m, pools, api.fiscal_quarters)):
            if included:
                candidates[i].append(m)
        if m['sector'] not in ('banks', 'insurance'):
            candidates[3].append(m)
    states = store.call("sec_market_sync_state?id=eq.financial-signals:history-targets&select=payload")
    targets = {str(r["cik"]): r for r in states[0]["payload"].get("companies", [])} if states else {}
    limit = min(MAX_NEW_HISTORIES, max(0, MAX_TRACKED_HISTORIES-len(targets)))
    selected, seen = [], set()
    while any(candidates) and len(selected) < limit:
        for group in candidates:
            if not group:
                continue
            m = group.pop(0)
            if m["cik"] not in seen:
                selected.append(m); seen.add(m["cik"])
            if len(selected) == limit:
                break
    for m in selected:
        cik = int(m["cik"])
        doc = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        if not doc:
            continue
        store.upsert("sec_market_frames", "id", {"id": f"companyfacts/{cik:010d}", "payload_gzip": api.pack({"doc": doc, "through": today.isoformat(), "pending": []}), "fetched_at": api.stamp()})
        docs[cik] = doc
        targets[str(cik)] = {"cik": str(cik), "symbol": m["symbol"]}
    if targets:
        store.upsert("sec_market_sync_state", "id", {"id": "financial-signals:history-targets", "payload": {"companies": list(targets.values())}, "updated_at": api.stamp()})
    print(f"Financial signals: {len(qualified)} issuers screened; {len(selected)} new full histories selected; {len(docs)} permanent histories available.", flush=True)
    return docs


def build(meta, pools, docs, today, quarter_function, stamp):
    from sec_report_checks import GROUPS, METHODS, analyze
    groups = {key: [] for key in ('turnaround', 'shares', 'stress') + GROUPS}
    coverage = {"eligible": 0, "operating": 0, "cash_flow": 0, "shares": 0, "stress": 0, "full_histories": 0}
    coverage.update({key: 0 for key in GROUPS})
    for m in meta:
        if m["revenue_current"] < MIN_REVENUE:
            continue
        coverage["eligible"] += 1
        recovery, op_covered = operating_signals(m, pools, quarter_function)
        coverage["operating"] += int(op_covered)
        share_signals, stress_signals = [], []
        checks = {key: [] for key in GROUPS}
        doc = docs.get(int(m["cik"]))
        if doc:
            coverage["full_histories"] += 1
            extra, share_signals, stress_signals, covered = full_signals(m, doc, today, quarter_function)
            recovery += extra
            for key, value in covered.items():
                coverage[key] += int(value)
            checks, checked = analyze(m, doc, today)
            for key, value in checked.items():
                coverage[key] += int(value)
        for key, signals in [("turnaround", recovery), ("shares", share_signals), ("stress", stress_signals), *checks.items()]:
            if signals:
                row = base_row(m)
                row["signals"] = signals
                primary = signals[0]
                row["source"] = source(m["cik"], {"accn": primary.get("accession")})
                groups[key].append(row)
    counts = {key: len(rows) for key, rows in groups.items()}
    for key, rows in groups.items():
        order = turnaround_order if key == 'turnaround' else (lambda r: (-r['signals'][0]['strength'], -r['signals'][0]['gap_amount'] / r['revenue_current'], r['symbol'])) if key in GROUPS else lambda r: (-r['revenue_current'], r['symbol'])
        rows.sort(key=order)
        groups[key] = rows if key == 'turnaround' or key in GROUPS else rows[:MAX_LIST]
    return {"version": 1, "updated_at": stamp, "coverage": coverage, "counts": counts, "groups": groups,
            "methodology": {
                **METHODS,
                "universe": "Exchange-listed operating companies from the sector universe, with comparable USD US-GAAP data and at least $100 million of TTM revenue. All matching turnarounds and profit/cash, payout and working-capital highlights are available in the details; share changes and stress include up to 24 companies each. Coverage is reported separately for each screen; full-history checks cover a subset of the universe. Full histories expand from shared screens and a bounded large-company queue, and remain cached. Share changes and stress are ranked by TTM revenue; new checks rank by relative divergence.",
                "turnaround": "Operating losses turning into profit come first, ranked by the reported quarterly profit swing in USD: current profit minus prior-year loss. Operating margin change breaks equal-dollar ties, then TTM revenue and ticker. Current and prior quarterly profit margins use their respective quarterly revenues. Operating income must change from at most -$1 million to at least +$1 million YoY, with margins crossing -0.5% and +0.5%. Two-quarter persistence requires three contiguous fiscal quarters. Cash-flow turnarounds follow, ranked by the quarterly operating cash-flow swing in USD. The latest YoY comparison must cross from negative to positive; both current quarters must be positive and improve YoY by at least $1 million and 10% of the prior amount. Banking and insurance are excluded from the cash-flow screen. Cumulative cash-flow statements are converted to standalone quarters by exact period boundaries.",
                "shares": "At least 2% YoY change in weighted-average basic common shares for comparable fiscal quarters, using both figures from the same SEC filing. Published comparative figures incorporate stock and reverse-stock splits. Basic shares avoid diluted-EPS denominator changes when a company moves between loss and profit. Buybacks labels require both fewer average shares and reported repurchase cash; other reductions are labelled Share reduction. Dilution labels mean a higher average share count, which can reflect compensation, option exercise, acquisitions or financing. Selected acquisition explanations are separately sourced, dated and expire as the comparison moves beyond the event. Buyback cash retains its exact period and can coexist with net share growth.",
                "stress": "Non-financial businesses require negative operating cash flow over four contiguous fiscal quarters plus cash reserves down at least 20% and $10 million, or reported current plus noncurrent debt up at least 25% and $25 million. Balance-sheet comparisons use the same filing and disclose both dates. Banking and insurance are excluded from this screen. Missing components are excluded rather than set to zero. These are report-based warning signals, not default probabilities.",
                "updates": "Latest fiscal period matched to each company's current sector report, without a waiting period after quarter end. Tracked company submissions are checked daily for newly accepted filings; a successful history refresh triggers this snapshot. Nightly filing indices provide a shared backstop. A delayed daily run retains the last successful snapshot. All visitors read one prepared snapshot; no visitor-triggered SEC or market-price requests."}}
