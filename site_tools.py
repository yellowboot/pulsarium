"""
Free investing calculators (/tools/), rendered by site_build.py.

Each page works on its own in the browser (a small inline script, no
data leaves the page) and explains its formula, so the page is useful to
read as well as to use. Numbers are currency-neutral.
"""

import html

from site_pages import closing_cta, faq

esc = html.escape

TOOLS = [
    ("/tools/dividend-yield-calculator/", "Dividend yield calculator",
     "Yield, yield on cost and the income your shares pay per year and month."),
    ("/tools/dca-calculator/", "DCA calculator",
     "What regular monthly investing can grow to, year by year."),
    ("/tools/rebalancing-calculator/", "Portfolio rebalancing calculator",
     "How much to buy or sell of each holding to get back to your target weights."),
]

DISCLAIMER = ("For illustration only — not investment advice. Results depend entirely on the numbers you enter; "
              "real returns, dividends and taxes vary.")


def tool_head(eyebrow: str, h1: str, lead: str) -> str:
    return f"""<header class="page-head">
  <span class="eyebrow">{esc(eyebrow)}</span>
  <h1>{esc(h1)}</h1>
  <p class="lead">{lead}</p>
</header>"""


def other_tools(current: str) -> str:
    links = "".join(f'<a class="chip" href="{p}">{esc(n)}</a>' for p, n, _ in TOOLS if p != current)
    return f'<section class="block"><h2>More calculators</h2><div class="chips">{links}</div></section>'


def field(name: str, label: str, value: str, step: str = "any", suffix: str = "", minimum: str = "0") -> str:
    return (f'<label class="calc-field"><span>{esc(label)}</span><span class="calc-input">'
            f'<input type="number" inputmode="decimal" name="{name}" value="{value}" step="{step}" min="{minimum}">'
            f'{f"<em>{esc(suffix)}</em>" if suffix else ""}</span></label>')


SHARED_JS = """
const fmt = (v, d = 2) => Number.isFinite(v) ? v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
const num = (form, name) => { const v = parseFloat(form.elements[name].value); return Number.isFinite(v) ? v : 0; };
"""


def dividend_yield() -> dict:
    path = "/tools/dividend-yield-calculator/"
    form = f"""<section class="panel calc">
  <form class="calc-form" id="dy-form" onsubmit="return false">
    {field("price", "Share price today", "50")}
    {field("dividend", "Dividend per share", "0.5")}
    <label class="calc-field"><span>Paid</span><span class="calc-input"><select name="freq">
      <option value="4" selected>Quarterly</option><option value="12">Monthly</option>
      <option value="2">Twice a year</option><option value="1">Once a year</option></select></span></label>
    {field("shares", "Shares you own", "100", "1")}
    {field("cost", "Your average purchase price (optional)", "", "any")}
  </form>
  <div class="calc-results" aria-live="polite">
    <div><span>Dividend yield</span><strong id="dy-yield">—</strong></div>
    <div><span>Yield on cost</span><strong id="dy-yoc">—</strong></div>
    <div><span>Income per year</span><strong id="dy-year">—</strong></div>
    <div><span>Income per month (average)</span><strong id="dy-month">—</strong></div>
  </div>
</section>
<script>
(() => {{
{SHARED_JS}
  const form = document.getElementById('dy-form');
  const out = id => document.getElementById(id);
  function update() {{
    const price = num(form, 'price'), dividend = num(form, 'dividend'), freq = num(form, 'freq'),
          shares = num(form, 'shares'), cost = num(form, 'cost');
    const annual = dividend * freq;
    out('dy-yield').textContent = price > 0 ? fmt(annual / price * 100) + '%' : '—';
    out('dy-yoc').textContent = cost > 0 ? fmt(annual / cost * 100) + '%' : 'enter a purchase price';
    out('dy-year').textContent = fmt(annual * shares);
    out('dy-month').textContent = fmt(annual * shares / 12);
  }}
  form.addEventListener('input', update);
  update();
}})();
</script>"""
    faq_section, faq_ld = faq([
        ("How is dividend yield calculated?",
         "Annual dividend per share divided by the current share price. A company paying 0.50 a quarter "
         "(2.00 a year) at a share price of 50 yields 4%."),
        ("What is yield on cost?",
         "The same annual dividend divided by the price you paid. If you bought at 40, that 2.00 dividend is a "
         "5% yield on your cost — it grows as the company raises its dividend."),
        ("Is a higher dividend yield better?",
         "Not by itself. A yield can be high because the share price has fallen on bad news, and a dividend can "
         "be cut. Look at how well the dividend is covered by earnings and cash flow."),
    ])
    body = (
        tool_head("Calculator", "Dividend yield calculator",
                  "Enter a share price and dividend to see the yield, your yield on cost and how much income your "
                  "shares pay per year and per month.")
        + form
        + f'<p class="calc-note">{esc(DISCLAIMER)}</p>'
        + closing_cta("Track your real dividend income",
                      "The free cabinet records every dividend your holdings pay and shows the ones coming next in a calendar.")
        + faq_section
        + other_tools(path)
    )
    return {"path": path, "crumb": "Dividend yield calculator", "parent": ("Tools", "/tools/"),
            "title": "Dividend yield calculator: yield, yield on cost and income | Pulsarium",
            "description": "Free dividend yield calculator: enter the share price, dividend and shares to get the "
                           "dividend yield, yield on cost and your yearly and monthly dividend income.",
            "body": body, "head": faq_ld}


