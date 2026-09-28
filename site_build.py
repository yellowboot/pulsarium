"""
Static SEO pages for pulsarium.finance, built from the news archive.

fetch_news.py keeps only the newest 80 headlines in news_data.js, so on
its own the site is two URLs. This script, run right after it (the
GitHub Actions workflow does), folds every news_data.js into a day-by-day
archive and turns that history into pages people search for:

  data/archive/YYYY-MM-DD.json   every headline seen, by publish day (UTC)
  news/<ticker>/                 one page per company/ticker (last 30 days)
  news/sector/<slug>/            one page per sector
  news/daily/<date>/             one page per day, plus news/daily/
  news/companies/                the A–Z hub linking all of the above
  news/feed.xml                  RSS of the latest headlines
  sitemap.xml                    every indexable page

Tickers are re-detected on every build with fetch_news.detect_watchlist_
matches, so a company added to COMPANY_MAP shows up in older headlines
too. Sentiment, importance and content type come from the latest
snapshot (DeepSeek refines them there).

Usage:
  python site_build.py                     # after fetch_news.py
  python site_build.py --backfill          # one-off: archive every news_data.js in git history
  python site_build.py --indexnow FILE     # ping IndexNow with the URLs listed in FILE

Pure standard library, like fetch_news.py.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

import fetch_news
import site_pages

SITE ="https://pulsarium.finance"
APP_URL = "https://app.pulsarium.finance/"
ARCHIVE_DIR = os.path.join("data", "archive")
WINDOW_DAYS = 30
# A company/sector page with fewer headlines than this in the window is
# built (links keep working) but marked noindex and left out of the
# sitemap, so search engines don't see near-empty pages.
MIN_INDEXABLE = 3
MIN_INDEXABLE_DAY = 5
ITEMS_PER_PAGE = 60
BREAKING_IMPORTANCE = 60  # same threshold as the LIVE/BREAKING badge on /news/
ARCHIVE_FIELDS = ("title", "link", "description", "source", "published",
                  "sentiment", "importance", "content_type")
INDEXNOW_KEY = "8dc724623930bde2d9ce70c606857a9e"

# Handwritten pages that belong in the sitemap next to the generated ones.
STATIC_PAGES = [
    ("/", "weekly"),  # cabinet feature pages come from site_pages.py
    ("/news/", "hourly"),
]

esc = html.escape


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def parse_news_data(text: str) -> dict:
    start = text.index("{")
    end = text.rindex("}")
    return json.loads(text[start:end + 1])


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def item_key(item: dict) -> str:
    return (item.get("link") or "").strip() or item["title"].lower()[:60]


class Archive:
    """data/archive/YYYY-MM-DD.json files, loaded on demand."""

    def __init__(self, root: str = ARCHIVE_DIR):
        self.root = root
        self.days = {}
        self.dirty = set()

    def path(self, day: str) -> str:
        return os.path.join(self.root, f"{day}.json")

    def load(self, day: str) -> dict:
        if day not in self.days:
            entries = {}
            if os.path.exists(self.path(day)):
                with open(self.path(day), encoding="utf-8") as f:
                    for item in json.load(f):
                        entries[item_key(item)] = item
            self.days[day] = entries
        return self.days[day]

    def all_days(self) -> list:
        if not os.path.isdir(self.root):
            return []
        return sorted(name[:-5] for name in os.listdir(self.root) if name.endswith(".json"))

    def add(self, items: list, seen_at: datetime) -> None:
        for item in items:
            published = parse_time(item.get("published"))
            # a missing or future date files under the day it was seen; a
            # months-old one (stale feeds do this) isn't archived at all
            if published is None or published > seen_at + timedelta(days=1):
                published = seen_at
            if published < seen_at - timedelta(days=WINDOW_DAYS):
                continue
            day = published.date().isoformat()
            entries = self.load(day)
            key = item_key(item)
            record = {field: item.get(field) for field in ARCHIVE_FIELDS}
            record["published"] = published.isoformat()
            previous = entries.get(key)
            record["first_seen"] = previous["first_seen"] if previous else seen_at.isoformat()
            if previous != record:
                entries[key] = record
                self.dirty.add(day)

    def save(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        for day in sorted(self.dirty):
            items = sorted(self.days[day].values(), key=lambda i: i["published"], reverse=True)
            # one headline per line: compact, and git diffs stay readable;
            # _underscore keys are build-time tags, not archive data
            lines = ",\n".join(
                json.dumps({k: v for k, v in item.items() if not k.startswith("_")},
                           ensure_ascii=False, separators=(",", ":"))
                for item in items
            )
            with open(self.path(day), "w", encoding="utf-8", newline="\n") as f:
                f.write(f"[\n{lines}\n]\n")
        self.dirty.clear()

    def items_for(self, days: list) -> list:
        items = []
        for day in days:
            items.extend(self.load(day).values())
        return items


def backfill(archive: Archive) -> None:
    """Folds every news_data.js in git history into the archive."""
    hashes = subprocess.run(
        ["git", "log", "--reverse", "--format=%H %cI", "--", "news_data.js"],
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    count = 0
    for line in hashes:
        if not line.strip():
            continue
        commit, committed = line.split(" ", 1)
        raw = subprocess.run(["git", "show", f"{commit}:news_data.js"], capture_output=True, check=True).stdout
        try:
            payload = parse_news_data(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        seen_at = parse_time(payload.get("generated_at")) or parse_time(committed)
        archive.add(payload.get("items", []), seen_at)
        count += 1
    archive.save()
    print(f"Backfilled {count} snapshots into {archive.root}")


# ---------------------------------------------------------------------------
# Companies, sectors, tagging
# ---------------------------------------------------------------------------

def ticker_slug(ticker: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")


def sector_slug(sector: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sector.lower()).strip("-")


def build_companies() -> dict:
    companies = {}
    for entry in fetch_news.COMPANY_MAP:
        name = entry["names"][0] if entry["names"] else entry["ticker"]
        companies[entry["ticker"]] = {
            "ticker": entry["ticker"],
            "name": name,
            "sector": entry["sector"],
            "slug": ticker_slug(entry["ticker"]),
        }
    return companies


def company_label(company: dict) -> str:
    """"Nvidia (NVDA)", or just "UBS" when the name is the ticker."""
    if company["name"].upper() == company["ticker"].upper():
        return company["ticker"]
    return f"{company['name']} ({company['ticker']})"


# What the pages call the headlines about a ticker in this sector.
NON_STOCK_SECTORS = {"Bonds / Macro": "news", "Cryptocurrencies": "news", "ETFs / Indices": "news"}


def news_noun(company: dict) -> str:
    return NON_STOCK_SECTORS.get(company["sector"], "stock news")


def tag_items(items: list) -> list:
    """Adds _tickers, _sectors and _time; items already tagged keep theirs
    (the archive hands out the same dicts, so each is matched once)."""
    for item in items:
        if "_tickers" not in item:
            matches = fetch_news.detect_watchlist_matches(f"{item['title']} {item.get('description') or ''}")
            item["_tickers"] = [m["ticker"] for m in matches]
            item["_sectors"] = sorted({m["sector"] for m in matches})
            item["_time"] = parse_time(item["published"])
    items.sort(key=lambda i: i["_time"], reverse=True)
    return items


def mood(items: list) -> dict:
    """Sentiment over market-signal headlines only, like Quick Analysis on /news/."""
    signal = [i for i in items if (i.get("content_type") or "market_signal") == "market_signal"]
    pos = sum(1 for i in signal if i.get("sentiment") == "positive")
    neg = sum(1 for i in signal if i.get("sentiment") == "negative")
    neu = len(signal) - pos - neg
    label = "neutral"
    if pos > neg * 1.3:
        label = "leaning optimistic"
    if neg > pos * 1.3:
        label = "leaning cautious"
    return {"total": len(signal), "positive": pos, "negative": neg, "neutral": neu, "label": label}


# ---------------------------------------------------------------------------
# HTML pieces
# ---------------------------------------------------------------------------

FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' "
    "height='64' rx='16' fill='%2304050c'/%3E%3Ccircle cx='32' cy='32' r='24' fill='none' stroke='%23ff2bd6' "
    "stroke-width='1.5' opacity='0.45'/%3E%3Ccircle cx='32' cy='32' r='16' fill='none' stroke='%2300f0ff' "
    "stroke-width='2' opacity='0.6'/%3E%3Cpath d='M32 32 L29.5 4 L34.5 4 Z M32 32 L29.5 60 L34.5 60 Z' "
    "fill='%2300f0ff' opacity='0.85' transform='rotate(25 32 32)'/%3E%3Ccircle cx='32' cy='32' r='7' "
    "fill='%23ffffff'/%3E%3C/svg%3E"
)


def page(*, path: str, title: str, description: str, body: str, crumbs: list,
         indexable: bool = True, extra_head: str = "") -> str:
    url = SITE + path
    crumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": n + 1, "name": name, "item": SITE + href}
            for n, (name, href) in enumerate(crumbs)
        ],
    }
    crumb_links = " <span aria-hidden=\"true\">›</span> ".join(
        f'<a href="{esc(href)}">{esc(name)}</a>' if n < len(crumbs) - 1 else f'<span aria-current="page">{esc(name)}</span>'
        for n, (name, href) in enumerate(crumbs)
    )
    robots = "index, follow, max-image-preview:large" if indexable else "noindex, follow"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Pulsarium">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{SITE}/og/pulsarium-og-v2.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE}/og/pulsarium-og-v2.jpg">
<link rel="alternate" type="application/rss+xml" title="Pulsarium market news" href="/news/feed.xml">
<link rel="icon" type="image/svg+xml" href="{FAVICON}">
<link rel="stylesheet" href="/fonts/fonts.css">
<link rel="stylesheet" href="/assets/site.css">
<script type="application/ld+json">{json.dumps(crumb_ld, ensure_ascii=False)}</script>
{extra_head}</head>
<body>
<div class="bg-layer bg-nebula" aria-hidden="true"></div>
<div class="bg-layer bg-stars" aria-hidden="true"></div>
<div class="bg-scan" aria-hidden="true"></div>

<header class="site-header">
  <a class="brand" href="/" aria-label="Pulsarium home">
    <span class="mini-pulsar" aria-hidden="true"><span class="mini-ping"></span><span class="mini-ping late"></span><span class="mini-core"></span></span>
    <span class="wordmark">PULSARIUM</span>
  </a>
  <nav class="site-nav" aria-label="Main">
    <a href="/portfolio-tracker/">Portfolio tracker</a>
    <a href="/news/">Live news</a>
    <a class="nav-secondary" href="/news/companies/">Companies</a>
    <a class="nav-secondary" href="/news/daily/">Daily digest</a>
    <a class="btn btn-primary" href="{APP_URL}">Sign in</a>
  </nav>
</header>

<main class="page">
<nav class="crumbs" aria-label="Breadcrumb">{crumb_links}</nav>
{body}
</main>

<footer class="site-footer">
  <nav class="footer-cabinet" aria-label="Personal cabinet">{"".join(f'<a href="{href}">{esc(name)}</a>' for href, name in site_pages.CABINET_LINKS)}</nav>
  <p>Headlines come from public RSS feeds and link to their original publishers. Sentiment and importance are automated scores, not investment advice.</p>
  <nav class="footer-legal" aria-label="Legal">
    <a class="footer-faq" href="{APP_URL}?legal=faq">FAQ</a>
    <a href="{APP_URL}?legal=disclaimer">Financial notice</a>
    <a href="{APP_URL}?legal=imprint">Imprint</a>
    <a href="{APP_URL}?legal=privacy">Privacy</a>
    <a href="{APP_URL}?legal=terms">Terms</a>
    <a href="/news/feed.xml">RSS</a>
    <button type="button" class="link-button" data-analytics-settings>Analytics settings</button>
  </nav>
</footer>
<script src="/assets/analytics.js" defer></script>
</body>
</html>
"""


