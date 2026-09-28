"""
Pages about the personal cabinet (app.pulsarium.finance), rendered by
site_build.py with the same layout as the news pages.

Every claim here must match what the cabinet really does - check the
investor-platform code before adding one. Broker export steps were
checked against the brokers' own help pages (September 2026).
"""

import html
import json

esc = html.escape

APP_URL = "https://app.pulsarium.finance/"

CABINET_LINKS = [
    ("/portfolio-tracker/", "Portfolio tracker"),
    ("/dividend-tracker/", "Dividend tracker"),
    ("/price-alerts/", "Price alerts"),
    ("/import/", "Broker import"),
    ("/why-pulsarium/", "Why Pulsarium"),
]


def hero(eyebrow: str, h1: str, lead: str, secondary=None) -> str:
    second = (f'<a class="btn btn-ghost" href="{esc(secondary[0])}">{esc(secondary[1])}</a>'
              if secondary else "")
    return f"""<header class="page-head feature-head">
  <span class="eyebrow">{esc(eyebrow)}</span>
  <h1>{h1}</h1>
  <p class="lead">{lead}</p>
  <div class="hero-actions">
    <a class="btn btn-primary" href="{APP_URL}">Create a free account</a>
    {second}
  </div>
  <p class="hero-note">Free during the public beta · No broker login needed · Two-factor sign-in</p>
</header>"""


# Screenshots of a demo account (sample data, no real portfolio): name ->
# height at 1600 px wide. WebP for the page, JPEG as the sharing image.
SHOTS = {
    "cabinet-overview": 769,
    "cabinet-overview-sectors": 776,
    "cabinet-portfolio": 780,
    "cabinet-dividends": 772,
    "cabinet-watchlists": 772,
    "cabinet-overview-calm": 773,
}


def shot_pair(title: str, left: tuple, right: tuple) -> str:
    """Two screenshots side by side, each (name, alt, label)."""
    figures = "".join(
        f'<figure class="panel shot"><img src="/assets/shots/{name}.webp" alt="{esc(alt)}" width="1600" '
        f'height="{SHOTS[name]}" loading="lazy" decoding="async"><figcaption>{esc(label)}</figcaption></figure>'
        for name, alt, label in (left, right)
    )
    return f'<section class="block"><h2>{esc(title)}</h2><div class="shot-pair">{figures}</div></section>'


def shot(name: str, alt: str, caption: str, first: bool = False) -> str:
    """A cabinet screenshot; the first one on a page loads eagerly (it's in view)."""
    loading = 'fetchpriority="high"' if first else 'loading="lazy"'
    return (f'<figure class="panel shot"><img src="/assets/shots/{name}.webp" alt="{esc(alt)}" '
            f'width="1600" height="{SHOTS[name]}" {loading} decoding="async">'
            f'<figcaption>{esc(caption)} <span>Demo account with sample data.</span></figcaption></figure>')


def features(title: str, cards: list) -> str:
    items = "".join(
        f'<article class="panel feature"><h3>{esc(h)}</h3><p>{p}</p></article>' for h, p in cards
    )
    # full rows: 3 across for 3/6 cards, 2 across for 2/4
    cols = 3 if len(cards) % 3 == 0 else 2
    return f'<section class="block"><h2>{esc(title)}</h2><div class="feature-grid cols-{cols}">{items}</div></section>'


def steps(title: str, items: list) -> str:
    lis = "".join(f"<li><strong>{esc(h)}</strong><span>{p}</span></li>" for h, p in items)
    return f'<section class="block"><h2>{esc(title)}</h2><ol class="steps">{lis}</ol></section>'


def faq(items: list) -> tuple:
    """Returns the FAQ section and its FAQPage JSON-LD."""
    body = "".join(
        f'<details class="faq-item"><summary>{esc(q)}</summary><p>{a}</p></details>' for q, a in items
    )
    ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": html.unescape(_strip_tags(a))}}
            for q, a in items
        ],
    }
    section = f'<section class="block"><h2>Questions</h2><div class="faq">{body}</div></section>'
    return section, f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>\n'


def _strip_tags(text: str) -> str:
    out, depth = [], 0
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def closing_cta(title: str, text: str) -> str:
    return f"""<aside class="panel cta">
  <div>
    <span class="eyebrow">Personal cabinet</span>
    <h2>{esc(title)}</h2>
    <p>{esc(text)}</p>
  </div>
  <a class="btn btn-primary" href="{APP_URL}">Get started free</a>
</aside>"""


