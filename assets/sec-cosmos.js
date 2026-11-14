/* SEC Market Cosmos & Gravity Forecast — Public Site Interactive Engine
   Draws exclusively from real SEC XBRL snapshots (sec_sector_analytics, sec_insider_radar, industry_valuation)
   plus PULSARIUM_SEASON when present. Zero random links: every orbit, black hole pull, and supernova
   is driven by reported SEC metrics. */
(function () {
  'use strict';

  const SUPABASE_REST = 'https://omkeplyeuxwlsqnblsjm.supabase.co/rest/v1/';
  const SUPABASE_KEY = 'sb_publishable_Iike5dnuHEuwIIF-qruzzg_WkWJodcF';
  const SECTOR_CACHE = 'pulsarium-sector-snapshot-v7';
  const RADAR_CACHE = 'pulsarium:insider-radar:v2';
  const VAL_CACHE = 'pulsarium:industry-val:v1';

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const usdCompact = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });
  const fmtMoney = (n) => (Number.isFinite(Number(n)) && Number(n) !== 0 ? usdCompact.format(Number(n)) : '—');
  const fmtPct = (n) => (Number.isFinite(Number(n)) ? `${Number(n) > 0 ? '+' : ''}${Number(n).toFixed(1)}%` : '—');

  const PRESETS = {
    bigtech: { label: 'Big Tech & AI Core', symbols: ['NVDA', 'MSFT', 'AAPL', 'GOOG', 'GOOGL', 'AMZN', 'META', 'AVGO', 'AMD', 'ORCL', 'PLTR', 'INTC', 'SMCI', 'NOW', 'ANET', 'CSCO', 'IBM', 'QCOM', 'MU'] },
    healthcare: { label: 'Pharma & Healthcare', symbols: ['LLY', 'UNH', 'JNJ', 'ABBV', 'MRK', 'TMO', 'ABT', 'ISRG', 'AMGN', 'GILD', 'VRTX', 'REGN', 'PFE', 'BMY', 'CVS', 'BBIO', 'NVO'] },
    value: { label: 'Value, Cash & Dividends', symbols: ['JPM', 'XOM', 'V', 'MA', 'PG', 'KO', 'CVX', 'MCK', 'CAT', 'LMT', 'UNP', 'GS', 'BLK', 'RTX', 'HON', 'DE', 'SNX', 'COLM'] },
  };

  function hashAngle(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 6283) / 1000;
  }

  function cleanName(raw) {
    return String(raw || '').replace(/\s*\/[a-z]+\/?$/i, '').trim();
  }

  async function loadCachedOrFetch(cacheKey, tableQuery) {
    try {
      const raw = sessionStorage.getItem(cacheKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && parsed.payload) return parsed.payload;
      }
    } catch (_) { /* ignore storage errors */ }
    try {
      const res = await fetch(SUPABASE_REST + tableQuery, { headers: { apikey: SUPABASE_KEY } });
      if (!res.ok) return null;
      const rows = await res.json();
      const payload = rows && rows[0] && rows[0].payload ? rows[0].payload : null;
      if (payload) {
        try { sessionStorage.setItem(cacheKey, JSON.stringify({ ts: Date.now(), payload })); } catch (_) {}
      }
      return payload;
    } catch (_) {
      return null;
    }
  }

  function buildUniverse(secSnap, radarSnap, valSnap) {
    const rawSectors = Array.isArray(secSnap?.sectors) ? secSnap.sectors : [];
    const valInd = valSnap?.industries || {};
    const valComp = valSnap?.companies || {};

    const sortedSectors = [...rawSectors].sort((a, b) => Number(b.revenue_current || 0) - Number(a.revenue_current || 0));
    const totalSectors = sortedSectors.length || 12;

    const sectors = [];
    const sectorById = new Map();

    sortedSectors.forEach((s, idx) => {
      const id = String(s.id);
      const name = String(s.name || id);
      const revYoy = Number(s.revenue_total_yoy ?? s.revenue_yoy ?? 0);
      const marginChange = Number(s.margin_change ?? 0);
      const direction = s.direction || (revYoy >= 5 ? 'growth' : revYoy < 0 ? 'pressure' : 'mixed');
      const medianPe = Number(valInd[id]?.all?.median || valInd[id]?.large?.median || 21);

      const ring = idx < 6 ? 1 : 2;
      const ringCount = ring === 1 ? Math.min(6, totalSectors) : Math.max(1, totalSectors - 6);
      const ringIdx = ring === 1 ? idx : idx - 6;
      const angle = (ringIdx / ringCount) * Math.PI * 2 - Math.PI / 2 + (ring === 2 ? 0.25 : 0);
      const dist = ring === 1 ? 150 : 308;
      const gx = Math.cos(angle) * dist;
      const gy = Math.sin(angle) * dist * 0.82;
      const radius = ring === 1 ? 64 : 50;

      const color = direction === 'growth' && marginChange >= 0
        ? '#00f0ff'
        : direction === 'growth'
          ? '#3dffa2'
          : direction === 'pressure'
            ? '#ff6b8e'
            : '#7fa8e8';

      const meta = { id, name, revYoy, marginChange, direction, medianPe, angle, gx, gy, radius, color };
      sectors.push(meta);
      sectorById.set(id, meta);
    });

    const bySym = new Map();
    const ensure = (sym) => {
      const key = String(sym || '').toUpperCase();
      if (!key) return null;
      let item = bySym.get(key);
      if (!item) {
        item = {
          symbol: key,
          name: key,
          sectorId: '',
          revenueTtm: 0,
          marketCap: 0,
          revYoy: null,
          marginChange: null,
          pe: null,
          earningsMove: null,
          risks: [],
          alphas: [],
          source: null,
        };
        bySym.set(key, item);
      }
      return item;
    };

    // 1. Companies from each sector in sec_sector_analytics
    for (const s of rawSectors) {
      for (const c of s.companies || []) {
        const item = ensure(c.symbol);
        if (!item) continue;
        item.name = cleanName(c.name || c.symbol);
        item.sectorId = String(s.id);
        item.revenueTtm = Math.max(item.revenueTtm, Number(c.ttm_revenue_current || c.revenue_current || 0));
        item.revYoy = c.ttm_revenue_yoy != null ? Number(c.ttm_revenue_yoy) : c.revenue_yoy != null ? Number(c.revenue_yoy) : item.revYoy;
        item.marginChange = c.margin_change != null ? Number(c.margin_change) : item.marginChange;
        item.source = item.source || c.source || null;
        if (item.revYoy >= 12 && (item.marginChange ?? 0) >= 2) {
          item.alphas.push({
            title: `Strong Sector Momentum (${fmtPct(item.revYoy)} Sales YoY)`,
            detail: `Operating margin expanded ${item.marginChange > 0 ? '+' : ''}${item.marginChange.toFixed(1)} pp YoY.`,
            weight: 1.4,
            code: 'turnaround',
            source: c.source || null,
          });
        }
      }
    }

    // 2. All 6 SEC Financial Signal Groups
    const groups = secSnap?.financial_signals?.groups || {};
    for (const r of groups.turnaround || []) {
      const item = ensure(r.symbol);
      if (!item) continue;
      item.name = cleanName(r.name || item.name);
      item.sectorId = item.sectorId || r.sector || valComp[item.symbol] || '';
      item.revenueTtm = Math.max(item.revenueTtm, Number(r.revenue_current || 0));
      item.source = item.source || r.source || null;
      const s0 = r.signals && r.signals[0];
      const swing = s0 && s0.swing_amount ? ` (${fmtMoney(s0.swing_amount)} swing)` : '';
      item.alphas.push({
        title: s0?.code === 'operating_turnaround' ? 'Operating Profit Turnaround' : 'Operating Cash-Flow Turnaround',
        detail: `${s0?.label || 'Turned positive YoY'}${swing} in period ended ${r.period_end}.`,
        weight: 2.0,
        code: 'turnaround',
        source: r.source || null,
      });
    }

    for (const r of groups.shares || []) {
      const item = ensure(r.symbol);
      if (!item) continue;
      item.name = cleanName(r.name || item.name);
      item.sectorId = item.sectorId || r.sector || valComp[item.symbol] || '';
      item.revenueTtm = Math.max(item.revenueTtm, Number(r.revenue_current || 0));
      item.source = item.source || r.source || null;
      const s0 = (r.signals || []).find((x) => x.code === 'share_reduction' || x.code === 'share_increase') || (r.signals && r.signals[0]);
      const cash = (r.signals || []).find((x) => x.code === 'buyback_cash');
      const pct = Number(s0?.change_pct || 0);
      if (pct < 0) {
        item.alphas.push({
          title: `Share Count Reduced ${fmtPct(pct)} YoY`,
          detail: `Shrinking share count via buybacks${cash?.current ? ' (' + fmtMoney(cash.current) + ')' : ''} (${r.period_end}).`,
          weight: 1.5,
          code: 'buybacks',
          source: r.source || null,
        });
      } else if (pct > 0) {
        item.risks.push({
          title: `Share Dilution ${fmtPct(pct)} YoY`,
          detail: `Basic common shares increased ${fmtPct(pct)} YoY (${r.period_end}).`,
          weight: 1.4,
          code: 'dilution',
          source: r.source || null,
        });
      }
    }

    for (const r of groups.stress || []) {
      const item = ensure(r.symbol);
      if (!item) continue;
      item.name = cleanName(r.name || item.name);
      item.sectorId = item.sectorId || r.sector || valComp[item.symbol] || '';
      item.revenueTtm = Math.max(item.revenueTtm, Number(r.revenue_current || 0));
      item.source = item.source || r.source || null;
      const ocf = (r.signals || []).find((x) => x.code === 'negative_operating_cash');
      const cashDec = (r.signals || []).find((x) => x.code === 'cash_decline');
      const debtInc = (r.signals || []).find((x) => x.code === 'debt_increase');
      const parts = [];
      if (ocf?.current) parts.push(`TTM OCF ${fmtMoney(ocf.current)}`);
      if (cashDec?.change_pct) parts.push(`cash ${fmtPct(cashDec.change_pct)}`);
      if (debtInc?.change_pct) parts.push(`debt ${fmtPct(debtInc.change_pct)}`);
      item.risks.push({
        title: 'SEC Financial Stress Warning',
        detail: parts.join(' · ') || 'Negative 12M operating cash flow with balance-sheet pressure.',
        weight: 2.2,
        code: 'stress',
        source: r.source || null,
      });
    }

    for (const r of groups.earnings_cash || []) {
      const item = ensure(r.symbol);
      if (!item) continue;
      item.name = cleanName(r.name || item.name);
      item.sectorId = item.sectorId || r.sector || valComp[item.symbol] || '';
      item.revenueTtm = Math.max(item.revenueTtm, Number(r.revenue_current || 0));
      item.source = item.source || r.source || null;
      const ni = (r.signals || []).find((x) => x.code === 'profit_cash_gap');
      const ocf = (r.signals || []).find((x) => x.code === 'report_operating_cash');
      item.risks.push({
        title: 'Profit vs. Cash-Flow Divergence',
        detail: `Net income ${fmtPct(ni?.change_pct)} while operating cash flow fell ${fmtPct(ocf?.change_pct)} (${r.period_end}).`,
        weight: 2.1,
        code: 'earnings_cash',
        source: r.source || null,
      });
    }

    for (const r of groups.payouts || []) {
      const item = ensure(r.symbol);
      if (!item) continue;
      item.name = cleanName(r.name || item.name);
      item.sectorId = item.sectorId || r.sector || valComp[item.symbol] || '';
      item.revenueTtm = Math.max(item.revenueTtm, Number(r.revenue_current || 0));
      item.source = item.source || r.source || null;
      const gap = (r.signals || []).find((x) => x.code === 'payout_gap');
      const fcf = (r.signals || []).find((x) => x.code === 'report_free_cash');
      item.risks.push({
        title: 'Payouts Exceed Free Cash Flow',
        detail: `Paid out ${fmtMoney(gap?.current)} vs. Free Cash Flow ${fmtMoney(fcf?.current)} (${r.period_end}).`,
        weight: 1.6,
        code: 'payouts',
        source: r.source || null,
      });
    }

    for (const r of groups.working_capital || []) {
      const item = ensure(r.symbol);
      if (!item) continue;
      item.name = cleanName(r.name || item.name);
      item.sectorId = item.sectorId || r.sector || valComp[item.symbol] || '';
      item.revenueTtm = Math.max(item.revenueTtm, Number(r.revenue_current || 0));
      item.source = item.source || r.source || null;
      const s0 = r.signals && r.signals[0];
      const kind = s0?.code === 'inventory_growth' ? 'Inventories' : 'Receivables';
      item.risks.push({
        title: `${kind} Growing Faster Than Sales`,
        detail: `${kind} ${fmtPct(s0?.change_pct)} vs. revenue ${fmtPct(s0?.revenue_growth_pct)} (${r.period_end}).`,
        weight: 1.5,
        code: 'working_capital',
        source: r.source || null,
      });
    }

    // 3. Insider Form 4 Radar
    const buys = Array.isArray(radarSnap?.companies) ? radarSnap.companies : [...(radarSnap?.clusters || []), ...(radarSnap?.large || [])];
    for (const b of buys) {
      if (!b || !b.symbol) continue;
      const item = ensure(b.symbol);
      if (!item) continue;
      item.name = cleanName(b.name || item.name);
      item.sectorId = item.sectorId || valComp[item.symbol] || '';
      const buyers = Number(b.buyers || (b.people && b.people.length) || 1);
      item.alphas.push({
        title: buyers > 1 ? `Cluster Insider Buying (${buyers} officers)` : 'Insider Purchase (SEC Form 4)',
        detail: `${fmtMoney(b.amount)} bought in open-market Form 4 filings (${b.end || 'this week'}).`,
        weight: buyers > 1 ? 2.0 : 1.5,
        code: 'insider_buy',
        source: (b.sources && b.sources[0] && b.sources[0].url) || null,
      });
    }

    const sales = Array.isArray(radarSnap?.sales?.companies) ? radarSnap.sales.companies : [...(radarSnap?.sales?.clusters || []), ...(radarSnap?.sales?.large || [])];
    for (const s of sales) {
      if (!s || !s.symbol) continue;
      const amt = Number(s.amount || 0);
      const planned = Number(s.planned_amount || 0);
      if (amt >= 1500000 && planned < amt * 0.75) {
        const item = ensure(s.symbol);
        if (!item) continue;
        item.name = cleanName(s.name || item.name);
        item.sectorId = item.sectorId || valComp[item.symbol] || '';
        item.risks.push({
          title: 'Unplanned Insider Selling (SEC Form 4)',
          detail: `${fmtMoney(amt)} sold by officers/directors${planned > 0 ? ' (partly 10b5-1)' : ' outside 10b5-1 plan'}.`,
          weight: 1.2,
          code: 'insider_sell',
          source: (s.sources && s.sources[0] && s.sources[0].url) || null,
        });
      }
    }

    // 4. Merge PULSARIUM_SEASON market caps & earnings moves when available
    if (window.PULSARIUM_SEASON && Array.isArray(window.PULSARIUM_SEASON.rows)) {
      for (const row of window.PULSARIUM_SEASON.rows) {
        const sym = String(row[0] || '').toUpperCase();
        const capB = Number(row[4] || 0);
        const move = Number(row[5] || 0);
        const beat = Number(row[6] || 0);
        const mappedSec = valComp[sym];
        if (!mappedSec && !bySym.has(sym) && capB < 25) continue;
        const item = ensure(sym);
        if (!item) continue;
        if (item.name === item.symbol && row[1]) item.name = cleanName(row[1]);
        item.marketCap = Math.max(item.marketCap, capB * 1e9);
        item.earningsMove = move;
        if (mappedSec && !item.sectorId) item.sectorId = mappedSec;
        if (beat === 1 && move >= 5 && !item.alphas.length) {
          item.alphas.push({
            title: `Earnings Beat & Breakout (+${move.toFixed(1)}%)`,
            detail: `Beat quarterly EPS consensus and rose +${move.toFixed(1)}% on report day.`,
            weight: 1.2,
            code: 'turnaround',
            source: null,
          });
        }
      }
    }

    // Finalize StarNodes
    const stars = [];
    for (const item of bySym.values()) {
      const secId = item.sectorId && sectorById.has(item.sectorId)
        ? item.sectorId
        : (valComp[item.symbol] && sectorById.has(valComp[item.symbol]) ? valComp[item.symbol] : (sectors[0]?.id || ''));
      const sec = sectorById.get(secId) || sectors[0];
      if (!sec) continue;

      const sizeMetric = item.marketCap > 0 ? item.marketCap : item.revenueTtm > 0 ? item.revenueTtm * 2.2 : 2e9;
      const logSize = Math.log10(Math.max(5e8, sizeMetric));
      const baseRadius = clamp(3.0 + ((logSize - 8.7) / 3.9) * 6.4, 3.0, 9.5);
      const riskScore = item.risks.reduce((sum, r) => sum + r.weight, 0);
      const alphaScore = item.alphas.reduce((sum, a) => sum + a.weight, 0);
      const angle = hashAngle(item.symbol);

      stars.push({
        symbol: item.symbol,
        name: item.name,
        sectorId: sec.id,
        sectorName: sec.name,
        industryPe: sec.medianPe,
        marketCap: item.marketCap,
        revenueTtm: item.revenueTtm,
        revYoy: item.revYoy ?? sec.revYoy,
        marginChange: item.marginChange ?? sec.marginChange,
        riskScore,
        alphaScore,
        risks: item.risks,
        alphas: item.alphas,
        baseRadius,
        orbitAngle: angle,
        source: item.source || item.risks[0]?.source || item.alphas[0]?.source || null,
      });
    }

    stars.sort((a, b) => (b.marketCap || b.revenueTtm) - (a.marketCap || a.revenueTtm));
    return { sectors, sectorById, stars, starMap: new Map(stars.map((s) => [s.symbol, s])) };
  }

  function mountCosmos(root) {
    const canvas = root.querySelector('canvas');
    const side = root.querySelector('[data-cosmos-side]');
    const banner = root.querySelector('[data-cosmos-banner]');
    const sectorSelect = root.querySelector('[data-cosmos-sector]');
    const presetSelect = root.querySelector('[data-cosmos-preset]');
    const searchInput = root.querySelector('[data-cosmos-search]');
    const filterWrap = root.querySelector('[data-cosmos-filters]');
    if (!canvas || !side) return;
    const ctx = canvas.getContext('2d');

    let mode = 'galaxies';
    let sectorFilter = 'all';
    let signalFilter = 'all';
    let presetKey = 'bigtech';
    let query = '';
    let selectedSym = null;
    let hoveredSym = null;
    let universe = { sectors: [], sectorById: new Map(), stars: [], starMap: new Map() };

    const view = { zoom: 1, panX: 0, panY: 0, targetZoom: 1, targetPanX: 0, targetPanY: 0 };
    const posCache = new Map();
    let drawnStars = [];
    let drawnGalaxies = [];

    const updateBanner = () => {
      if (!banner) return;
      if (mode === 'galaxies') {
        banner.innerHTML = '<strong>GALAXY ORBITS · SEC SECTOR FUNDAMENTALS</strong><span>Each galaxy is an SEC industry · Inner core = strong catalysts &amp; cash flow · Outer rim = stretched or pressured</span>';
      } else if (mode === 'blackhole') {
        banner.innerHTML = '<strong style="color:#ff8fab">BLACK HOLE EVENT HORIZON · SEC RED FLAGS</strong><span>Pulls in companies with diverging profit vs. cash flow, negative OCF + debt, inventory bloat, or uncovered payouts</span>';
      } else if (mode === 'supernova') {
        banner.innerHTML = '<strong style="color:#7dffc4">SUPERNOVA CORE · FORWARD ALPHA SIGNALS</strong><span>Clusters companies with operating turnarounds, Form 4 insider buying, and share buybacks</span>';
      } else {
        banner.innerHTML = '<strong style="color:#ffc27a">PORTFOLIO &amp; BASKET GRAVITY X-RAY</strong><span>Separates tracked stocks into Supernova Catalysts (left), Steady Orbit (center), and SEC Risk Warnings (right)</span>';
      }
    };

    const renderFilterChips = () => {
      if (!filterWrap) return;
      if (mode === 'blackhole') {
        const items = [
          ['all', 'All SEC Risks'],
          ['earnings_cash', 'Profit vs. Cash Gap'],
          ['stress', 'Financial Stress'],
          ['working_capital', 'Inventory / Receivables Bloat'],
          ['payouts', 'Uncovered Payouts'],
          ['dilution', 'Share Dilution'],
        ];
        filterWrap.innerHTML = items.map(([k, l]) => `<button type="button" class="sec-cosmos-chip ${signalFilter === k ? 'is-on' : ''}" data-sig="${k}">${esc(l)}</button>`).join('');
      } else if (mode === 'supernova') {
        const items = [
          ['all', 'All Catalysts'],
          ['turnaround', 'Profit / Cash Turnarounds'],
          ['insider_buy', 'Insider Buying (Form 4)'],
          ['buybacks', 'Share Buybacks'],
        ];
        filterWrap.innerHTML = items.map(([k, l]) => `<button type="button" class="sec-cosmos-chip ${signalFilter === k ? 'is-on' : ''}" data-sig="${k}">${esc(l)}</button>`).join('');
      } else {
        filterWrap.innerHTML = '<span style="font-size:11.5px;color:var(--text-mid,#a9bddf)">Click any star or galaxy to inspect its SEC filings</span>';
      }
    };

    const isMatched = (node) => {
      if (sectorFilter !== 'all' && node.sectorId !== sectorFilter) return false;
      if (query && !node.symbol.toLowerCase().includes(query) && !node.name.toLowerCase().includes(query)) return false;
      if (signalFilter !== 'all') {
        if (mode === 'blackhole' && !node.risks.some((r) => r.code === signalFilter)) return false;
        if (mode === 'supernova' && !node.alphas.some((a) => a.code === signalFilter)) return false;
      }
      return true;
    };

    const renderSide = () => {
      const sym = selectedSym || hoveredSym;
      const node = sym ? universe.starMap.get(sym) : null;
      const presetSet = new Set(PRESETS[presetKey]?.symbols || []);

      if (node) {
        const peers = universe.stars
          .filter((s) => s.sectorId === node.sectorId && s.symbol !== node.symbol)
          .slice(0, 4);
        const sizeLabel = node.marketCap > 0 ? `Cap ${fmtMoney(node.marketCap)}` : node.revenueTtm > 0 ? `TTM Sales ${fmtMoney(node.revenueTtm)}` : '';

        side.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;border-bottom:1px solid rgba(120,220,255,0.12);padding-bottom:8px">
            <div>
              <h3><span class="sec-cosmos-sym">${esc(node.symbol)}</span> <span>${esc(node.name)}</span></h3>
              <p style="margin:3px 0 0;font-size:11.5px;color:var(--text-mid,#a9bddf)">${esc(node.sectorName)}${sizeLabel ? ' · ' + esc(sizeLabel) : ''}</p>
            </div>
            ${selectedSym === node.symbol ? '<button type="button" class="sec-cosmos-chip" data-unpin>Unpin ✕</button>' : ''}
          </div>
          <div class="sec-cosmos-kpis">
            <div class="sec-cosmos-kpi"><small>Sector P/E Med</small><strong>${node.industryPe ? node.industryPe.toFixed(1) + 'x' : '—'}</strong></div>
            <div class="sec-cosmos-kpi"><small>Sales YoY</small><strong style="color:${(node.revYoy ?? 0) >= 0 ? 'var(--sc-pos)' : 'var(--sc-neg)'}">${fmtPct(node.revYoy)}</strong></div>
            <div class="sec-cosmos-kpi"><small>SEC Gravity</small><strong style="color:${node.riskScore >= 1.2 ? 'var(--sc-neg)' : node.alphaScore >= 1.2 ? 'var(--sc-pos)' : 'inherit'}">${node.riskScore >= 1.2 ? '🕳️ ' + node.risks.length + ' Risk' : node.alphaScore >= 1.2 ? '🌟 ' + node.alphas.length + ' Alpha' : 'Stable'}</strong></div>
          </div>
          <div style="display:flex;flex-direction:column;gap:6px">
            ${node.risks.map((r) => `<div class="sec-cosmos-sig is-risk"><strong>🕳️ ${esc(r.title)}</strong>${esc(r.detail)}</div>`).join('')}
            ${node.alphas.map((a) => `<div class="sec-cosmos-sig is-alpha"><strong>🌟 ${esc(a.title)}</strong>${esc(a.detail)}</div>`).join('')}
            ${!node.risks.length && !node.alphas.length ? `<div class="sec-cosmos-sig"><strong>Stable Sector Orbit</strong>No SEC stress or dilution alerts in the latest period. Sector median P/E is ${node.industryPe ? node.industryPe.toFixed(1) + 'x' : '—'}.</div>` : ''}
          </div>
          ${peers.length ? `
            <p style="margin:4px 0 0;font-size:10.5px;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-low,#7489b3)">Sector Peers (${esc(node.sectorName)})</p>
            <div class="sec-cosmos-list">
              ${peers.map((p) => `<button type="button" class="sec-cosmos-row" data-pick="${esc(p.symbol)}"><span><strong>${esc(p.symbol)}</strong> <small style="color:var(--text-mid)">${esc(p.name.slice(0, 15))}</small></span><span style="color:var(--text-mid)">Sales ${fmtPct(p.revYoy)}</span><span style="color:${p.riskScore >= 1.2 ? 'var(--sc-neg)' : p.alphaScore >= 1.2 ? 'var(--sc-pos)' : 'var(--text-mid)'}">${p.riskScore >= 1.2 ? '⚠ Risk' : p.alphaScore >= 1.2 ? '★ Alpha' : 'Peer'}</span></button>`).join('')}
            </div>
          ` : ''}
          <div class="sec-cosmos-actions">
            <a class="sec-cosmos-btn" href="https://app.pulsarium.finance/?watch=${encodeURIComponent(node.symbol)}">Track ${esc(node.symbol)} in Cabinet →</a>
            ${node.source ? `<a class="sec-cosmos-btn ghost" href="${esc(node.source)}" target="_blank" rel="noopener noreferrer">SEC Filing ↗</a>` : ''}
          </div>
        `;
        return;
      }

      // Default mode summary list
      let list = universe.stars.filter(isMatched);
      if (mode === 'blackhole') {
        list = list.filter((s) => s.riskScore > 0).sort((a, b) => b.riskScore - a.riskScore);
      } else if (mode === 'supernova') {
        list = list.filter((s) => s.alphaScore > 0 && s.riskScore < 1.5).sort((a, b) => b.alphaScore - a.alphaScore);
      } else if (mode === 'portfolio') {
        list = list.filter((s) => presetSet.has(s.symbol)).sort((a, b) => b.riskScore - a.riskScore || b.alphaScore - a.alphaScore);
      } else {
        list = list.sort((a, b) => (b.alphaScore + b.riskScore) - (a.alphaScore + a.riskScore));
      }
      const top = list.slice(0, 7);

      side.innerHTML = `
        <div>
          <h3>${mode === 'blackhole' ? '🕳️ SEC Risk Black Hole' : mode === 'supernova' ? '🌟 Supernova SEC Catalysts' : mode === 'portfolio' ? '🛰️ Basket Gravity X-Ray' : '🌌 SEC Market Cosmos'}</h3>
          <p style="margin:4px 0 0;font-size:12px;color:var(--text-mid,#a9bddf)">Point at or click any star on the map to read its SEC 10-K/10-Q &amp; Form 4 diagnosis and compare sector peers.</p>
        </div>
        <p style="margin:2px 0 0;font-size:10.5px;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-low,#7489b3)">
          ${mode === 'blackhole' ? 'Strongest Black Hole Pull (SEC Red Flags)' : mode === 'supernova' ? 'Top Supernova Leaders' : mode === 'portfolio' ? 'Tracked Basket Diagnostics' : 'Highlighted SEC Movers'}
        </p>
        <div class="sec-cosmos-list">
          ${top.map((item) => `<button type="button" class="sec-cosmos-row" data-pick="${esc(item.symbol)}">
            <span><strong>${esc(item.symbol)}</strong> <small style="color:var(--text-mid)">${esc(item.name.slice(0, 16))}</small></span>
            <span style="color:var(--text-mid);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(mode === 'blackhole' ? (item.risks[0]?.title || item.sectorName) : mode === 'supernova' ? (item.alphas[0]?.title || item.sectorName) : item.sectorName)}</span>
            <span style="font-weight:700;color:${item.riskScore >= 1.2 ? 'var(--sc-neg)' : item.alphaScore >= 1.2 ? 'var(--sc-pos)' : 'var(--text-mid)'}">${item.riskScore >= 1.2 ? '⚠ ' + item.risks.length : item.alphaScore >= 1.2 ? '★ ' + item.alphas.length : fmtPct(item.revYoy)}</span>
          </button>`).join('')}
        </div>
        <div class="sec-cosmos-actions">
          <a class="sec-cosmos-btn" href="https://app.pulsarium.finance/">Check My Own Portfolio in Cabinet →</a>
          ${root.dataset.secCosmos === 'mini' ? '<a class="sec-cosmos-btn ghost" href="/market-cosmos/">Full Screen Cosmos ↗</a>' : ''}
        </div>
      `;
    };

    const targetWorldPos = (node, t) => {
      const sec = universe.sectorById.get(node.sectorId);
      const secAngle = sec ? sec.angle : 0;
      const presetSet = new Set(PRESETS[presetKey]?.symbols || []);

      if (mode === 'galaxies') {
        if (!sec) return { x: 0, y: 0 };
        const norm = node.alphaScore >= 1.4 && node.riskScore === 0 ? 0.28 : node.riskScore >= 1.4 ? 0.9 : 0.62;
        const r = sec.radius * norm;
        const dir = sec.revYoy >= 0 ? 1 : -0.6;
        const speed = clamp(Math.abs(sec.revYoy) * 0.0024, 0.02, 0.13) * dir;
        const a = node.orbitAngle + t * speed;
        return { x: sec.gx + Math.cos(a) * r, y: sec.gy + Math.sin(a) * r * 0.84 };
      }
      if (mode === 'blackhole') {
        if (node.riskScore > 0) {
          const sev = clamp(node.riskScore / 4.0, 0.2, 1);
          const r = lerp(205, 42, sev);
          const a = secAngle * 0.4 + node.orbitAngle + t * lerp(0.06, 0.24, sev);
          return { x: Math.cos(a) * r, y: Math.sin(a) * r * 0.82 };
        }
        const r = 278 + (node.orbitAngle / (Math.PI * 2)) * 75;
        const a = secAngle + (node.orbitAngle - Math.PI) * 0.16 + t * 0.014;
        return { x: Math.cos(a) * r, y: Math.sin(a) * r * 0.82 };
      }
      if (mode === 'supernova') {
        if (node.alphaScore > 0 && node.riskScore < 1.5) {
          const str = clamp(node.alphaScore / 3.8, 0.22, 1);
          const r = lerp(200, 36, str);
          const a = secAngle * 0.35 + node.orbitAngle - t * lerp(0.04, 0.14, str);
          return { x: Math.cos(a) * r, y: Math.sin(a) * r * 0.82 };
        }
        const r = 278 + (node.orbitAngle / (Math.PI * 2)) * 75;
        const a = secAngle + (node.orbitAngle - Math.PI) * 0.16 + t * 0.012;
        return { x: Math.cos(a) * r, y: Math.sin(a) * r * 0.82 };
      }
      // Portfolio / Preset X-Ray
      if (presetSet.has(node.symbol)) {
        if (node.riskScore >= 1.1) {
          const r = clamp(88 - node.riskScore * 14, 26, 82);
          const a = node.orbitAngle + t * 0.16;
          return { x: 225 + Math.cos(a) * r, y: Math.sin(a) * r * 0.85 };
        }
        if (node.alphaScore >= 1.2) {
          const r = clamp(88 - node.alphaScore * 14, 26, 82);
          const a = node.orbitAngle - t * 0.12;
          return { x: -225 + Math.cos(a) * r, y: Math.sin(a) * r * 0.85 };
        }
        const r = 32 + (node.orbitAngle / (Math.PI * 2)) * 52;
        const a = node.orbitAngle + t * 0.06;
        return { x: Math.cos(a) * r, y: Math.sin(a) * r * 0.85 };
      }
      const r = 305 + (node.orbitAngle / (Math.PI * 2)) * 55;
      const a = secAngle + (node.orbitAngle - Math.PI) * 0.15;
      return { x: Math.cos(a) * r, y: Math.sin(a) * r * 0.82 };
    };

    let start = performance.now();
    let last = start;

    function tick(now) {
      const dt = Math.min(0.08, (now - last) / 1000);
      last = now;
      const t = (now - start) / 1000;

      const rect = canvas.getBoundingClientRect();
      if (rect.width > 40 && rect.height > 40) {
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const cw = Math.round(rect.width * dpr);
        const ch = Math.round(rect.height * dpr);
        if (canvas.width !== cw || canvas.height !== ch) {
          canvas.width = cw;
          canvas.height = ch;
        }
        const W = rect.width;
        const H = rect.height;

        ctx.save();
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, W, H);

        const ease = 1 - Math.exp(-dt * 8);
        view.zoom = lerp(view.zoom, view.targetZoom, ease);
        view.panX = lerp(view.panX, view.targetPanX, ease);
        view.panY = lerp(view.panY, view.targetPanY, ease);

        const scale = Math.min(W / 820, H / 600) * view.zoom;
        const cx = W / 2 + view.panX;
        const cy = H / 2 + view.panY;
        const toScreen = (wx, wy) => ({ x: cx + wx * scale, y: cy + wy * scale });

        drawnGalaxies = [];
        if (mode === 'galaxies') {
          for (const sec of universe.sectors) {
            if (sectorFilter !== 'all' && sec.id !== sectorFilter) continue;
            const sc = toScreen(sec.gx, sec.gy);
            const sr = sec.radius * scale;
            drawnGalaxies.push({ sec, sx: sc.x, sy: sc.y, sr });

            const g = ctx.createRadialGradient(sc.x, sc.y, sr * 0.05, sc.x, sc.y, sr * 1.15);
            g.addColorStop(0, sec.color + '38');
            g.addColorStop(0.55, sec.color + '12');
            g.addColorStop(1, 'transparent');
            ctx.fillStyle = g;
            ctx.beginPath();
            ctx.arc(sc.x, sc.y, sr * 1.15, 0, Math.PI * 2);
            ctx.fill();

            ctx.strokeStyle = sec.color + '35';
            ctx.setLineDash([3, 4]);
            ctx.beginPath();
            ctx.ellipse(sc.x, sc.y, sr * 0.45, sr * 0.45 * 0.84, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.font = '600 10px "Chakra Petch", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillStyle = '#e6f1ff';
            ctx.fillText(sec.name, sc.x, sc.y - sr * 0.88 - 4);
          }
        } else if (mode === 'blackhole') {
          const bh = toScreen(0, 0);
          const hR = 34 * scale;
          const dR = 215 * scale;
          const dg = ctx.createRadialGradient(bh.x, bh.y, hR * 0.85, bh.x, bh.y, dR);
          dg.addColorStop(0, 'rgba(255, 60, 105, 0.6)');
          dg.addColorStop(0.3, 'rgba(255, 107, 142, 0.22)');
          dg.addColorStop(1, 'transparent');
          ctx.fillStyle = dg;
          ctx.beginPath();
          ctx.arc(bh.x, bh.y, dR, 0, Math.PI * 2);
          ctx.fill();

          ctx.fillStyle = '#020206';
          ctx.beginPath();
          ctx.arc(bh.x, bh.y, hR, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = '#ff6b8e';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(bh.x, bh.y, hR, 0, Math.PI * 2);
          ctx.stroke();
        } else if (mode === 'supernova') {
          const sn = toScreen(0, 0);
          const cR = 210 * scale;
          const sg = ctx.createRadialGradient(sn.x, sn.y, 4, sn.x, sn.y, cR);
          sg.addColorStop(0, 'rgba(61, 255, 162, 0.52)');
          sg.addColorStop(0.35, 'rgba(0, 240, 255, 0.2)');
          sg.addColorStop(1, 'transparent');
          ctx.fillStyle = sg;
          ctx.beginPath();
          ctx.arc(sn.x, sn.y, cR, 0, Math.PI * 2);
          ctx.fill();
        } else if (mode === 'portfolio') {
          const zones = [
            { wx: -225, wy: 0, r: 98, title: 'SUPERNOVA CATALYSTS', color: '#3dffa2' },
            { wx: 0, wy: 0, r: 98, title: 'STEADY ORBIT', color: '#00f0ff' },
            { wx: 225, wy: 0, r: 98, title: 'SEC RISK BLACK HOLE', color: '#ff6b8e' },
          ];
          for (const z of zones) {
            const sc = toScreen(z.wx, z.wy);
            const sr = z.r * scale;
            ctx.strokeStyle = z.color + '55';
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.ellipse(sc.x, sc.y, sr, sr * 0.85, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.font = '700 10px "Chakra Petch", sans-serif';
            ctx.fillStyle = z.color;
            ctx.textAlign = 'center';
            ctx.fillText(z.title, sc.x, sc.y - sr * 0.85 - 8);
          }
        }

        const presetSet = new Set(PRESETS[presetKey]?.symbols || []);
        const posEase = 1 - Math.exp(-dt * 5.5);
        drawnStars = [];

        for (const node of universe.stars) {
          const target = targetWorldPos(node, t);
          const prev = posCache.get(node.symbol);
          const cur = prev ? { x: lerp(prev.x, target.x, posEase), y: lerp(prev.y, target.y, posEase) } : target;
          posCache.set(node.symbol, cur);
          const sc = toScreen(cur.x, cur.y);
          const sr = node.baseRadius * Math.pow(view.zoom, 0.35);
          drawnStars.push({ node, sx: sc.x, sy: sc.y, sr, matched: isMatched(node) });
        }

        const activeSym = hoveredSym || selectedSym;
        const activeDrawn = activeSym ? drawnStars.find((d) => d.node.symbol === activeSym) : null;
        if (activeDrawn) {
          const peers = drawnStars.filter((d) => d.node.sectorId === activeDrawn.node.sectorId && d.node.symbol !== activeSym && d.matched).slice(0, 4);
          for (const p of peers) {
            ctx.strokeStyle = 'rgba(0, 240, 255, 0.35)';
            ctx.setLineDash([3, 3]);
            ctx.beginPath();
            ctx.moveTo(activeDrawn.sx, activeDrawn.sy);
            ctx.lineTo(p.sx, p.sy);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }

        for (const d of drawnStars) {
          const { node, sx, sy, sr, matched } = d;
          const isSel = node.symbol === selectedSym || node.symbol === hoveredSym;
          const tracked = presetSet.has(node.symbol);
          let alpha = matched ? 0.92 : 0.12;
          if (mode === 'portfolio' && !tracked) alpha *= 0.25;

          const color = node.riskScore >= 1.2 && (mode === 'blackhole' || node.riskScore >= node.alphaScore)
            ? '#ff6b8e'
            : node.alphaScore >= 1.2
              ? '#3dffa2'
              : '#7fa8e8';

          ctx.globalAlpha = alpha;
          ctx.fillStyle = isSel ? '#ffffff' : color;
          ctx.beginPath();
          ctx.arc(sx, sy, sr, 0, Math.PI * 2);
          ctx.fill();

          if (tracked && mode === 'portfolio') {
            ctx.strokeStyle = '#ffc27a';
            ctx.lineWidth = 1.6;
            ctx.beginPath();
            ctx.arc(sx, sy, sr + 3, 0, Math.PI * 2);
            ctx.stroke();
          }

          if (isSel || (matched && (sr >= 6.2 || (mode === 'portfolio' && tracked)))) {
            ctx.font = `${isSel ? '700 11px' : '600 9.5px'} "JetBrains Mono", monospace`;
            ctx.textAlign = 'center';
            ctx.fillStyle = isSel ? '#ffffff' : '#d8e8ff';
            ctx.fillText(node.symbol, sx, sy - sr - 4);
          }
        }

        ctx.globalAlpha = 1;
        ctx.restore();
      }
      requestAnimationFrame(tick);
    }

    // Event bindings
    root.addEventListener('click', (e) => {
      const modeBtn = e.target.closest('[data-cosmos-mode]');
      if (modeBtn) {
        mode = modeBtn.dataset.cosmosMode;
        signalFilter = 'all';
        root.querySelectorAll('[data-cosmos-mode]').forEach((b) => b.classList.toggle('is-on', b === modeBtn));
        if (presetSelect) presetSelect.hidden = mode !== 'portfolio';
        view.targetZoom = 1; view.targetPanX = 0; view.targetPanY = 0;
        updateBanner(); renderFilterChips(); renderSide();
        return;
      }
      const sigBtn = e.target.closest('[data-sig]');
      if (sigBtn) {
        signalFilter = sigBtn.dataset.sig;
        renderFilterChips(); renderSide();
        return;
      }
      const pickBtn = e.target.closest('[data-pick]');
      if (pickBtn) {
        selectedSym = pickBtn.dataset.pick;
        renderSide();
        return;
      }
      if (e.target.closest('[data-unpin]')) {
        selectedSym = null;
        renderSide();
        return;
      }
      const zBtn = e.target.closest('[data-zoom]');
      if (zBtn) {
        const act = zBtn.dataset.zoom;
        if (act === 'in') view.targetZoom = clamp(view.targetZoom * 1.25, 0.65, 3.0);
        else if (act === 'out') view.targetZoom = clamp(view.targetZoom * 0.8, 0.65, 3.0);
        else { view.targetZoom = 1; view.targetPanX = 0; view.targetPanY = 0; selectedSym = null; renderSide(); }
      }
    });

    if (sectorSelect) {
      sectorSelect.addEventListener('change', () => {
        sectorFilter = sectorSelect.value;
        renderSide();
      });
    }
    if (presetSelect) {
      presetSelect.addEventListener('change', () => {
        presetKey = presetSelect.value;
        renderSide();
      });
    }
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        query = searchInput.value.trim().toLowerCase();
        const exact = universe.starMap.get(query.toUpperCase());
        if (exact) selectedSym = exact.symbol;
        renderSide();
      });
    }

    let dragging = false, startX = 0, startY = 0, moved = false;
    const hitStar = (clientX, clientY) => {
      const r = canvas.getBoundingClientRect();
      const px = clientX - r.left, py = clientY - r.top;
      let best = null, bestD = 15;
      for (const d of drawnStars) {
        if (!d.matched) continue;
        const dist = Math.hypot(px - d.sx, py - d.sy);
        if (dist < bestD) { bestD = dist; best = d.node; }
      }
      return best;
    };

    canvas.addEventListener('pointerdown', (e) => {
      dragging = true; moved = false;
      startX = e.clientX - view.targetPanX; startY = e.clientY - view.targetPanY;
    });
    canvas.addEventListener('pointermove', (e) => {
      if (dragging) {
        const nx = e.clientX - startX, ny = e.clientY - startY;
        if (Math.hypot(nx - view.targetPanX, ny - view.targetPanY) > 4) moved = true;
        view.targetPanX = clamp(nx, -450, 450); view.targetPanY = clamp(ny, -350, 350);
        return;
      }
      const hit = hitStar(e.clientX, e.clientY);
      const nextHov = hit ? hit.symbol : null;
      if (nextHov !== hoveredSym) {
        hoveredSym = nextHov;
        canvas.style.cursor = hit ? 'pointer' : 'grab';
        renderSide();
      }
    });
    window.addEventListener('pointerup', (e) => {
      if (!dragging) return;
      dragging = false;
      if (!moved && e.target === canvas) {
        const hit = hitStar(e.clientX, e.clientY);
        selectedSym = hit ? (selectedSym === hit.symbol ? null : hit.symbol) : null;
        renderSide();
      }
    });
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      view.targetZoom = clamp(view.targetZoom * (e.deltaY < 0 ? 1.15 : 0.87), 0.65, 3.0);
    }, { passive: false });

    // Load data and boot
    Promise.all([
      loadCachedOrFetch(SECTOR_CACHE, 'sec_sector_analytics?id=eq.global&select=payload'),
      loadCachedOrFetch(RADAR_CACHE, 'sec_insider_radar?id=eq.global&select=payload'),
      loadCachedOrFetch(VAL_CACHE, 'industry_valuation?id=eq.global&select=payload'),
    ]).then(([secSnap, radarSnap, valSnap]) => {
      universe = buildUniverse(secSnap, radarSnap, valSnap);
      root.hidden = false;
      if (sectorSelect) {
        sectorSelect.innerHTML = '<option value="all">All ' + universe.sectors.length + ' SEC Sectors</option>' +
          universe.sectors.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${s.revYoy >= 0 ? '+' : ''}${s.revYoy.toFixed(1)}%)</option>`).join('');
      }
      const cGal = root.querySelector('[data-count-galaxies]');
      const cBh = root.querySelector('[data-count-blackhole]');
      const cSn = root.querySelector('[data-count-supernova]');
      if (cGal) cGal.textContent = String(universe.sectors.length);
      if (cBh) cBh.textContent = String(universe.stars.filter((s) => s.riskScore >= 1.2).length);
      if (cSn) cSn.textContent = String(universe.stars.filter((s) => s.alphaScore >= 1.2 && s.riskScore < 1.5).length);

      updateBanner();
      renderFilterChips();
      renderSide();
      requestAnimationFrame(tick);
    });
  }

  function boot() {
    document.querySelectorAll('[data-sec-cosmos]').forEach(mountCosmos);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
