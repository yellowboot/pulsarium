/* One shared snapshot serves the overview, map and details. */
(() => {
  'use strict';
  const overview = document.getElementById('sector-overview');
  const sheet = document.getElementById('sector-sheet');
  if (!overview || !sheet || typeof sheet.showModal !== 'function') return;
  const endpoint = 'https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/sec_sector_analytics?id=eq.global&select=payload';
  const publishableKey = 'sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF';
  const cacheKey = 'pulsarium-sector-snapshot-v1';
  const ttl = 15 * 60 * 1000;
  const body = sheet.querySelector('.sector-sheet-body');
  let snapshot, selected, view = 'map', more = false, opener, newsY = 0, oldBodyOverflow, oldHtmlOverflow;
  const positions = { map: 0, details: 0 };
  const query = selector => sheet.querySelector(selector);
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const count = value => Number.isInteger(value) && value >= 0;
  const signed = (value, suffix = '%') => number(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}${suffix}` : '—';
  const percent = value => number(value) ? `${value.toFixed(0)}%` : '—';
  const tone = value => number(value) && value !== 0 ? value > 0 ? 'sector-up' : 'sector-down' : '';
  const money = value => number(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 }).format(value) : '—';
  const date = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? new Date(`${value}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : 'not available';
  const stateName = s => ({ growth: 'Business growth', pressure: 'Under pressure', mixed: 'Mixed trends', limited: 'Limited sample' })[s.direction];
  const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  function valid(data) {
    return data?.version === 1 && /^Q[1-4] \d{4}$/.test(data.period) && /^\d{4}$/.test(data.cash_flow_period) &&
      count(data.coverage?.included) && count(data.coverage?.eligible) && count(data.coverage?.classified) &&
      Array.isArray(data.sectors) && data.sectors.length > 0 && data.sectors.every(s =>
        /^[a-z][a-z-]+$/.test(s.id) && typeof s.name === 'string' && count(s.company_count) && number(s.revenue_yoy) &&
        ['growth', 'pressure', 'mixed', 'limited'].includes(s.direction) && count(s.growing_count) && count(s.declining_count) &&
        count(s.flat_count) && count(s.margin_count) && count(s.fcf_count) && count(s.fcf_positive_count) &&
        s.growing_count + s.declining_count + s.flat_count === s.company_count && s.margin_count <= s.company_count &&
        s.fcf_count <= s.company_count && s.fcf_positive_count <= s.fcf_count && Array.isArray(s.companies));
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
    root.replaceChildren(el('p', 'sector-measure', 'Quarterly business signals · median'));
    const growing = snapshot.sectors.filter(s => s.direction === 'growth').sort((a, b) => b.revenue_yoy - a.revenue_yoy);
    const pressure = snapshot.sectors.filter(s => s.company_count >= 10 && (s.revenue_yoy < 0 || (s.margin_count >= 10 && s.margin_change < 0)))
      .sort((a, b) => {
        if ((a.revenue_yoy < 0) !== (b.revenue_yoy < 0)) return a.revenue_yoy < 0 ? -1 : 1;
        return a.revenue_yoy < 0 ? a.revenue_yoy - b.revenue_yoy : a.margin_change - b.margin_change;
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
        const marginPressure = color === 'sector-down' && s.revenue_yoy >= 0;
        const value = marginPressure ? s.margin_change : s.revenue_yoy;
        const name = el('span', '', s.name);
        name.append(el('small', 'sector-summary-label', marginPressure ? 'Margin change · YoY' : 'Revenue · YoY'));
        row.append(name, el('strong', tone(value), signed(value, marginPressure ? ' pp' : '%')));
        root.append(row);
      });
    });
  }
  function renderMap() {
    const map = query('[data-sector-map]'), table = query('[data-sector-table]');
    map.replaceChildren(); table.replaceChildren();
    snapshot.sectors.forEach(s => {
      const tile = industryButton(s, 'sector-tile');
      tile.dataset.direction = s.direction;
      tile.setAttribute('aria-label', `${s.name}: ${stateName(s)}, median revenue change ${signed(s.revenue_yoy)}, ${s.company_count} companies. Open details.`);
      tile.append(el('span', 'sector-tile-name', s.name), el('strong', `sector-tile-value ${tone(s.revenue_yoy)}`, signed(s.revenue_yoy)),
        el('span', 'sector-tile-foot', `${s.company_count} companies · ${stateName(s)}`));
      map.append(tile);
      const row = el('tr'), name = el('td'), button = industryButton(s, '');
      button.textContent = s.name; name.append(button);
      row.append(name, el('td', '', s.company_count), el('td', tone(s.revenue_yoy), signed(s.revenue_yoy)),
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
    const root = query('[data-sector-details]');
    const title = el('h3', 'sector-detail-title', s.name); title.id = 'sector-detail-title';
    root.replaceChildren(title, el('p', 'sector-detail-description', `${stateName(s)} · ${s.company_count} companies with comparable quarterly revenue · ${snapshot.period}`));
    const metrics = el('div', 'sector-metrics');
    metrics.append(metric('Revenue change · median', signed(s.revenue_yoy), `${s.company_count} companies · YoY`, tone(s.revenue_yoy)),
      metric('Operating margin change', signed(s.margin_change, ' pp'), `${s.margin_count} companies · YoY`, tone(s.margin_change)),
      metric('Positive free cash flow', percent(s.fcf_positive_pct), s.fcf_count ? `${s.fcf_positive_count} of ${s.fcf_count} companies · annual ${snapshot.cash_flow_period}` : ['banks', 'insurance'].includes(s.id) ? 'This cash-flow measure is not comparable for banking and insurance.' : `Comparable cash-flow data unavailable · annual ${snapshot.cash_flow_period}`));
    root.append(metrics);
    const breadth = el('section', 'sector-card');
    breadth.append(el('h3', '', 'How broad is the trend?'));
    const bar = el('div', 'sector-breadth'); bar.setAttribute('aria-hidden', 'true');
    [['sector-breadth-growing', s.growing_count], ['sector-breadth-declining', s.declining_count], ['', s.flat_count]].forEach(([cls, value]) => {
      const segment = el('span', cls); segment.style.width = `${100 * value / s.company_count}%`; bar.append(segment);
    });
    const labels = el('div', 'sector-breadth-labels');
    labels.append(el('span', 'sector-up', `${s.growing_count} growing`), el('span', 'sector-down', `${s.declining_count} declining`), el('span', '', `${s.flat_count} flat`));
    breadth.append(bar, labels, el('p', 'sector-note', 'Revenue growth and margin improvement together determine the industry signal. Mixed trends can include rising revenue with falling profitability.'));
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
    companies.append(el('h3', '', 'Companies behind the trend'), el('p', 'sector-note', 'Highest and lowest revenue changes in the covered sample. Open a ticker to see its SEC filing.'));
    const list = el('div', 'sector-company-list');
    s.companies.forEach(c => {
      const row = el('div', 'sector-company'), info = el('div'), values = el('div');
      const url = typeof c.source === 'string' && /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/\d+\/\d+\/\d{10}-\d{2}-\d{6}-index\.html$/.test(c.source) ? c.source : null;
      const ticker = el(url ? 'a' : 'span', '', c.symbol);
      if (url) { ticker.href = url; ticker.target = '_blank'; ticker.rel = 'noopener noreferrer'; ticker.setAttribute('aria-label', `${c.symbol}: open SEC filing`); }
      info.append(ticker, el('p', 'sector-company-name', c.name), el('small', '', `Quarter ended ${date(c.period_end)}`));
      values.append(el('strong', tone(c.revenue_yoy), signed(c.revenue_yoy)), el('small', '', 'Revenue YoY'));
      row.append(info, values); list.append(row);
    });
    companies.append(list); root.append(companies);
  }
  function render(data) {
    snapshot = data;
    selected = snapshot.sectors.some(s => s.id === selected) ? selected : snapshot.sectors.find(s => s.direction === 'pressure')?.id || snapshot.sectors[0].id;
    overview.querySelector('[data-sector-state]').hidden = true;
    overview.querySelector('[data-sector-summary]').hidden = false;
    overview.querySelector('[data-sector-period]').textContent = `Company filings · ${snapshot.period}`;
    overview.querySelector('[data-sector-more]').hidden = false;
    overview.querySelectorAll('[data-sector-open]').forEach(button => { button.disabled = false; });
    const meta = query('[data-sector-meta]');
    meta.replaceChildren(el('strong', '', snapshot.period), el('span', '', `${snapshot.coverage.included.toLocaleString('en-US')} companies · ${snapshot.sectors.length} industries`));
    const coverage = snapshot.coverage;
    const updated = new Date(snapshot.updated_at);
    query('[data-sector-coverage]').textContent = `SEC coverage: ${coverage.classified.toLocaleString('en-US')} of ${coverage.eligible.toLocaleString('en-US')} eligible issuers classified. ${coverage.complete ? '' : 'Coverage is expanding. '}Annual cash flow: ${snapshot.cash_flow_period}. ${Number.isNaN(updated.getTime()) ? '' : `Updated ${updated.toLocaleDateString('en-US', { timeZone: 'UTC' })}.`}`;
    const select = query('[data-sector-select]'); select.replaceChildren();
    [...snapshot.sectors].sort((a, b) => a.name.localeCompare(b.name)).forEach(s => { const option = el('option', '', s.name); option.value = s.id; select.append(option); });
    select.value = selected;
    const methods = query('[data-sector-methodology]'); methods.replaceChildren();
    Object.entries(snapshot.methodology || {}).forEach(([key, text]) => {
      const p = el('p'), label = el('strong', '', `${({ universe: 'Coverage', revenue: 'Revenue', margin: 'Operating margin', cash_flow: 'Free cash flow', direction: 'Industry signal', insiders: 'Insider activity', cache: 'Updates' })[key] || key}: `);
      p.append(label, document.createTextNode(String(text))); methods.append(p);
    });
    renderSummary(); renderMap(); renderDetails(); setView(view);
  }
  async function load() {
    let cached;
    try { cached = JSON.parse(sessionStorage.getItem(cacheKey)); } catch (_) { /* Storage can be unavailable. */ }
    if (cached && valid(cached.payload) && Date.now() - cached.saved < ttl) { render(cached.payload); return; }
    try {
      const response = await fetch(endpoint, { headers: { apikey: publishableKey }, signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error('Snapshot unavailable');
      const rows = await response.json(), data = rows?.[0]?.payload;
      if (!valid(data)) throw new Error('Snapshot not ready');
      render(data);
      try { sessionStorage.setItem(cacheKey, JSON.stringify({ saved: Date.now(), payload: data })); } catch (_) { /* Storage can be unavailable. */ }
    } catch (_) {
      if (cached && valid(cached.payload)) {
        render(cached.payload);
        const state = overview.querySelector('[data-sector-state]'); state.hidden = false; state.textContent = 'Showing the saved snapshot. Refresh is temporarily unavailable.';
      } else {
        const state = overview.querySelector('[data-sector-state]');
        state.textContent = 'Industry data is temporarily unavailable.';
        const retry = el('button', 'sector-more', 'Retry'); retry.type = 'button';
        retry.addEventListener('click', () => { retry.remove(); state.textContent = 'Loading industry fundamentals…'; load(); });
        state.after(retry);
      }
    }
  }
  load();
})();
