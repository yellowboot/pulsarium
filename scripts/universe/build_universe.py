"""Market Universe page (/universe/): the 3D prototype turned into the published page.

    python scripts/universe/build_universe.py        # writes universe/index.html

Input: scripts/universe/prototype.html, the prototype exactly as it was designed (kept untouched as the source of the
look and the animation). This script applies the changes on top of it as a list of asserted replacements, so a
prototype that changed shape stops the build instead of publishing half a fix:
  - the page no longer carries its data: it reads window.UNIVERSE_DATA from /universe/data.js, which
    scripts/universe/build_data.py rebuilds from our SEC snapshots and end-of-day prices (the prototype's hand-typed
    market values, its "upside" percentage with no valuation behind it and sector averages shown as company figures
    are gone with the embedded data);
  - labels that read like advice are renamed, simulations are labelled as such;
  - the look: a darker sky, the black hole (flat shadow, edge-on disk, softened lens edge);
  - embedding: a picture-only mode for the news page, messages from the cabinet (theme, the user's tickers).
Run it only when the page itself changes; the data refreshes without it.
"""
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
html = io.open(os.path.join(HERE, "prototype.html"), encoding="utf8").read()

# ---------- data: out of the page, into /universe/data.js ----------
for name, field in (("RAW_COMPANIES", "companies"), ("RAW_GALAXIES", "galaxies"), ("REAL_MARKET", "market")):
    found = re.search(r"const %s = [\[{].*?[\]}];\n" % name, html, re.S)
    assert found, name
    html = html[:found.start()] + f"const {name} = window.UNIVERSE_DATA.{field};\n" + html[found.end():]


def swap(old: str, new: str, count: int = 1) -> None:
    global html
    found = html.count(old)
    assert found == count, f"expected {count}, found {found}: {old[:90]}"
    html = html.replace(old, new)


# ---------- page head and labels ----------
swap("<title>SEC Market Gravity (SFX Preview) — 3D WebGL Cosmos & Fundamental Forecasting</title>",
     "<title>Market Universe — SEC Filings as a 3D Map of the Stock Market | Pulsarium</title>\n"
     '<meta name="description" content="Large US companies as stars, their sectors as galaxies: revenue growth, '
     'margins and cash-flow signals from SEC filings, with real end-of-day prices and correlations. Free, no account.">\n'
     '<link rel="canonical" href="https://pulsarium.finance/universe/">\n'
     '<link rel="icon" href="/favicon.ico">\n<script src="/universe/data.js"></script>')
swap('<h1 class="wordmark">SEC Market Gravity</h1>', '<h1 class="wordmark">Market Universe</h1>')
swap("Portfolio & Watchlist Gravity X-Ray: Separates tracked stocks into Supernova Leaders (left), Fair Orbit (center), and SEC Risk Black Hole (right)",
     "Watchlist X-Ray: separates a list of stocks into growth signals (left), neutral (centre) and SEC risk signals (right)")
swap("🔬 Portfolio X-Ray <span", "🔬 Watchlist X-Ray <span")
swap('<div class="sc-up-lbl">SEC Fair-Value Upside</div>', '<div class="sc-up-lbl">SEC Revenue, Year on Year</div>')
swap("<span>Alpha</span>", "<span>Growth signals</span>")
swap("<span>Risk Pull</span>", "<span>Risk signals</span>")
swap('title="What-If Systemic Crash (+95% stress shock on real betas)">Crash Shock</button>',
     'title="A what-if simulation, not a forecast: +95% stress shock on real betas">Crash Simulation</button>')
swap('<div class="legend" id="legend" role="group" aria-label="Sector Galaxies: click to fly"></div>',
     '<div class="legend" id="legend" role="group" aria-label="Sector Galaxies: click to fly"></div>\n'
     '    <p class="pz-note" style="margin:2px 0 0;font:500 9px/1.4 var(--font-mono);letter-spacing:.03em;color:var(--muted-2);'
     'text-align:center;pointer-events:auto">Figures from SEC filings and end-of-day prices. Stress, crash and shockwave '
     'modes are simulations, not forecasts. Not investment advice. '
     '<a href="/" style="color:var(--teal);text-decoration:none">pulsarium.finance</a></p>')

