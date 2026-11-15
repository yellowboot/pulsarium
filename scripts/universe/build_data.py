"""Market Universe data (/universe/data.js), rebuilt by the SEC workflow.

Everything here comes from sources that are already public:
  - our own snapshots in Supabase (read with the site's publishable key): sec_sector_analytics (sectors, their largest
    companies, financial signals), sec_insider_radar (Form 4 purchases and sales), industry_valuation (P/E medians);
  - assets/season-q3-2026.js (the move on the first trading day after the Q3 report, beat / missed);
  - Yahoo's public chart endpoint: three months of daily closes per company, the S&P 500 and the VIX;
  - scripts/universe/caps_snapshot.json: market values on one day (Nasdaq's public screener, saved by hand), carried
    to today by each company's price change since that day;
  - scripts/universe/extra_figures.json: own SEC figures for the companies our sector snapshot does not list
    (fill_figures.ts).

Nothing is invented: a figure that is missing stays empty and the page shows "n/a". There is no price target and no
"upside"; the two scores are plain sums of the weights of the signals listed for a company.

    python scripts/universe/build_data.py            # writes universe/data.js
    python scripts/universe/build_data.py --offline <returns.json>   # prices from a saved file (tests)

A run that cannot get the S&P 500 or prices for most companies stops without touching the published file.
"""
import io
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SUPABASE = "https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/"
PUBLISHABLE_KEY = "sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF"  # the site's public read key (assets/market-map.js)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
WINDOW = 50  # trading days of returns the page works with


