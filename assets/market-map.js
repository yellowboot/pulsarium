// Pulsarium market map.
// 1. On wide screens the news page's three top panels (Quick analysis, Top mentions, Main indices) fold to one
//    compact row; a click opens the full panel under its cell. Phones keep the folding headings (panel-fold.js).
// 2. The earnings-season ring: one line per company (assets/season-q3-2026.js), drawn on a canvas in the colours of
//    the current theme. Used small on the news page ([data-season="mini"]) and large on /earnings-season/ ("full").
(() => {
  'use strict';
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const wide = window.matchMedia('(min-width: 701px)');
  const still = window.matchMedia('(prefers-reduced-motion: reduce)');
  const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------- 1. compact top row ----------
  function compactRow() {
    const grid = document.querySelector('.dash > .hero-grid');
    if (!grid) return;
    const summaries = {
      '.panel-analytics': (sum) => {
        const bold = $$('#analytics-text b'), bar = document.getElementById('sentiment-bar');
        if (bold.length < 5 || !bar) return '';
        const mood = bold[4].textContent.trim();
        sum.classList.add('mm-mood-row');
        return `<span class="mm-mood">${esc(mood.charAt(0).toUpperCase() + mood.slice(1))}</span>` +
          `<span class="mm-counts" title="Positive, neutral, negative"><i style="background:var(--pos)"></i>${esc(bold[1].textContent)}` +
          `<i style="background:var(--neu)"></i>${esc(bold[3].textContent)}<i style="background:var(--neg)"></i>${esc(bold[2].textContent)}</span>` +
          `<span class="sentiment-bar">${bar.innerHTML}</span>`;
      },
      '.panel-mentions': () => $$('#top-tickers-list .ticker-row').slice(0, 10).map((row) => {
        const name = row.querySelector('.name'), num = row.querySelector('.num');
        return name ? `<a class="mm-chip" href="${esc(name.getAttribute('href') || '#')}">${esc(name.textContent)} <b>${esc(num ? num.textContent : '')}</b></a>` : '';
      }).join(''),
      '.quotes-panel': () => $$('#quotes-list .quote-row').slice(0, 8).map((row) => {
        const name = row.querySelector('.quote-name'), change = row.querySelector('.quote-change');
        if (!name || !change) return '';
        const kind = change.classList.contains('quote-up') ? 'up' : change.classList.contains('quote-down') ? 'down'
          : change.classList.contains('quote-vix-hot') ? 'hot' : 'cool';
        return `<span class="mm-chip">${esc(name.textContent)} <b class="${kind}">${esc(change.textContent.trim())}</b></span>`;
      }).join(''),
    };
    const cells = [];
    for (const [selector, build] of Object.entries(summaries)) {
      const panel = grid.querySelector(':scope > ' + selector), heading = panel && panel.querySelector(':scope > h2');
      if (!heading) continue;
      // everything under the heading moves into the pop-over; the page's own code finds these nodes by id
      const pop = document.createElement('div');
      pop.className = 'mm-pop';
      [...panel.children].filter((el) => el !== heading && !el.classList.contains('panel-edge')).forEach((el) => pop.append(el));
      const sum = document.createElement('div');
      sum.className = 'mm-sum';
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'mm-toggle';
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', `Show or hide ${heading.textContent.trim()}`);
      // the title in its own box, so a long one is cut with an ellipsis instead of wrapping
      const title = document.createElement('span');
      title.className = 'mm-title';
      const words = [...heading.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
      if (words.length) { heading.insertBefore(title, words[0]); words.forEach((node) => title.append(node)); }
      heading.append(toggle);
      panel.append(sum, pop);
      // the cell no longer clips its content (the pop-over hangs below it), so the light that runs along
      // the top edge gets a clipping box of its own
      const edge = panel.querySelector(':scope > .panel-edge');
      if (edge) { const clip = document.createElement('div'); clip.className = 'mm-clip'; clip.setAttribute('aria-hidden', 'true'); panel.insertBefore(clip, edge); clip.append(edge); }
      panel.classList.add('mm-compact');
      const fill = () => { sum.innerHTML = build(sum); };
      let queued = false;
      new MutationObserver(() => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => { queued = false; fill(); });
      }).observe(pop, { childList: true, subtree: true, characterData: true });
      fill();
      const open = (isOpen) => { panel.classList.toggle('is-open', isOpen); toggle.setAttribute('aria-expanded', String(isOpen)); };
      panel.addEventListener('click', (event) => {
        if (!wide.matches || event.target.closest('.mm-pop, a')) return;
        const willOpen = !panel.classList.contains('is-open');
        cells.forEach((cell) => cell.open(false));
        open(willOpen);
      });
      cells.push({ panel, open });
    }
    if (!cells.length) return;
    grid.classList.add('mm-row');
    document.addEventListener('click', (event) => { cells.forEach((cell) => { if (!cell.panel.contains(event.target)) cell.open(false); }); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') cells.forEach((cell) => cell.open(false)); });
  }

  // ---------- 2. the earnings-season ring ----------
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, x) => a + (b - a) * x;
  const easeOut = (x) => 1 - Math.pow(1 - x, 3);
  const easeInOut = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const dayLabel = (date) => `${MONTHS[Number(date.slice(5, 7)) - 1]} ${Number(date.slice(8, 10))}`;
  const signed = (value) => `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(1)}%`;
  const SLOT = ['before the open', 'after the close', 'during trading'];
  const SECTOR_SHORT = { 'Consumer Discretionary': 'Consumer disc.', Technology: 'Tech' };
  const R0 = 98, R1 = 206, DIST = 1300;

  function seasonRing(root) {
    const data = window.PULSARIUM_SEASON, canvas = root.querySelector('canvas'), info = root.querySelector('[data-season-info]');
    const stage = canvas && canvas.parentElement;
    if (!data || !canvas || !canvas.getContext || !info) return;
    const full = root.dataset.season === 'full', ctx = canvas.getContext('2d');
    const heightScale = full ? 2.7 : 1.75, HMAX = 36 * heightScale, ELEV = (full ? 29 : 17) * Math.PI / 180, se = Math.sin(ELEV), ce = Math.cos(ELEV);
    let seed = 7;
    const rnd = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let x = Math.imul(seed ^ seed >>> 15, 1 | seed); x = x + Math.imul(x ^ x >>> 7, 61 | x) ^ x; return ((x ^ x >>> 14) >>> 0) / 4294967296; };

    const lines = data.rows.map(([s, n, d, slot, cap, move, beat, sec]) => ({ s, n, d, slot, cap, move, beat, sec, P: [], w: 1, lag: rnd() * 0.45,
      h: Math.sign(move) * Math.min(Math.abs(move), 36) * heightScale, wd: cap >= 500 ? 2.6 : cap >= 100 ? 1.9 : cap >= 30 ? 1.4 : 1.05 }));
    const group = (list, key) => { const map = new Map(); for (const item of list) { const k = key(item); if (!map.has(k)) map.set(k, []); map.get(k).push(item); } return map; };
    const wedges = (groups, span) => { const w = groups.map((g) => Math.sqrt(g.items.length + 4)), total = w.reduce((a, b) => a + b, 0); let acc = 0;
      groups.forEach((g, i) => { g.a0 = acc / total * span; g.a1 = (acc + w[i]) / total * span; g.mid = (g.a0 + g.a1) / 2; acc += w[i]; }); };
    const polarFill = (groups, j) => { for (const g of groups) { const pad = (g.a1 - g.a0) * 0.1, width = g.a1 - g.a0 - 2 * pad;
      const m = Math.max(1, Math.round(width * 150 / 6.5)), rows = Math.ceil(g.items.length / m), dr = Math.min(7.5, (R1 - R0) / Math.max(1, rows));
      g.items.forEach((l, i) => { const row = Math.floor(i / m), f = ((i % m) + 0.5 + (row % 2 ? 0.25 : -0.25)) / m, th = g.a0 + pad + f * width, r = R1 - row * dr;
        l.P[j] = [r * Math.cos(th), r * Math.sin(th)]; if (j === 0) l.th = th; }); } };
    // layout 0: a ring, a wedge per trading day, the largest companies on the rim
    const SPAN = 2 * Math.PI - 0.5, days = Object.keys(data.perDay).sort(), byDay = group(lines, (l) => l.d);
    const dayGroups = days.map((d) => ({ key: d, items: (byDay.get(d) || []).slice().sort((a, b) => b.cap - a.cap) }));
    wedges(dayGroups, SPAN); polarFill(dayGroups, 0);
    const BUILD = 2.4;
    lines.forEach((l) => { if (l.th == null) { l.th = 0; l.P[0] = [R1, 0]; } l.tb = 0.2 + l.th / SPAN * BUILD; });
    // layout 1: four islands, result x reaction, the biggest moves in the middle of each
    const islands = [
      { test: (l) => l.beat === 1 && l.move > 0, c: [-92, -66], name: 'Beat + rose', up: true, lift: 104 },
      { test: (l) => l.beat === 1 && l.move < 0, c: [-92, 84], name: 'Beat + fell', up: false, lift: 26 },
      { test: (l) => l.beat === -1 && l.move > 0, c: [104, -52], name: 'Missed + rose', up: true, lift: 58 },
      { test: (l) => l.beat === -1 && l.move < 0, c: [104, 62], name: 'Missed + fell', up: false, lift: 26 },
      { test: () => true, c: [22, 8], name: '', lift: 0 }];
    { const taken = new Set(); for (const g of islands) { g.items = lines.filter((l) => !taken.has(l) && g.test(l)).sort((a, b) => Math.abs(b.move) - Math.abs(a.move));
      g.items.forEach((l, i) => { taken.add(l); const r = 4.3 * Math.sqrt(i + 0.5), a = i * 2.399963; l.P[1] = [g.c[0] + r * Math.cos(a), g.c[1] + r * Math.sin(a)]; });
      g.R = 4.3 * Math.sqrt(g.items.length) + 7; g.lift *= heightScale / 2.7; } }
    // layout 2: a ring again, a wedge per sector (best first), the biggest gainers on the rim
    const bySector = group(lines, (l) => l.sec);
    const sectorGroups = [...bySector.entries()].map(([sec, items]) => ({ key: sec, name: data.sectors[sec], items: items.sort((a, b) => b.move - a.move),
      up: items.filter((l) => l.move > 0).length / items.length * 100 })).sort((a, b) => (a.name === 'Other') - (b.name === 'Other') || b.up - a.up);
    wedges(sectorGroups, 2 * Math.PI - 0.3); polarFill(sectorGroups, 2);
    // the smaller companies of the same days: dust inside the date ring
    const dust = [];
    for (const g of dayGroups) { const n = Math.max(0, data.perDay[g.key] - g.items.length);
      for (let i = 0; i < n; i++) { const th = g.a0 + rnd() * (g.a1 - g.a0), r = 24 + Math.sqrt(rnd()) * 66; dust.push({ x: r * Math.cos(th), z: r * Math.sin(th), s: rnd(), tb: 0.2 + th / SPAN * BUILD }); } }
    const weeks = dayGroups.filter((g) => new Date(g.key + 'T12:00:00Z').getUTCDay() === 1);
    const six = new Set(data.six || []);
    const FILTERS = { all: () => 1, beatfell: (l) => (l.beat === 1 && l.move < 0 ? 1 : 0.1), move10: (l) => (Math.abs(l.move) >= 10 ? 1 : 0.1), giants: (l) => (six.has(l.s) ? 1 : 0.14) };

    // ----- state -----
    let W = 0, H = 0, zoom = 1, ox = 0, oy = 0, yaw = Math.PI / 2 + 0.35, sy = 0, cy = 1;
    let layout = { a: 0, b: 0, tm: -99 }, filter = 'all', hot = null, pointerIn = false, visible = false, raf = 0, start = 0, last = 0, drawn = [];
    let theme = {};
    const readTheme = () => { const cs = getComputedStyle(root), v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
      theme = { pos: v('--pos', '#3dffa2'), neg: v('--neg', '#ff6b8e'), acc: v('--gold-1', v('--accent', '#00f0ff')), hi: v('--text-hi', '#e6f1ff'),
        mid: v('--text-mid', '#a9bddf'), low: v('--text-low', '#7489b3'), bg: v('--bg-1', '#070a16'), mono: v('--font-mono', "'JetBrains Mono', monospace") }; };
    const fit = () => { const box = stage.getBoundingClientRect(); if (box.width < 40 || box.height < 40) return false;
      W = box.width; H = box.height; const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const top = 240 * se * 0.9 + HMAX * ce + 14, bottom = 240 * se * 1.12 + HMAX * ce * 1.12 + 6;
      zoom = Math.min(W / 530, H / (top + bottom)); ox = W / 2; oy = (H - (top + bottom) * zoom) / 2 + top * zoom; return true; };
    const project = (x, y, z) => { const x1 = x * cy - z * sy, z1 = x * sy + z * cy, depth = y * se + z1 * ce, s = zoom * DIST / (DIST - depth);
      return { x: ox + x1 * s, y: oy - (y * ce - z1 * se) * s, s, depth }; };
    const near = (target, ref) => target + 2 * Math.PI * Math.round((ref - target) / (2 * Math.PI));

    function draw(t, dt) {
      const motion = !still.matches;
      // the ring turns slowly; the islands only sway, so "beat" stays on the left; a pointer over the picture holds it
      if (layout.b === 1) yaw += (near(0.2 + (motion ? 0.16 * Math.sin(t * 0.3) : 0), yaw) - yaw) * Math.min(1, dt * 2.5);
      else if (motion && !pointerIn) yaw -= 0.07 * dt;
      sy = Math.sin(yaw); cy = Math.cos(yaw);
      const k = motion ? clamp((t - layout.tm - 0.15) / 1.1) : 1, floor = (j) => (layout.b === j ? k : 0) + (layout.a === j && layout.a !== layout.b ? 1 - k : 0);
      const w0 = floor(0), w1 = floor(1), w2 = floor(2), built = motion ? SPAN * clamp((t - 0.2) / BUILD) : SPAN;
      ctx.clearRect(0, 0, W, H); ctx.lineCap = 'round';
      const P = (x, z, y = 0) => project(x, y, z);
      const depthA = (q) => 0.55 + 0.45 * clamp((q.depth + 230) / 460);
      const stroke = (color, alpha, width) => { ctx.strokeStyle = color; ctx.globalAlpha = clamp(alpha); ctx.lineWidth = width; ctx.stroke(); };
      const circle = (cx, cz, r, color, alpha, width = 0.8) => { ctx.beginPath(); for (let i = 0; i <= 72; i++) { const a = i / 72 * 2 * Math.PI, q = P(cx + r * Math.cos(a), cz + r * Math.sin(a)); if (i) ctx.lineTo(q.x, q.y); else ctx.moveTo(q.x, q.y); } stroke(color, alpha, width); };
      const spoke = (a, color, alpha) => { const p = P((R0 - 6) * Math.cos(a), (R0 - 6) * Math.sin(a)), q = P((R1 + 7) * Math.cos(a), (R1 + 7) * Math.sin(a)); ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); stroke(color, alpha, 0.6); };
      const text = (label, q, size, color, alpha, align = 'center') => { ctx.font = `600 ${size}px ${theme.mono}`; ctx.textAlign = align; ctx.textBaseline = 'middle'; ctx.fillStyle = color; ctx.globalAlpha = clamp(alpha); ctx.fillText(label, q.x, q.y); };
      const plate = (x, y, w, h, alpha, edge) => { ctx.beginPath(); ctx.roundRect(x - w / 2, y - h / 2, w, h, 6); ctx.fillStyle = theme.bg; ctx.globalAlpha = clamp(alpha * 0.82); ctx.fill(); if (edge) stroke(edge, alpha * 0.55, 0.8); };

      // floors
      if (w0 > 0) {
        circle(0, 0, 20, theme.acc, 0.4 * w0); circle(0, 0, R0 - 6, theme.acc, 0.24 * w0); circle(0, 0, R1 + 7, theme.acc, 0.4 * w0, 1.1); circle(0, 0, R1 + 30, theme.acc, 0.12 * w0);
        for (const g of dayGroups) if (g.a0 <= built) spoke(g.a0, theme.acc, 0.14 * w0);
        ctx.fillStyle = theme.acc;
        for (const p of dust) { if (motion && t < p.tb) continue; const q = P(p.x, p.z); ctx.globalAlpha = clamp((0.2 + 0.4 * p.s) * depthA(q) * w0 * (motion ? clamp((t - p.tb) / 0.3) : 1)); ctx.fillRect(q.x - 0.6, q.y - 0.6, 1.2 * q.s, 1.2 * q.s); }
      }
      if (w1 > 0) for (const g of islands) if (g.name) circle(g.c[0], g.c[1], g.R, g.up ? theme.pos : theme.neg, 0.4 * w1, 1);
      if (w2 > 0) { circle(0, 0, R0 - 6, theme.acc, 0.24 * w2); circle(0, 0, R1 + 7, theme.acc, 0.4 * w2, 1.1); for (const g of sectorGroups) spoke(g.a0, theme.acc, 0.26 * w2); }
      // the sweep that builds the ring
      const sweep = motion ? clamp((t - 0.05) / 0.3) * (1 - clamp((t - 0.2 - BUILD) / 0.6)) : 0;
      if (sweep > 0) { const a = P(0, 0), b = P((R1 + 30) * Math.cos(built), (R1 + 30) * Math.sin(built)); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); stroke(theme.hi, 0.7 * sweep, 1.4); }

      // the lines, far to near
      drawn = [];
      for (const l of lines) {
        if (motion && t < l.tb) continue;
        const m = motion ? easeInOut(clamp(t - layout.tm - l.lag)) : 1, A = l.P[layout.a], B = l.P[layout.b], x = lerp(A[0], B[0], m), z = lerp(A[1], B[1], m);
        const grow = motion ? easeOut(clamp((t - l.tb) / 0.5)) : 1;
        l.w += (FILTERS[filter](l) - l.w) * Math.min(1, dt * 6);
        drawn.push({ l, a: project(x, 0, z), b: project(x, l.h * grow, z) });
      }
      drawn.sort((p, q) => p.a.depth - q.a.depth);
      for (const { l, a, b } of drawn) {
        const color = l.move >= 0 ? theme.pos : theme.neg, alpha = depthA(a) * l.w * (hot && hot !== l ? 0.55 : 1);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); stroke(color, alpha * 0.2, l.wd * a.s * 3.2);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); stroke(color, alpha, l.wd * a.s);
        if (Math.abs(l.move) >= 10 && l.w > 0.5) { ctx.beginPath(); ctx.arc(b.x, b.y, 1.6 * a.s, 0, 2 * Math.PI); ctx.fillStyle = theme.hi; ctx.globalAlpha = clamp(alpha); ctx.fill(); }
      }
      // labels
      if (w0 > 0) for (const g of weeks) { if (g.a0 > built) continue; const q = P((R1 + 22) * Math.cos(g.a0), (R1 + 22) * Math.sin(g.a0)); text(dayLabel(g.key).toUpperCase(), { x: clamp(q.x, 22, W - 22), y: q.y }, 9.5, theme.mid, depthA(q) * w0); }
      if (w1 > 0) for (const g of islands) if (g.name) { const color = g.up ? theme.pos : theme.neg;
        // on a narrow picture the four labels would cover the islands: they go to the corners instead
        // (beat on the left, missed on the right; rose at the back, fell at the front)
        const q = W < 440 ? { x: g.c[0] < 0 ? 52 : W - 52, y: g.c[1] < 0 ? 24 : H - 16 } : P(g.c[0], g.c[1], g.lift);
        plate(q.x, q.y - 4, 96, 36, w1, color); text(g.name.toUpperCase(), { x: q.x, y: q.y - 13 }, 9, theme.mid, w1); text(String(g.items.length), { x: q.x, y: q.y + 3 }, 17, color, w1); }
      if (w2 > 0) for (const g of sectorGroups) { const q = P((R1 + 26) * Math.cos(g.mid), (R1 + 26) * Math.sin(g.mid)), label = `${(SECTOR_SHORT[g.name] || g.name).toUpperCase()} ${Math.round(g.up)}%`;
        ctx.font = `600 9.5px ${theme.mono}`; const tw = ctx.measureText(label).width + 10, x = clamp(q.x, tw / 2 + 2, W - tw / 2 - 2);
        plate(x, q.y, tw, 16, w2 * 0.9); text(label, { x, y: q.y }, 9.5, g.up < 50 ? theme.neg : theme.mid, (0.75 + 0.25 * depthA(q)) * w2); }
      // the line under the pointer
      if (hot) { const hit = drawn.find((p) => p.l === hot); if (hit) { const color = hot.move >= 0 ? theme.pos : theme.neg;
        ctx.beginPath(); ctx.moveTo(hit.a.x, hit.a.y); ctx.lineTo(hit.b.x, hit.b.y); stroke(color, 1, Math.max(2.2, hot.wd * hit.a.s * 1.6));
        ctx.beginPath(); ctx.arc(hit.b.x, hit.b.y, 5, 0, 2 * Math.PI); stroke(theme.hi, 1, 1.4);
        const label = `${hot.s} ${signed(hot.move)}`; ctx.font = `600 11px ${theme.mono}`; const tw = ctx.measureText(label).width + 14;
        const lx = clamp(hit.b.x, tw / 2 + 2, W - tw / 2 - 2), ly = clamp(hit.b.y + (hot.move >= 0 ? -18 : 18), 12, H - 12);
        plate(lx, ly, tw, 20, 1.15, color); text(label, { x: lx, y: ly + 0.5 }, 11, theme.hi, 1); } }
      ctx.globalAlpha = 1;
    }

    function tick(now) {
      raf = 0;
      if (!visible) return;
      if (!start) { start = now; last = now; }
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      draw((now - start) / 1000, dt);
      raf = requestAnimationFrame(tick);
    }
    const run = () => { if (visible && !raf && W) raf = requestAnimationFrame(tick); };

    // ----- the side card -----
    const stats = data.stats, beatShare = Math.round(stats.beat / stats.withEps * 100);
    const touch = window.matchMedia('(hover: none)').matches;
    function card() {
      if (!hot) {
        info.innerHTML = `<p class="mm-k">${stats.big} companies worth $10B+${data.to ? ` · ${dayLabel(data.from)} – ${dayLabel(data.to)}` : ''}</p>` +
          `<p class="mm-big"><span class="up">${Math.round(stats.up)}% rose</span> · <span class="down">${Math.round(stats.down)}% fell</span></p>` +
          `<p class="mm-line">${beatShare}% beat profit forecasts. <b>${Math.round(stats.beatFell)}% of them fell anyway.</b></p>` +
          `<p class="mm-hint">${touch ? 'Tap' : 'Point at'} a line to see the company.</p>`;
        return;
      }
      const result = hot.beat === 1 ? 'Beat the profit forecast' : hot.beat === -1 ? 'Missed the profit forecast' : 'In line with the forecast';
      info.innerHTML = `<p class="mm-k">${esc(hot.s)} · ${dayLabel(hot.d)} · ${SLOT[hot.slot]}</p><p class="mm-name">${esc(hot.n)}</p>` +
        `<p class="mm-move ${hot.move >= 0 ? 'up' : 'down'}">${signed(hot.move)}</p><p class="mm-line">${result}. Move on the first trading day after the report.</p>`;
    }
    function pick(event, reach) {
      const box = canvas.getBoundingClientRect(), px = event.clientX - box.left, py = event.clientY - box.top;
      let best = null, bestD = reach;
      for (const { l, a, b } of drawn) {
        if (l.w < 0.5) continue;
        const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy || 1, u = clamp(((px - a.x) * dx + (py - a.y) * dy) / len);
        const d = Math.hypot(px - (a.x + u * dx), py - (a.y + u * dy));
        if (d < bestD) { bestD = d; best = l; }
      }
      if (best !== hot) { hot = best; card(); }
    }
    canvas.addEventListener('pointermove', (event) => { if (event.pointerType !== 'touch') { pointerIn = true; pick(event, 9); } });
    canvas.addEventListener('pointerleave', () => { pointerIn = false; if (hot) { hot = null; card(); } });
    canvas.addEventListener('pointerdown', (event) => { if (event.pointerType === 'touch') pick(event, 20); });

    // ----- buttons -----
    const mark = (attr, value) => $$(`[${attr}]`, root).forEach((button) => { const on = button.getAttribute(attr) === String(value); button.classList.toggle('is-on', on); button.setAttribute('aria-pressed', String(on)); });
    root.addEventListener('click', (event) => {
      const byLayout = event.target.closest('[data-season-layout]'), byFilter = event.target.closest('[data-season-filter]');
      if (byLayout) { const to = Number(byLayout.dataset.seasonLayout); if (to !== layout.b) { layout = { a: layout.b, b: to, tm: start ? (performance.now() - start) / 1000 : 0 }; mark('data-season-layout', to); } }
      if (byFilter) { filter = FILTERS[byFilter.dataset.seasonFilter] ? byFilter.dataset.seasonFilter : 'all'; mark('data-season-filter', filter); if (hot) { hot = null; card(); } }
    });
    mark('data-season-layout', 0); mark('data-season-filter', 'all');

    // ----- life cycle -----
    readTheme(); card();
    new MutationObserver(readTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-color-theme', 'class', 'style'] });
    new ResizeObserver(() => { if (fit()) run(); }).observe(stage);
    new IntersectionObserver((entries) => { visible = entries.some((entry) => entry.isIntersecting); if (visible) { last = performance.now(); fit(); run(); } }, { rootMargin: '80px' }).observe(stage);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(readTheme);
  }

  // ---------- 3. insider trades of the week: floating bubbles ----------
  // One bubble per company whose officers or directors bought (green) or sold (red) its shares, from the
  // public snapshot the Insider activity panel already reads (its session cache is shared, so normally no
  // second request). The bubbles drift, push each other apart, move away from the pointer and can be dragged.
  // A sale made under a 10b5-1 trading plan is an empty dashed ring: only the part of a bubble that was
  // NOT planned is filled.
  const RADAR = 'https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/sec_insider_radar?id=eq.global&select=payload';
  const RADAR_KEY = 'sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF', RADAR_CACHE = 'pulsarium:insider-radar:v2';
  const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });
  const shortDay = (value) => (/^\d{4}-\d{2}-\d{2}$/.test(value || '') ? dayLabel(value) : '—');
  function radarPayload() {
    const cached = () => { try { const hit = JSON.parse(sessionStorage.getItem(RADAR_CACHE)); return hit && hit.payload && hit.payload.version === 1 ? hit.payload : null; } catch (_) { return null; } };
    return new Promise((resolve) => {
      let tries = 0;
      const wait = () => {
        const hit = cached();
        if (hit) return resolve(hit);
        if (++tries < 8) return setTimeout(wait, 350);          // the panel is reading the same snapshot right now
        fetch(RADAR, { headers: { apikey: RADAR_KEY } }).then((response) => (response.ok ? response.json() : []))
          .then((rows) => resolve(rows[0] && rows[0].payload && rows[0].payload.version === 1 ? rows[0].payload : null)).catch(() => resolve(null));
      };
      wait();
    });
  }

  function insiderMap(root) {
    const canvas = root.querySelector('canvas'), info = root.querySelector('[data-insider-info]'), stage = canvas && canvas.parentElement;
    if (!canvas || !canvas.getContext || !info) return;
    const ctx = canvas.getContext('2d');
    root.hidden = false;
    info.innerHTML = '<p class="mm-hint">Loading SEC disclosures…</p>';
    radarPayload().then((payload) => {
      // the wide list (every company of the week from $100K) once the export carries it, the top-five lists until then
      const side = (wide, lists, kind) => {
        const bySymbol = new Map();
        for (const card of (Array.isArray(wide) && wide.length ? wide : lists.flat())) {
          if (!card || !card.symbol || !(Number(card.amount) > 0)) continue;
          const old = bySymbol.get(card.symbol);
          if (!old || Number(card.amount) > Number(old.amount)) bySymbol.set(card.symbol, card);
        }
        return [...bySymbol.values()].map((card) => ({ card, side: kind, amount: Number(card.amount),
          planned: kind === 'sell' ? clamp(Number(card.planned_amount || 0) / Number(card.amount)) : 0 }));
      };
      const sales = (payload && payload.sales) || {};
      const all = payload ? [...side(payload.companies, [payload.large || [], payload.clusters || []], 'buy'),
        ...side(sales.companies, [sales.large || [], sales.clusters || []], 'sell')].sort((a, b) => b.amount - a.amount) : [];
      if (!all.length) { root.hidden = true; return; }
      // sizes on a logarithmic scale: a $100K purchase and an $80M sale both stay readable, the label carries the dollars
      const low = Math.log(all[all.length - 1].amount), high = Math.log(all[0].amount);
      let seed = 11;
      const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
      all.forEach((b, i) => { b.u = high > low ? (Math.log(b.amount) - low) / (high - low) : 0.5; b.phase = rnd() * 6.283; b.grow = -i * 0.025; b.x = b.y = b.vx = b.vy = 0; b.fx = rnd(); b.fy = rnd(); });

      let W = 0, H = 0, theme = {}, hot = null, drag = null, raf = 0, visible = false, last = 0, clock = 0, placed = false;
      const pointer = { in: false, x: 0, y: 0, vx: 0, vy: 0, dx: 0, dy: 0, moved: 0 };
      const readTheme = () => { const cs = getComputedStyle(root), v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
        theme = { pos: v('--pos', '#3dffa2'), neg: v('--neg', '#ff6b8e'), hi: v('--text-hi', '#e6f1ff'), mid: v('--text-mid', '#a9bddf'), mono: v('--font-mono', "'JetBrains Mono', monospace") }; };
      const tint = (color, alpha) => (/^#[0-9a-f]{6}$/i.test(color) ? color + Math.round(alpha * 255).toString(16).padStart(2, '0') : color);
      const home = (b) => (b.side === 'buy' ? W * 0.3 : W * 0.7);
      const fit = () => {
        const box = stage.getBoundingClientRect(); if (box.width < 40 || box.height < 40) return false;
        const oldW = W, oldH = H;
        W = box.width; H = box.height; const dpr = Math.min(2, window.devicePixelRatio || 1);
        canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // the bubbles together cover about a third of the picture: the rest is open water for them to move in
        const base = all.map((b) => 13 + 42 * Math.pow(b.u, 1.3)), area = base.reduce((sum, r) => sum + Math.PI * r * r, 0);
        const scale = clamp(Math.sqrt(0.34 * W * H / area), 0.4, 1.7);
        all.forEach((b, i) => { b.r = Math.max(9, base[i] * scale);
          if (!placed) { b.x = home(b) + (b.fx - 0.5) * W * 0.34; b.y = H * (0.15 + 0.7 * b.fy); } else { b.x *= W / oldW; b.y *= H / oldH; } });
        if (!placed) { placed = true; for (let i = 0; i < 160; i++) step(1 / 60, 0, true); }      // settle before the first frame
        return true;
      };
      function step(dt, t, quiet) {
        const calm = quiet || still.matches, damp = Math.pow(quiet ? 0.9 : 0.945, dt * 60);       // light water: a pushed bubble glides on
        for (const b of all) {
          if (b === drag) continue;
          b.vx += (home(b) - b.x) * (quiet ? 0.3 : 0.1) * dt; b.vy += (H / 2 - b.y) * (quiet ? 0.22 : 0.05) * dt;   // each colour drifts back to its side
          if (!calm) { b.vx += Math.cos(t * 0.5 + b.phase) * 18 * dt; b.vy += Math.sin(t * 0.43 + b.phase * 1.3) * 18 * dt; }   // slow drift
          if (pointer.in && !quiet && b !== hot) {                                                 // the pointer parts the bubbles around it
            const dx = b.x - pointer.x, dy = b.y - pointer.y, d = Math.hypot(dx, dy) || 1, reach = b.r + 78;
            if (d < reach) { const push = (1 - d / reach); b.vx += (dx / d * 1100 + pointer.vx * 2.4) * push * dt; b.vy += (dy / d * 1100 + pointer.vy * 2.4) * push * dt; }
          }
          b.vx *= damp; b.vy *= damp; b.x += b.vx * dt; b.y += b.vy * dt;
        }
        for (let pass = 0; pass < 3; pass++) {
          for (let i = 0; i < all.length; i++) for (let j = i + 1; j < all.length; j++) {
            const a = all[i], b = all[j], dx = b.x - a.x, dy = b.y - a.y, gap = a.r + b.r + 1.5, d2 = dx * dx + dy * dy;
            if (d2 >= gap * gap) {
              // neighbours that are close but not touching ease apart, so the field spreads out instead of clumping
              const room = gap + 18; if (!pass && !quiet && d2 < room * room) { const d = Math.sqrt(d2), ease = (1 - (d - gap) / 18) * 46 * dt, ma = a.r * a.r, mb = b.r * b.r;
                a.vx -= dx / d * ease * mb / (ma + mb); a.vy -= dy / d * ease * mb / (ma + mb); b.vx += dx / d * ease * ma / (ma + mb); b.vy += dy / d * ease * ma / (ma + mb); }
              continue;
            }
            const d = Math.sqrt(d2) || 0.01, over = gap - d, nx = dx / d, ny = dy / d, ma = a.r * a.r, mb = b.r * b.r;
            const sa = a === drag ? 0 : b === drag ? 1 : mb / (ma + mb), sb = 1 - sa;
            a.x -= nx * over * sa; a.y -= ny * over * sa; b.x += nx * over * sb; b.y += ny * over * sb;
            if (!pass && !quiet) { const kick = over * 7; a.vx -= nx * kick * sa; a.vy -= ny * kick * sa; b.vx += nx * kick * sb; b.vy += ny * kick * sb; }   // a bump passes the push on
          }
          for (const b of all) {
            if (b.x < b.r + 2) { b.x = b.r + 2; b.vx = Math.abs(b.vx) * 0.3; } else if (b.x > W - b.r - 2) { b.x = W - b.r - 2; b.vx = -Math.abs(b.vx) * 0.3; }
            if (b.y < b.r + 2) { b.y = b.r + 2; b.vy = Math.abs(b.vy) * 0.3; } else if (b.y > H - b.r - 2) { b.y = H - b.r - 2; b.vy = -Math.abs(b.vy) * 0.3; }
          }
        }
      }
      const label = (text, x, y, size, color) => { ctx.font = `600 ${size}px ${theme.mono}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = color; ctx.fillText(text, x, y); };
      const draw = (dt) => {
        ctx.clearRect(0, 0, W, H);
        for (const b of all.slice().sort((p, q) => (p === hot) - (q === hot) || q.r - p.r)) {
          b.grow = Math.min(1, b.grow + dt * 1.6);
          if (b.grow <= 0) continue;
          const R = b.r * easeOut(b.grow), color = b.side === 'buy' ? theme.pos : theme.neg, on = hot === b || drag === b;
          // sold under a trading plan: that share of the bubble stays empty inside a dashed ring
          const filled = R * Math.sqrt(1 - b.planned);
          if (filled > 0.6) {
            const glow = ctx.createRadialGradient(b.x, b.y, filled * 0.2, b.x, b.y, filled);
            glow.addColorStop(0, tint(color, on ? 0.2 : 0.07)); glow.addColorStop(1, tint(color, on ? 0.6 : 0.42));
            ctx.beginPath(); ctx.arc(b.x, b.y, filled, 0, 2 * Math.PI); ctx.fillStyle = glow; ctx.fill();
          }
          ctx.beginPath(); ctx.arc(b.x, b.y, R, 0, 2 * Math.PI); ctx.setLineDash(b.planned > 0.01 ? [4, 3] : []);
          ctx.strokeStyle = on ? theme.hi : color; ctx.lineWidth = on ? 1.8 : 1.2; ctx.stroke(); ctx.setLineDash([]);
          if (R >= 15) { label(b.card.symbol, b.x, b.y - (R >= 27 ? 5 : 0), clamp(R * 0.38, 8.5, 13), theme.hi); if (R >= 27) label(usd.format(b.amount), b.x, b.y + 9, clamp(R * 0.26, 8.5, 10.5), theme.mid); }
        }
      };
      const card = () => {
        if (!hot) {
          info.innerHTML = `<p class="mm-k">Last ${Number(payload.window_days) || 7} days · through ${shortDay(payload.through)} · ${all.length} companies</p>` +
            '<p class="mm-line">Each bubble is a company whose officers or directors bought or sold its shares (SEC Form 4). A bigger bubble is more money.</p>' +
            `<p class="mm-hint">${window.matchMedia('(hover: none)').matches ? 'Tap a bubble, or drag it.' : 'Point at a bubble, or drag it.'}</p>`;
          return;
        }
        const c = hot.card, count = Number(hot.side === 'buy' ? c.buyers : c.sellers) || 1;
        const plan = hot.planned > 0.99 ? ' <b>Sold under a 10b5-1 trading plan.</b>' : hot.planned > 0.01 ? ` <b>${usd.format(Number(c.planned_amount))} of it under a 10b5-1 trading plan.</b>` : '';
        info.innerHTML = `<p class="mm-k">${esc(c.symbol)} · ${hot.side === 'buy' ? 'bought' : 'sold'} · ${shortDay(c.start)}${c.end && c.end !== c.start ? '–' + shortDay(c.end) : ''}</p>` +
          `<p class="mm-name">${esc(c.name || c.symbol)}</p><p class="mm-move ${hot.side === 'buy' ? 'up' : 'down'}">${usd.format(hot.amount)}</p>` +
          `<p class="mm-line">${count} officer${count === 1 ? '' : 's'} / director${count === 1 ? '' : 's'}.${plan}</p>`;
      };
      const under = () => all.filter((b) => Math.hypot(b.x - pointer.x, b.y - pointer.y) <= b.r + 2).sort((a, b) => a.r - b.r)[0] || null;
      const tick = (now) => {
        raf = 0; if (!visible) return;
        const dt = Math.min(0.05, (now - (last || now)) / 1000); last = now; clock += dt;
        step(dt, clock, false);
        pointer.vx *= 0.8; pointer.vy *= 0.8;
        if (!drag && (pointer.in || hot)) { const hit = pointer.in ? under() : null; if (hit !== hot) { hot = hit; card(); canvas.style.cursor = hot ? 'grab' : ''; } }
        draw(dt);
        raf = requestAnimationFrame(tick);
      };
      const run = () => { if (visible && !raf && W) { last = 0; raf = requestAnimationFrame(tick); } };
      const track = (event) => { const box = canvas.getBoundingClientRect(), x = event.clientX - box.left, y = event.clientY - box.top;
        pointer.vx = (x - pointer.x) * 30; pointer.vy = (y - pointer.y) * 30; pointer.x = x; pointer.y = y; };
      canvas.addEventListener('pointermove', (event) => {
        const wasIn = pointer.in; track(event); if (!wasIn) { pointer.vx = pointer.vy = 0; }
        pointer.in = event.pointerType !== 'touch' || !!drag;
        if (drag) { pointer.moved += Math.abs(pointer.vx) + Math.abs(pointer.vy); drag.x = pointer.x + pointer.dx; drag.y = pointer.y + pointer.dy; drag.vx = pointer.vx * 0.9; drag.vy = pointer.vy * 0.9; }
      });
      canvas.addEventListener('pointerdown', (event) => {
        track(event); pointer.vx = pointer.vy = 0; pointer.moved = 0;
        const hit = under(); if (hit !== hot) { hot = hit; card(); }
        if (hit) { drag = hit; pointer.dx = hit.x - pointer.x; pointer.dy = hit.y - pointer.y; canvas.style.cursor = 'grabbing'; try { canvas.setPointerCapture(event.pointerId); } catch (_) { /* no capture */ } }
        // a click on open water sends a ripple: everything nearby is thrown outwards
        else if (!still.matches) for (const b of all) { const dx = b.x - pointer.x, dy = b.y - pointer.y, d = Math.hypot(dx, dy) || 1, reach = b.r + 190; if (d < reach) { const kick = 620 * (1 - d / reach); b.vx += dx / d * kick; b.vy += dy / d * kick; } }
      });
      const release = (event) => { if (drag) { drag = null; canvas.style.cursor = hot ? 'grab' : ''; } if (event.pointerType === 'touch') pointer.in = false; };
      canvas.addEventListener('pointerup', release); canvas.addEventListener('pointercancel', release);
      canvas.addEventListener('pointerleave', () => { if (!drag) { pointer.in = false; if (hot) { hot = null; card(); } } });
      readTheme(); card();
      new MutationObserver(readTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-color-theme', 'class', 'style'] });
      new ResizeObserver(() => { if (fit()) run(); }).observe(stage);
      new IntersectionObserver((entries) => { visible = entries.some((entry) => entry.isIntersecting); if (visible) { fit(); run(); } }, { rootMargin: '80px' }).observe(stage);
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(readTheme);
    });
  }

  const boot = () => {
    try { compactRow(); } catch (error) { console.error('market-map: compact row', error); }
    const deck = document.querySelector('.mm-deck');
    if (deck) deck.hidden = false;
    $$('[data-season]').forEach((root) => {
      try { if (window.PULSARIUM_SEASON) { root.hidden = false; seasonRing(root); } } catch (error) { console.error('market-map: season ring', error); }
    });
    $$('[data-insider-map]').forEach((root) => { try { insiderMap(root); } catch (error) { root.hidden = true; console.error('market-map: insiders', error); } });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