# ---------- script: the "upside" percentage is gone; real figures stand in its place ----------
swap("if (a.role === 'black_hole_risk' || (a.blackHolePull || 0) >= 52 || (a.upsidePct || 0) <= -12) return 2; // Right: SEC Risk Black Hole",
     "if (a.role === 'black_hole_risk' || (a.blackHolePull || 0) >= 52) return 2; // Right: SEC risk signals")
swap("if (a.role === 'supernova' || (a.upsidePct || 0) >= 14) return 0; // Left: Supernova & Undervalued",
     "if (a.role === 'supernova') return 0; // Left: growth signals")
swap("return 1; // Center: Fair / Premium Orbit", "return 1; // Centre: neutral")
swap("const up = a.upsidePct || 0;\n  const upEl = $('#scUpside');\n  upEl.textContent = (up >= 0 ? '+' : '') + nf1.format(up) + '%';\n"
     "  upEl.className = 'sc-up-val ' + (up >= 0 ? 'pos' : 'neg');",
     "const up = a.revYoY;\n  const upEl = $('#scUpside');\n  upEl.textContent = up == null ? 'n/a' : (up >= 0 ? '+' : '') + nf1.format(up) + '%';\n"
     "  upEl.className = 'sc-up-val ' + ((up || 0) >= 0 ? 'pos' : 'neg');")
swap("const sup = clamp(Math.round(a.supernovaScore || 25), 0, 100);", "const sup = clamp(Math.round(a.supernovaScore || 0), 0, 100);")
swap("const rsk = clamp(Math.round(a.blackHolePull || 15), 0, 100);", "const rsk = clamp(Math.round(a.blackHolePull || 0), 0, 100);")
swap("""    <div class="sc-cell"><div class="sc-cell-k">SEC Rev YoY</div><div class="sc-cell-v" style="color:${(a.revYoY||0)>=0?'var(--positive)':'var(--negative)'}">${revYoY}</div></div>""",
     """    <div class="sc-cell"><div class="sc-cell-k">Day After Q3 Report</div><div class="sc-cell-v" style="color:${a.earnMove==null?'var(--muted)':a.earnMove>=0?'var(--positive)':'var(--negative)'}">${a.earnMove==null?'N/A':(a.earnMove>=0?'+':'')+nf1.format(a.earnMove)+'%'} <small style="font-size:9px;color:var(--muted)">${a.earnBeat===1?'beat':a.earnBeat===-1?'missed':''}</small></div></div>""")
swap("{type: 'bullish', title: 'SEC 10-Q Operating Momentum', detail: `Revenue ${revYoY} YoY with operating margin delta ${opDelta}.`}",
     "{type: 'neutral', title: 'Latest SEC Report', detail: a.revYoY != null ? `Revenue ${revYoY} year on year, operating margin ${opDelta}. No signals flagged.` : 'No year-on-year figures for this company in our SEC summary yet.'}")
swap("${s.type==='bullish'?'BULLISH':'SEC SIGNAL'}", "${s.type==='bullish'?'POSITIVE':'SEC SIGNAL'}")
swap("const pup = (pa.upsidePct >= 0 ? '+' : '') + nf1.format(pa.upsidePct || 0) + '%';",
     "const pup = pa.revYoY != null ? (pa.revYoY >= 0 ? '+' : '') + nf1.format(pa.revYoY) + '% rev' : '';")
swap("""<span style="color:${(pa.upsidePct||0)>=0?'var(--positive)':'var(--negative)'}">${pup}</span></button>`;""",
     """<span style="color:${(pa.revYoY||0)>=0?'var(--positive)':'var(--negative)'}">${pup}</span></button>`;""")
swap("'Post-Earnings Coiled Spring'", "'Beat Forecast, Stock Fell'", 2)
swap("'Beat Forecast, Stock Fell': 'Coiled Springs'", "'Beat Forecast, Stock Fell': 'Beat & Fell'")
swap("""<span class="dr-val pos">+${nf1.format(a.upsidePct||0)}%</span>""",
     """<span class="dr-val ${(a.revYoY||0)>=0?'pos':'neg'}" title="SEC revenue, year on year">${a.revYoY!=null?(a.revYoY>=0?'+':'')+nf1.format(a.revYoY)+'% rev':'n/a'}</span>""")
