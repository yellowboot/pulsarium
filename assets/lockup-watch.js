// "Lock-up watch": the next three IPO lock-up releases from /assets/lockups.json (one per company), each with a ring
// for the share of its lock-up already served. On the news page it takes the IPO watch panel's place from
// data-lockup-from on (that panel hides itself at the same moment). Dates are New York calendar days; a step's date is the first day its shares may be sold.
(() => {
  const roots = [...document.querySelectorAll('[data-lockup-watch]')];
  if (!roots.length) return;
  const DAY = 864e5;
  const ts = (d) => Date.parse(d + 'T00:00:00Z');
  const nyToday = () => ts(new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()));
  const fmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  let data = null;
  const load = () => data || (data = fetch(roots[0].dataset.src).then((r) => (r.ok ? r.json() : Promise.reject(r.status))));

  roots.forEach((root) => {
    const from = Date.parse(root.dataset.lockupFrom || '') || 0;
    const start = () => load().then((d) => draw(root, d)).catch(() => {});
    if (Date.now() >= from) { start(); return; }
    const timer = setInterval(() => { if (Date.now() >= from) { clearInterval(timer); start(); } }, 30e3);
  });

  function draw(root, d) {
    const today = nyToday(), seen = new Set();
    const next = d.companies.flatMap((c) => c.steps.filter((s) => s.date && ts(s.date) >= today).map((s) => ({ c, s })))
      .sort((a, b) => ts(a.s.date) - ts(b.s.date))
      .filter(({ c }) => !seen.has(c.ticker) && seen.add(c.ticker))
      .slice(0, 3);
    const list = root.querySelector('[data-lockup-list]');
    if (!next.length || !list) return;
    const R = 22, L = 2 * Math.PI * R;
    list.innerHTML = next.map(({ c, s }, i) => {
      const days = Math.round((ts(s.date) - today) / DAY);
      const served = Math.min(1, Math.max(0, (today - ts(c.ipo_date)) / Math.max(DAY, ts(s.date) - ts(c.ipo_date))));
      const when = days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`;
      return `<a class="luw-item" href="/lock-up-calendar/#${esc(c.ticker.toLowerCase())}" style="--i:${i}">
        <span class="luw-ring"><svg viewBox="0 0 52 52" aria-hidden="true"><circle class="bg" cx="26" cy="26" r="${R}"/><circle class="fg" cx="26" cy="26" r="${R}"
          stroke-dasharray="${L.toFixed(1)}" stroke-dashoffset="${L.toFixed(1)}" data-off="${(L * (1 - served)).toFixed(1)}"/></svg><b>${days}</b></span>
        <span class="luw-txt"><span class="luw-tk">${esc(c.ticker)}</span><span class="luw-name">${esc(c.name)}</span>
          <span class="luw-when">${when} · ${fmt.format(ts(s.date))}</span>${s.pct != null ? `<span class="luw-pct">${s.pct}% of shares</span>` : ''}</span></a>`;
    }).join('');
    root.hidden = false;
    // the news page: take the IPO watch panel's place in the layout
    const grid = root.closest('.hero-grid');
    if (grid) {
      const ipo = grid.querySelector('#ipo-watch');
      if (ipo) ipo.hidden = true;
      grid.classList.add('has-ipo');
    }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      root.querySelectorAll('.luw-ring .fg').forEach((circle) => { circle.style.strokeDashoffset = circle.dataset.off; });
    }));
  }
})();
