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
import functools
import hashlib
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
import site_tools

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
ITEMS_SHOWN = 6    # a company page shows this many headlines, the rest behind "Show more"
PEERS_SHOWN = 12   # "More in <sector>" chips on a company page
BREAKING_IMPORTANCE = 60  # same threshold as the LIVE/BREAKING badge on /news/
ARCHIVE_FIELDS = ("title", "link", "description", "source", "published",
                  "sentiment", "importance", "content_type")
INDEXNOW_KEY = "8dc724623930bde2d9ce70c606857a9e"

# Handwritten pages that belong in the sitemap next to the generated ones.
STATIC_PAGES = [
    ("/", "weekly"),  # cabinet feature pages come from site_pages.py
    ("/news/", "hourly"),
]

NEWS_LINKS = [
    ("/news/", "Live news"),
    ("/news/companies/", "News by company"),
    ("/news/daily/", "Daily digest"),
    ("/mood/", "Mood Index"),
    ("/tools/", "Calculators"),
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
        # companies_sec.py entries carry a "label"; their first name may be a matching variant
        name = entry.get("label") or (entry["names"][0] if entry["names"] else entry["ticker"])
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


def news_only(items) -> list:
    """The archive without Nasdaq's machine-written ticker-list posts
    (fetch_news.is_templated_post): the feed drops them now, and the ones
    archived before stay in the files but leave the pages and the Mood Index."""
    return [i for i in items if not fetch_news.is_templated_post(i["title"], i.get("source"))]


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
# Pulsarium Mood Index
# ---------------------------------------------------------------------------

# A day needs this many market-signal headlines for an index value.
MIN_MOOD_ITEMS = 10
MOOD_LABELS = [(30, "Cautious"), (44, "Leaning cautious"), (55, "Neutral"), (69, "Leaning optimistic"), (100, "Optimistic")]


def mood_label(value: int) -> str:
    for limit, label in MOOD_LABELS:
        if value <= limit:
            return label
    return MOOD_LABELS[-1][1]


def mood_index(items: list, minimum: int = MIN_MOOD_ITEMS):
    """0-100 from the sentiment of market-signal headlines (macro context is
    left out, like everywhere on the site): positive +1, negative -1,
    neutral 0, each weighted by 1 + importance/50 so that big stories count
    more. 50 is neutral. None when there are too few headlines."""
    signal = [i for i in items if (i.get("content_type") or "market_signal") == "market_signal"]
    if len(signal) < minimum:
        return None
    total = score = 0.0
    for item in signal:
        weight = 1 + (item.get("importance") or 0) / 50
        sign = 1 if item.get("sentiment") == "positive" else -1 if item.get("sentiment") == "negative" else 0
        score += weight * sign
        total += weight
    value = round(50 + 50 * score / total)
    return {"value": value, "label": mood_label(value), "headlines": len(signal)}


def mood_history(days: list, per_day: dict) -> list:
    """[{date, value, label, headlines, avg7}] for every day with enough news."""
    history = []
    for day in days:
        m = mood_index(per_day[day])
        if m:
            history.append({"date": day, **m})
    for n, point in enumerate(history):
        start = date.fromisoformat(point["date"]) - timedelta(days=6)
        week = [p["value"] for p in history[: n + 1] if date.fromisoformat(p["date"]) >= start]
        point["avg7"] = round(sum(week) / len(week), 1)
    return history


def mood_chart(history: list) -> str:
    """Static SVG: a bar per day (above/below 50) and the 7-day average line."""
    if not history:
        return ""
    width, height, pad_l, pad_r, pad_t, pad_b = 1000, 300, 36, 12, 16, 34
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    first = date.fromisoformat(history[0]["date"])
    span = max(1, (date.fromisoformat(history[-1]["date"]) - first).days)
    # the scale fits the values seen (at least 30-70), so a calm month
    # isn't a row of flat stubs
    values = [p["value"] for p in history]
    low = min(30, min(values) // 10 * 10)
    high = max(70, -(-max(values) // 10) * 10)
    x = lambda d: pad_l + (date.fromisoformat(d) - first).days / span * plot_w
    y = lambda v: pad_t + (high - v) / (high - low) * plot_h
    bar_w = max(3, min(14, plot_w / (span + 1) * 0.55))
    parts = [f'<svg class="mood-chart" viewBox="0 0 {width} {height}" role="img" aria-label="Pulsarium Mood Index by day">',
             '<defs><linearGradient id="mood-line" x1="0" x2="1"><stop offset="0" stop-color="#2f7bff"/>'
             '<stop offset="1" stop-color="#00f0ff"/></linearGradient></defs>']
    for level in range(low, high + 1, 10):
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(level):.1f}" y2="{y(level):.1f}" class="grid{" mid" if level == 50 else ""}"/>'
                     f'<text x="{pad_l - 8}" y="{y(level) + 4:.1f}" class="axis" text-anchor="end">{level}</text>')
    for p in history:
        top, bottom = sorted((y(p["value"]), y(50)))
        tone = "pos" if p["value"] > 50 else "neg" if p["value"] < 50 else "neu"
        parts.append(f'<rect x="{x(p["date"]) - bar_w / 2:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{max(1.5, bottom - top):.1f}" rx="2" class="bar {tone}">'
                     f'<title>{p["date"]}: {p["value"]} ({p["label"]})</title></rect>')
    line = " ".join(f'{x(p["date"]):.1f},{y(p["avg7"]):.1f}' for p in history)
    parts.append(f'<polyline points="{line}" class="avg"/>')
    last_label = None
    for p in history:
        d = date.fromisoformat(p["date"])
        if d.weekday() == 0 and (last_label is None or (d - last_label).days >= 7):
            # a label near either edge is aligned to it instead of cut off
            px = x(p["date"])
            anchor = "end" if px > width - pad_r - 30 else "start" if px < pad_l + 30 else "middle"
            parts.append(f'<text x="{px:.1f}" y="{height - 10}" class="axis" text-anchor="{anchor}">{d.day} {d.strftime("%b")}</text>')
            last_label = d
    parts.append("</svg>")
    return "".join(parts)


def mood_page(history: list, window: list, now: datetime) -> tuple:
    path = "/mood/"
    today = history[-1] if history else None
    prev = history[-2] if len(history) > 1 else None
    week_items = [i for i in window if i["_time"] >= now - timedelta(days=7)]
    sectors = {}
    for item in week_items:
        for s in item["_sectors"]:
            sectors.setdefault(s, []).append(item)
    sector_rows = []
    for sector, items in sectors.items():
        m = mood_index(items, minimum=8)
        if m:
            sector_rows.append((sector, m))
    sector_rows.sort(key=lambda row: -row[1]["value"])
    sector_html = "".join(
        f'<a class="mood-row" href="/news/sector/{sector_slug(s)}/"><span>{esc(s)}</span>'
        f'<span class="mood-meter"><i style="width:{m["value"]}%" class="{"pos" if m["value"] > 50 else "neg" if m["value"] < 50 else "neu"}"></i></span>'
        f'<strong>{m["value"]}</strong><small>{esc(m["label"])} · {m["headlines"]} headlines</small></a>'
        for s, m in sector_rows
    )
    recent = "".join(
        f'<a class="day-row" href="/news/daily/{p["date"]}/"><strong>{fmt_day(date.fromisoformat(p["date"]))}</strong>'
        f'<span>{p["value"]} · {esc(p["label"])}</span><span>{p["headlines"]} headlines · 7-day avg {p["avg7"]:g}</span></a>'
        for p in reversed(history[-14:])
    )
    if today:
        diff = today["value"] - prev["value"] if prev else None
        change = ("first reading" if diff is None else "same as the previous day" if diff == 0
                  else f"{diff:+d} vs previous day")
        headline = f'{today["value"]} — {today["label"]}'
        gauge = f"""<section class="stats">
  <div class="panel stat mood-now"><span class="stat-label">Latest · {fmt_short_day(date.fromisoformat(today["date"]))}</span><strong>{today["value"]}<small>/100</small></strong><span class="mood-label">{esc(today["label"])}</span><small>{change}</small></div>
  <div class="panel stat"><span class="stat-label">7-day average</span><strong>{today["avg7"]:g}</strong><small>{esc(mood_label(round(today["avg7"])))}</small></div>
  <div class="panel stat"><span class="stat-label">Based on</span><strong>{today["headlines"]}</strong><small>market headlines that day</small></div>
</section>"""
        title = f"Stock market mood today: {today['value']}/100, {today['label'].lower()} | Pulsarium Mood Index"
        description = (f"The Pulsarium Mood Index reads the sentiment of the day's stock market headlines: "
                       f"{today['value']}/100 ({today['label'].lower()}) on {fmt_day(date.fromisoformat(today['date']))}, "
                       f"7-day average {today['avg7']:g}. Daily history and sector moods.")
    else:
        headline, gauge = "not enough headlines yet", ""
        title = "Stock market mood index from the news | Pulsarium"
        description = "The Pulsarium Mood Index reads the sentiment of each day's stock market headlines."
    body = f"""<header class="page-head">
  <span class="eyebrow">Pulsarium Mood Index</span>
  <h1>Stock market mood: {esc(headline)}</h1>
  <p class="lead">How the day's market news reads, on a scale from 0 (every headline negative) to 100 (every headline positive); 50 is neutral. Updated with every news refresh.</p>
</header>
{gauge}
<section class="block">
  <h2>Day by day <small>bars: the day's value · line: 7-day average</small></h2>
  <div class="panel chart-panel">{mood_chart(history)}</div>
</section>
{f'<section class="block"><h2>Sector moods <small>last 7 days, sectors with at least 8 headlines</small></h2><div class="mood-list fit-rows">{sector_html}</div></section>' if sector_html else ""}
{cta("Your holdings, with the mood around them",
     "The free cabinet filters the news to what you own and watch, so you see which way your names are being written about.")}
<section class="block">
  <h2>Recent days</h2>
  <div class="day-list fit-rows">{recent}</div>
</section>
{MOOD_EMBED}
{FIT_ROWS_SCRIPT}
<section class="block method">
  <h2>How the index is made</h2>
  <p>Every headline Pulsarium collects from public sources is scored as positive, negative or neutral and given an importance from 0 to 100 (by a language model when available, otherwise by keyword rules). Macro and geopolitical context without a direct market target is left out. For each day, positive headlines count +1 and negative −1, each weighted by 1 + importance/50, and the weighted average is mapped to 0–100. Days with fewer than {MIN_MOOD_ITEMS} market headlines get no value.</p>
  <p>The index describes the tone of news coverage, not prices, and is not investment advice. The full history is available as <a href="/mood/history.json">JSON</a>; please link to this page if you use it.</p>
</section>
"""
    crumbs = [("Home", "/"), ("News", "/news/"), ("Mood Index", path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs), True


# Long lists show their first rows (four, or data-rows) and scroll the rest.
# Measured rather than a fixed height, because rows wrap on phones.
FIT_ROWS_SCRIPT = """<script>
(function () {
  var lists = document.querySelectorAll('.fit-rows');
  function fit(list) {
    var count = Number(list.dataset.rows) || 4, rows = list.children, last = rows[count - 1];
    list.style.maxHeight = rows.length > count && last ? (last.offsetTop + last.offsetHeight) + 'px' : '';
  }
  lists.forEach(fit);
  if (window.ResizeObserver) {
    var observer = new ResizeObserver(function () { lists.forEach(fit); });
    lists.forEach(function (list) { observer.observe(list); });
  }
})();
</script>"""

# Hides a list's "more" headlines behind a button (item_list's collapse_after).
# Without scripts everything stays visible.
SHOW_MORE_SCRIPT = """<script>
(function () {
  var box = document.currentScript.previousElementSibling;
  if (!box || !box.classList.contains('show-more')) return;
  box.classList.add('is-collapsed');
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-ghost show-more-button';
  button.textContent = 'Show ' + box.dataset.hidden + ' more';
  button.addEventListener('click', function () { box.classList.remove('is-collapsed'); button.remove(); });
  box.after(button);
})();
</script>"""

# The embed code offered on /mood/. The iframe shows the card; the plain
# link under it is what search engines count, so the snippet keeps it.
MOOD_SNIPPET = ('<iframe src="{site}/mood/widget/?theme={theme}" title="Pulsarium Mood Index" width="340" height="140" '
                'style="border:0;max-width:100%" loading="lazy"></iframe>\n'
                '<p style="margin:4px 0 0;font:12px/1.4 sans-serif"><a href="{site}/mood/">Stock market mood index</a> by Pulsarium</p>')

MOOD_EMBED = f"""<section class="block" id="embed">
  <h2>Put the Mood Index on your site <small>free · updates by itself</small></h2>
  <div class="embed-grid">
    <div class="embed-preview">
      <iframe src="/mood/widget/?theme=dark" title="Pulsarium Mood Index, dark" width="340" height="140" loading="lazy"></iframe>
      <iframe src="/mood/widget/?theme=light" title="Pulsarium Mood Index, light" width="340" height="140" loading="lazy"></iframe>
    </div>
    <div class="panel embed-code">
      <div class="embed-switch" role="group" aria-label="Widget theme">
        <button type="button" class="active" data-theme="dark" aria-pressed="true">Dark</button>
        <button type="button" data-theme="light" aria-pressed="false">Light</button>
      </div>
      <textarea id="embed-snippet" readonly rows="5" aria-label="Embed code">{esc(MOOD_SNIPPET.format(site=SITE, theme="dark"))}</textarea>
      <button type="button" class="embed-copy" id="embed-copy">Copy code</button>
      <p>Paste it into any HTML block of your blog or site. The card shows today's value and refreshes on its own; please keep the link under it.</p>
    </div>
  </div>
  <script>
  (function () {{
    var box = document.getElementById('embed-snippet'), copy = document.getElementById('embed-copy');
    var template = {json.dumps(MOOD_SNIPPET.replace("{site}", SITE))};
    document.querySelectorAll('.embed-switch button').forEach(function (button) {{
      button.addEventListener('click', function () {{
        document.querySelectorAll('.embed-switch button').forEach(function (b) {{
          b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', b === button);
        }});
        box.value = template.replace('{{theme}}', button.dataset.theme);
      }});
    }});
    copy.addEventListener('click', function () {{
      var done = function () {{ copy.textContent = 'Copied'; setTimeout(function () {{ copy.textContent = 'Copy code'; }}, 1600); }};
      if (navigator.clipboard) navigator.clipboard.writeText(box.value).then(done, function () {{ box.select(); }});
      else {{ box.select(); document.execCommand('copy'); done(); }}
    }});
  }})();
  </script>
</section>"""


def mood_widget(history: list) -> str:
    """/mood/widget/: the card other sites embed. Today's value on the 0-100
    scale, its label, the change from the previous reading and the 7-day
    average; the whole card links to /mood/. ?theme=light for light pages
    (dark Neon by default). Fonts come from this site, so embedding it sends
    no visitor data to anyone else; not indexed, not in the sitemap."""
    today = history[-1] if history else None
    prev = history[-2] if len(history) > 1 else None
    if today:
        tone = "neg" if today["value"] <= 44 else "neu" if today["value"] <= 55 else "pos"
        diff = today["value"] - prev["value"] if prev else None
        change = "" if diff is None else (
            f'<span class="change {"pos" if diff > 0 else "neg" if diff < 0 else "neu"}" title="vs previous day">'
            f'{"▲" if diff > 0 else "▼" if diff < 0 else "±"}{abs(diff)}</span>')
        day = date.fromisoformat(today["date"])
        content = f"""<div class="top"><span>Market mood</span><time datetime="{today["date"]}">{fmt_short_day(day)}<span class="count"> · {today["headlines"]} headlines</span></time></div>
  <div class="main"><span class="value">{today["value"]}<small>/100</small></span><span class="label {tone}">{esc(today["label"])}</span>{change}</div>
  <div class="scale" aria-hidden="true"><i style="left:{today["value"]}%"></i></div>
  <div class="ticks" aria-hidden="true"><span>0 · cautious</span><span>50</span><span>optimistic · 100</span></div>
  <div class="foot"><span>7-day average {today["avg7"]:g}</span><b>Pulsarium Mood Index ↗</b></div>"""
        summary = f'Stock market mood {today["value"]}/100, {today["label"].lower()}'
    else:
        content = """<div class="top"><span>Market mood</span></div>
  <div class="main"><span class="label neu">Not enough headlines yet today</span></div>
  <div class="foot"><span>Back after the next news refresh</span><b>Pulsarium Mood Index ↗</b></div>"""
        summary = "Pulsarium Mood Index"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{esc(summary)} · Pulsarium</title>
<link rel="stylesheet" href="/fonts/fonts.css">
<script>try {{ var t = new URLSearchParams(location.search).get('theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; }} catch (e) {{}}</script>
<style>
:root {{ --card: linear-gradient(160deg, #0b1024, #05070f); --ring: #05070f; --line: rgba(120, 220, 255, .22); --text: #e6f1ff; --mid: #a9bddf; --low: #7489b3;
  --accent: #00f0ff; --pos: #3dffa2; --neg: #ff6b8e; --neu: #9db2d6; --track: linear-gradient(90deg, #ff6b8e, #56627f 50%, #3dffa2); --glow: 0 0 12px rgba(0, 240, 255, .6); }}
:root[data-theme="light"] {{ --card: #ffffff; --ring: #ffffff; --line: #d6e0e4; --text: #16232b; --mid: #46565f; --low: #6b7b84;
  --accent: #007f8c; --pos: #1d7a4d; --neg: #c43b5c; --neu: #5b6b74; --track: linear-gradient(90deg, #eeb1c0, #d5dee2 50%, #a8dcc0); --glow: 0 1px 4px rgba(0, 60, 70, .35); }}
html, body {{ margin: 0; height: 100%; background: transparent; }}
.card {{ box-sizing: border-box; height: 100%; display: flex; flex-direction: column; justify-content: space-between; gap: 7px; padding: 11px 14px 10px;
  border: 1px solid var(--line); border-radius: 12px; background: var(--card); color: var(--text); text-decoration: none; overflow: hidden;
  font-family: 'Chakra Petch', system-ui, -apple-system, sans-serif; }}
.card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: -2px; }}
.top {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; color: var(--accent);
  font: 600 10px/1 'Orbitron', system-ui, sans-serif; letter-spacing: .16em; text-transform: uppercase; }}
.top time {{ color: var(--low); font: 400 10.5px/1 'JetBrains Mono', ui-monospace, monospace; letter-spacing: 0; text-transform: none; white-space: nowrap; }}
.main {{ display: flex; align-items: baseline; gap: 10px; min-width: 0; }}
.value {{ font: 700 30px/1 'JetBrains Mono', ui-monospace, monospace; }}
.value small {{ font-size: 12px; font-weight: 400; color: var(--low); }}
.label {{ font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.change {{ margin-left: auto; font: 600 12px/1 'JetBrains Mono', ui-monospace, monospace; }}
.pos {{ color: var(--pos); }} .neg {{ color: var(--neg); }} .neu {{ color: var(--neu); }}
.scale {{ position: relative; height: 6px; margin: 3px 6px 0; border-radius: 99px; background: var(--track); }}
.scale i {{ position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; box-sizing: border-box; border-radius: 50%;
  background: var(--accent); border: 2px solid var(--ring); box-shadow: var(--glow); }}
.ticks {{ display: flex; justify-content: space-between; color: var(--low); font: 400 9.5px/1 'JetBrains Mono', ui-monospace, monospace; }}
.foot {{ display: flex; justify-content: space-between; gap: 8px; color: var(--mid); font-size: 11.5px; line-height: 1.2; }}
.foot b {{ color: var(--accent); font-weight: 600; white-space: nowrap; }}
.card:hover .foot b {{ text-decoration: underline; }}
@media (max-width: 300px) {{ .value {{ font-size: 25px; }} .label {{ font-size: 13px; }} .count, .foot span {{ display: none; }} }}
</style>
</head>
<body>
<a class="card" href="{SITE}/mood/" target="_blank" rel="noopener" title="How today's stock market news reads, from 0 to 100 — open the Pulsarium Mood Index">
  {content}
</a>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML pieces
# ---------------------------------------------------------------------------



@functools.lru_cache(maxsize=None)
def asset_url(path: str) -> str:
    """/assets/x?v=<content hash>: a changed file gets a new URL, so browsers
    don't keep an old copy for GitHub Pages' 10-minute cache. Line endings
    are ignored so a Windows checkout hashes like the Linux runner."""
    with open(path.lstrip("/"), "rb") as f:
        digest = hashlib.sha1(f.read().replace(b"\r\n", b"\n")).hexdigest()[:10]
    return f"{path}?v={digest}"


def page(*, path: str, title: str, description: str, body: str, crumbs: list,
         indexable: bool = True, extra_head: str = "", og_image: str = "/og/pulsarium-og-v2.jpg") -> str:
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
<meta property="og:image" content="{SITE}{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE}{og_image}">
<link rel="alternate" type="application/rss+xml" title="Pulsarium market news" href="/news/feed.xml">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="stylesheet" href="/fonts/fonts.css">
<link rel="stylesheet" href="{asset_url('/assets/site.css')}">
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
    <a href="/mood/">Mood Index</a>
    <a href="/tools/">Calculators</a>
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
  <nav class="footer-cabinet footer-news" aria-label="News">{"".join(f'<a href="{href}">{esc(name)}</a>' for href, name in NEWS_LINKS)}</nav>
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
<script src="{asset_url('/assets/analytics.js')}" defer></script>
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
    # a ticker links to its page only when it has one (see "paged" in build)
    tags = "".join(
        f'<a class="tag" href="/news/{companies[t]["slug"]}/">{esc(t)}</a>'
        if companies[t].get("paged", True) else f'<span class="tag">{esc(t)}</span>'
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


def item_list(items: list, companies: dict, skip_ticker: str = None, group_by_day: bool = True,
              collapse_after: int = None) -> str:
    """Headlines, grouped under their day. With collapse_after, the rest are
    marked "more" and SHOW_MORE_SCRIPT hides them behind a button; they stay
    in the page (and visible without scripts) for readers and search engines."""
    if not items:
        return '<p class="empty">No headlines in the last 30 days yet. This page updates automatically when one appears.</p>'
    parts = []
    current = None
    shown = items[:ITEMS_PER_PAGE]
    for n, item in enumerate(shown):
        more = ' data-more' if collapse_after is not None and n >= collapse_after else ""
        day = item["_time"].date()
        if group_by_day and day != current:
            if current is not None:
                parts.append("</div>")
            parts.append(f'<h3 class="day-head"{more}><a href="/news/daily/{day.isoformat()}/">{fmt_day(day)}</a></h3><div class="items">')
            current = day
        parts.append(item_html(item, companies, skip_ticker).replace('<article class="item">', f'<article class="item"{more}>', 1))
    if group_by_day and current is not None:
        parts.append("</div>")
    body = "\n".join(parts)
    body = body if group_by_day else f'<div class="items">{body}</div>'
    if collapse_after is not None and len(shown) > collapse_after:
        body = f'<div class="show-more" data-hidden="{len(shown) - collapse_after}">{body}</div>{SHOW_MORE_SCRIPT}'
    return body


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
    # the busiest dozen; the sector page lists the rest
    active_peers = [c for c in peers if sector_counts.get(c["ticker"], 0) > 0]
    peer_links = "".join(
        f'<a class="chip" href="/news/{c["slug"]}/">{esc(c["ticker"])}<small>{esc(c["name"])}</small></a>'
        for c in active_peers[:PEERS_SHOWN]
    )
    if len(active_peers) > PEERS_SHOWN:
        peer_links += (f'<a class="chip" href="/news/sector/{sector_slug(company["sector"])}/">'
                       f'All {len(active_peers) + 1}<small>{esc(company["sector"])} companies</small></a>')
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
  {item_list(items, companies, skip_ticker=ticker, collapse_after=ITEMS_SHOWN)}
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
    # companies without a page (no headline yet) aren't listed
    members = sorted((c for c in companies.values() if c["sector"] == sector and c.get("paged", True)),
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


def daily_page(day: str, items: list, companies: dict, prev_day, next_day, mood_point=None) -> tuple:
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
  {f'<div class="panel stat"><span class="stat-label">Mood Index</span><strong>{mood_point["value"]}</strong><small>{esc(mood_point["label"])} · <a href="/mood/">about the index</a></small></div>' if mood_point else ""}
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
<section class="block day-list fit-rows" data-rows="7">{"".join(rows)}</section>
{FIT_ROWS_SCRIPT}
"""
    title = "Daily stock market news digest: top stories by day | Pulsarium"
    description = "An archive of daily stock market headlines with top stories, sentiment and the most mentioned companies for each day."
    crumbs = [("Home", "/"), ("News", "/news/"), ("Daily digest", path)]
    return path, page(path=path, title=title, description=description, body=body, crumbs=crumbs), True


def companies_hub(companies: dict, counts: dict, sector_totals: dict) -> tuple:
    path = "/news/companies/"
    listed = {t: c for t, c in companies.items() if c.get("paged", True)}  # companies with a page
    sections = []
    for sector in sorted({c["sector"] for c in listed.values()}):
        members = sorted((c for c in listed.values() if c["sector"] == sector), key=lambda c: c["name"].lower())
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
  <p class="lead">{len(listed)} companies, funds and market themes Pulsarium tags in the news. Each page collects the last 30 days of headlines with a sentiment summary. The number is headlines in the last 30 days.</p>
</header>
{cta("Follow the companies you own",
     "Pick your names once: the free cabinet keeps their news, prices, dividends and alerts together.")}
{"".join(sections)}
"""
    title = "Stock news by company and sector | Pulsarium"
    description = (f"Browse stock market news by company and sector: Nvidia, Apple, Microsoft, Tesla and "
                   f"{len(listed) - 4} more, each with headlines and sentiment from the last 30 days.")
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
    window = tag_items(news_only(archive.items_for(window_days)))
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

    # A company gets a page once it has had a headline; a page that exists is
    # kept (as noindex while empty) so links to it keep working. With 650
    # companies, most of the 500 from SEC's list would otherwise be empty pages.
    paged = {t for t, c in companies.items()
             if by_ticker.get(t) or os.path.exists(url_file(f"/news/{c['slug']}/"))}
    for ticker, company in companies.items():
        company["paged"] = ticker in paged  # read by item_html and the chip lists
    for company in companies.values():
        items = by_ticker.get(company["ticker"], [])
        if not company["paged"]:
            continue
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
    per_day = {day: news_only(archive.load(day).values()) for day in days}
    history = mood_history(days, per_day)
    mood_by_day = {p["date"]: p for p in history}
    for n, day in enumerate(days):
        day_path = f"/news/daily/{day}/"
        freq = "daily" if n + 1 == len(days) else "monthly"
        if full or n >= len(days) - 3 or not os.path.exists(url_file(day_path)):
            prev_day = days[n - 1] if n > 0 else None
            next_day = days[n + 1] if n + 1 < len(days) else None
            emit(daily_page(day, tag_items(per_day[day]), companies, prev_day, next_day, mood_by_day.get(day)), day, freq)
        elif len(per_day[day]) >= MIN_INDEXABLE_DAY:
            sitemap_entries.append((day_path, day, freq))
    if days:
        emit(daily_index(days, per_day), days[-1], "daily")
    emit(companies_hub(companies, counts, sector_totals), today.isoformat(), "daily")
    emit(mood_page(history, window, now), history[-1]["date"] if history else None, "daily")
    write_if_changed(url_file("/mood/widget/"), mood_widget(history), changed_files)
    write_if_changed(os.path.join("mood", "history.json"),
                     json.dumps({"index": "Pulsarium Mood Index", "scale": "0-100, 50 = neutral",
                                 "source": f"{SITE}/mood/", "days": history}, indent=1) + "\n",
                     changed_files)

    # the personal cabinet's feature and broker-import pages (site_pages.py)
    # and the calculators (site_tools.py)
    for spec in site_pages.all_pages() + site_tools.all_pages():
        crumbs = [("Home", "/")] + ([spec["parent"]] if spec.get("parent") else []) + [(spec["crumb"], spec["path"])]
        emit((spec["path"], page(path=spec["path"], title=spec["title"], description=spec["description"],
                                 body=spec["body"], crumbs=crumbs, extra_head=spec.get("head", ""),
                                 **({"og_image": spec["og_image"]} if spec.get("og_image") else {})), True),
             None, "monthly")

    write_if_changed(os.path.join("news", "feed.xml"), rss_feed(window, now), changed_files)
    static = [(p, None, f) for p, f in STATIC_PAGES]
    write_if_changed("sitemap.xml", sitemap(static + sorted(sitemap_entries)), changed_files)

    print(f"Pages: {len(paged)} of {len(companies)} companies, {len(by_sector)} sectors with news, {len(days)} days; "
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