swap("""<span class="dr-val neg">${nf1.format(a.upsidePct||0)}%</span>""",
     """<span class="dr-val neg" title="Sum of the risk signals' weights, 0-100">risk ${a.blackHolePull||0}</span>""")
swap("""      const up = (a.upsidePct >= 0 ? '+' : '') + nf1.format(a.upsidePct || 0) + '%';
      const sig = (a.signals && a.signals[0]) ? a.signals[0].detail : `SEC Rev YoY +${nf1.format(a.revYoY||0)}%`;""",
     """      const up = a.revYoY != null ? (a.revYoY >= 0 ? '+' : '') + nf1.format(a.revYoY) + '% rev' : 'n/a';
      const sig = (a.signals && a.signals[0]) ? a.signals[0].detail : (a.revYoY != null ? `SEC revenue ${a.revYoY>=0?'+':''}${nf1.format(a.revYoY)}% year on year` : 'No signals flagged');""")
swap("""<span class="dr-val ${(a.upsidePct||0)>=0?'pos':'neg'}">${up}</span>""",
     """<span class="dr-val ${(a.revYoY||0)>=0?'pos':'neg'}" title="SEC revenue, year on year">${up}</span>""")
swap("""    const up = (a.upsidePct >= 0 ? '+' : '') + nf1.format(a.upsidePct || 0) + '%';
    return `<div class="s-item" data-idx="${i}"><div><b>${a.ticker}</b>${a.name}</div><span style="color:${(a.upsidePct||0)>=0?'var(--positive)':'var(--negative)'}">${up}</span></div>`;""",
     """    const up = a.r20d != null ? (a.r20d >= 0 ? '+' : '') + nf1.format(a.r20d) + '% 20D' : '';
    return `<div class="s-item" data-idx="${i}"><div><b>${a.ticker}</b>${a.name}</div><span style="color:${(a.r20d||0)>=0?'var(--positive)':'var(--negative)'}" title="Price change over 20 trading days">${up}</span></div>`;""")
swap("""const up = a.upsidePct != null ? `<em class="${a.upsidePct>=0?'pos':'neg'}">${a.upsidePct>=0?'+':''}${Math.round(a.upsidePct)}%</em>` : '';""",
     """const up = a.r20d != null ? `<em class="${a.r20d>=0?'pos':'neg'}" title="Price change over 20 trading days">${a.r20d>=0?'+':''}${Math.round(a.r20d)}%</em>` : '';""")
swap("  const up = (a.upsidePct >= 0 ? '+' : '') + nf1.format(a.upsidePct || 0) + '%';\n  const roleTxt", "  const roleTxt")
swap("""<span>SEC Fair Upside</span><span style="color:${(a.upsidePct||0)>=0?'var(--positive)':'var(--negative)'}">${up}</span>""",
     """<span>SEC Signals</span><span>${(a.signals||[]).length ? a.signals.length + ' flagged' : 'none flagged'}</span>""")
swap("`SEC Rev YoY +${nf1.format(a.revYoY||0)}%`", "(a.revYoY != null ? `SEC revenue ${a.revYoY>=0?'+':''}${nf1.format(a.revYoY)}% year on year` : 'No year-on-year figures yet')")

# ---------- script: plain names ----------
swap("`🔬 Portfolio Gravity X-Ray (${trackedAssets.length})`", "`🔬 Watchlist X-Ray (${trackedAssets.length})`")
swap("{id: 'portfolio', lbl: `My Portfolio`}", "{id: 'portfolio', lbl: `Sample Watchlist`}")
swap("lbl: `◆ Fair (${fairCnt})`", "lbl: `◆ Neutral (${fairCnt})`")
swap("◆ Fair Orbit</span>`", "◆ Neutral</span>`")
swap('"Supernovas", "Black Hole Risks", or "Portfolio X-Ray"', '"Supernovas", "Black Hole Risks", or "Watchlist X-Ray"')
swap("to simulate a -3σ panic shock", "to simulate (not forecast) a -3σ panic shock")
swap("{title: 'SUPERNOVA & UNDERVALUED', sub: 'Strong SEC Catalysts / Low P/E',", "{title: 'GROWTH SIGNALS', sub: 'Positive Signals in SEC Filings',")
swap("{title: 'FAIR / PREMIUM ORBIT', sub: 'Steady Fundamentals',", "{title: 'NEUTRAL ORBIT', sub: 'No Signals Flagged',")
swap("'SUPERNOVA CORE · FORWARD ALPHA SIGNALS'", "'SUPERNOVA CORE · GROWTH SIGNALS'")
swap("{title: 'SAFE OUTER HALO', sub: 'Healthy FCF & Steady Orbit Stocks',", "{title: 'OUTER HALO', sub: 'No Risk Signals Flagged',")

