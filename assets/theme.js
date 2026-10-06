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

  // Signed in to the cabinet? It leaves a plain cookie on .pulsarium.finance
  // (lib/siteBridge.ts there): the first letter of the name and the address
  // of an uploaded photo, no token. The header's "Sign in" then becomes that
  // avatar, still opening the cabinet.
  var APP = 'https://app.pulsarium.finance/';
  var OWN_PHOTO = /^https:\/\/omkeplyeuxwlsqnblsjm\.supabase\.co\/storage\/v1\/object\/public\/avatars\//;
  function member() {
    var pair = document.cookie.split('; ').filter(function (item) { return item.indexOf('pulsarium_member=') === 0; })[0];
    if (!pair) return null;
    try {
      var value = JSON.parse(decodeURIComponent(pair.slice('pulsarium_member='.length)));
      return value && typeof value.i === 'string' ? value : null;
    } catch (error) { return null; }
  }

  function showMember() {
    var who = member();
    var signIn = document.querySelector('header a.btn-primary[href^="' + APP + '"], header a.header-signin');
    if (!who || !signIn) return;
    var avatar = document.createElement('a');
    avatar.className = 'header-avatar';
    avatar.href = APP;
    avatar.title = 'Your Pulsarium cabinet';
    avatar.setAttribute('aria-label', 'Open your Pulsarium cabinet');
    var letter = document.createElement('span');
    letter.textContent = (who.i || '?').charAt(0).toUpperCase();
    avatar.appendChild(letter);
    if (typeof who.a === 'string' && OWN_PHOTO.test(who.a)) {
      var photo = document.createElement('img');
      photo.alt = '';
      photo.addEventListener('error', function () { photo.remove(); });
      photo.src = who.a;
      avatar.appendChild(photo);
    }
    signIn.replaceWith(avatar);
  }

  function ready() {
    install();
    showMember();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, { once: true });
  else ready();

  window.addEventListener('storage', function (event) {
    if (event.key === key || event.key === null) apply(preferredTheme());
  });
  window.addEventListener('pageshow', function () { apply(preferredTheme()); });

  // A light sheen crosses a news card when the pointer comes onto it, as on
  // the cabinet's cards (themes.css draws it in Calm). Mouse and trackpad
  // only, and never with reduced motion.
  var fine = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (fine && !still) {
    var lit = null;
    var light = function (card) {
      if (card === lit) return;
      if (lit) lit.classList.remove('is-lit');
      lit = card;
      if (!card) return;
      if (!card.querySelector(':scope > .card-glow')) {
        var glow = document.createElement('span');
        glow.className = 'card-glow';
        glow.setAttribute('aria-hidden', 'true');
        card.appendChild(glow);
        void glow.offsetWidth; // settle the resting position so the first hover sweeps too
      }
      card.classList.add('is-lit');
    };
    document.addEventListener('pointerover', function (event) {
      var target = event.target;
      light(target && target.closest ? target.closest('.card, .item') : null);
    }, { passive: true });
    document.addEventListener('mouseout', function (event) { if (!event.relatedTarget) light(null); });
  }
}());
