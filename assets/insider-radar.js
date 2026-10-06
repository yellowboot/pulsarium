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
  const rulesSummary = root.querySelector('[data-radar-rules-summary]');
  const buttons = [...root.querySelectorAll('[data-radar-tab]')];
  const modeButtons = [...root.querySelectorAll('[data-radar-mode]')];
  const tabs = { purchases: 'clusters', sales: 'large' };
  let data, mode = 'purchases';
  // A recent headline guarantees that the existing site builder writes a
  // company page. SEC also covers companies that the news site has not met.
  const news = typeof NEWS_DATA !== 'undefined' ? NEWS_DATA : null;
  const companyPages = new Set((news?.items || []).flatMap(item => item.tickers || []));
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
    const selling = mode === 'sales';
    const tab = tabs[mode];
    modeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.radarMode === mode)));
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.radarTab === tab)));
    buttons.forEach(button => {
      button.textContent = button.dataset.radarTab === 'clusters' ?
        (selling ? 'Seller groups' : 'Buyer groups') : (selling ? 'Large sales' : 'Large buys');
    });
    rulesSummary.textContent = (selling ? 'Sales' : 'Purchases') +
      ' total at least $100,000 per company. Reported transaction values. Updated twice daily.';
    list.replaceChildren();
    if (!data) return;
    const view = selling ? data.sales : data;
    const coverage = view?.coverage || {};
    const through = data.through ? ' · Disclosures through ' + date(data.through) : '';
    meta.textContent = 'Last ' + data.window_days + ' days' + through +
      (selling && view?.history_from ? ' · Sales collected since ' + date(view.history_from) : '');
    state.hidden = false;
    if (!coverage.complete) {
      state.textContent = 'History is updating · ' + Number(coverage.processed || 0).toLocaleString('en-US') +
        ' of ' + Number(coverage.discovered || 0).toLocaleString('en-US') + ' filings processed.';
    }
    const cards = Array.isArray(view?.[tab]) ? view[tab].slice(0, 5) : [];
    if (!cards.length) {
      if (selling && !coverage.complete && !coverage.discovered) {
        state.textContent = 'Sales collection ' + (view?.history_from ? 'started ' + date(view.history_from) : 'is starting') +
          '. Results will appear after the next completed SEC disclosure update.';
      } else {
        state.textContent = coverage.complete ? (tab === 'clusters' ?
          'No qualifying groups of ' + (selling ? 'sellers' : 'buyers') + ' in this window.' :
          'No qualifying ' + (selling ? 'sales' : 'purchases') + ' above $100,000 in this window.') :
          state.textContent + ' Results appear as filings are processed.';
      }
    } else {
      if (coverage.complete) state.hidden = true;
      cards.forEach(card => {
        const article = element('article', 'insider-radar-card');
        const head = element('div', 'insider-radar-card-head');
        const ticker = element(companyPages.has(card.symbol) ? 'a' : 'span', 'insider-radar-symbol', card.symbol);
        if (ticker.tagName === 'A') ticker.href = '/news/' + String(card.symbol).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '/';
        head.append(ticker, element('span', 'insider-radar-value', money.format(Number(card.amount) || 0)));
        article.append(head, element('p', 'insider-radar-company', card.name));
        const people = Array.isArray(card.people) ? card.people : [];
        const personTitle = people[0]?.title;
        const count = selling ? card.sellers : card.buyers;
        const title = count > 1 ? count + ' officers / directors' :
          (personTitle && !/^see remarks$/i.test(personTitle) ? personTitle : 'Officer / director');
        article.append(element('p', 'insider-radar-detail', title + (selling ? ' · direct sales' : ' · direct purchases')));
        if (selling) {
          const names = people.slice(0, 2).map(person => person.name).filter(Boolean).join(' · ');
          if (names) article.append(element('p', 'insider-radar-detail', names + (people.length > 2 ? ' · +' + (people.length - 2) : '')));
          if (Number(card.planned_amount) > 0) {
            article.append(element('p', 'insider-radar-detail', '10b5-1 indicated' +
              (Number(card.planned_amount) < Number(card.amount) - 0.01 ? ' · ' + money.format(Number(card.planned_amount)) + ' of reported sales' : '')));
          }
          const reduction = card.holding_reduction;
          if (reduction && Number.isFinite(reduction.percent)) {
            article.append(element('p', 'insider-radar-detail', 'One filing: ' + reduction.percent +
              '% of reported direct ' + reduction.security + ' holding · ' + reduction.person));
          }
        }
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
    method.textContent = view?.methodology || (selling ? 'Sales are collected from new SEC disclosures.' : '');
  }
  buttons.forEach(button => button.addEventListener('click', function () {
    tabs[mode] = this.dataset.radarTab; render();
  }));
  modeButtons.forEach(button => button.addEventListener('click', function () {
    mode = this.dataset.radarMode; render();
  }));
  const cacheKey = 'pulsarium:insider-radar:v2';
  function accept(payload) {
    if (!payload || payload.version !== 1 || !Array.isArray(payload.clusters) || !Array.isArray(payload.large)) return false;
    data = payload;
    if (!data.clusters.length && data.large.length) tabs.purchases = 'large';
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