def related(current: str) -> str:
    links = "".join(
        f'<a class="chip" href="{href}">{esc(name)}</a>' for href, name in CABINET_LINKS if href != current
    )
    return f'<section class="block"><h2>More in the cabinet</h2><div class="chips">{links}</div></section>'


COMMON_FAQ = {
    "free": ("Is Pulsarium free?",
             "Yes. The personal cabinet is free during the public beta: portfolios, dividends, watchlists, "
             "price alerts, broker import and the news feed. Paid plans with an AI research assistant are planned; "
             "the free features stay available."),
    "broker": ("Do I have to connect my broker account?",
               "No. Pulsarium never asks for broker logins. You export a transaction file from your broker and "
               "upload it, or add trades by hand. Nothing reaches your broker account."),
    "delay": ("How current are the prices?",
              "Market data may be delayed; the cabinet uses end-of-day and delayed quotes. It is built for "
              "investors who follow their holdings, not for intraday trading."),
    "privacy": ("Who can see my portfolio?",
                "Only you. Every row is isolated to your account in the database, sign-in supports two-factor "
                "authentication, and you can export all your data or delete the account yourself at any time."),
}


# ---------------------------------------------------------------------------
# Broker import pages
# ---------------------------------------------------------------------------

IMPORT_REVIEW_STEPS = [
    ("Upload the file",
     "In the cabinet open <em>Account → Export / Import CSV → Import from a broker</em> and choose the file. "
     "CSV and TSV work; save an Excel file as CSV first."),
    ("Check the columns",
     "Pick the target portfolio. Pulsarium suggests the broker from the file name and pre-selects the date, operation, instrument, "
     "quantity, price and fee columns — check them and whether dates are day- or month-first. After a successful "
     "import the column choices for that file format are remembered on your device."),
    ("Review every row",
     "Click <em>Review rows</em>. Each trade is matched to a listing (the exchange is named, the most likely one is "
     "marked <em>best match</em>), already imported rows are skipped and possible duplicates are flagged. Nothing "
     "is saved until you confirm."),
]