def dca() -> dict:
    path = "/tools/dca-calculator/"
    form = f"""<section class="panel calc">
  <form class="calc-form" id="dca-form" onsubmit="return false">
    {field("initial", "Starting amount", "1000")}
    {field("monthly", "Invested every month", "200")}
    {field("years", "Years", "10", "1", "", "1")}
    {field("rate", "Expected yearly return", "6", "0.1", "%", "-50")}
  </form>
  <div class="calc-results" aria-live="polite">
    <div><span>Value at the end</span><strong id="dca-final">—</strong></div>
    <div><span>Total invested</span><strong id="dca-invested">—</strong></div>
    <div><span>Growth</span><strong id="dca-growth">—</strong></div>
  </div>
  <div class="calc-table-wrap"><table class="calc-table"><thead><tr><th>Year</th><th>Invested</th><th>Value</th><th>Growth</th></tr></thead><tbody id="dca-rows"></tbody></table></div>
</section>
<script>
(() => {{
{SHARED_JS}
  const form = document.getElementById('dca-form');
  const out = id => document.getElementById(id);
  function update() {{
    const initial = num(form, 'initial'), monthly = num(form, 'monthly'),
          years = Math.min(60, Math.max(1, Math.round(num(form, 'years')))), rate = num(form, 'rate') / 100;
    const monthlyRate = Math.pow(1 + rate, 1 / 12) - 1;
    let value = initial, invested = initial, rows = '';
    for (let y = 1; y <= years; y++) {{
      for (let m = 0; m < 12; m++) {{ value = value * (1 + monthlyRate) + monthly; invested += monthly; }}
      rows += `<tr><td>${{y}}</td><td>${{fmt(invested, 0)}}</td><td>${{fmt(value, 0)}}</td><td>${{fmt(value - invested, 0)}}</td></tr>`;
    }}
    out('dca-final').textContent = fmt(value, 0);
    out('dca-invested').textContent = fmt(invested, 0);
    out('dca-growth').textContent = fmt(value - invested, 0);
    out('dca-rows').innerHTML = rows;
  }}
  form.addEventListener('input', update);
  update();
}})();
</script>"""
    faq_section, faq_ld = faq([
        ("What is dollar-cost averaging (DCA)?",
         "Investing the same amount at regular intervals — for example every month — whatever the market does. "
         "You buy more shares when prices are low and fewer when they are high, and you don't have to time the market."),
        ("How does this calculator work?",
         "It adds your monthly amount at the end of each month and grows the balance at the monthly equivalent of "
         "the yearly return you enter (compounded monthly). It ignores fees and taxes."),
        ("What return should I enter?",
         "Nobody knows future returns. Try a few — for example 3%, 6% and 9% — to see a range rather than a single "
         "number."),
    ])
    body = (
        tool_head("Calculator", "DCA calculator: what monthly investing can grow to",
                  "Enter a starting amount, a monthly contribution, a period and an expected return to see the "
                  "end value, how much you put in and the growth, year by year.")
        + form
        + f'<p class="calc-note">{esc(DISCLAIMER)}</p>'
        + closing_cta("See how your real portfolio is doing",
                      "Import your broker file into the free cabinet and follow your actual performance over time.")
        + faq_section
        + other_tools(path)
    )
    return {"path": path, "crumb": "DCA calculator", "parent": ("Tools", "/tools/"),
            "title": "DCA calculator: monthly investing growth year by year | Pulsarium",
            "description": "Free dollar-cost averaging calculator: see what a starting amount plus a monthly "
                           "investment could grow to at the return you choose, with a year-by-year table.",
            "body": body, "head": faq_ld}


