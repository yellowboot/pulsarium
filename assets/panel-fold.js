// On phones the news page's side panels fold to their headings, so the 80
// news cards come sooner; a tap on a heading opens its panel, and each
// panel stays as the reader left it on this device. Desktops are untouched
// (panel-fold.css applies only to narrow screens).
(() => {
  const PANELS = [
    ['.panel-mentions', 'mentions'], ['.quotes-panel', 'indices'],
    ['#insider-radar', 'insider'], ['#sector-overview', 'sectors'], ['#financial-signals', 'reports'],
  ];
  const phone = window.matchMedia('(max-width: 700px)');
  const KEY = 'pulsarium-open-panels';
  let open = [];
  try { open = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { open = []; }
  if (!Array.isArray(open)) open = [];
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(open)); } catch { /* private mode */ } };
  // a link to a panel (the homepage's SEC card: news/#insider-radar) opens it
  const linked = [];
  const openLinked = () => {
    for (const { panel, show } of linked) if (panel.id && location.hash === `#${panel.id}`) show(true);
  };

  for (const [selector, name] of PANELS) {
    const panel = document.querySelector(selector);
    const heading = panel && [...panel.children].find((el) => !el.classList.contains('panel-edge'));
    if (!heading) continue;
    const title = (heading.querySelector('h2') || heading).textContent.trim();
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'panel-fold';
    toggle.setAttribute('aria-label', `Show or hide ${title}`);
    heading.classList.add('panel-fold-head');
    heading.append(toggle);
    const show = (isOpen) => {
      panel.dataset.folded = String(!isOpen);
      toggle.setAttribute('aria-expanded', String(isOpen));
    };
    show(open.includes(name));
    linked.push({ panel, show });
    heading.addEventListener('click', (event) => {
      // the sector and report expand icons keep their own job
      if (!phone.matches || event.target.closest('a, button:not(.panel-fold)')) return;
      const isOpen = panel.dataset.folded === 'true';
      show(isOpen);
      open = isOpen ? [...new Set([...open, name])] : open.filter((item) => item !== name);
      save();
    });
  }
  openLinked();
  window.addEventListener('hashchange', openLinked);
})();