BROKERS = {
    "revolut": {
        "region": "global",
        "name": "Revolut",
        "export": [
            ("Open the statements", "In the Revolut app go to <em>Invest</em> (Stocks), tap <em>More</em> and then <em>Statements</em>."),
            ("Download the trading statement", "Choose the trading account statement, set the period to your whole history and download it as a spreadsheet (CSV or Excel)."),
        ],
        "notes": [
            ("Commission included", "Revolut's statement has no separate fee column; Pulsarium reads the commission from the difference between the total and quantity × price."),
            ("Cash rows handled", "Top-ups and withdrawals are listed separately and can be excluded in one click; stock-split rows are explained and left out, because positions already reflect the split."),
        ],
        "faq": ("Does the Revolut statement need editing first?",
                "No. Upload the statement as downloaded (CSV, or TSV/Excel saved as CSV). Pulsarium recognises "
                "Revolut's order types such as “BUY - MARKET” and “SELL - LIMIT”."),
    },
    "degiro": {
        "region": "global",
        "name": "DEGIRO",
        "export": [
            ("Open Transactions on the web", "Log in to DEGIRO in a browser (the app can't export). Hover the <em>Inbox</em> icon in the left menu and open <em>Transactions</em>."),
            ("Export as CSV", "Set the date range to cover your whole history, click <em>Export</em> at the top right and choose <em>CSV</em>."),
        ],
        "notes": [
            ("ISIN-only files", "DEGIRO lists products by ISIN rather than ticker; Pulsarium turns each ISIN into its listings and lets you pick the exchange you actually bought on."),
            ("Buys and sells from the sign", "The file has no buy/sell column — a negative quantity marks a sale, as DEGIRO writes it."),
        ],
        "faq": ("Which DEGIRO export should I use?",
                "The Transactions export (Inbox → Transactions → Export → CSV). It holds every buy and sell with "
                "quantity, price and currency."),
    },
    "trading-212": {
        "region": "global",
        "name": "Trading 212",
        "export": [
            ("Open History", "In Trading 212 open the menu, go to <em>History</em> and tap the export icon at the top right."),
            ("Export CSV", "Pick the period (up to one year per file), include <em>Orders</em> and <em>Dividends</em>, and tap <em>Export CSV</em>. For a longer history, export one file per year and upload them one after another."),
        ],
        "notes": [
            ("Dividends come along", "Trading 212 names dividend rows like “Dividend (Ordinary)”; Pulsarium reads them as dividends."),
            ("Repeat uploads are safe", "Use the same broker name each time: rows imported before are recognised and skipped."),
        ],
        "faq": ("My Trading 212 history is longer than a year — what then?",
                "Trading 212 exports at most one year per file. Export each year separately and upload the files "
                "one by one; overlapping rows are recognised and skipped."),
    },
    "interactive-brokers": {
        "region": "global",
        "name": "Interactive Brokers",
        "short": "IBKR",
        "export": [
            ("Run an activity statement", "In the IBKR Client Portal open <em>Performance &amp; Reports → Statements</em> and run an <em>Activity</em> statement."),
            ("Download as CSV", "Choose the period (a year per file at most) and <em>CSV</em> as the format, then download it."),
        ],
        "notes": [
            ("Statement format recognised", "Pulsarium finds the Trades section inside the multi-section activity statement by itself."),
            ("One year per file", "For a longer history run one statement per year and upload them in turn; duplicates are skipped."),
        ],
        "faq": ("Do I need a Flex Query?",
                "No. A standard Activity statement exported as CSV is enough; Pulsarium reads its Trades section."),
    },
    # US brokers: parsing was checked on sample files in each broker's style;
    # the pages say so and point to the in-app report for anything unread.
    "robinhood": {
        "region": "us",
        "name": "Robinhood",
        "export": [
            ("Request an activity report", "In the Robinhood app or website open <em>Account → Reports and statements</em>, create a custom account activity report and choose the start and end dates."),
            ("Download the CSV", "Robinhood prepares the report in the background — usually within a couple of hours, at most a day — and notifies you; then download it as CSV from <em>Reports</em>."),
        ],
        "notes": [
            ("US formats understood", "Month-first dates, dollar signs and thousands separators in amounts are read as Robinhood writes them."),
            ("Trades and dividends", "Buys, sells and dividends are read; rows that are neither, such as transfers, are flagged so you can exclude them."),
        ],
        "faq": ("What if a row from my Robinhood file isn't recognised?",
                "It is flagged in the preview instead of being guessed. Exclude it or fix the ticker — and send the "
                "report offered on the import screen, so we can add that format."),
    },
    "fidelity": {
        "region": "us",
        "name": "Fidelity",
        "export": [
            ("Open your history", "Log in to Fidelity, pick the account and open <em>Activity &amp; Orders → History</em>."),
            ("Download the CSV", "Choose a date range, apply it and click <em>Download</em>. If Fidelity limits the range of one download, download a few periods and upload them one after another — rows already imported are skipped."),
        ],
        "notes": [
            ("Fees and commissions", "Separate fee and commission columns are both counted in the trade's cost."),
            ("US formats understood", "Month-first dates, dollar signs and thousands separators in amounts are read as Fidelity writes them."),
        ],
        "faq": ("Can I import several Fidelity accounts?",
                "Yes. Download each account's history and import it into the same portfolio or into separate ones; "
                "use the same broker name each time so repeated rows are recognised."),
    },
    "charles-schwab": {
        "region": "us",
        "name": "Charles Schwab",
        "short": "Schwab",
        "export": [
            ("Open Transactions history", "Log in to Schwab, go to <em>Accounts → History</em>, choose the account and set the date range (all transaction types)."),
            ("Export as CSV", "Click <em>Export</em> at the top right, choose CSV and save the file."),
        ],
        "notes": [
            ("Fees and commissions", "Schwab's combined “Fees &amp; Comm” column is read as the trade's full cost."),
            ("US formats understood", "Month-first dates, dollar signs and thousands separators in amounts are read as Schwab writes them."),
        ],
        "faq": ("Does Pulsarium connect to my Schwab account?",
                "No. Nothing connects to Schwab: you export your history as a CSV and upload the file. Your Schwab "
                "login never leaves your hands."),
    },
}