def rebalancing() -> dict:
    path = "/tools/rebalancing-calculator/"
    form = """<section class="panel calc">
  <form class="calc-form calc-rebalance" id="rb-form" onsubmit="return false">
    <div class="rb-row rb-head" aria-hidden="true"><span>Holding</span><span>Value now</span><span>Target weight</span><span></span></div>
    <div class="rb-rows" id="rb-rows"></div>
    <div class="rb-actions">
      <button type="button" class="btn btn-ghost" id="rb-add">Add a holding</button>
      <label class="calc-field"><span>New cash to invest</span><span class="calc-input"><input type="number" inputmode="decimal" name="cash" value="0" step="any" min="0"></span></label>
      <label class="calc-check"><input type="checkbox" name="buyonly"> Only buy — use the cash, don't sell</label>
    </div>
  </form>
  <p class="calc-warning" id="rb-warning" hidden></p>
  <div class="calc-table-wrap"><table class="calc-table"><thead><tr><th>Holding</th><th>Now</th><th>Target</th><th>Buy / sell</th><th>After</th></tr></thead><tbody id="rb-result"></tbody></table></div>
</section>
<script>
(() => {
""" + SHARED_JS + """
  const form = document.getElementById('rb-form');
  const rowsEl = document.getElementById('rb-rows');
  const start = [['Stocks ETF', 7000, 60], ['Bond ETF', 2000, 30], ['Gold', 1000, 10]];
  function addRow(name = '', value = '', target = '') {
    const row = document.createElement('div');
    row.className = 'rb-row';
    row.innerHTML = '<input type="text" aria-label="Holding" placeholder="Holding" class="rb-name">' +
      '<span class="calc-input"><input type="number" inputmode="decimal" aria-label="Current value" placeholder="Value now" class="rb-value" step="any" min="0"></span>' +
      '<span class="calc-input"><input type="number" inputmode="decimal" aria-label="Target weight" placeholder="Target" class="rb-target" step="any" min="0"><em>%</em></span>' +
      '<button type="button" class="rb-remove" aria-label="Remove">×</button>';
    row.querySelector('.rb-name').value = name;
    row.querySelector('.rb-value').value = value;
    row.querySelector('.rb-target').value = target;
    row.querySelector('.rb-remove').onclick = () => { row.remove(); update(); };
    rowsEl.appendChild(row);
  }
  function update() {
    const rows = [...rowsEl.querySelectorAll('.rb-row')].map(r => ({
      name: r.querySelector('.rb-name').value || 'Holding',
      value: parseFloat(r.querySelector('.rb-value').value) || 0,
      target: parseFloat(r.querySelector('.rb-target').value) || 0,
    }));
    const cash = num(form, 'cash'), buyOnly = form.elements.buyonly.checked;
    const targetSum = rows.reduce((s, r) => s + r.target, 0);
    const warning = document.getElementById('rb-warning');
    warning.hidden = Math.abs(targetSum - 100) < 0.01;
    warning.textContent = `Target weights add up to ${fmt(targetSum, 1)}% — they should add up to 100%.`;
    const total = rows.reduce((s, r) => s + r.value, 0) + cash;
    let trades = rows.map(r => total * r.target / 100 - r.value);
    if (buyOnly) {
      const needs = trades.map(t => Math.max(0, t));
      const needed = needs.reduce((s, n) => s + n, 0);
      trades = needed > 0 ? needs.map(n => n / needed * Math.min(cash, needed)) : rows.map(r => cash * r.target / (targetSum || 1));
      const left = cash - trades.reduce((s, t) => s + t, 0);
      if (left > 0.005) trades = trades.map((t, i) => t + left * rows[i].target / (targetSum || 1));
    }
    document.getElementById('rb-result').innerHTML = rows.map((r, i) => {
      const after = r.value + trades[i];
      const t = trades[i];
      const cls = t > 0.005 ? 'buy' : t < -0.005 ? 'sell' : '';
      const label = t > 0.005 ? 'Buy ' + fmt(t) : t < -0.005 ? 'Sell ' + fmt(-t) : '—';
      return `<tr><td>${r.name.replace(/[<>&]/g, '')}</td><td>${fmt(r.value)} <small>${fmt(r.value / (total - cash || 1) * 100, 1)}%</small></td>` +
        `<td>${fmt(r.target, 1)}%</td><td class="${cls}">${label}</td><td>${fmt(after)} <small>${fmt(after / (total || 1) * 100, 1)}%</small></td></tr>`;
    }).join('');
  }
  start.forEach(r => addRow(...r));
  document.getElementById('rb-add').onclick = () => { addRow(); update(); };
  form.addEventListener('input', update);
  update();
})();
</script>"""
    faq_section, faq_ld = faq([
        ("What does rebalancing mean?",
         "Bringing your holdings back to the weights you chose. After a strong year for stocks, a 60/40 portfolio "
         "might be 70/30; rebalancing sells some of the winner or buys more of the rest to restore 60/40."),
        ("Can I rebalance without selling?",
         "Yes — tick “Only buy”. New cash then goes to the holdings furthest below their target, which avoids "
         "selling (and, in many countries, the tax on selling)."),
        ("How often should I rebalance?",
         "Common choices are once or twice a year, or whenever a holding drifts more than a few percentage points "
         "from its target. The Pulsarium cabinet lets you set a maximum weight per position and flags when one grows past it."),
    ])
    body = (
        tool_head("Calculator", "Portfolio rebalancing calculator",
                  "List your holdings with their value today and the weight you want, add any new cash, and see how "
                  "much to buy or sell of each to get back to target.")
        + form
        + f'<p class="calc-note">{esc(DISCLAIMER)}</p>'
        + closing_cta("Let the cabinet watch your weights",
                      "Set a maximum weight per position in the free cabinet and see when one drifts past it.")
        + faq_section
        + other_tools(path)
    )
    return {"path": path, "crumb": "Rebalancing calculator", "parent": ("Tools", "/tools/"),
            "title": "Portfolio rebalancing calculator: buy and sell to target weights | Pulsarium",
            "description": "Free portfolio rebalancing calculator: enter your holdings, their values and target "
                           "weights to see what to buy or sell - or how to rebalance with new cash only.",
            "body": body, "head": faq_ld}


def tools_hub() -> dict:
    cards = "".join(
        f'<a class="panel feature feature-link" href="{p}"><h3>{esc(n)}</h3><p>{esc(d)} →</p></a>' for p, n, d in TOOLS
    )
    body = (
        tool_head("Tools", "Free investing calculators",
                  "Quick, private calculators for dividend investors and long-term savers. Everything is computed in "
                  "your browser; nothing you type is sent anywhere.")
        + f'<section class="block"><div class="feature-grid cols-3">{cards}</div></section>'
        + closing_cta("From calculator to your real portfolio",
                      "Track holdings, dividends and weights with live data in the free Pulsarium cabinet.")
    )
    return {"path": "/tools/", "crumb": "Tools",
            "title": "Free investing calculators: dividend yield, DCA, rebalancing | Pulsarium",
            "description": "Free investing calculators: dividend yield and income, dollar-cost averaging growth and "
                           "portfolio rebalancing. Private - everything is computed in your browser.",
            "body": body, "head": ""}


def all_pages() -> list:
    return [tools_hub(), dividend_yield(), dca(), rebalancing()]
