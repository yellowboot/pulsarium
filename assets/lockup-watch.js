// "Lock-up watch" on the news page, in the style of the IPO watch panel it replaces: the next three IPO lock-up
// releases from /assets/lockups.json (one per company), each with days and hours to the market open on its first free
// day and the share of the company it frees. It takes the IPO watch panel's place from data-lockup-from on (that panel
// hides itself at the same moment). A step's date is the first day its shares may be sold.
(() => {
  const root = document.querySelector('[data-lockup-watch]');
  if (!root) return;
  const ts = (d) => Date.parse(d + 'T00:00:00Z');
  // 9:30 New York on a given day (EST or EDT)
  const openAt = (d) => {
    const guess = Date.parse(d + 'T14:30:00Z');
    const h = +new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hour12: false }).format(guess);
    return guess - (h - 9) * 3600e3;
  };
  const fmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const pad = (n) => String(n).padStart(2, '0');
  const list = root.querySelector('[data-luw-list]');
  if (!list) return;

  const from = Date.parse(root.dataset.lockupFrom || '') || 0;
  const start = () => fetch(root.dataset.src).then((r) => (r.ok ? r.json() : Promise.reject(r.status))).then(run).catch(() => {});
  if (Date.now() >= from) start();
  else { const wait = setInterval(() => { if (Date.now() >= from) { clearInterval(wait); start(); } }, 30e3); }

  function run(data) {
    const all = data.companies.flatMap((c) => c.steps.filter((s) => s.date).map((s) => ({ c, s, at: openAt(s.date) })))
      .sort((a, b) => a.at - b.at || (b.s.pct ?? 0) - (a.s.pct ?? 0));
    let shown = '';
    const draw = () => {
      const now = Date.now(), seen = new Set();
      const next = all.filter((r) => r.at > now && !seen.has(r.c.ticker) && seen.add(r.c.ticker)).slice(0, 3);
      if (!next.length) { root.hidden = true; return false; }
      const key = next.map((r) => r.c.ticker + r.s.date).join();
      if (key !== shown) {
        shown = key;
        list.innerHTML = next.map(({ c, s }) => `<a class="luw-col" href="/lock-up-calendar/#${esc(c.ticker.toLowerCase())}">
          <span class="luw-co" title="${esc(c.name)}"><i>${esc(c.ticker)}</i><span>${esc(c.name)}</span></span>
          <span class="ipo-clock"><span class="ipo-unit"><b data-d>--</b><i>days</i></span><span class="ipo-unit"><b data-h>--</b><i>hrs</i></span></span>
          <span class="luw-meta">${fmt.format(ts(s.date))}${s.pct != null ? ` · <b class="luw-pct">${s.pct}%</b> of shares` : ''}</span></a>`).join('');
      }
      [...list.children].forEach((col, i) => {
        const left = Math.max(0, next[i].at - now);
        col.querySelector('[data-d]').textContent = pad(Math.floor(left / 864e5));
        col.querySelector('[data-h]').textContent = pad(Math.floor((left % 864e5) / 3600e3));
      });
      return true;
    };
    if (!draw()) return;
    root.hidden = false;
    // take the IPO watch panel's place in the layout
    const grid = root.closest('.hero-grid');
    const ipo = grid?.querySelector('#ipo-watch');
    // the same height as the IPO watch panel at this width: measure it unseen, then keep it hidden
    const match = () => {
      if (!ipo) return;
      ipo.style.visibility = 'hidden'; ipo.hidden = false;
      root.style.minHeight = '';
      const h = ipo.offsetHeight;
      ipo.hidden = true; ipo.style.visibility = '';
      if (h > root.offsetHeight) root.style.minHeight = h + 'px';
    };
    if (grid) {
      if (ipo) ipo.hidden = true;
      grid.classList.add('has-ipo');
      match();
      let resize = 0;
      window.addEventListener('resize', () => { clearTimeout(resize); resize = setTimeout(match, 150); });
    }
    setInterval(draw, 60e3);
  }
})();