def broker_page(slug: str, broker: dict) -> dict:
    name = broker["name"]
    short = broker.get("short", name)
    notes = features(f"What Pulsarium does with a {short} file", broker["notes"])
    faq_section, faq_ld = faq([broker["faq"], COMMON_FAQ["broker"], COMMON_FAQ["free"]])
    body = (
        hero(f"Broker import · {name}",
             f"Import your {esc(name)} trades into a portfolio tracker",
             f"Export your transaction history from {esc(name)} and upload the file to Pulsarium's free cabinet: "
             "every trade is checked with you before it is saved, and your broker login never leaves your hands.",
             ("/import/", "All supported brokers"))
        + steps(f"1. Export the file from {name}", broker["export"])
        + steps("2. Import it into Pulsarium", IMPORT_REVIEW_STEPS)
        + shot("cabinet-portfolio", "Imported trades shown as positions in the Pulsarium portfolio table",
               "After the import: every position with its average cost, P&L, dividends and weight.")
        + notes
        + closing_cta(f"Track your {short} portfolio in one place",
                      "Positions, dividends, price alerts and news about what you hold — free during the beta.")
        + faq_section
        + related("/import/")
    )
    return {
        "path": f"/import/{slug}/",
        "crumb": name,
        "parent": ("Broker import", "/import/"),
        "title": f"Import {name} trades into a portfolio tracker: step by step | Pulsarium",
        "description": f"How to export your {name} transaction history and import it into Pulsarium's free portfolio "
                       f"tracker: every trade reviewed before saving, no broker login needed.",
        "body": body,
        "head": faq_ld,
    }


# ---------------------------------------------------------------------------
# Feature pages
# ---------------------------------------------------------------------------

def portfolio_tracker() -> dict:
    faq_section, faq_ld = faq([COMMON_FAQ["free"], COMMON_FAQ["broker"],
                               ("Can I track several portfolios in different currencies?",
                                "Yes. Create as many portfolios as you need, each with its own base currency; "
                                "positions in other currencies are converted for the totals."),
                               COMMON_FAQ["delay"], COMMON_FAQ["privacy"]])
    body = (
        hero("Portfolio tracker",
             "A private, free stock portfolio tracker",
             "See every holding, its weight and performance in one calm workspace — with the dividends it pays, "
             "alerts at your price levels and the news that concerns it. Import trades from your broker's file; "
             "no broker login needed.",
             ("/import/", "Import from your broker"))
        + shot("cabinet-overview", "Pulsarium overview: portfolio value, performance chart, top positions, news and alert bands",
               "The overview: value and P&L, performance, top positions, the news about them and alert bands.", first=True)
        + features("Everything about your holdings, in one place", [
            ("Several portfolios and currencies", "Keep separate portfolios — each with its own base currency — and see positions, weights and value converted for you."),
            ("Performance over time", "A performance chart with the ranges you need, plus the day's move of every position."),
            ("Weight limits", "Set a maximum weight per position and see when one grows past it — a simple risk check most trackers leave out."),
            ("Dividends and splits handled", "Dividends and stock splits are detected automatically and applied once you confirm them. See the <a href=\"/dividend-tracker/\">dividend calendar</a>."),
            ("News about what you own", "The Signal Feed filters market news to your holdings and watchlists, tagged by sentiment."),
            ("Your data stays yours", "Two-factor sign-in, data isolated per account, full export of transactions and account data, and self-service deletion."),
        ])
        + shot("cabinet-portfolio", "Pulsarium portfolio table with shares, average cost, price, P&L, dividends and weight",
               "Every position with its average cost, price, unrealized and realized P&L, dividends and weight.")
        + steps("Start in three steps", [
            ("Create a free account", "Sign up with email or Google and turn on two-factor authentication if you like."),
            ("Add your trades", "Upload your broker's file — <a href=\"/import/\">Revolut, DEGIRO, Trading 212, IBKR, Robinhood, Fidelity, Schwab</a> or any CSV/TSV — or add trades by hand."),
            ("Follow along", "Watch weights, dividends and news; set <a href=\"/price-alerts/\">price alerts</a> on the names you are waiting for."),
        ])
        + closing_cta("Put your portfolio in one calm place",
                      "Free during the public beta. No broker login, no ads, no selling of your data.")
        + faq_section
        + related("/portfolio-tracker/")
    )
    return {
        "path": "/portfolio-tracker/", "crumb": "Portfolio tracker", "og_image": "/assets/shots/cabinet-overview.jpg",
        "title": "Free stock portfolio tracker with dividends, alerts and news | Pulsarium",
        "description": "Track several stock portfolios in different currencies: weights, performance, dividends, "
                       "price alerts and news about your holdings. Import from Revolut, DEGIRO, Trading 212, IBKR, Robinhood, Fidelity or Schwab "
                       "files - no broker login. Free during the beta.",
        "body": body, "head": faq_ld,
    }


