// /lock-up-calendar/: draws the "next to unlock" cards and the padlock timeline from /assets/lockups.json and keeps
// the release statuses of the company cards (rendered by site_pages.py) current. Dates are New York calendar days.
// If the data can't be read the page keeps its static company cards.
(() => {
  const root = document.querySelector('.lu');
  if (!root) return;
  const DAY = 864e5;
  const ts = (d) => Date.parse(d + 'T00:00:00Z');
  const today = (() => {
    const p = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    return ts(p);
  })();
  const fmt = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  const fmtMonth = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' });
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  // a step's date is the first day its shares may be sold (lockups.json stores it that way)
  const freeDay = (d) => d;
  // 9:30 New York on a given day, as a timestamp (EST or EDT)
  const openAt = (d) => {
    const guess = Date.parse(d + 'T14:30:00Z');
    const h = +new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hour12: false }).format(guess);
    return guess - (h - 9) * 3600e3;
  };
  const daysTo = (d) => Math.round((ts(freeDay(d)) - today) / DAY);
  const qty = (n) => n >= 1e9 ? (n / 1e9).toFixed(2).replace(/\.?0+$/, '') + 'B'
    : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e8 ? 0 : 1).replace(/\.0$/, '') + 'M' : Math.round(n).toLocaleString('en-US');
  const status = (d) => {
    if (!d) return 'tbd';
    const f = ts(freeDay(d));
    return f < today ? 'done' : f - today <= 14 * DAY ? 'soon' : 'up';
  };
  const chipText = (d, st) => st === 'done' ? 'Released' : st === 'tbd' ? 'Date TBD' : (daysTo(d) === 0 ? 'Today' : 'In ' + daysTo(d) + ' d');
  // days and hours to the open on the first free day
  const left = (d) => {
    const ms = openAt(freeDay(d)) - Date.now();
    if (ms <= 0) return null;
    return { d: Math.floor(ms / DAY), h: Math.floor((ms % DAY) / 3600e3) };
  };
  const pad = (n) => String(n).padStart(2, '0');
  const size = (s) => s.pct != null ? (s.shares ? `${qty(s.shares)} shares · ${s.pct}% of the company` : `${s.pct}% of the company`)
    : s.shares ? `${qty(s.shares)} shares` : 'Size not disclosed';
  const LOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="lu-shackle" d="M7 11V8a5 5 0 0 1 10 0v3" fill="none" '
    + 'stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><rect x="4.5" y="11" width="15" height="10.5" rx="2.6" '
    + 'fill="currentColor"/><circle class="lu-hole" cx="12" cy="16.2" r="1.7"/></svg>';

  // 1. the static company cards: status chips for today's date
  root.querySelectorAll('.lu-steps li').forEach((li) => {
    const chip = li.querySelector('.lu-chip');
    if (!chip) return;
    const st = status(li.dataset.date);
    chip.className = 'lu-chip ' + st;
    chip.textContent = chipText(li.dataset.date, st);
  });

  // each company ring's countdown to its next release: days, and hours underneath, refreshed every minute
  root.classList.add('js');
  const counts = [...root.querySelectorAll('.lu-orb-c b[data-date]')];
  const tick = () => counts.forEach((el) => {
    const l = left(el.dataset.date);
    el.textContent = l ? l.d : 'FREE';
    const small = el.nextElementSibling;
    if (small) small.textContent = l ? (l.d === 1 ? 'day' : 'days') : 'released';
  });
  if (counts.length) { tick(); setInterval(tick, 60e3); }
  // each block animates when it comes into view
  const reveal = (el, then) => {
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      el.classList.add('is-in');
      then?.();
      io.disconnect();
    }, { threshold: 0.15 });
    io.observe(el);
  };
  reveal(root.querySelector('.lu-list'));

  fetch(root.dataset.src).then((r) => r.ok ? r.json() : Promise.reject(r.status)).then(draw).catch(() => {});

  function draw(data) {
    const cos = data.companies.filter((c) => c.ipo_date && c.steps?.length);
    nextCards(cos);
    timeline(cos);
    reveal(root.querySelector('.lu-next'), () => root.querySelectorAll('.lu-ring .fg').forEach((c) => { c.style.strokeDashoffset = c.dataset.off; }));
    reveal(root.querySelector('.lu-chart'));
  }

  // 2. the next releases, with a ring for the share of the lock-up already behind
  function nextCards(cos) {
    const box = root.querySelector('.lu-next');
    const up = cos.flatMap((c) => c.steps.filter((s) => s.date && left(s.date)).map((s) => ({ c, s })))
      .sort((a, b) => ts(a.s.date) - ts(b.s.date)).slice(0, 4);
    if (!box || !up.length) return;
    const R = 32, L = 2 * Math.PI * R;
    const ring = (s) => { const l = left(s.date); return l ? `${l.d}<small>${l.d === 1 ? 'DAY' : 'DAYS'}</small>` : 'FREE'; };
    box.innerHTML = up.map(({ c, s }, i) => {
      const done = Math.min(1, Math.max(0, (Date.now() - ts(c.ipo_date)) / (openAt(freeDay(s.date)) - ts(c.ipo_date))));
      return `<article class="panel lu-card${i === 0 ? ' is-first' : ''}" style="--i:${i}">
        <div class="lu-ring"><svg viewBox="0 0 74 74"><circle class="bg" cx="37" cy="37" r="${R}"/><circle class="fg" cx="37" cy="37" r="${R}"
          stroke-dasharray="${L.toFixed(1)}" stroke-dashoffset="${L.toFixed(1)}" data-off="${(L * (1 - done)).toFixed(1)}"/></svg>
          <b data-date="${s.date}">${ring(s)}</b></div>
        <div><span class="lu-tk">${esc(c.ticker)}</span><h3>${esc(c.name)}: ${esc(s.label)}</h3>
          <p>Shares free from <strong>${fmt.format(ts(freeDay(s.date)))}</strong> · ${s.pct != null ? (s.shares ? qty(s.shares) + ' shares · ' : '') + `<b class="lu-pct">${s.pct}%</b> of the company` : esc(size(s))}</p></div></article>`;
    }).join('');
    setInterval(() => box.querySelectorAll('.lu-ring b[data-date]').forEach((b) => { b.innerHTML = ring({ date: b.dataset.date }); }), 60e3);
    box.closest('[hidden]')?.removeAttribute('hidden');
  }

  // 3. the timeline: one lane per IPO, a padlock per release, sized by the share of the company it frees
  function timeline(cos) {
    const grid = root.querySelector('.lu-grid');
    if (!grid) return;
    // the near term gets the width; steps later than ~8 months out (and those without a date) sit in the last column
    const horizon = today + 240 * DAY;
    const isLater = (s) => !s.date || ts(s.date) > horizon;
    const dated = cos.flatMap((c) => [ts(c.ipo_date), ...c.steps.filter((s) => !isLater(s)).map((s) => ts(s.date))]);
    const start = Math.min(...dated) - 12 * DAY;
    const end = Math.max(...dated, today + 45 * DAY) + 18 * DAY;
    const anyTbd = cos.some((c) => c.steps.some(isLater));
    const span = anyTbd ? 91 : 100;
    const x = (t) => ((t - start) / (end - start)) * span;
    const tbdX = (k, n) => 91 + 7.5 * (k + 1) / (n + 1);
    const nextUp = (c) => Math.min(...c.steps.filter((s) => s.date && ts(freeDay(s.date)) >= today).map((s) => ts(s.date)), Infinity);
    const lastStep = (c) => Math.max(...c.steps.filter((s) => s.date).map((s) => ts(s.date)), ts(c.ipo_date));
    // largest company first: all its shares at the IPO price (Anthropic at the midpoint until it is priced)
    const value = (c) => (c.outstanding || 0) * (c.price || c.price_mid || 0);
    // companies whose lock-ups are all behind them go last
    const done = (c) => c.steps.every((s) => s.date && ts(s.date) < today);
    const order = [...cos].sort((a, b) => (done(a) - done(b)) || value(b) - value(a));

    const months = [];
    for (let d = new Date(start); d.getTime() <= end; d.setUTCMonth(d.getUTCMonth() + 1, 1)) {
      const m = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
      if (m >= start) months.push(m);
    }
    const gridlines = months.map((m) => `<span class="lu-gridline" style="left:${x(m).toFixed(2)}%"></span>`).join('');
    const nowLine = `<span class="lu-now" style="left:${x(today).toFixed(2)}%"></span>`;
    const tbdZone = anyTbd ? `<span class="lu-tbdzone" style="left:91%"></span>` : '';
    const narrow = grid.clientWidth < 720;
    // keep month labels clear of the TODAY pill and the Later column: ~40px either way, in % of the track
    const gap = 4000 / Math.max(200, grid.clientWidth - (narrow ? 90 : 206));
    const every = months.length > 14 ? (narrow ? 3 : 2) : (narrow ? 2 : 1);
    const shown = months.filter((m, i) => (new Date(m).getUTCMonth() === 0 || i % every === 0) && Math.abs(x(m) - x(today)) > gap && (!anyTbd || x(m) < 91 - gap));
    const axis = `<div class="lu-axis"><div></div><div class="lu-track">${shown.map((m) => {
      const jan = new Date(m).getUTCMonth() === 0;
      return `<span class="lu-month${jan ? ' is-year' : ''}" style="left:${x(m).toFixed(2)}%">${fmtMonth.format(m)}${jan ? " '" + String(new Date(m).getUTCFullYear()).slice(2) : ''}</span>`;
    }).join('')}<span class="lu-nowlabel" style="left:${x(today).toFixed(2)}%">TODAY</span>${anyTbd ? '<span class="lu-tbdlabel" style="left:91%">Later</span>' : ''}</div></div>`;

    const lanes = order.map((c, i) => {
      const steps = c.steps.map((s, k) => ({ s, k }));
      const tbd = steps.filter(({ s }) => isLater(s));
      const firstUp = nextUp(c);
      const allDone = steps.every(({ s }) => s.date && ts(s.date) < today);
      const barEnd = tbd.length ? 91 : x(lastStep(c));
      const bar = `<span class="lu-bar${allDone ? ' is-free' : ''}" style="left:${x(ts(c.ipo_date)).toFixed(2)}%;width:${Math.max(0, barEnd - x(ts(c.ipo_date))).toFixed(2)}%"></span>`;
      const locks = steps.map(({ s, k }) => {
        const pos = !isLater(s) ? x(ts(s.date)) : tbdX(tbd.findIndex((e) => e.k === k), tbd.length);
        const st = status(s.date);
        const cls = st === 'done' ? 'is-passed' : st === 'tbd' ? 'is-tbd' : (st === 'soon' ? 'is-soon' : '') + (ts(s.date) === firstUp ? ' is-next' : '');
        const px = s.pct != null ? 15 + 21 * Math.sqrt(Math.min(100, s.pct) / 100) : 18;
        const delay = 150 + pos * 9;
        return `<button type="button" class="lu-lock ${cls}" style="left:${pos.toFixed(2)}%;--s:${px.toFixed(1)}px;--d:${delay.toFixed(0)}ms"
          data-co="${cos.indexOf(c)}" data-k="${k}" aria-label="${esc(c.name)}: ${esc(s.label)}, ${s.date ? fmt.format(ts(s.date)) : 'date to be set'}">${LOCK}</button>`;
      }).join('');
      return `<div class="lu-lane" style="--i:${i}"><div class="lu-name"><span class="lu-tk">${esc(c.ticker)}</span><span>${esc(c.name)}</span></div>
        <div class="lu-track">${gridlines}${tbdZone}${bar}<span class="lu-ipo" style="left:${x(ts(c.ipo_date)).toFixed(2)}%" title="IPO ${fmt.format(ts(c.ipo_date))}"></span>${nowLine}${locks}</div></div>`;
    }).join('');
    grid.innerHTML = axis + lanes;
    grid.closest('[hidden]')?.removeAttribute('hidden');

    // tooltip: hover, focus or tap on a padlock
    const chart = root.querySelector('.lu-chart');
    const tip = chart.querySelector('.lu-tip');
    const show = (btn) => {
      const c = cos[+btn.dataset.co], s = c.steps[+btn.dataset.k];
      const st = status(s.date);
      tip.innerHTML = `<b>${esc(c.name)} · ${esc(s.label)}</b><em>${s.date ? 'Free from ' + fmt.format(ts(freeDay(s.date))) : 'Date to be set'} · ${esc(chipText(s.date, st))}</em>
        <p>${esc(size(s))}${s.trigger ? '. ' + esc(s.trigger) : ''}</p>`;
      const cr = chart.getBoundingClientRect(), br = btn.getBoundingClientRect();
      const left = Math.min(Math.max(8, br.left - cr.left + br.width / 2 - 130), cr.width - 268);
      const below = br.top - cr.top < 150;
      tip.style.left = left + 'px';
      tip.style.top = (below ? br.bottom - cr.top + 10 : br.top - cr.top - tip.offsetHeight - 10) + 'px';
      tip.classList.add('is-on');
    };
    const hide = () => tip.classList.remove('is-on');
    grid.addEventListener('pointerover', (e) => { const b = e.target.closest('.lu-lock'); if (b) show(b); });
    grid.addEventListener('pointerout', (e) => { if (e.target.closest('.lu-lock')) hide(); });
    grid.addEventListener('focusin', (e) => { const b = e.target.closest('.lu-lock'); if (b) show(b); });
    grid.addEventListener('focusout', hide);
    grid.addEventListener('click', (e) => { const b = e.target.closest('.lu-lock'); if (b) show(b); });
    root.querySelector('.lu-scroll')?.addEventListener('scroll', hide, { passive: true });
  }
})();
