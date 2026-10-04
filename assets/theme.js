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

  apply(storedTheme());

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
    if (event.key === key || event.key === null) apply(storedTheme());
  });
  window.addEventListener('pageshow', function () {
    var theme = storedTheme();
    if (theme === 'neon' || theme === 'calm') apply(theme);
  });
}());