swap("""  supabaseTables: ['portfolios', 'portfolio_transactions', 'watchlist_items', 'instruments', 'screener_companies', 'sec_sector_analytics'],
  edgeFunction: 'market-data',
""", "")
swap("+ '% YoY') : '+9.5% YoY';", "+ '% YoY') : 'n/a';")
swap("nf1.format(gData.fcfPosPct) + '%' : '78%';", "nf1.format(gData.fcfPosPct) + '%' : 'n/a';")
swap("+ ' pp') : '+1.2 pp';", "+ ' pp') : 'n/a';")

# ---------- look: a slightly darker sky and a softer, wider accretion disk ----------
swap("--bg: #04050c;", "--bg: #020308;")
swap("--bg-deep: #03040a;", "--bg-deep: #010206;")
swap("const BG_CALM = new THREE.Color('#04050c'), BG_HOT = new THREE.Color('#0d0308');",
     "const BG_CALM = new THREE.Color('#020308'), BG_HOT = new THREE.Color('#090206');")
swap("new THREE.RingGeometry(1.5, 3.4, 160, 6)", "new THREE.RingGeometry(1.3, 6.5, 220, 14)")
# the disk: soft inner edge, a long ragged fade outwards, sheared streaks, the approaching side brighter and bluer
swap("""  void main(){ float r=length(vP); float t=clamp((r-1.5)/1.9,0.0,1.0); float a=atan(vP.y,vP.x);
    float sw=a/6.2831+uTime*0.4/r; float n=texture2D(uNoise,vec2(sw*3.0,t*1.4-uTime*0.02)).r; float n2=texture2D(uNoise,vec2(sw*7.0+0.3,t*3.0)).r;
    vec3 hot=mix(vec3(1.0,0.96,0.88),vec3(1.0,0.56,0.2),smoothstep(0.0,0.4,t)); hot=mix(hot,vec3(0.6,0.1,0.05),smoothstep(0.4,1.0,t));
    float dop=0.45+1.0*(0.5+0.5*sin(a)); float al=pow(1.0-t,1.4)*smoothstep(0.0,0.05,t)*(0.35+0.8*n*n2*1.6)*dop*uI;
    gl_FragColor=vec4(hot*al,1.0); }`}));""",
     """  void main(){ float r=length(vP); float t=clamp((r-1.3)/5.2,0.0,1.0); float a=atan(vP.y,vP.x);
    float sw=a/6.2831+uTime*0.55/pow(r,1.5);
    float n=texture2D(uNoise,vec2(sw*2.0,r*0.55-uTime*0.015),-8.0).r; float n2=texture2D(uNoise,vec2(sw*9.0+0.3,r*1.9),-8.0).r; float n3=texture2D(uNoise,vec2(sw*23.0+0.7,r*4.0+uTime*0.01),-8.0).r;
    float streak=0.1+2.6*n*n2+1.1*n2*n3;
    float inner=smoothstep(0.0,0.14,t); float fall=exp(-t*4.2); float rim=1.0-smoothstep(0.3+0.45*n,0.95,t);
    float ds=-cos(a); float dop=pow(1.0+0.38*ds,3.0)*0.62;
    vec3 hot=mix(vec3(1.0,0.88,0.7),vec3(1.0,0.6,0.25),smoothstep(0.0,0.2,t)); hot=mix(hot,vec3(0.72,0.15,0.05),smoothstep(0.2,0.8,t));
    hot=mix(hot,hot*vec3(0.85,0.96,1.25),clamp(ds,0.0,1.0)*0.35);
    float al=inner*fall*rim*streak*dop*uI*2.0; al=al/(1.0+1.1*al);
    gl_FragColor=vec4(hot*al,1.0); }`}));""")
