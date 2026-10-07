/* Financial signals share Sector Overview's prepared snapshot and read. */
(() => {
  'use strict';
  const overview=document.getElementById('financial-signals'), sheet=document.getElementById('financial-sheet');
  if(!overview||!sheet||typeof sheet.showModal!=='function') return;
  const titles={turnaround:'Financial turnaround',shares:'Buybacks & share growth',stress:'Financial stress'};
  const names={operating_turnaround:'Quarterly operating profit',cash_flow_improvement:'Quarterly operating cash flow',share_reduction:'Average basic common shares',share_increase:'Average basic common shares',buyback_cash:'Reported buyback cash',option_cash:'Option-exercise cash',issue_cash:'Share-issuance cash',negative_operating_cash:'Operating cash flow · TTM',cash_decline:'Cash and cash equivalents',debt_increase:'Current + noncurrent reported debt'};
  const q=s=>sheet.querySelector(s), body=q('.sector-sheet-body'), state=overview.querySelector('[data-financial-state]'), list=overview.querySelector('[data-financial-list]');
  const num=n=>typeof n==='number'&&Number.isFinite(n), count=n=>Number.isInteger(n)&&n>=0;
  const validDate=s=>/^\d{4}-\d{2}-\d{2}$/.test(s||'')&&Number.isFinite(Date.parse(s+'T12:00:00Z'));
  const date=s=>validDate(s)?new Date(s+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}):'Date unavailable';
  const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:1}).format(n);
  const amount=(s,n)=>s.unit==='shares'?new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(n):money(n);
  const signed=n=>`${n>0?'+':''}${n.toFixed(1)}%`;
  const tone=s=>['operating_turnaround','cash_flow_improvement','share_reduction'].includes(s.code)?'sector-up':['share_increase','negative_operating_cash','cash_decline','debt_increase'].includes(s.code)?'sector-down':'';
  const el=(tag,cls='',text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const source=s=>typeof s==='string'&&/^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/\d+\/\d{18}\/\d{10}-\d{2}-\d{6}-index\.html$/.test(s);
  let snapshot,industries={},group='turnaround',selected,opener,newsY,oldBody,oldHtml;
  function valid(data){return data?.version===1&&Number.isFinite(Date.parse(data.updated_at))&&['eligible','operating','cash_flow','shares','stress','full_histories'].every(k=>count(data.coverage?.[k]))&&Object.keys(titles).every(k=>count(data.counts?.[k])&&Array.isArray(data.groups?.[k])&&data.groups[k].every(r=>/^\d+$/.test(r.cik)&&typeof r.symbol==='string'&&typeof r.name==='string'&&typeof r.sector==='string'&&num(r.revenue_current)&&validDate(r.period_end)&&Array.isArray(r.signals)&&r.signals.length&&r.signals.every(s=>Object.hasOwn(names,s.code)&&typeof s.label==='string'&&['USD','shares'].includes(s.unit)&&num(s.current)&&(s.previous===undefined||num(s.previous))&&validDate(s.period_end)&&(s.change_pct===undefined||num(s.change_pct)))));}
  function setGroup(next){if(!titles[next])return;group=next;for(const root of[overview,sheet])root.querySelectorAll('[data-financial-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.financialTab===group)));if(snapshot){renderSummary();renderDetails();}body.scrollTop=0;}
  function renderSummary(){
    list.replaceChildren();const rows=snapshot.groups[group];
    if(!rows.length){list.append(el('p','financial-empty','No companies meet this screen in the covered data.'));return;}
    rows.slice(0,3).forEach(r=>{
      const b=el('button','financial-row'),s=r.signals[0];b.type='button';b.dataset.financialCompany=r.cik;b.setAttribute('aria-label',`Details for ${r.symbol}: ${s.label}`);
      const head=el('span','financial-row-head');head.append(el('span','financial-symbol',r.symbol),el('span',`financial-value ${tone(s)}`,s.unit==='shares'?signed(s.change_pct):amount(s,s.current)));
      b.append(head,el('span','financial-name',r.name),el('span','financial-reason',s.label),el('span','financial-date',`Quarter ended ${date(r.period_end)}`));b.addEventListener('click',()=>open(b,r.cik));list.append(b);
    });
  }
  function coverage(){const c=snapshot.coverage;if(group==='turnaround')return`${c.operating.toLocaleString('en-US')} companies with comparable operating profit · ${c.cash_flow.toLocaleString('en-US')} with quarterly cash-flow history.`;if(group==='shares')return`${c.shares.toLocaleString('en-US')} companies with comparable basic shares from the same report.`;return`${c.stress.toLocaleString('en-US')} non-financial companies with matching balance sheets and 12-month operating cash flow.`;}
  function evidence(s){
    const block=el('section','financial-evidence');block.append(el('h4','',s.label));const values=el('div',`financial-evidence-values ${tone(s)}`);
    if(num(s.previous))values.append(el('span','financial-previous',`${amount(s,s.previous)} →`));values.append(el('span','',amount(s,s.current)));if(num(s.change_pct))values.append(el('span','financial-value',`(${signed(s.change_pct)})`));
    block.append(values,el('p','',names[s.code]),el('p','',validDate(s.period_start)?`${date(s.period_start)} – ${date(s.period_end)}`:`As of ${date(s.period_end)}`));
    if(validDate(s.previous_end))block.append(el('p','',`Comparison: ${validDate(s.previous_start)?date(s.previous_start)+' – ':''}${date(s.previous_end)}`));
    if(s.derived)block.append(el('p','',s.code==='negative_operating_cash'?'Sum of four consecutive fiscal quarters.':'Standalone quarter derived from reported cumulative figures.'));
    if(Array.isArray(s.quarters))s.quarters.forEach(r=>{if(num(r.current)&&validDate(r.period_end))block.append(el('p','',`Quarter ended ${date(r.period_end)}: ${num(r.previous)?money(r.previous)+' → ':''}${money(r.current)}${num(r.previous)?' YoY':''}.`));});
    if(validDate(s.filed))block.append(el('p','',`Filed ${date(s.filed)}`));return block;
  }
  function renderDetails(){
    q('[data-financial-title]').textContent=titles[group];q('[data-financial-count]').textContent=`${snapshot.counts[group].toLocaleString('en-US')} companies match · ranked by TTM revenue`;
    q('[data-financial-coverage]').textContent=`${coverage()} ${snapshot.coverage.full_histories.toLocaleString('en-US')} full histories verified; ${snapshot.coverage.eligible.toLocaleString('en-US')} eligible operating businesses screened. Refreshed ${new Date(snapshot.updated_at).toLocaleString('en-US',{month:'short',day:'numeric',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'UTC'})} UTC.`;
    const root=q('[data-financial-details]');root.replaceChildren();snapshot.groups[group].forEach(r=>{
      const card=el('article','financial-detail-card');card.dataset.financialDetail=r.cik;card.dataset.selected=String(r.cik===selected);card.append(el('h3','',r.symbol),el('p','financial-name',r.name),el('p','financial-industry',`${industries[r.sector]||r.sector} · TTM revenue ${money(r.revenue_current)}`));r.signals.forEach(s=>card.append(evidence(s)));
      if(source(r.source)){const a=el('a','financial-report-link','SEC report ↗');a.href=r.source;a.target='_blank';a.rel='noopener noreferrer';card.append(a);}root.append(card);
    });
    if(!snapshot.groups[group].length)root.append(el('p','financial-empty','No companies meet this screen in the covered data.'));
    const rules=q('[data-financial-method]');rules.replaceChildren();for(const key of['universe',group,'updates'])if(typeof snapshot.methodology?.[key]==='string'){const p=el('p');p.append(el('strong','',`${({universe:'Coverage',turnaround:'Turnaround',shares:'Share changes',stress:'Financial stress',updates:'Updates'})[key]}: `),document.createTextNode(snapshot.methodology[key]));rules.append(p);}
  }
  function open(button,cik){
    if(!snapshot)return;opener=button;selected=cik;renderDetails();if(sheet.open)return;newsY=window.scrollY;oldBody=document.body.style.overflow;oldHtml=document.documentElement.style.overflow;document.body.style.overflow='hidden';document.documentElement.style.overflow='hidden';sheet.showModal();body.scrollTop=0;
    [...sheet.querySelectorAll('[data-financial-detail]')].find(n=>n.dataset.financialDetail===selected)?.scrollIntoView({block:'start'});
  }
  function receive(data){
    industries=Object.fromEntries((data?.sectors||[]).map(s=>[s.id,s.name]));if(!valid(data?.financial_signals)){state.hidden=false;state.textContent='Financial signals are being prepared from company reports.';return;}
    snapshot=data.financial_signals;const stale=Date.now()-Date.parse(snapshot.updated_at)>36*60*60*1000;state.hidden=!stale;if(stale)state.textContent='Update delayed. Showing the last available reports.';overview.querySelectorAll('[data-financial-open]').forEach(b=>b.disabled=false);setGroup(group);
  }
  for(const root of[overview,sheet])root.querySelectorAll('[data-financial-tab]').forEach(b=>b.addEventListener('click',()=>{selected=undefined;setGroup(b.dataset.financialTab);}));
  overview.querySelectorAll('[data-financial-open]').forEach(b=>b.addEventListener('click',()=>open(b)));q('[data-financial-close]').addEventListener('click',()=>sheet.close());
  q('[data-financial-maximize]').addEventListener('click',function(){const expanded=sheet.dataset.expanded!=='true';sheet.dataset.expanded=String(expanded);this.setAttribute('aria-pressed',String(expanded));this.setAttribute('aria-label',expanded?'Restore panel width':'Expand to full width');this.querySelector('path').setAttribute('d',expanded?'M3 8h5V3M21 8h-5V3M16 21v-5h5M8 21v-5H3':'M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5');});
  sheet.addEventListener('close',()=>{document.body.style.overflow=oldBody||'';document.documentElement.style.overflow=oldHtml||'';window.scrollTo({top:newsY,behavior:'instant'});opener?.focus({preventScroll:true});});
  sheet.addEventListener('click',e=>{if(e.target!==sheet)return;const r=sheet.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)sheet.close();});
  window.addEventListener('pulsarium:sector-snapshot',e=>receive(e.detail));window.addEventListener('pulsarium:sector-unavailable',()=>{if(!snapshot){state.hidden=false;state.textContent='Company-report signals are temporarily unavailable.';}});if(window.pulsariumSectorSnapshot)receive(window.pulsariumSectorSnapshot);
})();
