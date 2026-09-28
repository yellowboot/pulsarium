// Consent-based visit analytics for the public pages, the same as the
// homepage's inline script: with the visitor's permission, one landing_view
// per browser session goes to the cabinet's analytics_events table, and
// links into app.pulsarium.finance carry the campaign source so a signup
// can be attributed. The consent is shared with the homepage (same
// localStorage key and cookie), so a visitor is asked once.
(() => {
  const consentKey = 'pulsarium.landing-analytics-consent.v1';
  const sourceKey = 'pulsarium.landing-analytics-source.v1';
  const visitKey = 'pulsarium.landing-analytics-visit.v1';
  const consentCookie = 'pulsarium_analytics_consent';
  const key = 'sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF';
  const endpoint = 'https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/analytics_events';

  const banner = document.createElement('aside');
  banner.className = 'analytics-consent';
  banner.setAttribute('aria-label', 'Analytics preference');
  banner.hidden = true;
  banner.innerHTML =
    '<p><strong>Help improve Pulsarium?</strong> With your permission, we count visits and signup journeys and save the campaign source for this browser session. No advertising trackers. Read the <a href="https://app.pulsarium.finance/?legal=privacy">Privacy policy</a>.</p>' +
    '<div class="analytics-consent-actions"><button type="button" data-choice="no">Decline</button><button type="button" data-choice="yes">Allow analytics</button></div>';

  const consent = () => localStorage.getItem(consentKey) ||
    document.cookie.split('; ').find(item => item.startsWith(consentCookie + '='))?.split('=')[1];
  const clean = value => /^[a-z0-9][a-z0-9._-]{0,79}$/.test((value || '').trim().toLowerCase())
    ? value.trim().toLowerCase() : null;

  function source() {
    const saved = sessionStorage.getItem(sourceKey);
    if (saved) return JSON.parse(saved);
    const params = new URLSearchParams(location.search);
    let fallback = 'direct';
    try {
      const ref = document.referrer ? new URL(document.referrer).hostname : '';
      if (ref && !ref.endsWith('pulsarium.finance')) fallback = clean(ref.replace(/^www\./, '')) || 'referral';
    } catch { /* no usable referrer */ }
    const result = {
      utm_source: clean(params.get('utm_source')) || fallback,
      utm_medium: clean(params.get('utm_medium')),
      utm_campaign: clean(params.get('utm_campaign')),
    };
    sessionStorage.setItem(sourceKey, JSON.stringify(result));
    return result;
  }

  function visit() {
    let id = sessionStorage.getItem(visitKey);
    if (!id) { id = crypto.randomUUID(); sessionStorage.setItem(visitKey, id); }
    return id;
  }

  function record() {
    if (consent() !== 'yes') return;
    const payload = { event_name: 'landing_view', visitor_id: visit(), user_id: null, ...source() };
    fetch(endpoint, {
      method: 'POST',
      headers: { apikey: key, 'Content-Type': 'application/json', Prefer: 'return=minimal' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  }

  try {
    document.body.appendChild(banner);
    if (!consent()) banner.hidden = false;
    else record();
    banner.addEventListener('click', event => {
      const choice = event.target.closest('button[data-choice]');
      if (!choice) return;
      localStorage.setItem(consentKey, choice.dataset.choice);
      document.cookie = consentCookie + '=' + choice.dataset.choice +
        '; Domain=.pulsarium.finance; Path=/; Max-Age=31536000; SameSite=Lax; Secure';
      banner.hidden = true;
      if (choice.dataset.choice === 'yes') record();
      else { sessionStorage.removeItem(sourceKey); sessionStorage.removeItem(visitKey); }
    });
    document.querySelectorAll('[data-analytics-settings]').forEach(button =>
      button.addEventListener('click', () => { banner.hidden = false; }));
    document.addEventListener('click', event => {
      const link = event.target.closest('a[href^="https://app.pulsarium.finance/"]');
      if (!link || consent() !== 'yes') return;
      const url = new URL(link.href);
      if (url.searchParams.has('legal')) return;
      for (const [name, value] of Object.entries(source())) if (value) url.searchParams.set(name, value);
      url.searchParams.set('pv', visit());
      link.href = url.toString();
    }, true);
  } catch { banner.hidden = true; }
})();