def dividend_tracker() -> dict:
    faq_section, faq_ld = faq([
        ("How does Pulsarium know about my dividends?",
         "When a company you hold declares a dividend, the cabinet detects it and shows it for your confirmation; "
         "once confirmed, it is recorded against the position. Dividends in broker files are imported too."),
        ("Does it show upcoming dividends?",
         "Yes. The dividend calendar lists expected payouts for your holdings and a monthly view of your dividend flow."),
        COMMON_FAQ["free"], COMMON_FAQ["broker"],
    ])
    body = (
        hero("Dividend tracker",
             "Track your dividends and see what's coming",
             "A dividend calendar for your own holdings: expected payouts, your monthly dividend flow and every "
             "dividend recorded against the position that paid it.",
             ("/portfolio-tracker/", "See the portfolio tracker"))
        + shot("cabinet-dividends", "Pulsarium dividend calendar with expected payouts and monthly dividend flow",
               "The dividend calendar: announced and estimated payouts, the next 90 days and the monthly flow.", first=True)
        + features("Built for dividend investors", [
            ("Dividend calendar", "Expected payouts for the companies you hold, in one calendar."),
            ("Monthly dividend flow", "See how much income arrives month by month."),
            ("Detected, then confirmed", "New dividends and stock splits are detected automatically and applied only after you confirm them — no silent changes to your records."),
            ("Imported with your trades", "Dividend rows in broker files (for example Trading 212's “Dividend (Ordinary)”) come in with the trades."),
        ])
        + closing_cta("See your dividend income at a glance",
                      "Add your holdings once — the calendar and monthly flow follow. Free during the beta.")
        + faq_section
        + related("/dividend-tracker/")
    )
    return {
        "path": "/dividend-tracker/", "crumb": "Dividend tracker", "og_image": "/assets/shots/cabinet-dividends.jpg",
        "title": "Free dividend tracker and dividend calendar for your stocks | Pulsarium",
        "description": "A dividend calendar for your own portfolio: expected payouts, monthly dividend flow, "
                       "dividends and splits detected automatically and confirmed by you. Free during the beta.",
        "body": body, "head": faq_ld,
    }


def price_alerts() -> dict:
    faq_section, faq_ld = faq([
        ("When is an alert checked?",
         "Once every trading day after the US market closes, against that day's closing prices. Pulsarium is built "
         "for investors waiting for a level, not for intraday trading."),
        ("How am I notified?",
         "In the cabinet and by email, if you keep email notifications on. You choose which notifications you get."),
        COMMON_FAQ["free"],
    ])
    body = (
        hero("Price alerts",
             "Stock price alerts at the levels you choose",
             "Put the stocks you are waiting for on a watchlist, set an upper or lower price, and get an email when "
             "the close crosses it — no need to check the market every day.",
             ("/portfolio-tracker/", "See the portfolio tracker"))
        + shot("cabinet-watchlists", "Pulsarium watchlist with upper and lower price alerts per stock",
               "A watchlist with an upper and a lower alert on each name.", first=True)
        + features("Wait for your price, calmly", [
            ("Upper and lower levels", "Set a price above, below, or both for any name on a watchlist."),
            ("Alert bands on the overview", "See at a glance how close each name is to its alert — the closest ones first."),
            ("Email and in-app", "Notifications reach you by email and in the cabinet; turn each kind on or off."),
            ("News next to the price", "Watchlist names feed the Signal Feed, so you see why a price is moving."),
        ])
        + closing_cta("Set your first price alert",
                      "Create a watchlist, add a level and let Pulsarium watch the close for you.")
        + faq_section
        + related("/price-alerts/")
    )
    return {
        "path": "/price-alerts/", "crumb": "Price alerts", "og_image": "/assets/shots/cabinet-watchlists.jpg",
        "title": "Free stock price alerts by email for your watchlist | Pulsarium",
        "description": "Set upper and lower price alerts on a stock watchlist and get an email when the closing "
                       "price crosses your level. Free during the beta, no broker login needed.",
        "body": body, "head": faq_ld,
    }


