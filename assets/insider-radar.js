(function () {
  'use strict';
  const root = document.getElementById('insider-radar');
  if (!root) return;
  const endpoint = 'https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/sec_insider_radar?id=eq.global&select=payload';
  const publicKey = 'sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF';
  const list = root.querySelector('[data-radar-list]');
  const meta = root.querySelector('[data-radar-meta]');
  const state = root.querySelector('[data-radar-state]');
  const method = root.querySelector('[data-radar-method]');
  const buttons = [...root.querySelectorAll('[data-radar-tab]')];
  let data, tab = 'clusters';
  const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD',
    notation: 'compact', maximumFractionDigits: 1 });
  const date = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return '—';
    return new Date(value + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  };
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function secLink(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && url.hostname === 'www.sec.gov' &&
        /^\/Archives\/edgar\/data\/\d+\/\d{10}-\d{2}-\d{6}\.txt$/.test(url.pathname) ? url.href : null;
    } catch (_) { return null; }
  }
  function render() {
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.radarTab === tab)));
    list.replaceChildren();
    if (!data) return;
    const coverage = data.coverage || {};
    const through = data.through ? ' · Disclosures through ' + date(data.through) : '';
    meta.textContent = 'Last ' + data.window_days + ' days' + through;
    state.hidden = false;
    if (!coverage.complete) {
      state.textContent = 'History is updating · ' + Number(coverage.processed || 0).toLocaleString('en-US') +
        ' of ' + Number(coverage.discovered || 0).toLocaleString('en-US') + ' filings processed.';
    }
    const cards = Array.isArray(data[tab]) ? data[tab].slice(0, 5) : [];
    if (!cards.length) {
      state.textContent = coverage.complete ? (tab === 'clusters' ?
        'No qualifying groups of buyers in this window.' : 'No qualifying purchases above $100,000 in this window.') :
        state.textContent + ' Results appear as filings are processed.';
    } else {
      if (coverage.complete) state.hidden = true;
      cards.forEach(card => {
        const article = element('article', 'insider-radar-card');
        const head = element('div', 'insider-radar-card-head');
        const ticker = element('a', 'insider-radar-symbol', card.symbol);
        ticker.href = '/news/' + String(card.symbol).toLowerCase().replace(/[^a-z0-9]+/g, '-') + '/';
        head.append(ticker, element('span', 'insider-radar-value', money.format(Number(card.amount) || 0)));
        article.append(head, element('p', 'insider-radar-company', card.name));
        const people = Array.isArray(card.people) ? card.people : [];
        const title = card.buyers > 1 ? card.buyers + ' officers / directors' : (people[0]?.title || 'Officer / director');
        article.append(element('p', 'insider-radar-detail', title + ' · direct purchases'));
        article.append(element('p', 'insider-radar-detail', 'Traded ' + date(card.start) +
          (card.end !== card.start ? '–' + date(card.end) : '') + ' · Filed ' + date(card.disclosed)));
        const sources = element('p', 'insider-radar-sources');
        (Array.isArray(card.sources) ? card.sources : []).slice(0, 3).forEach((source, i) => {
          const href = secLink(source.url);
          if (!href) return;
          const link = element('a', '', card.sources.length > 1 ? 'Form 4 · ' + (i + 1) : 'SEC Form 4');
          link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
          sources.append(link);
        });
        article.append(sources);
        list.append(article);
      });
    }
    method.textContent = data.methodology || '';
  }
  buttons.forEach(button => button.addEventListener('click', function () {
    tab = this.dataset.radarTab; render();
  }));
  const cacheKey = 'pulsarium:insider-radar:v1';
  function accept(payload) {
    if (!payload || payload.version !== 1 || !Array.isArray(payload.clusters) || !Array.isArray(payload.large)) return false;
    data = payload;
    if (!data.clusters.length && data.large.length) tab = 'large';
    render();
    return true;
  }
  let hit;
  try { hit = JSON.parse(sessionStorage.getItem(cacheKey)); } catch (_) {}
  if (hit?.payload) accept(hit.payload);
  // This endpoint only reads the prepared public snapshot. Opening the
  // page, tabs and theme changes never invoke SEC or the collector.
  if (hit && Date.now() - hit.stored < 15 * 60 * 1000 && data) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  fetch(endpoint, { headers: { apikey: publicKey }, signal: controller.signal })
    .then(response => { if (!response.ok) throw new Error('Radar read failed'); return response.json(); })
    .then(rows => {
      if (!accept(rows[0]?.payload)) {
        state.hidden = false;
        state.textContent = 'The first SEC history update is in progress.';
        return;
      }
      try { sessionStorage.setItem(cacheKey, JSON.stringify({ stored: Date.now(), payload: data })); } catch (_) {}
    })
    .catch(() => {
      if (!data) state.textContent = 'SEC disclosures are temporarily unavailable.';
    })
    .finally(() => clearTimeout(timeout));
})();
