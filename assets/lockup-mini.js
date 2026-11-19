// Home page "Lock-up watch" card (site_build.home_lockups): the build writes the next IPO lock-up releases with their
// times; this script keeps the countdown current, grows each bar from empty to how far the wait has gone, and drops a
// row once its shares are free (the next site build brings the following release).
(() => {
  const root = document.querySelector('[data-lockup-mini]');
  if (!root) return;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const rows = [...root.querySelectorAll('[data-at]')];
  const draw = () => {
    const now = Date.now();
    for (const row of rows) {
      const at = +row.dataset.at, from = +row.dataset.from, left = at - now;
      if (left <= 0) { row.hidden = true; continue; }
      const days = Math.floor(left / 864e5), hours = Math.floor(left / 3600e3);
      const [n, unit] = days >= 1 ? [days, days === 1 ? 'day' : 'days'] : [Math.max(hours, 1), hours > 1 ? 'hrs' : 'hr'];
      row.querySelector('[data-n]').textContent = n;
      row.querySelector('[data-unit]').textContent = unit;
      const f = Math.min(1, Math.max(0.02, (now - from) / (at - from || 1)));
      row.style.setProperty('--f', f.toFixed(3));
    }
    if (rows.every((row) => row.hidden)) root.hidden = true;
  };
  if (!still) rows.forEach((row) => row.style.setProperty('--f', '0'));
  setTimeout(draw, 60);
  setInterval(draw, 60e3);
})();