def import_hub() -> dict:
    def cards(region: str) -> str:
        chosen = [(slug, b) for slug, b in BROKERS.items() if b["region"] == region]
        items = "".join(
            f'<a class="panel feature feature-link" href="/import/{slug}/"><h3>{esc(b["name"])}</h3>'
            f'<p>How to export your {esc(b["name"])} history and import it, step by step →</p></a>'
            for slug, b in chosen
        )
        return f'<div class="feature-grid cols-{3 if len(chosen) % 3 == 0 else 2}">{items}</div>'

    faq_section, faq_ld = faq([
        ("My broker isn't listed — can I still import?",
         "Usually yes. Any CSV or TSV transaction file with a date, operation, instrument, quantity and price "
         "can be imported: you match the columns once and review the rows before saving."),
        COMMON_FAQ["broker"], COMMON_FAQ["free"],
    ])
    body = (
        hero("Broker import",
             "Import your trades from a broker file — no broker login",
             "Pulsarium reads the transaction exports of Revolut, DEGIRO, Trading 212, Interactive Brokers, Robinhood, "
             "Fidelity and Charles Schwab — and CSV or TSV files from other brokers such as E*TRADE, Webull, Saxo or XTB "
             "once you match their columns. Every row is reviewed with you before anything is saved.",
             ("/portfolio-tracker/", "See the portfolio tracker"))
        + f'<section class="block"><h2>Europe and global brokers</h2>{cards("global")}</section>'
        + f'<section class="block"><h2>US brokers</h2>{cards("us")}</section>'
        + steps("How the import works", IMPORT_REVIEW_STEPS)
        + features("Why file import instead of a broker connection", [
            ("Your login stays with you", "Pulsarium never asks for broker credentials and no third-party aggregator gets access to your account."),
            ("You see every row", "Duplicates, unknown instruments and cash movements are flagged for you to decide — nothing is merged silently."),
            ("Repeat uploads are safe", "Upload a newer file later: rows already imported are recognised and skipped."),
        ])
        + closing_cta("Bring your trades in", "Create a free account and upload your first broker file.")
        + faq_section
    )
    return {
        "path": "/import/", "crumb": "Broker import",
        "title": "Import broker trades into a portfolio tracker: Revolut, DEGIRO, IBKR, Robinhood, Schwab | Pulsarium",
        "description": "Import your broker's transaction export into Pulsarium's free portfolio tracker: Revolut, "
                       "DEGIRO, Trading 212, Interactive Brokers, Robinhood, Fidelity, Schwab or any CSV/TSV. "
                       "No broker login, every row reviewed.",
        "body": body, "head": faq_ld,
    }


def why_pulsarium() -> dict:
    faq_section, faq_ld = faq([COMMON_FAQ["broker"], COMMON_FAQ["privacy"], COMMON_FAQ["free"], COMMON_FAQ["delay"]])
    body = (
        hero("Why Pulsarium",
             "A portfolio tracker that doesn't need your broker login",
             "Many portfolio apps sync by connecting to your broker account through a data aggregator. Pulsarium takes "
             "the other road: you upload the file your broker already gives you, and your holdings, dividends, alerts "
             "and news live in a private cabinet only you can open.",
             ("/import/", "See supported brokers"))
        + shot("cabinet-overview-sectors", "Pulsarium overview with allocation by sector, performance and news",
               "The overview with allocation by sector, performance and the news about your holdings.", first=True)
        + features("What sets it apart", [
            ("No credentials, no aggregator", "Nothing connects to your broker. You decide what goes in, file by file."),
            ("News where your money is", "A live news desk tagged by ticker and sector, and a feed filtered to what you hold — not a social timeline."),
            ("Risk you can see", "Weight limits per position and alert bands that show how close each name is to your level."),
            ("Calm by design", "No ads, no public portfolios, no leaderboards. Two themes — a neon night mode and a calm light one."),
            ("Privacy you can check", "Two-factor sign-in, data isolated per account, full export and self-service deletion."),
            ("Free to start", "The cabinet is free during the public beta; paid plans will add an AI research assistant."),
        ])
        + shot_pair("Two themes, one workspace",
                    ("cabinet-overview", "Pulsarium overview in the dark Neon theme", "Neon — a night mode with glow"),
                    ("cabinet-overview-calm", "Pulsarium overview in the light Calm theme", "Calm — a quiet light theme"))
        + closing_cta("Try it with your own portfolio", "Create a free account and import your broker file in minutes.")
        + faq_section
        + related("/why-pulsarium/")
    )
    return {
        "path": "/why-pulsarium/", "crumb": "Why Pulsarium", "og_image": "/assets/shots/cabinet-overview-sectors.jpg",
        "title": "A private portfolio tracker without broker logins - why Pulsarium | Pulsarium",
        "description": "Pulsarium tracks your stocks, dividends and price alerts from broker files you upload - no "
                       "broker credentials, no aggregator, no ads. Free during the public beta.",
        "body": body, "head": faq_ld,
    }


def all_pages() -> list:
    pages = [portfolio_tracker(), dividend_tracker(), price_alerts(), import_hub(), why_pulsarium()]
    pages += [broker_page(slug, broker) for slug, broker in BROKERS.items()]
    return pages