def fmt_day(day: date) -> str:
    return f"{day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}"


def fmt_short_day(day: date) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def sentiment_bar(m: dict) -> str:
    total = m["total"] or 1
    pos = round(m["positive"] / total * 100)
    neg = round(m["negative"] / total * 100)
    neu = max(0, 100 - pos - neg)
    return (
        f'<div class="sent-bar" role="img" aria-label="{m["positive"]} positive, {m["neutral"]} neutral, '
        f'{m["negative"]} negative">'
        f'<span class="sb-pos" style="width:{pos}%"></span><span class="sb-neu" style="width:{neu}%"></span>'
        f'<span class="sb-neg" style="width:{neg}%"></span></div>'
        f'<div class="sent-legend"><span><i class="dot pos"></i>{m["positive"]} positive</span>'
        f'<span><i class="dot neu"></i>{m["neutral"]} neutral</span>'
        f'<span><i class="dot neg"></i>{m["negative"]} negative</span></div>'
    )


SENT_LABELS = {"positive": "Positive", "negative": "Negative", "neutral": "Neutral"}


def item_html(item: dict, companies: dict, skip_ticker: str = None) -> str:
    when = item["_time"]
    macro = (item.get("content_type") or "market_signal") == "macro_context"
    sentiment = item.get("sentiment") if item.get("sentiment") in SENT_LABELS else "neutral"
    badge = ('<span class="badge badge-macro">Macro context</span>' if macro
             else f'<span class="badge badge-{sentiment}">{SENT_LABELS[sentiment]}</span>')
    breaking = ('<span class="badge badge-breaking">Breaking</span>'
                if (item.get("importance") or 0) >= BREAKING_IMPORTANCE else "")
    tags = "".join(
        f'<a class="tag" href="/news/{companies[t]["slug"]}/">{esc(t)}</a>'
        for t in item["_tickers"] if t in companies and t != skip_ticker
    )
    desc = f'<p>{esc(item["description"])}</p>' if item.get("description") else ""
    link = item.get("link") or "#"
    return f"""<article class="item">
  <div class="item-meta"><span class="item-source">{esc(item.get("source") or "")}</span><time datetime="{when.isoformat()}">{fmt_short_day(when.date())}, {when.strftime("%H:%M")} UTC</time>{breaking}{badge}</div>
  <h3><a href="{esc(link)}" target="_blank" rel="noopener">{esc(item["title"])}</a></h3>
  {desc}
  {f'<div class="item-tags">{tags}</div>' if tags else ""}
</article>"""