# the camera-facing glow: a thin photon ring, the far side of the disk bent over and under the shadow, a faint halo
swap("""void main(){ float d=length(vUv*2.0-1.0); float ring=exp(-pow((d-0.345)/0.018,2.0))*1.4; float halo=d>0.33?exp(-(d-0.33)*7.0)*0.35:0.0;
    gl_FragColor=vec4(vec3(1.0,0.7,0.45)*(ring+halo)*uI*(1.0-smoothstep(0.85,1.0,d)),1.0); }`}));""",
     # no atan() here: at the centre pixel atan(0,0) is NaN, and one NaN pixel blacks out the whole bloom pass
     """void main(){ vec2 q=vUv*2.0-1.0; float d=length(q); float up=abs(q.y)/max(d,0.0001);
    float g1=(d-0.345)/0.011, g2=(d-0.405)/0.05;
    float ring=exp(-g1*g1)*0.5; float tw=abs(q.y*0.9689-q.x*0.2474)/max(d,0.0001); float arc=smoothstep(0.338,0.36,d)*exp(-(d-0.355)*9.0)*(0.1+1.05*tw*tw);
    float halo=d>0.335?exp(-(d-0.335)*6.5)*0.12:0.0;
    vec3 col=vec3(1.0,0.76,0.5)*ring+mix(vec3(1.0,0.86,0.66),vec3(1.0,0.5,0.2),smoothstep(0.36,0.5,d))*arc+vec3(1.0,0.6,0.3)*halo;
    gl_FragColor=vec4(col*uI*(1.0-smoothstep(0.8,1.0,d)),1.0); }`}));""")

# the lens pass clamped every pixel near the hole to one ring of the picture: a flat, hard-edged plate with radial
# streaks (the "glowing disk with hard edges"). A plain point-lens mapping instead: no plateau, nothing stretched.
# the prototype's lens, with one change: its outer edge. A hard max() made the smeared plate end on a sharp line;
# a soft maximum lets the plate fade into the real sky over about a fifth of the lens radius.
swap("float rr=max(r-k*fall,rs*1.03);",
     "float lo=rs*1.03, sf=rs*0.22, x=(r-k*fall-lo)/sf; float rr=x>12.0?r-k*fall:lo+sf*log(1.0+exp(x));")

# the disk keeps one pose towards the viewer, nearly edge-on with a slight roll (the classic picture): its near side
# crosses in front of the shadow, and the glow above carries the bent image of its far side
swap("  photon.quaternion.copy(camera.quaternion);",
     "  photon.quaternion.copy(camera.quaternion); disk.quaternion.copy(camera.quaternion).multiply(DISK_POSE);")
swap("disk.rotation.x = -Math.PI/2 + 0.24; bh.add(disk);",
     "const DISK_POSE = new THREE.Quaternion().setFromEuler(new THREE.Euler(-1.37, 0, 0.25, 'ZXY')); bh.add(disk);")

# the lens pass painted the whole shadow black in screen space, which also erased the disk's near side crossing
# in front of it; bright pixels (the disk) now stay, only the dim bloom haze over the shadow is darkened
swap("vec4 c=texture2D(tDiffuse,uv); gl_FragColor=vec4(c.rgb*shade,c.a); }`",
     "vec4 c=texture2D(tDiffuse,uv); shade=max(shade,smoothstep(0.4,0.85,dot(c.rgb,vec3(0.3,0.5,0.2)))); gl_FragColor=vec4(c.rgb*shade,c.a); }`")

# the shadow was a sphere and its glow a flat camera-facing ring: seen off-centre or close up, a sphere's outline
# shifts and stretches while the ring does not, so the two circles slid apart. The shadow is now a flat
# camera-facing disc in the same plane as the ring.
swap("const horizon = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32),", "const horizon = new THREE.Mesh(new THREE.CircleGeometry(1, 96),")
swap("multiply(DISK_POSE);", "multiply(DISK_POSE); horizon.quaternion.copy(camera.quaternion);")

