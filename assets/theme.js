/* Public-site Neon / Calm. Apply the device preference before the first paint.
   This is the cabinet's storage key; each hostname keeps its own preference. */
(function () {
  'use strict';
  var key = 'pulsarium-color-theme';
  var root = document.documentElement;
  var button;
  var icons = {
    calm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/></svg>',
    neon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7Z"/></svg>'
  };

  function storedTheme() {
    try { return window.localStorage.getItem(key); } catch (error) { return null; }
  }

  // Without a choice of their own, visitors get the theme their device uses:
  // a light system setting opens Calm. The toggle's choice is remembered.
  var lightQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  function preferredTheme() {
    var stored = storedTheme();
    if (stored === 'calm' || stored === 'neon') return stored;
    return lightQuery && lightQuery.matches ? 'calm' : 'neon';
  }

  function apply(theme) {
    theme = theme === 'calm' ? 'calm' : 'neon';
    root.setAttribute('data-color-theme', theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'theme-color';
      document.head.appendChild(meta);
    }
    meta.content = theme === 'calm' ? '#eef1f7' : '#04050c';
    if (button) {
      var next = theme === 'calm' ? 'neon' : 'calm';
      var label = 'Switch to ' + (next === 'calm' ? 'Calm (light)' : 'Neon (dark)') + ' theme';
      button.innerHTML = icons[next];
      button.setAttribute('aria-label', label);
      button.title = label;
    }
  }

  apply(preferredTheme());
  if (lightQuery && lightQuery.addEventListener) {
    lightQuery.addEventListener('change', function () { if (!storedTheme()) apply(preferredTheme()); });
  }

  function install() {
    var actions = document.querySelector('header .header-actions, .site-header .main-nav, .site-header .site-nav');
    if (!actions || document.querySelector('[data-theme-toggle]')) return;
    button = document.createElement('button');
    button.type = 'button';
    button.className = 'theme-toggle';
    button.setAttribute('data-theme-toggle', '');
    button.addEventListener('click', function () {
      var next = root.getAttribute('data-color-theme') === 'calm' ? 'neon' : 'calm';
      apply(next);
      try { window.localStorage.setItem(key, next); } catch (error) { /* The switch still works when storage is blocked. */ }
    });
    actions.appendChild(button);
    apply(root.getAttribute('data-color-theme'));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();

  window.addEventListener('storage', function (event) {
    if (event.key === key || event.key === null) apply(preferredTheme());
  });
  window.addEventListener('pageshow', function () { apply(preferredTheme()); });

  // A soft light follows the pointer across a news card (themes.css draws it
  // in Calm from --glow-x/--glow-y). Mouse and trackpad only, and never with
  // reduced motion; one position update per frame.
  var fine = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (fine && !still) {
    var lit = null, pending = null, frame = 0;
    var light = function (card) {
      if (lit && lit !== card) lit.classList.remove('is-lit');
      lit = card;
    };
    document.addEventListener('pointermove', function (event) {
      pending = event;
      if (frame) return;
      frame = window.requestAnimationFrame(function () {
        frame = 0;
        var target = pending.target;
        var card = target && target.closest ? target.closest('.card, .item') : null;
        light(card);
        if (!card) return;
        if (!card.querySelector(':scope > .card-glow')) {
          var glow = document.createElement('span');
          glow.className = 'card-glow';
          glow.setAttribute('aria-hidden', 'true');
          card.appendChild(glow);
        }
        var box = card.getBoundingClientRect();
        card.style.setProperty('--glow-x', (pending.clientX - box.left) + 'px');
        card.style.setProperty('--glow-y', (pending.clientY - box.top) + 'px');
        card.classList.add('is-lit');
      });
    }, { passive: true });
    document.addEventListener('mouseout', function (event) { if (!event.relatedTarget) light(null); });
  }
}());