def item_list(items: list, companies: dict, skip_ticker: str = None, group_by_day: bool = True) -> str:
    if not items:
        return '<p class="empty">No headlines in the last 30 days yet. This page updates automatically when one appears.</p>'
    parts = []
    current = None
    for item in items[:ITEMS_PER_PAGE]:
        day = item["_time"].date()
        if group_by_day and day != current:
            if current is not None:
                parts.append("</div>")
            parts.append(f'<h3 class="day-head"><a href="/news/daily/{day.isoformat()}/">{fmt_day(day)}</a></h3><div class="items">')
            current = day
        parts.append(item_html(item, companies, skip_ticker))
    if group_by_day and current is not None:
        parts.append("</div>")
    body = "\n".join(parts)
    return body if group_by_day else f'<div class="items">{body}</div>'


def cta(title: str, text: str, button: str = "Get started free") -> str:
    return f"""<aside class="panel cta">
  <div>
    <span class="eyebrow">Personal cabinet</span>
    <h2>{esc(title)}</h2>
    <p>{esc(text)}</p>
  </div>
  <a class="btn btn-primary" href="{APP_URL}">{esc(button)}</a>
</aside>"""


def sources_phrase(items: list) -> str:
    counts = {}
    for item in items:
        counts[item.get("source")] = counts.get(item.get("source"), 0) + 1
    names = [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if s][:3]
    if not names:
        return "public financial news sources"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def ticker_page(company: dict, items: list, companies: dict, sector_counts: dict, now: datetime) -> tuple:
    ticker = company["ticker"]
    label = company_label(company)
    noun = news_noun(company)
    week = [i for i in items if i["_time"] >= now - timedelta(days=7)]
    m = mood(items)
    indexable = len(items) >= MIN_INDEXABLE
    sources = sources_phrase(items)
    path = f"/news/{company['slug']}/"

    peers = sorted(
        (c for c in companies.values() if c["sector"] == company["sector"] and c["ticker"] != ticker),
        key=lambda c: (-sector_counts.get(c["ticker"], 0), c["ticker"]),
    )
    peer_links = "".join(
        f'<a class="chip" href="/news/{c["slug"]}/">{esc(c["ticker"])}<small>{esc(c["name"])}</small></a>'
        for c in peers if sector_counts.get(c["ticker"], 0) > 0
    )
    last = (f'{fmt_short_day(items[0]["_time"].date())}, {items[0]["_time"].strftime("%H:%M")} UTC'
            if items else "—")

    title = f"{label} {noun} today: headlines & sentiment | Pulsarium"
    if items:
        pos_share = round(m["positive"] / m["total"] * 100) if m["total"] else 0
        description = (f"Latest {label} headlines from {sources}, scored for sentiment: "
                       f"{len(week)} in the last 7 days, {len(items)} in 30 days, {pos_share}% positive. "
                       f"Updated through the trading day.")
    else:
        description = f"{label} headlines from public financial news, scored for sentiment and importance."

    body = f"""<header class="page-head">
  <span class="eyebrow">{esc(company["sector"])} · {esc(ticker)}</span>
  <h1>{esc(label)} {esc(noun)}</h1>
  <p class="lead">Every headline mentioning {esc(company["name"])} from {esc(sources)} over the last 30 days, each scored for sentiment and importance. Updated automatically through the trading day.</p>
</header>

<section class="stats">
  <div class="panel stat"><span class="stat-label">Headlines</span><strong>{len(week)}</strong><small>last 7 days · {len(items)} in 30 days</small></div>
  <div class="panel stat stat-wide"><span class="stat-label">Sentiment, 30 days</span>{sentiment_bar(m)}</div>
  <div class="panel stat"><span class="stat-label">Latest headline</span><strong class="stat-text">{last}</strong><small><a href="/news/sector/{sector_slug(company["sector"])}/">{esc(company["sector"])}</a></small></div>
</section>

{cta(f"Follow {ticker} in your own cabinet",
     f"Add {company['name']} to a free watchlist, set a price alert and see its headlines next to your portfolio — private, with two-factor sign-in.")}

<section class="block">
  <h2>Latest {esc(ticker)} headlines</h2>
  {item_list(items, companies, skip_ticker=ticker)}
</section>

{f'<section class="block"><h2>More in {esc(company["sector"])}</h2><div class="chips">{peer_links}</div></section>' if peer_links else ""}
"""
    crumbs = [("Home", "/"), ("News", "/news/"), ("Companies", "/news/companies/"), (ticker, path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs, indexable=indexable), indexable


def sector_page(sector: str, items: list, companies: dict, counts: dict, now: datetime) -> tuple:
    slug = sector_slug(sector)
    path = f"/news/sector/{slug}/"
    week = [i for i in items if i["_time"] >= now - timedelta(days=7)]
    m = mood(items)
    indexable = len(items) >= MIN_INDEXABLE
    members = sorted((c for c in companies.values() if c["sector"] == sector),
                     key=lambda c: (-counts.get(c["ticker"], 0), c["ticker"]))
    member_links = "".join(
        f'<a class="chip" href="/news/{c["slug"]}/">{esc(c["ticker"])}<small>{esc(c["name"])} · {counts.get(c["ticker"], 0)}</small></a>'
        if counts.get(c["ticker"], 0) else
        f'<span class="chip chip-muted">{esc(c["ticker"])}<small>{esc(c["name"])}</small></span>'
        for c in members
    )
    title = f"{sector} news today: headlines & sentiment | Pulsarium"
    description = (f"{sector} headlines from {sources_phrase(items)}: {len(week)} in the last 7 days, "
                   f"sentiment {m['label']}. Covers {', '.join(c['name'] for c in members[:5])} and more.")
    body = f"""<header class="page-head">
  <span class="eyebrow">Sector</span>
  <h1>{esc(sector)} news</h1>
  <p class="lead">Headlines about {esc(sector.lower())} companies from public financial news over the last 30 days, scored for sentiment and importance.</p>
</header>

<section class="stats">
  <div class="panel stat"><span class="stat-label">Headlines</span><strong>{len(week)}</strong><small>last 7 days · {len(items)} in 30 days</small></div>
  <div class="panel stat stat-wide"><span class="stat-label">Sentiment, 30 days</span>{sentiment_bar(m)}</div>
</section>

<section class="block">
  <h2>Companies</h2>
  <div class="chips">{member_links}</div>
</section>

{cta(f"Track {sector.lower()} in one place",
     "Build a watchlist of the names you follow, get alerts at your price levels and a feed filtered to your holdings.")}

<section class="block">
  <h2>Latest {esc(sector)} headlines</h2>
  {item_list(items, companies)}
</section>
"""
    crumbs = [("Home", "/"), ("News", "/news/"), ("Companies", "/news/companies/"), (sector, path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs, indexable=indexable), indexable


def daily_page(day: str, items: list, companies: dict, prev_day, next_day) -> tuple:
    d = date.fromisoformat(day)
    path = f"/news/daily/{day}/"
    m = mood(items)
    indexable = len(items) >= MIN_INDEXABLE_DAY
    counts = {}
    for item in items:
        for t in item["_tickers"]:
            counts[t] = counts.get(t, 0) + 1
    top_tickers = [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
    top = sorted(items, key=lambda i: -(i.get("importance") or 0))[:8]
    summary = (f"{len(items)} headlines. Of the {m['total']} with a specific market target, "
               f"{m['positive']} were positive, {m['negative']} negative and {m['neutral']} neutral — "
               f"overall mood {m['label']}.")
    if top_tickers:
        summary += " Most mentioned: " + ", ".join(top_tickers[:3]) + "."
    ticker_chips = "".join(
        f'<a class="chip" href="/news/{companies[t]["slug"]}/">{esc(t)}<small>{counts[t]} headlines</small></a>'
        for t in top_tickers if t in companies
    )
    pager = '<nav class="pager">'
    pager += (f'<a href="/news/daily/{prev_day}/">← {fmt_short_day(date.fromisoformat(prev_day))}</a>' if prev_day else "<span></span>")
    pager += '<a href="/news/daily/">All days</a>'
    pager += (f'<a href="/news/daily/{next_day}/">{fmt_short_day(date.fromisoformat(next_day))} →</a>' if next_day else "<span></span>")
    pager += "</nav>"

    title = f"Stock market news for {d.day} {d.strftime('%B %Y')}: top stories & sentiment | Pulsarium"
    description = f"Stock market headlines from {fmt_day(d)}: {summary}"
    body = f"""<header class="page-head">
  <span class="eyebrow">Daily digest</span>
  <h1>Stock market news — {fmt_day(d)}</h1>
  <p class="lead">{esc(summary)}</p>
</header>

<section class="stats">
  <div class="panel stat stat-wide"><span class="stat-label">Sentiment</span>{sentiment_bar(m)}</div>
  <div class="panel stat stat-wide"><span class="stat-label">Most mentioned</span><div class="chips">{ticker_chips or '<small>—</small>'}</div></div>
</section>

<section class="block">
  <h2>Top stories</h2>
  {item_list(top, companies, group_by_day=False)}
</section>

{cta("Your portfolio, with the news that moves it",
     "Pulsarium's free cabinet tracks your holdings and dividends, alerts you at your price levels and filters this feed to what you own.")}

<section class="block">
  <h2>All headlines</h2>
  {item_list(items, companies, group_by_day=False)}
</section>
{pager}
"""
    crumbs = [("Home", "/"), ("News", "/news/"), ("Daily digest", "/news/daily/"), (d.isoformat(), path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs, indexable=indexable), indexable


def daily_index(days: list, per_day: dict) -> tuple:
    path = "/news/daily/"
    rows = []
    for day in reversed(days):
        items = per_day[day]
        m = mood(items)
        d = date.fromisoformat(day)
        rows.append(
            f'<a class="day-row" href="/news/daily/{day}/"><strong>{fmt_day(d)}</strong>'
            f'<span>{len(items)} headlines</span><span class="mood mood-{m["label"].split()[-1]}">{m["label"]}</span></a>'
        )
    body = f"""<header class="page-head">
  <span class="eyebrow">Daily digest</span>
  <h1>Stock market news, day by day</h1>
  <p class="lead">Every trading day's headlines in one place: top stories by importance, the day's sentiment and the most mentioned companies.</p>
</header>
<section class="block day-list">{"".join(rows)}</section>
"""
    title = "Daily stock market news digest: top stories by day | Pulsarium"
    description = "An archive of daily stock market headlines with top stories, sentiment and the most mentioned companies for each day."
    crumbs = [("Home", "/"), ("News", "/news/"), ("Daily digest", path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs), True


def companies_hub(companies: dict, counts: dict, sector_totals: dict) -> tuple:
    path = "/news/companies/"
    sections = []
    for sector in sorted({c["sector"] for c in companies.values()}):
        members = sorted((c for c in companies.values() if c["sector"] == sector), key=lambda c: c["name"].lower())
        links = "".join(
            f'<a class="chip" href="/news/{c["slug"]}/">{esc(c["ticker"])}<small>{esc(c["name"])} · {counts.get(c["ticker"], 0)}</small></a>'
            if counts.get(c["ticker"], 0) else
            f'<span class="chip chip-muted">{esc(c["ticker"])}<small>{esc(c["name"])}</small></span>'
            for c in members
        )
        sections.append(
            f'<section class="block"><h2><a href="/news/sector/{sector_slug(sector)}/">{esc(sector)}</a>'
            f'<small>{sector_totals.get(sector, 0)} headlines in 30 days</small></h2><div class="chips">{links}</div></section>'
        )
    body = f"""<header class="page-head">
  <span class="eyebrow">Browse</span>
  <h1>Stock news by company and sector</h1>
  <p class="lead">{len(companies)} companies, funds and market themes Pulsarium tags in the news. Each page collects the last 30 days of headlines with a sentiment summary. The number is headlines in the last 30 days.</p>
</header>
{cta("Follow the companies you own",
     "Pick your names once: the free cabinet keeps their news, prices, dividends and alerts together.")}
{"".join(sections)}
"""
    title = "Stock news by company and sector | Pulsarium"
    description = "Browse stock market news by company and sector: Nvidia, Apple, Microsoft, Tesla and 150 more, each with headlines and sentiment from the last 30 days."
    crumbs = [("Home", "/"), ("News", "/news/"), ("Companies", path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs), True


def rss_feed(items: list, now: datetime) -> str:
    entries = []
    for item in items[:50]:
        categories = "".join(f"<category>{esc(t)}</category>" for t in item["_tickers"])
        entries.append(f"""  <item>
    <title>{esc(item["title"])}</title>
    <link>{esc(item.get("link") or SITE + "/news/")}</link>
    <guid isPermaLink="false">{esc(item_key(item))}</guid>
    <pubDate>{format_datetime(item["_time"])}</pubDate>
    <source url="{SITE}/news/feed.xml">{esc(item.get("source") or "")}</source>
    <description>{esc(item.get("description") or "")}</description>
    {categories}
  </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Pulsarium — stock market news</title>
  <link>{SITE}/news/</link>
  <atom:link href="{SITE}/news/feed.xml" rel="self" type="application/rss+xml"/>
  <description>Stock market headlines from public sources, tagged by ticker and sector.</description>
  <language>en</language>
  <lastBuildDate>{format_datetime(now)}</lastBuildDate>
{chr(10).join(entries)}
</channel>
</rss>
"""


def sitemap(entries: list) -> str:
    urls = "\n".join(
        f"  <url>\n    <loc>{SITE}{esc(path)}</loc>\n"
        + (f"    <lastmod>{lastmod}</lastmod>\n" if lastmod else "")
        + f"    <changefreq>{freq}</changefreq>\n  </url>"
        for path, lastmod, freq in entries
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def write_if_changed(path: str, content: str, changed: list) -> None:
    """Writes only real changes, and records the page URL for IndexNow."""
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == content:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    changed.append(path)


def url_file(url_path: str) -> str:
    return url_path.strip("/").replace("/", os.sep) + os.sep + "index.html"


def build(archive: Archive, now: datetime, full: bool = False) -> list:
    """Regenerates the pages; returns the URLs of indexable pages that changed."""
    companies = build_companies()
    today = now.date()
    window_days = [(today - timedelta(days=n)).isoformat() for n in range(WINDOW_DAYS)]
    window = tag_items(archive.items_for(window_days))
    window = [i for i in window if i["_time"] >= now - timedelta(days=WINDOW_DAYS)]

    by_ticker, by_sector = {}, {}
    for item in window:
        for t in item["_tickers"]:
            by_ticker.setdefault(t, []).append(item)
        for s in item["_sectors"]:
            by_sector.setdefault(s, []).append(item)
    counts = {t: len(v) for t, v in by_ticker.items()}
    sector_totals = {s: len(v) for s, v in by_sector.items()}

    changed_files, sitemap_entries, indexable_urls = [], [], {}

    def emit(result, lastmod=None, freq="daily"):
        path, content, indexable = result
        before = len(changed_files)
        write_if_changed(url_file(path), content, changed_files)
        if indexable:
            sitemap_entries.append((path, lastmod, freq))
            if len(changed_files) > before:
                indexable_urls[path] = True

    for company in companies.values():
        items = by_ticker.get(company["ticker"], [])
        lastmod = items[0]["_time"].date().isoformat() if items else None
        emit(ticker_page(company, items, companies, counts, now), lastmod, "hourly")

    for sector in sorted({c["sector"] for c in companies.values()}):
        items = by_sector.get(sector, [])
        lastmod = items[0]["_time"].date().isoformat() if items else None
        emit(sector_page(sector, items, companies, counts, now), lastmod, "hourly")

    # A past day's page only changes when its "next day" link appears, so
    # only the last three days are rebuilt (plus any missing page); --full
    # rebuilds them all, e.g. after a template or COMPANY_MAP change.
    days = archive.all_days()
    per_day = {day: list(archive.load(day).values()) for day in days}
    for n, day in enumerate(days):
        day_path = f"/news/daily/{day}/"
        freq = "daily" if n + 1 == len(days) else "monthly"
        if full or n >= len(days) - 3 or not os.path.exists(url_file(day_path)):
            prev_day = days[n - 1] if n > 0 else None
            next_day = days[n + 1] if n + 1 < len(days) else None
            emit(daily_page(day, tag_items(per_day[day]), companies, prev_day, next_day), day, freq)
        elif len(per_day[day]) >= MIN_INDEXABLE_DAY:
            sitemap_entries.append((day_path, day, freq))
    if days:
        emit(daily_index(days, per_day), days[-1], "daily")
    emit(companies_hub(companies, counts, sector_totals), today.isoformat(), "daily")

    # the personal cabinet's feature and broker-import pages (site_pages.py)
    for spec in site_pages.all_pages():
        crumbs = [("Home", "/")] + ([spec["parent"]] if spec.get("parent") else []) + [(spec["crumb"], spec["path"])]
        emit((spec["path"], page(path=spec["path"], title=spec["title"], description=spec["description"],
                                 body=spec["body"], crumbs=crumbs, extra_head=spec.get("head", "")), True),
             None, "monthly")

    write_if_changed(os.path.join("news", "feed.xml"), rss_feed(window, now), changed_files)
    static = [(p, None, f) for p, f in STATIC_PAGES]
    write_if_changed("sitemap.xml", sitemap(static + sorted(sitemap_entries)), changed_files)

    print(f"Pages: {len(companies)} companies, {len(by_sector)} sectors with news, {len(days)} days; "
          f"{len(changed_files)} files changed, {len(sitemap_entries) + len(static)} URLs in the sitemap")
    return [SITE + p for p in indexable_urls]


def ping_indexnow(urls_file: str) -> None:
    with open(urls_file, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]
    if not urls:
        print("IndexNow: nothing changed")
        return
    payload = json.dumps({
        "host": "pulsarium.finance",
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE}/{INDEXNOW_KEY}.txt",
        "urlList": urls[:10000],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.indexnow.org/indexnow", data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"IndexNow: {len(urls)} URLs, HTTP {response.status}")
    except Exception as error:  # a failed ping must never fail the news update
        print(f"IndexNow ping failed: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backfill", action="store_true", help="archive every news_data.js in git history first")
    parser.add_argument("--indexnow", metavar="FILE", help="ping IndexNow with the URLs in FILE and exit")
    parser.add_argument("--changed-urls", metavar="FILE", help="write the changed indexable URLs here")
    parser.add_argument("--full", action="store_true", help="rebuild every daily page, not just the last three days")
    args = parser.parse_args()

    if args.indexnow:
        ping_indexnow(args.indexnow)
        return

    archive = Archive()
    if args.backfill:
        backfill(archive)

    with open("news_data.js", encoding="utf-8") as f:
        payload = parse_news_data(f.read())
    now = datetime.now(timezone.utc)
    archive.add(payload.get("items", []), parse_time(payload.get("generated_at")) or now)
    archive.save()

    changed = build(archive, now, full=args.full or args.backfill)
    if args.changed_urls:
        with open(args.changed_urls, "w", encoding="utf-8") as f:
            f.write("\n".join(changed))


if __name__ == "__main__":
    sys.exit(main())
