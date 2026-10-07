/* One shared snapshot serves the overview, map and details. */
(() => {
  'use strict';
  const overview = document.getElementById('sector-overview');
  const sheet = document.getElementById('sector-sheet');
  if (!overview || !sheet || typeof sheet.showModal !== 'function') return;
  const endpoint = 'https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/sec_sector_analytics?id=eq.global&select=payload';
  const publishableKey = 'sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF';
  const cacheKey = 'pulsarium-sector-snapshot-v5';
  const ttl = 15 * 60 * 1000;
  const body = sheet.querySelector('.sector-sheet-body');
  let snapshot, selected, view = 'map', more = false, opener, newsY = 0, oldBodyOverflow, oldHtmlOverflow;
  const positions = { map: 0, details: 0 };
  const query = selector => sheet.querySelector(selector);
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const count = value => Number.isInteger(value) && value >= 0;
  const signed = (value, suffix = '%') => number(value) ? `${value > 0 ? '+' : ''}${value.toFixed(value !== 0 && Math.abs(value) < .1 ? 2 : 1)}${suffix}` : '—';
  const percent = value => number(value) ? `${value.toFixed(0)}%` : '—';
  const weighted = s => number(s.revenue_total_yoy);
  const ttm = data => data?.version === 2 && data.revenue_basis === "ttm";
  const range = (first, last) => first === last ? date(last) : `${date(first)} – ${date(last)}`;
  const reporting = (c, total) => `${c.reporting_target} or later: ${c.latest_report_count.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} companies · ${percent(c.latest_report_revenue_pct)} of prior-year TTM revenue.`;
  const ranked = data => data.company_ranking === 'revenue';
  const revenue = s => weighted(s) ? s.revenue_total_yoy : s.revenue_yoy;
  const tone = value => number(value) && value !== 0 ? value > 0 ? 'sector-up' : 'sector-down' : '';
  const money = value => number(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 }).format(value) : '—';
  const date = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? new Date(`${value}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : 'not available';
  const marginPressure = s => s.company_count >= 10 && revenue(s) >= 0 && s.margin_count >= 10 && s.margin_change < 0;
  const stateName = s => marginPressure(s) ? 'Margin pressure' : ({ growth: 'Business growth', pressure: 'Under pressure', mixed: 'Mixed trends', limited: 'Limited sample' })[s.direction];
  const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  function valid(data) {
    return (data?.version === 1 && /^Q[1-4] \d{4}$/.test(data.period) && /^\d{4}$/.test(data.cash_flow_period) || ttm(data) && data.period === "Latest reported" && data.cash_flow_period === "Latest annual" && count(data.coverage?.latest_report_count) && number(data.coverage?.latest_report_revenue_pct)) && (data.company_ranking === undefined || ranked(data)) &&
      count(data.coverage?.included) && count(data.coverage?.eligible) && count(data.coverage?.classified) &&
      Array.isArray(data.sectors) && data.sectors.length > 0 && data.sectors.every(s =>
        /^[a-z][a-z-]+$/.test(s.id) && typeof s.name === 'string' && count(s.company_count) && s.company_count > 0 && number(s.revenue_yoy) &&
        ['growth', 'pressure', 'mixed', 'limited'].includes(s.direction) && count(s.growing_count) && count(s.declining_count) &&
        count(s.flat_count) && count(s.margin_count) && count(s.fcf_count) && count(s.fcf_positive_count) &&
        s.growing_count + s.declining_count + s.flat_count === s.company_count && s.margin_count <= s.company_count &&
        s.fcf_count <= s.company_count && s.fcf_positive_count <= s.fcf_count && Array.isArray(s.companies) &&
        (s.revenue_total_yoy === undefined || (weighted(s) && number(s.revenue_current) && s.revenue_current > 0 &&
          number(s.revenue_previous) && s.revenue_previous > 0 && s.companies.every(c =>
            number(c.revenue_current) && c.revenue_current > 0 && number(c.revenue_previous) && c.revenue_previous > 0 &&
            typeof c.low_revenue_base === 'boolean'))));
  }
  function selectSector(id, showDetails = true) {
    if (!snapshot.sectors.some(s => s.id === id)) return;
    if (selected !== id) positions.details = 0;
    selected = id;
    query('[data-sector-select]').value = id;
    renderDetails();
    if (showDetails) setView('details');
  }
  function setView(next) {
    if (!['map', 'details'].includes(next)) return;
    if (view !== next) positions[view] = body.scrollTop;
    view = next;
    sheet.querySelectorAll('[data-sector-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.sectorView === view)));
    sheet.querySelectorAll('[data-sector-content]').forEach(section => { section.hidden = section.dataset.sectorContent !== view; });
    body.scrollTop = positions[view];
  }
  function open(button, id) {
    if (!snapshot) return;
    opener = button;
    if (id) { positions.details = 0; selectSector(id); }
    else setView('map');
    if (sheet.open) return;
    newsY = window.scrollY;
    oldBodyOverflow = document.body.style.overflow;
    oldHtmlOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    sheet.showModal();
    body.scrollTop = positions[view];
  }
  function close() {
    positions[view] = body.scrollTop;
    sheet.close();
  }
  sheet.addEventListener('close', () => {
    document.body.style.overflow = oldBodyOverflow || '';
    document.documentElement.style.overflow = oldHtmlOverflow || '';
    window.scrollTo({ top: newsY, behavior: 'instant' });
    opener?.focus({ preventScroll: true });
  });
  sheet.addEventListener('cancel', event => { event.preventDefault(); close(); });
  sheet.addEventListener('click', event => {
    if (event.target !== sheet) return;
    const rect = sheet.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
  });
  query('[data-sector-close]').addEventListener('click', close);
  query('[data-sector-maximize]').addEventListener('click', function () {
    const expanded = sheet.dataset.expanded !== 'true';
    sheet.dataset.expanded = String(expanded);
    this.setAttribute('aria-pressed', String(expanded));
    this.setAttribute('aria-label', expanded ? 'Restore panel width' : 'Expand to full width');
    this.querySelector('path').setAttribute('d', expanded ? 'M3 8h5V3M21 8h-5V3M16 21v-5h5M8 21v-5H3' : 'M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5');
  });
  sheet.querySelectorAll('[data-sector-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.sectorView)));
  overview.querySelectorAll('[data-sector-open]').forEach(button => button.addEventListener('click', () => open(button)));
  query('[data-sector-select]').addEventListener('change', function () { positions.details = 0; selectSector(this.value); });
  overview.querySelector('[data-sector-more]').addEventListener('click', function () {
    more = !more;
    this.setAttribute('aria-expanded', String(more));
    this.textContent = more ? 'Show less' : 'More sectors';
    renderSummary();
  });
  function industryButton(s, className) {
    const button = el('button', className);
    button.type = 'button';
    button.dataset.sectorId = s.id;
    button.addEventListener('click', () => sheet.open ? selectSector(s.id) : open(button, s.id));
    return button;
  }
  function renderSummary() {
    const root = overview.querySelector('[data-sector-summary]');
    root.replaceChildren(el('p', 'sector-measure', snapshot.sectors.every(weighted) ? 'Revenue totals · same-company sample' : 'Quarterly business signals · median'));
    const growing = snapshot.sectors.filter(s => s.direction === 'growth').sort((a, b) => revenue(b) - revenue(a));
    const pressure = snapshot.sectors.filter(s => s.company_count >= 10 && (revenue(s) < 0 || (s.margin_count >= 10 && s.margin_change < 0)))
      .sort((a, b) => {
        if ((revenue(a) < 0) !== (revenue(b) < 0)) return revenue(a) < 0 ? -1 : 1;
        return revenue(a) < 0 ? revenue(a) - revenue(b) : a.margin_change - b.margin_change;
      });
    const groups = [
      ['Business growth', 'sector-up', growing],
      ['Under pressure', 'sector-down', pressure]
    ];
    if (more) groups.push(['Other trends / limited sample', '', snapshot.sectors.filter(s => !growing.includes(s) && !pressure.includes(s))]);
    groups.forEach(([title, color, sectors]) => {
      root.append(el('h3', `sector-group-title ${color}`, title));
      const visible = more ? sectors : sectors.slice(0, 3);
      if (!visible.length) root.append(el('p', 'sector-caption', 'No industries meet these criteria.'));
      visible.forEach(s => {
        const row = industryButton(s, 'sector-summary-row');
        const marginPressure = color === 'sector-down' && revenue(s) >= 0;
        const value = marginPressure ? s.margin_change : revenue(s);
        const name = el('span', '', s.name);
        name.append(el('small', 'sector-summary-label', marginPressure ? ttm(snapshot) ? 'Margin change · TTM median' : 'Margin change · median' : weighted(s) ? ttm(snapshot) ? 'Total revenue · TTM YoY' : 'Total revenue · YoY' : 'Revenue · YoY · median'));
        row.append(name, el('strong', tone(value), signed(value, marginPressure ? ' pp' : '%')));
        root.append(row);
      });
    });
  }
  function renderMap() {
    const map = query('[data-sector-map]'), table = query('[data-sector-table]');
    map.replaceChildren(); table.replaceChildren();
    const totals = snapshot.sectors.every(weighted);
    query('[data-sector-map-measure]').textContent = `${totals ? ttm(snapshot) ? 'Total revenue · TTM YoY' : 'Total revenue · YoY' : 'Revenue · YoY · median'} · margin change below`;
    query('[data-sector-revenue-heading]').replaceChildren(document.createTextNode(totals ? 'Total revenue' : 'Revenue'), el('br'), document.createTextNode(ttm(snapshot) ? 'TTM YoY' : 'YoY'));
    query('[data-sector-median-heading]').replaceChildren(document.createTextNode('Median'), el('br'), document.createTextNode(ttm(snapshot) ? 'TTM YoY' : 'YoY'));
    query('[data-sector-comparison-note]').textContent = ttm(snapshot) ? 'Revenue and margin figures cover the latest four reported fiscal quarters. Revenue totals use the same companies in both periods; each company counts once in the median and growing share. Margin change is in percentage points. Free cash flow uses each issuer’s latest annual report. Open an industry for reporting dates, amounts, sample sizes and filing sources.' : 'Revenue totals use the same companies in both periods. Growing is the share with increasing revenue; median YoY gives each company equal weight. Margin change is in percentage points; free cash flow uses the annual reporting window. Open an industry for revenue amounts, sample sizes and filing sources.';
    snapshot.sectors.forEach(s => {
      const tile = industryButton(s, 'sector-tile');
      tile.dataset.direction = s.direction;
      tile.setAttribute('aria-label', `${s.name}: ${stateName(s)}, ${weighted(s) ? 'total' : 'median'} revenue change ${signed(revenue(s))}, median margin change ${signed(s.margin_change, ' pp')}, ${s.company_count} companies. Open details.`);
      tile.append(el('span', 'sector-tile-name', s.name), el('strong', `sector-tile-value ${tone(revenue(s))}`, signed(revenue(s))),
        el('span', 'sector-tile-measure', ttm(snapshot) ? 'Revenue · TTM YoY' : 'Revenue · YoY'));
      const margin = el('span', 'sector-tile-margin');
      margin.append(el('span', '', 'Margin change'), el('strong', tone(s.margin_change), signed(s.margin_change, ' pp')));
      tile.append(margin, el('span', 'sector-tile-foot', `${s.company_count} companies · ${stateName(s)}`));
      map.append(tile);
      const row = el('tr'), name = el('td'), button = industryButton(s, '');
      button.textContent = s.name; name.append(button);
      row.append(name, el('td', '', s.company_count), el('td', tone(revenue(s)), signed(revenue(s))),
        el('td', tone(s.revenue_yoy), signed(s.revenue_yoy)), el('td', '', percent(100 * s.growing_count / s.company_count)),
        el('td', tone(s.margin_change), signed(s.margin_change, ' pp')), el('td', '', percent(s.fcf_positive_pct)));
      table.append(row);
    });
  }
  function metric(label, value, note, color = '') {
    const card = el('div', 'sector-metric');
    card.append(el('h4', '', label), el('strong', color, value), el('p', '', note));
    return card;
  }
  function renderDetails() {
    const s = snapshot.sectors.find(item => item.id === selected);
    if (!s) return;
    sheet.querySelectorAll('[data-sector-map] [data-sector-id]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.sectorId === selected));
    });
    const root = query('[data-sector-details]');
    const title = el('h3', 'sector-detail-title', s.name); title.id = 'sector-detail-title';
    root.replaceChildren(title, el('p', 'sector-detail-description', `${stateName(s)} · ${s.company_count} companies with comparable ${ttm(snapshot) ? "TTM" : "quarterly"} revenue · ${snapshot.period}`));
    const metrics = el('div', 'sector-metrics');
    if (weighted(s)) metrics.append(metric(ttm(snapshot) ? 'Total revenue · TTM YoY' : 'Total revenue · YoY', signed(s.revenue_total_yoy), `${money(s.revenue_current)} current · ${money(s.revenue_previous)} prior year · ${s.company_count} companies`, tone(s.revenue_total_yoy)));
    metrics.append(metric(ttm(snapshot) ? 'Company revenue · TTM median' : 'Company revenue · median', signed(s.revenue_yoy), `${s.company_count} companies · ${ttm(snapshot) ? "TTM YoY" : "YoY"} · equal company weight`, tone(s.revenue_yoy)),
      metric('Operating margin change · median', signed(s.margin_change, ' pp'), `${s.margin_count} companies · ${ttm(snapshot) ? "TTM YoY" : "YoY"}`, tone(s.margin_change)),
      metric('Positive free cash flow', percent(s.fcf_positive_pct), s.fcf_count ? `${s.fcf_positive_count} of ${s.fcf_count} companies · ${ttm(snapshot) ? `latest annual · years ended ${range(s.cash_flow_end_min, s.cash_flow_end_max)}` : `annual ${snapshot.cash_flow_period}`}` : ['banks', 'insurance'].includes(s.id) ? 'This cash-flow measure is not comparable for banking and insurance.' : `Comparable cash-flow data unavailable · annual ${snapshot.cash_flow_period}`));
    root.append(metrics);
    if (ttm(snapshot)) {
      const reports = el('section', 'sector-card sector-reporting');
      reports.append(el('h3', '', 'Reporting coverage'), el('p', '', reporting(s, s.company_count)),
        el('p', 'sector-note', `Latest quarters ended ${range(s.period_end_min, s.period_end_max)}. Each company uses its latest available four-quarter revenue; fiscal calendars differ.`));
      root.append(reports);
    }
    const breadth = el('section', 'sector-card');
    breadth.append(el('h3', '', 'How broad is the trend?'));
    const bar = el('div', 'sector-breadth'); bar.setAttribute('aria-hidden', 'true');
    [['sector-breadth-growing', s.growing_count], ['sector-breadth-declining', s.declining_count], ['', s.flat_count]].forEach(([cls, value]) => {
      const segment = el('span', cls); segment.style.width = `${100 * value / s.company_count}%`; bar.append(segment);
    });
    const labels = el('div', 'sector-breadth-labels');
    labels.append(el('span', 'sector-up', `${s.growing_count} growing · ${percent(100 * s.growing_count / s.company_count)}`),
      el('span', 'sector-down', `${s.declining_count} declining · ${percent(100 * s.declining_count / s.company_count)}`), el('span', '', `${s.flat_count} flat`));
    breadth.append(bar, labels, el('p', 'sector-note', weighted(s) ? 'Each company counts once in this breadth measure. The industry signal combines total revenue growth with the median change in operating margin.' : 'Revenue growth and margin improvement together determine the industry signal. Mixed trends can include rising revenue with falling profitability.'));
    root.append(breadth);
    const insiders = el('section', 'sector-card'), window = snapshot.insider_window || {};
    const amounts = s.insiders, through = window.through;
    insiders.append(el('h3', '', 'Insider purchases & sales'));
    const values = el('div', 'sector-insider-values');
    const purchases = el('div'), sales = el('div');
    purchases.append(el('p', '', 'Direct purchases'), el('strong', '', money(window.purchase_complete ? amounts?.purchases ?? 0 : null)),
      el('p', '', `Collected since ${date(window.purchase_since)}`));
    sales.append(el('p', '', 'Direct sales'), el('strong', '', money(window.coverage_complete ? amounts?.sales ?? 0 : null)),
      el('p', '', `Collected since ${date(window.sale_since)}`));
    values.append(purchases, sales); insiders.append(values);
    insiders.append(el('p', 'sector-note', window.coverage_complete ? `Identified 10b5-1 plan sales: ${money(amounts?.planned_sales ?? 0)}. Included in the sales total.` : 'Sales disclosures are still being collected; a complete sales total is not yet available.'));
    insiders.append(el('p', 'sector-note', `Available disclosures within the last 30 days · through ${date(through)}. ${window.partial ? 'The collection history currently covers part of this window. ' : ''}Classified issuers only; planned purchases are excluded.`));
    root.append(insiders);
    const companies = el('section', 'sector-card');
    const revenueRanking = ranked(snapshot);
    companies.append(el('h3', '', revenueRanking ? 'Largest companies by revenue' : 'Companies behind the trend'),
      el('p', 'sector-note', revenueRanking ? 'Largest quarterly revenue in the covered sample. YoY change in parentheses.' : 'Highest and lowest percentage revenue changes in the covered sample. Quarterly revenue shows the scale of each business.'));
    const list = el('div', 'sector-company-list');
    s.companies.forEach(c => {
      const row = el('div', 'sector-company'), info = el('div'), values = el('div', 'sector-company-values');
      const ticker = el('span', 'sector-company-ticker', c.symbol);
      info.append(ticker, el('p', 'sector-company-name', c.name), el('small', '', `Quarter ended ${date(c.period_end)}`));
      if (number(c.revenue_current) && number(c.revenue_previous)) {
        if (!revenueRanking) info.append(el('p', 'sector-company-revenue', `Revenue: ${money(c.revenue_current)}`));
        const previous = el('small', '', `Prior-year quarter: ${money(c.revenue_previous)}`);
        previous.title = `Quarter ended ${date(c.revenue_previous_end)}`; info.append(previous);
        if (c.low_revenue_base) {
          const flag = el('span', 'sector-company-flag', 'Low revenue base');
          flag.title = 'Prior-year quarterly revenue below $1 million.'; info.append(flag);
        }
      }
      if (revenueRanking) {
        const value = el('strong', 'sector-company-value');
        value.append(el('span', 'sector-company-amount', money(c.revenue_current)), el('span', `sector-company-yoy ${tone(c.revenue_yoy)}`, `(${signed(c.revenue_yoy)})`));
        values.append(value, el('small', '', 'Quarterly revenue · YoY'));
      } else values.append(el('strong', tone(c.revenue_yoy), signed(c.revenue_yoy)), el('small', '', 'Revenue YoY'));
      row.append(info, values); list.append(row);
    });
    companies.append(list); root.append(companies);
  }
  function render(data) {
    snapshot = data;
    selected = snapshot.sectors.some(s => s.id === selected) ? selected : snapshot.sectors.find(s => s.direction === 'pressure')?.id || snapshot.sectors[0].id;
    const status = overview.querySelector('[data-sector-state]');
    const stale = Date.now() - Date.parse(snapshot.updated_at) > 36 * 60 * 60 * 1000;
    status.hidden = !stale;
    if (stale) status.textContent = 'Update delayed. Showing the last available snapshot.';
    overview.querySelector('[data-sector-summary]').hidden = false;
    overview.querySelector('[data-sector-period]').textContent = `Company filings · ${ttm(snapshot) ? "Latest reported · TTM" : snapshot.period}`;
    overview.querySelector('[data-sector-more]').hidden = false;
    overview.querySelectorAll('[data-sector-open]').forEach(button => { button.disabled = false; });
    const meta = query('[data-sector-meta]');
    meta.replaceChildren(el('strong', '', snapshot.period), el('span', '', `${snapshot.coverage.included.toLocaleString('en-US')} companies · ${snapshot.sectors.length} industries`));
    const coverage = snapshot.coverage;
    const updated = new Date(snapshot.updated_at);
    const refreshed = Number.isNaN(updated.getTime()) ? '' : `Snapshot refreshed ${updated.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' })} UTC.`;
    query('[data-sector-coverage]').textContent = ttm(snapshot)
      ? `Revenue: latest reported TTM. ${reporting(coverage, coverage.included)} Latest quarters ended ${range(coverage.period_end_min, coverage.period_end_max)}. ${coverage.complete ? '' : 'Coverage is expanding. '}${refreshed}`
      : `Revenue period: ${snapshot.period}. SEC coverage: ${coverage.classified.toLocaleString('en-US')} of ${coverage.eligible.toLocaleString('en-US')} eligible issuers classified. ${coverage.complete ? '' : 'Coverage is expanding. '}Annual cash flow: ${snapshot.cash_flow_period}. ${refreshed}`;
    const select = query('[data-sector-select]'); select.replaceChildren();
    [...snapshot.sectors].sort((a, b) => a.name.localeCompare(b.name)).forEach(s => { const option = el('option', '', s.name); option.value = s.id; select.append(option); });
    select.value = selected;
    const methods = query('[data-sector-methodology]'); methods.replaceChildren();
    Object.entries(snapshot.methodology || {}).forEach(([key, text]) => {
      const p = el('p'), label = el('strong', '', `${({ universe: 'Coverage', reporting_period: 'Reporting period', revenue: 'Revenue', company_values: 'Company context', margin: 'Operating margin', cash_flow: 'Free cash flow', direction: 'Industry signal', insiders: 'Insider activity', cache: 'Updates' })[key] || key}: `);
      p.append(label, document.createTextNode(String(text))); methods.append(p);
    });
    renderSummary(); renderMap(); renderDetails(); setView(view);
    window.pulsariumSectorSnapshot = data;
    window.dispatchEvent(new CustomEvent('pulsarium:sector-snapshot', { detail: data }));
  }
  async function load() {
    let cached;
    try { cached = JSON.parse(sessionStorage.getItem(cacheKey)); } catch (_) { /* Storage can be unavailable. */ }
    if (cached && valid(cached.payload) && ttm(cached.payload) && ranked(cached.payload) && cached.payload.sectors.every(weighted) && Date.now() - cached.saved < ttl) { render(cached.payload); return; }
    try {
      const response = await fetch(endpoint, { headers: { apikey: publishableKey }, signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error('Snapshot unavailable');
      const rows = await response.json(), data = rows?.[0]?.payload;
      if (!valid(data)) throw new Error('Snapshot not ready');
      render(data);
      try { if (ttm(data) && ranked(data) && data.sectors.every(weighted)) sessionStorage.setItem(cacheKey, JSON.stringify({ saved: Date.now(), payload: data })); } catch (_) { /* Storage can be unavailable. */ }
    } catch (_) {
      if (cached && valid(cached.payload)) {
        render(cached.payload);
        const state = overview.querySelector('[data-sector-state]'); state.hidden = false; state.textContent = 'Showing the saved snapshot. Refresh is temporarily unavailable.';
      } else {
        const state = overview.querySelector('[data-sector-state]');
        state.textContent = 'Industry data is temporarily unavailable.';
        window.dispatchEvent(new CustomEvent('pulsarium:sector-unavailable'));
        const retry = el('button', 'sector-more', 'Retry'); retry.type = 'button';
        retry.addEventListener('click', () => { retry.remove(); state.textContent = 'Loading industry fundamentals…'; load(); });
        state.after(retry);
      }
    }
  }
  load();
})();
