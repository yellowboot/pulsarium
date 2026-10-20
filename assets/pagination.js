/* Shared compact pagination, matching the personal cabinet's Pager. */
(() => {
  'use strict';
  function pageNumbers(page, pages) {
    const keep = [...new Set([0, pages - 1, page - 1, page, page + 1])]
      .filter(n => n >= 0 && n < pages).sort((a, b) => a - b);
    const shown = [];
    let previous = -1;
    for (const n of keep) {
      if (n - previous === 2) shown.push(previous + 1);
      else if (n - previous > 2) shown.push(null);
      shown.push(n);
      previous = n;
    }
    return shown;
  }
  function create({page, pages, onPage, label = 'Pages'}) {
    const nav = document.createElement('nav');
    nav.className = 'pager-numbers';
    nav.setAttribute('aria-label', label);
    nav.hidden = pages <= 1;
    const button = (text, target, name, disabled = false) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = text;
      b.setAttribute('aria-label', name);
      b.disabled = disabled;
      if (target === page && /^\d+$/.test(text)) b.setAttribute('aria-current', 'page');
      b.addEventListener('click', () => onPage(target));
      nav.append(b);
    };
    button('‹', page - 1, 'Previous page', page === 0);
    for (const n of pageNumbers(page, pages)) {
      if (n === null) {
        const gap = document.createElement('span');
        gap.className = 'pager-gap';
        gap.textContent = '…';
        gap.setAttribute('aria-hidden', 'true');
        nav.append(gap);
      } else button(String(n + 1), n, `Page ${n + 1}`);
    }
    button('›', page + 1, 'Next page', page === pages - 1);
    return nav;
  }
  function enhanceLists() {
    document.querySelectorAll('.paged-list').forEach(list => {
      const size = Math.max(1, Number(list.dataset.perPage) || 10);
      const cards = [...list.children], pages = Math.ceil(cards.length / size);
      if (pages < 2) return;
      const section = list.closest('section'), heading = section?.querySelector('h2');
      const label = heading?.firstChild?.textContent.trim() || 'Companies';
      const top = document.createElement('div'), bottom = document.createElement('div');
      list.before(top); list.after(bottom);
      function show(page, scroll = false) {
        cards.forEach((card, i) => { card.hidden = Math.floor(i / size) !== page; });
        const onPage = next => show(next, true);
        top.replaceChildren(create({page, pages, onPage, label: `${label} pages (top)`}));
        bottom.replaceChildren(create({page, pages, onPage, label: `${label} pages (bottom)`}));
        if (scroll) {
          (section || list).scrollIntoView({block: 'start'});
          top.querySelector('[aria-current]')?.focus({preventScroll: true});
        }
      }
      show(0);
    });
  }
  window.PulsariumPagination = {pageNumbers, create};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', enhanceLists, {once: true});
  else enhanceLists();
})();