def snapshot(table: str) -> dict:
    request = urllib.request.Request(f"{SUPABASE}{table}?id=eq.global&select=payload",
                                     headers={"apikey": PUBLISHABLE_KEY, "Authorization": f"Bearer {PUBLISHABLE_KEY}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        rows = json.load(response)
    if not rows or not rows[0].get("payload"):
        raise RuntimeError(f"{table}: empty snapshot")
    return rows[0]["payload"]


def load_json(name: str, default=None):
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf8") as handle:
        return json.load(handle)


# ---------- the sector galaxies ----------
GALAXIES = [  # in the page's order: its index is the galaxy id
    dict(key="ai_chips", name="AI & Semiconductors", shortName="AI & Chips", sub="Semiconductors, HBM Memory & AI Servers",
         color="#3987e5", sectors=["semiconductors", "hardware"]),
    dict(key="energy_grid", name="Energy & Power Grid", shortName="Energy & Power", sub="Oil, Gas, LNG & Data-Center Power Utilities",
         color="#d95926", sectors=["energy", "utilities"]),
    dict(key="health_pharma", name="Healthcare & Pharma", shortName="Healthcare", sub="Biopharma, GLP-1 Leaders, MedTech & Payers",
         color="#199e70", sectors=["pharmaceuticals", "healthcare"]),
    dict(key="defensive_staples", name="Defensive & Staples", shortName="Defensives", sub="Household Staples, Healthcare Distribution & Services",
         color="#c98500", sectors=["consumer-staples", "business-services"]),
    dict(key="consumer_retail", name="Consumer & Retail", shortName="Consumer", sub="E-Commerce, Big-Box Retail, Apparel & EVs",
         color="#d55181", sectors=["retail", "consumer-products", "automotive"]),
    dict(key="industrials_aero", name="Industrials & Defense", shortName="Industrials", sub="Aerospace, Defense, Freight & Specialty Chemicals",
         color="#38d9c9", sectors=["industrials", "transportation", "chemicals"]),
    dict(key="finance_capital", name="Financials & REITs", shortName="Financials", sub="Money-Center Banks, Insurers, Payments & Data REITs",
         color="#9085e9", sectors=["banks", "insurance", "real-estate"]),
    dict(key="platforms_soft", name="Software & Cloud", shortName="Cloud & Platforms", sub="Hyperscalers, Enterprise SaaS & Telecom",
         color="#e66767", sectors=["software", "media-telecom"]),
]
GALAXY_OF = {sector: index for index, galaxy in enumerate(GALAXIES) for sector in galaxy["sectors"]}
FALLBACK_GALAXY = 5  # a sector we have not placed: industrials
EXTRA_GALAXY = 7     # a signal company with no known sector: software

# companies outside the sector lists that carry signals worth showing (hand-picked once; their figures are their own)
EXTRA_SYMBOLS = json.load(open(os.path.join(HERE, "extra_symbols.json"), encoding="utf8"))
SHORT_NAMES = {  # how people call them; a name only, never a figure
    "NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta Platforms",
    "BRK-B": "Berkshire Hathaway", "AVGO": "Broadcom", "LLY": "Eli Lilly", "TSLA": "Tesla", "WMT": "Walmart",
    "JPM": "JPMorgan Chase", "ORCL": "Oracle", "MA": "Mastercard", "COST": "Costco", "HD": "Home Depot",
    "PG": "Procter & Gamble", "JNJ": "Johnson & Johnson", "BAC": "Bank of America", "CRM": "Salesforce", "CVX": "Chevron",
    "KO": "Coca-Cola", "MRK": "Merck", "AMD": "AMD", "PEP": "PepsiCo", "ADBE": "Adobe", "CSCO": "Cisco", "WFC": "Wells Fargo",
    "TMUS": "T-Mobile", "PM": "Philip Morris", "IBM": "IBM", "CAT": "Caterpillar", "GE": "GE Aerospace",
    "MU": "Micron Technology", "CEG": "Constellation Energy", "VST": "Vistra", "NRG": "NRG Energy", "SNX": "TD Synnex",
    "BBIO": "BridgeBio Pharma", "COLM": "Columbia Sportswear",
}

# a signal: (positive?, weight, title). The weights are the prototype's; a score is their plain sum.
SIGNALS = {
    "stress": (False, 42, "Liquidity & Cash Burn Stress"),
    "earnings_cash": (False, 35, "Profit vs. Cash Flow Divergence"),
    "payouts": (False, 26, "Unfunded Payouts (Payouts > FCF)"),
    "working_capital": (False, 22, "Inventory & Receivables Bloat"),
    "turnaround": (True, 36, "SEC Operating Turnaround"),
    "shares": (True, 28, "Cannibal Buybacks (Share Reduction)"),
    "insider_buy": (True, 28, "Form 4 Insider Cluster Buy"),
    "insider_sell": (False, 20, "Form 4 Unplanned Insider Selling"),
    "hyper_growth": (True, 36, "SEC Revenue & Margin Breakout"),
    "solid_growth": (True, 24, "Operating Leverage Expansion"),
    "beat_dip": (True, 24, "Beat Forecast, Stock Fell"),
    "margin_compression": (False, 28, "Revenue & Margin Compression"),
}
FALLBACK_DETAIL = {
    "stress": "Negative 12M operating cash flow combined with >20% drop in cash reserves",
    "earnings_cash": "Positive GAAP net income while operating cash flow turned negative",
    "payouts": "Dividends and buybacks exceeded free cash flow (funded via debt or cash drawdown)",
    "working_capital": "Inventories and accounts receivable grew significantly faster than revenue YoY",
    "turnaround": "Swung from operating loss to positive operating profit YoY in latest 10-Q/10-K",
    "shares": "Net diluted share count reduced YoY via cash share repurchases",
}


def signal(code: str, detail: str) -> dict:
    positive, weight, title = SIGNALS[code]
    return {"code": code, "type": "bullish" if positive else "bearish", "weight": weight, "title": title, "detail": detail}


def filing_detail(group: str, item: dict) -> str:
    """One line from the figures of a financial signal, falling back to the plain description."""
    parts = item.get("signals") or []
    first = parts[0] if parts else {}
    second = parts[1] if len(parts) > 1 else {}
    try:
        if group == "shares" and first.get("change_pct") is not None:
            cash = second.get("current")
            return f"Share count reduced by {abs(first['change_pct']):.1f}% YoY" + (f" (${cash / 1e9:.2f}B cash buybacks)" if cash and cash >= 1e8 else "")
        if group == "stress" and first.get("current") is not None:
            drop = second.get("change_pct")
            return f"Operating cash flow -${abs(first['current']) / 1e6:.0f}M" + (f", cash reserves fell {abs(drop):.0f}%" if drop else "")
        if group == "earnings_cash" and first.get("current") is not None and second.get("current"):
            return f"GAAP profit +${first['current'] / 1e6:.0f}M, but operating cash flow is -${abs(second['current']) / 1e6:.0f}M"
        if group == "payouts" and first.get("gap_amount"):
            return f"Shareholder payouts exceeded free cash flow by ${first['gap_amount'] / 1e6:.0f}M"
        if group == "turnaround" and first.get("current") and first.get("previous"):
            return f"Operating income swung from -${abs(first['previous']) / 1e6:.0f}M loss to +${first['current'] / 1e6:.0f}M profit"
        if group == "working_capital" and first.get("change_pct"):
            return f"Inventories/receivables jumped +{first['change_pct']:.0f}% YoY (outpacing revenue growth)"
    except (TypeError, ValueError):
        pass
    return FALLBACK_DETAIL[group]


def clean_name(name: str) -> str:
    text = re.sub(r"\s*/[a-z]+/?$", "", str(name or ""), flags=re.I).strip()
    if text.isupper() and len(text) > 4:
        text = text.title()
    text = re.sub(r"\b(Inc\.?|Corp\.?|Corporation|Company|Co\.?|Plc|Group|Holdings?)\b", "", text, flags=re.I).strip(" ,.-")
    return text or name


def build_companies(sec: dict, radar: dict, valuation: dict, season: dict, extra: dict) -> tuple:
    sectors = {sector["id"]: sector for sector in sec.get("sectors", [])}
    industry_of = valuation.get("companies", {})
    season_of = {row[0].upper(): {"name": row[1], "move": float(row[5]), "beat": int(row[6])} for row in season.get("rows", [])}

    filing_signals, filing_item = {}, {}
    for group, items in (sec.get("financial_signals", {}).get("groups", {}) or {}).items():
        if group not in FALLBACK_DETAIL:
            continue
        for item in items:
            symbol = str(item.get("symbol") or "").upper()
            if not symbol:
                continue
            filing_item[symbol] = item
            filing_signals.setdefault(symbol, []).append(signal(group, filing_detail(group, item)))
    for item in (radar.get("clusters") or []) + (radar.get("large") or []):
        symbol, amount = str(item.get("symbol") or "").upper(), float(item.get("amount") or 0)
        if symbol and amount >= 200_000 and not any(s["code"] == "insider_buy" for s in filing_signals.get(symbol, [])):
            people = item.get("people") or []
            who = people[0].get("name", "Officer") if people else "Insiders"
            filing_signals.setdefault(symbol, []).append(signal("insider_buy", f"{who} & executives bought ${amount / 1e6:.2f}M in open-market stock"))
    sales = radar.get("sales", {}) or {}
    for item in (sales.get("clusters") or []) + (sales.get("large") or []):
        symbol = str(item.get("symbol") or "").upper()
        amount, planned = float(item.get("amount") or 0), float(item.get("planned_amount") or 0)
        if symbol and amount - planned >= 12_000_000 and not any(s["code"] == "insider_sell" for s in filing_signals.get(symbol, [])):
            filing_signals.setdefault(symbol, []).append(signal("insider_sell", f"Executives sold ${(amount - planned) / 1e6:.1f}M outside 10b5-1 plans"))

    raw = {}
    for sector in sec.get("sectors", []):
        for company in sector.get("companies", []):
            symbol = company["symbol"].upper()
            rev = company.get("revenue_yoy", company.get("ttm_revenue_yoy"))
            ttm = company.get("ttm_revenue_current") or company.get("revenue_current")
            raw[symbol] = dict(sector=sector["id"], sector_name=sector.get("name", sector["id"]), name=company.get("name", symbol),
                               rev=None if rev is None else round(float(rev), 1),
                               margin=None if company.get("margin_change") is None else round(float(company["margin_change"]), 2),
                               ttm=None if not ttm else round(float(ttm) / 1e9, 2),
                               source=company.get("source"), period_end=company.get("period_end"),
                               galaxy=GALAXY_OF.get(sector["id"], FALLBACK_GALAXY))
    withheld = []
    for symbol in EXTRA_SYMBOLS:
        if symbol in raw:
            continue
        item = filing_item.get(symbol) or {}
        sector_id = item.get("sector") or industry_of.get(symbol)
        own = extra.get(symbol) or {}
        rev, margin = own.get("revYoY"), own.get("opMarginDelta")
        # a figure that cannot stand as a plain number stays empty: revenue down by more than half or up more than
        # fourfold in a year (a deal or a tagging change), a margin change past 40 points (a loss larger than revenue)
        if rev is not None and not -50 <= rev <= 300:
            rev = margin = None
        if margin is not None and abs(margin) > 40:
            margin = None
        if rev is None:
            withheld.append(symbol)
        raw[symbol] = dict(sector=sector_id, sector_name=(sectors.get(sector_id) or {}).get("name") or "Other", name=item.get("name") or symbol,
                           rev=rev, margin=margin, ttm=own.get("ttmRevB") if rev is not None else None,
                           source=item.get("source"), period_end=own.get("periodEnd") if rev is not None else item.get("period_end"),
                           galaxy=GALAXY_OF.get(sector_id, EXTRA_GALAXY))

    companies = []
    for symbol, company in raw.items():
        earnings = season_of.get(symbol.replace("-", "."), season_of.get(symbol, {}))
        move, beat = earnings.get("move"), earnings.get("beat")
        signals = list(filing_signals.get(symbol) or filing_signals.get(symbol.replace("-", "."), []))
        rev, margin = company["rev"], company["margin"]
        if rev is not None and margin is not None:
            if rev >= 25 and margin >= 2:
                signals.insert(0, signal("hyper_growth", f"SEC 10-Q revenue +{rev:.1f}% YoY with operating margin expanding +{margin:.1f} pp"))
            elif rev >= 12 and margin >= 0.8:
                signals.insert(0, signal("solid_growth", f"SEC revenue +{rev:.1f}% YoY and operating margin +{margin:.1f} pp"))
            elif rev < -3 and margin < -1:
                signals.append(signal("margin_compression", f"SEC revenue {rev:+.1f}% YoY with operating margin falling {margin:+.1f} pp"))
        risk_so_far = sum(s["weight"] for s in signals if s["type"] != "bullish")
        if rev is not None and rev >= 8 and beat == 1 and move is not None and move <= -2 and risk_so_far < 25:
            signals.append(signal("beat_dip", f"Beat Q3 EPS with +{rev:.1f}% revenue growth, yet stock pulled back {move:+.1f}%"))
        good = [s for s in signals if s["type"] == "bullish"]
        risk = [s for s in signals if s["type"] != "bullish"]
        growth_score, risk_score = min(98, sum(s["weight"] for s in good)), min(98, sum(s["weight"] for s in risk))
        role = "black_hole_risk" if risk_score >= 22 and risk_score > growth_score else "supernova" if growth_score >= 22 else "core"
        ordered = (risk + good) if role == "black_hole_risk" else (good + risk)
        lead = ordered[0]["title"] if ordered and role != "core" else "Core Sector Compounder"
        galaxy = GALAXIES[company["galaxy"]]
        companies.append({
            "ticker": symbol,
            "name": SHORT_NAMES.get(symbol) or earnings.get("name") or clean_name(company["name"]),
            "galaxyId": company["galaxy"], "secIndustry": company["sector_name"],
            "revYoY": rev, "opMarginDelta": margin, "ttmRevB": company["ttm"],
            "marketCapB": None, "sectorPE": galaxy.get("medianPE"),
            "supernovaScore": growth_score, "blackHolePull": risk_score, "role": role, "supernovaType": lead, "riskType": lead,
            "periodEnd": company["period_end"], "secUrl": company["source"] or "",
            "signals": [{"type": s["type"], "title": s["title"], "detail": s["detail"]} for s in ordered[:4]],
            "peers": [], "earnMove": move, "earnBeat": beat,
        })
    return companies, withheld


def build_galaxies(sec: dict, valuation: dict, companies: list) -> list:
    sectors = {sector["id"]: sector for sector in sec.get("sectors", [])}
    industries = valuation.get("industries", {})
    out = []
    for index, galaxy in enumerate(GALAXIES):
        mine = [sectors[s] for s in galaxy["sectors"] if s in sectors]
        current = sum(float(s.get("revenue_current") or 0) for s in mine)
        previous = sum(float(s.get("revenue_previous") or 0) for s in mine)
        counted = sum(float(s.get("company_count") or 0) for s in mine)
        margins = [(float(s["margin_change"]), float(s.get("company_count") or 1)) for s in mine if s.get("margin_change") is not None]
        fcf = [float(s["fcf_positive_pct"]) for s in mine if s.get("fcf_positive_pct") is not None]
        medians = [m for m in ((industries.get(s) or {}).get("large", {}).get("median") or (industries.get(s) or {}).get("all", {}).get("median")
                               for s in galaxy["sectors"]) if m]
        stars = [c for c in companies if c["galaxyId"] == index]
        out.append({
            "id": index, "key": galaxy["key"], "name": galaxy["name"], "shortName": galaxy["shortName"], "sub": galaxy["sub"], "color": galaxy["color"],
            "revYoY": round((current - previous) / previous * 100, 1) if previous > 0 else None,
            "opMarginDelta": round(sum(m * w for m, w in margins) / sum(w for _, w in margins), 2) if margins and counted else None,
            "fcfPosPct": round(sum(fcf) / len(fcf), 1) if fcf else None,
            "medianPE": round(sum(medians) / len(medians), 1) if medians else None,
            "supernovaCount": sum(c["role"] == "supernova" for c in stars), "riskCount": sum(c["role"] == "black_hole_risk" for c in stars),
        })
    for company in companies:
        company["sectorPE"] = out[company["galaxyId"]]["medianPE"]
    return out


# ---------- prices ----------
def yahoo_history(symbol: str):
    name = urllib.parse.quote(symbol.replace(".", "-"))
    for host in ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"):
        try:
            request = urllib.request.Request(f"{host}/v8/finance/chart/{name}?range=3mo&interval=1d", headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=15) as response:
                result = ((json.load(response).get("chart") or {}).get("result") or [None])[0]
            if not result:
                continue
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
            days = {}
            for i, (stamp, close) in enumerate(zip(result.get("timestamp") or [], quote.get("close") or [])):
                value = adjusted[i] if i < len(adjusted) and isinstance(adjusted[i], (int, float)) and math.isfinite(adjusted[i]) and adjusted[i] > 0 else close
                if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
                    days[datetime.fromtimestamp(int(stamp), tz=timezone.utc).strftime("%Y-%m-%d")] = float(value)
            return symbol, days
        except Exception:  # noqa: BLE001 - one host or symbol failing must not stop the run
            continue
    return symbol, {}


def fetch_prices(symbols: list) -> dict:
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(yahoo_history, symbols + ["^GSPC", "^VIX"]))


def market_block(companies: list, series: dict, caps: dict) -> dict:
    index = series.get("^GSPC") or {}
    calendar = sorted(index)[-(WINDOW + 1):]
    if len(calendar) < WINDOW + 1:
        raise RuntimeError(f"S&P 500 history too short: {len(calendar)} days")
    priced = [c for c in companies if len(series.get(c["ticker"]) or {}) >= 40]
    if len(priced) < 0.8 * len(companies):
        raise RuntimeError(f"prices for only {len(priced)} of {len(companies)} companies")
    index_returns = [(index[calendar[i]] / index[calendar[i - 1]] - 1) * 100 for i in range(1, len(calendar))]
    index_mean = sum(index_returns) / WINDOW
    index_var = sum((x - index_mean) ** 2 for x in index_returns) / WINDOW
    caps_day, cap_values = caps.get("asOf"), caps.get("caps", {})
    matrix = []
    for company in companies:
        days = series.get(company["ticker"]) or {}
        path, last = [], None
        for day in calendar:
            last = days.get(day, last)
            path.append(last)
        first = next((x for x in path if x is not None), None)
        cap_now = cap_values.get(company["ticker"].replace("-", "/")) or cap_values.get(company["ticker"])
        if first is None:
            matrix.append([0.0] * WINDOW)
            company.update(px=None, r1d=None, r5d=None, r20d=None, r50d=None, volAnn=None, realBeta=None,
                           marketCapB=round(cap_now / 1e9, 1) if cap_now else None)
            continue
        path = [first if x is None else x for x in path]
        returns = [max(-25.0, min(25.0, (path[i] / path[i - 1] - 1) * 100)) for i in range(1, len(path))]
        mean = sum(returns) / WINDOW
        deviation = math.sqrt(sum((x - mean) ** 2 for x in returns) / WINDOW)
        beta = sum((returns[k] - mean) * (index_returns[k] - index_mean) for k in range(WINDOW)) / WINDOW / max(index_var, 1e-6)
        change = lambda back: round((path[-1] / path[-1 - back] - 1) * 100, 2)  # noqa: E731
        # the saved market value, carried to today by the price change since the day it was saved
        then = days.get(caps_day) if caps_day else None
        if cap_now and then:
            cap_now *= path[-1] / then
        company.update(px=round(path[-1], 2), r1d=change(1), r5d=change(5), r20d=change(20), r50d=change(WINDOW),
                       volAnn=round(deviation * math.sqrt(252), 1), realBeta=round(max(-0.5, min(3.0, beta)), 2),
                       marketCapB=round(cap_now / 1e9, 1) if cap_now else None)
        matrix.append([round(x, 3) for x in returns])
    vix = series.get("^VIX") or {}
    vix_days = sorted(vix)
    if len(vix_days) < 2:
        raise RuntimeError("no VIX history")
    return {
        "asOf": calendar[-1], "windowStart": calendar[1],
        "vix": round(vix[vix_days[-1]], 2) if vix_days else None,
        "vixChange": round(vix[vix_days[-1]] - vix[vix_days[-2]], 2) if len(vix_days) > 1 else None,
        "gspcClose": round(index[calendar[-1]], 2), "gspcRet50d": round((index[calendar[-1]] / index[calendar[0]] - 1) * 100, 2),
        "gspcReturns": [round(x, 3) for x in index_returns], "returnsMatrix": matrix,
    }


def main() -> None:
    sec, radar, valuation = snapshot("sec_sector_analytics"), snapshot("sec_insider_radar"), snapshot("industry_valuation")
    season_text = io.open(os.path.join(ROOT, "assets", "season-q3-2026.js"), encoding="utf8").read()
    season = json.loads(season_text.split("window.PULSARIUM_SEASON = ", 1)[1].rstrip(";\r\n "))
    companies, withheld = build_companies(sec, radar, valuation, season, load_json("extra_figures.json", {}) or {})
    galaxies = build_galaxies(sec, valuation, companies)
    if "--offline" in sys.argv:
        series = json.load(open(sys.argv[sys.argv.index("--offline") + 1], encoding="utf8"))
    else:
        series = fetch_prices([c["ticker"] for c in companies])
    market = market_block(companies, series, load_json("caps_snapshot.json", {}) or {})
    data = {"builtAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "secUpdatedAt": sec.get("updated_at"),
            "companies": companies, "galaxies": galaxies, "market": market}
    out = os.path.join(ROOT, "universe", "data.js")
    io.open(out, "w", encoding="utf8", newline="\n").write(
        "// Market Universe data: SEC snapshots and end-of-day prices. Rebuilt by scripts/universe/build_data.py.\n"
        "window.UNIVERSE_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"{out}: {len(companies)} companies ({sum(c['role'] == 'supernova' for c in companies)} growth, "
          f"{sum(c['role'] == 'black_hole_risk' for c in companies)} risk), prices to {market['asOf']}, VIX {market['vix']}; "
          f"no market value for {sum(c['marketCapB'] is None for c in companies)}; figures withheld: {' '.join(withheld) or 'none'}")


if __name__ == "__main__":
    main()