# the lens circle trailed the hole on fast camera moves: it was projected with last frame's camera matrices, and its
# radius used the straight-line distance to the scene centre, not the hole's depth along the view axis
swap("  v3.copy(bh.position).add(root.position).project(camera);", "  camera.updateMatrixWorld(); v3.copy(bh.position).add(root.position).project(camera);")
swap("  const d = camera.position.distanceTo(root.position);", "  const d = -pv3.copy(bh.position).add(root.position).applyMatrix4(camera.matrixWorldInverse).z;")
# More stress made the hole dimmer in the risk view: its heat was pinned at 0.92 there while the bloom is strongest
# when the market is calm. Stress now heats the hole in that view too, and the bloom follows the hole's heat.
swap("cosmosMode === 'risks' ? 0.92 : heat;", "cosmosMode === 'risks' ? 0.42 + 0.5 * heat : heat;")
swap("bloom.strength = lerp(0.88, 0.52, heat);",
     "const glow = Math.max(heat, cosmosMode === 'risks' || cosmosMode === 'xray' ? 0.8 : 0); bloom.strength = lerp(0.66, 0.44, glow);")
swap("bloom.threshold = 0.08 + 0.34 * heat;", "bloom.threshold = 0.13 + 0.33 * glow;")

# the tour said correlations put the power utilities and Oracle into the AI galaxy; the script places them there by hand
swap("— real empirical correlations group them together.", "— we placed them here because AI data centers drive their demand.")

# embed mode (/universe/?embed=1) for the small preview on the news page: the picture alone, no panels, and the
# mouse wheel left to the page it sits in
swap("<body>", "<body><script>if(/[?&]embed/.test(location.search))document.body.classList.add('immersive','embed');</script>")
swap("body.immersive .imm-exit { display: inline-block; }",
     "body.immersive .imm-exit { display: inline-block; } body.embed .imm-exit, body.embed .tip { display: none !important; }")
# wheel zoom: three r160's OrbitControls divides the wheel step by (devicePixelRatio | 0), which is 0 whenever the
# browser is zoomed below 100% (Nov 2026: one wheel click threw her camera to the far limit or into the hole). The
# controls' own zoom is switched off and the wheel moves the camera here: one click = one even 10% step, at any
# page zoom and with any mouse. Pinch zoom on touch screens still belongs to the controls (it has no such division).
swap("const controls = new OrbitControls(camera, canvas);",
     "const controls = new OrbitControls(camera, canvas); "
     "canvas.addEventListener('wheel', (e) => { if (mode === 'fly' || document.body.classList.contains('embed')) return; e.preventDefault(); e.stopImmediatePropagation(); "
     "const unit = e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 100 : 1, step = Math.max(-1, Math.min(1, e.deltaY * unit / 100)); "
     "const L = camera.position.distanceTo(controls.target), next = Math.max(controls.minDistance, Math.min(controls.maxDistance, L * Math.pow(1.1, step))); "
     "camera.position.sub(controls.target).setLength(next).add(controls.target); if (!nav.tw) nav.userCam = true; }, {capture: true, passive: false});")

# messages from the pages that embed this one: the news preview (same site) asks the camera to fly to a galaxy,
# the cabinet hands over the user's own tickers for the X-Ray. Only these origins are listened to.
swap("{id: 'portfolio', lbl: `Sample Watchlist`}", "{id: 'portfolio', lbl: listLabel}")
swap("🔬 Watchlist X-Ray <span", "🔬 <span id=\"xrayLbl\">Watchlist X-Ray</span> <span")
swap("window.SEC_COSMOS_ADAPTER = {", """let listLabel = 'Sample Watchlist';
const EMBEDDERS = [location.origin, 'https://app.pulsarium.finance', 'http://localhost:5173'];
addEventListener('message', (event) => {
  if (!EMBEDDERS.includes(event.origin)) return;
  const m = event.data || {};
  if (m.type === 'universe:fly') flyTo(Number.isInteger(m.to) && m.to >= 0 && m.to < NS ? m.to : m.to === 'bh' ? 'bh' : 'overview', true);
  if (m.type === 'universe:theme' && (m.theme === 'neon' || m.theme === 'calm') && document.documentElement.dataset.colorTheme !== m.theme) $('#themeBtn').click();
  if (m.type === 'universe:tickers' && Array.isArray(m.tickers)){
    listLabel = 'My Portfolio'; $('#xrayLbl').textContent = 'Portfolio X-Ray';
    window.SEC_COSMOS_ADAPTER.setPortfolioTickers(m.tickers.map(String));
    event.source.postMessage({type: 'universe:matched', matched: customWatchlist.size, total: m.tickers.length}, event.origin);
  }
});
if (parent !== window) parent.postMessage({type: 'universe:ready'}, '*');
window.SEC_COSMOS_ADAPTER = {""")

# a way back: the page stands alone, so its top bar carries a link to the news page (hidden when the page sits
# inside another one, where the host page is the way back)
swap('<h1 class="wordmark">Market Universe</h1>',
     '<a class="pill-btn nav-back" href="/news/" title="Back to Pulsarium market news" style="text-decoration:none">← News</a><h1 class="wordmark">Market Universe</h1>')
swap("document.body.classList.add('immersive','embed');</script>",
     "document.body.classList.add('immersive','embed');if(parent!==window)document.documentElement.classList.add('framed');</script>")
swap("body.embed .imm-exit, body.embed .tip { display: none !important; }",
     "body.embed .imm-exit, body.embed .tip, .framed .nav-back { display: none !important; } .framed .pz-note a { pointer-events: none; }")

# strong risks and light flags. One mild signal (payouts above free cash flow at a healthy company that buys back a
# lot of stock, e.g. Mastercard at 26) stood in the same list, with the same warning sign, as companies running out of
# cash. From 40 points it is a strong risk; below that a light flag: an amber flag instead of the warning sign, its
# own label, and in the X-Ray it sits in the neutral group, not at the black hole.
swap("if (a.role === 'black_hole_risk' || (a.blackHolePull || 0) >= 52) return 2; // Right: SEC risk signals",
     "if (a.role === 'black_hole_risk' && (a.blackHolePull || 0) >= 40) return 2; // Right: strong SEC risk signals")
swap("a.role === 'black_hole_risk' ? `🕳️ ${a.riskType || 'Black Hole Risk'}`",
     "a.role === 'black_hole_risk' ? ((a.blackHolePull || 0) >= 40 ? `🕳️ ${a.riskType || 'Black Hole Risk'}` : `⚑ Light flag: ${a.riskType || 'SEC signal'}`)")
swap("a.role === 'black_hole_risk' ? `🕳️ ${a.riskType || 'SEC Risk'}`",
     "a.role === 'black_hole_risk' ? ((a.blackHolePull || 0) >= 40 ? `🕳️ ${a.riskType || 'SEC Risk'}` : `⚑ Light flag: ${a.riskType || 'SEC signal'}`)")
swap("""if (a.role === 'black_hole_risk') return `<span class="role-ico risk" title="Black Hole SEC Risk">⚠️</span>`;""",
     """if (a.role === 'black_hole_risk') return (a.blackHolePull || 0) >= 40 ? `<span class="role-ico risk" title="Strong SEC risk signals">⚠️</span>` : `<span class="role-ico risk" title="Light flag: one mild SEC signal" style="color:var(--warning)">⚑</span>`;""")
swap("""const rMark = a.role === 'black_hole_risk' ? `<span class="role-ico risk" title="SEC Risk">⚠️</span>`""",
     """const rMark = a.role === 'black_hole_risk' ? ((a.blackHolePull || 0) >= 40 ? `<span class="role-ico risk" title="Strong SEC risk signals">⚠️</span>` : `<span class="role-ico risk" title="Light flag: one mild SEC signal" style="color:var(--warning)">⚑</span>`)""")
swap("`⚠️ Black Hole Pull — SEC Red Flags (${riskAssets.length})`",
     "`⚠️ SEC Risk Signals: ${riskAssets.filter(a => (a.blackHolePull || 0) >= 40).length} strong · ${riskAssets.filter(a => (a.blackHolePull || 0) < 40).length} light flags`")
swap("""<span class="dr-val neg" title="Sum of the risk signals' weights, 0-100">risk ${a.blackHolePull||0}</span>""",
     """<span class="dr-val" style="color:${(a.blackHolePull||0)>=40?'var(--negative)':'var(--warning)'}" title="Sum of the risk signals' weights, 0-100. From 40: strong risk; below: a light flag">${(a.blackHolePull||0)>=40?'risk':'flag'} ${a.blackHolePull||0}</span>""")

assert "upsidePct" not in html and "Fair-Value" not in html and "Coiled" not in html
out = os.path.join(ROOT, "universe", "index.html")
io.open(out, "w", encoding="utf8", newline="\n").write(html)
print(f"{out}: {len(html)} chars")
