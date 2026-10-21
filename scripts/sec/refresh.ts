// Refresh public SEC company data for the shared US instrument catalog.
// No portfolio, watchlist, account or user tables are accessible to this worker.
import { secGet, secTicker, summarize, US_MICS, secRequestCount } from './summarize.ts'
import { companyHistory, filingUpdates, restHistoryStore, tickerDirectory } from './history.ts'
import { industryFromSec, sectorFromSec } from './sectors.ts'

const base = Deno.env.get('SUPABASE_URL')
const url = base?.replace(/\/$/, '') + '/functions/v1/sec-ingest'
const key = Deno.env.get('SEC_INGEST_TOKEN')
if (!base || !key) throw new Error('SUPABASE_URL and SEC_INGEST_TOKEN are required')
const headers = { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }

async function rest(path: string) {
  const rows: any[] = []
  for (let from = 0; ; from += 1000) {
    const response = await fetch(`${url}/rest/v1/${path}`, { headers: { ...headers, Range: `${from}-${from + 999}` } })
    if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`)
    const page = await response.json()
    rows.push(...page)
    if (page.length < 1000) return rows
  }
}

const instruments = await rest('instruments?select=id,symbol,exchange_mic,sector,industry,sector_source&order=id')
const targets = instruments.filter((row) => US_MICS.has(String(row.exchange_mic ?? '').toUpperCase()))
console.log(`${targets.length} instruments to refresh`)

const history = restHistoryStore(url, headers)
const ciks = new Map(Object.entries(await tickerDirectory(history)))
const updates = await filingUpdates(history)
const supplemental = (await history.state('sector:history-targets'))?.companies ?? []
const signalTargets = (await history.state('financial-signals:history-targets'))?.companies ?? []
const sharedTargets = [...new Map([...supplemental, ...signalTargets].map((company: any) => [String(Number(company.cik)), company])).values()]
for (const company of sharedTargets) {
  try { await companyHistory(history, company.cik, updates) }
  catch (error) { console.error(`Sector supplementary history ${company.symbol} deferred: ${error instanceof Error ? error.message : error}`) }
}
console.log(`Updated ${sharedTargets.length} shared sector and financial-signal histories from the filing index`)
const savedHistories = new Set((await rest('sec_market_frames?select=id&id=like.companyfacts/*')).map(row => row.id))
let bootstrapBudget = 25, deferred = 0
const summaries = new Map<string, ReturnType<typeof summarize>>()
const existing = new Map((await rest('instrument_fundamentals?select=instrument_id,cik,entity_name,currency,period_end,filed,data,updated_at')).map(r => [r.instrument_id, r]))
let reused = 0

const rows: any[] = []
const skipped: string[] = []
const failed: string[] = []
for (const target of targets) {
  const match = ciks.get(secTicker(target.symbol))
  if (!match) { skipped.push(target.symbol); continue }
  const [cik, title] = match
  try {
    let summary = summaries.get(cik)
    if (!summaries.has(cik)) {
      if (!savedHistories.has(`companyfacts/${cik}`) && bootstrapBudget-- <= 0) { deferred++; continue }
      summary = summarize(await companyHistory(history, cik, updates))
      summaries.set(cik, summary)
    }
    if (!summary) { skipped.push(target.symbol); continue }
    rows.push({
      instrument_id: target.id, cik, entity_name: title,
      currency: summary.currency, period_end: summary.period_end, filed: summary.filed,
      data: summary, updated_at: new Date().toISOString(),
    })
  } catch (error) { // one company's odd filing must not stop the run
    // a fund or trust with a CIK but no company facts (QQQX): no SEC data, not a failure
    if (error instanceof Error && /^SEC 404 for .*\/companyfacts\//.test(error.message)) { skipped.push(target.symbol); continue }
    const old = existing.get(target.id)
    if (old) { rows.push(old); reused++ } // retain its original update time on failure
    failed.push(target.symbol)
    console.log(`  ${target.symbol}: ${error instanceof Error ? error.message : error}`)
  }
}

if (rows.length) {
  const response = await fetch(`${url}/rest/v1/instrument_fundamentals?on_conflict=instrument_id`, {
    method: 'POST', headers: { ...headers, Prefer: 'resolution=merge-duplicates,return=minimal' }, body: JSON.stringify(rows),
  })
  if (!response.ok) throw new Error(`store: ${response.status} ${await response.text()}`)
}
console.log(`New company histories deferred to later runs: ${deferred}`)
console.log(`stored ${rows.length}; no SEC data for ${skipped.length}: ${skipped.join(', ') || '—'}; failed ${failed.length}: ${failed.join(', ') || '—'}`)

// A sector from SEC's classification for companies that have none yet
// (Marketstack has no company data for some, Ouster and Microsoft among
// them). Only an empty sector, or one SEC gave before, is written, so a
// fix to the mapping reaches companies already filled; sectors from
// Marketstack stay, and users' own choices live in
// instrument_sector_overrides on top of all this.
const sectorNotes: string[] = []
const classifications = new Map((await rest('sec_market_companies?select=cik,payload')).map(r => [String(Number(r.cik)), r.payload]))
for (const target of targets.filter((row) => !row.sector || row.sector_source === 'sec')) {
  const match = ciks.get(secTicker(target.symbol))
  if (!match) continue
  try {
    const saved = classifications.get(String(Number(match[0])))
    const filer = saved ? { sic: saved.sic, sicDescription: saved.sic_description, ownerOrg: saved.owner_org } : await secGet(`https://data.sec.gov/submissions/CIK${match[0]}.json`)
    if (!saved) {
      const response = await fetch(`${url}/rest/v1/sec_market_companies?on_conflict=cik`, {
        method: 'POST', headers: { ...headers, Prefer: 'resolution=merge-duplicates,return=minimal' },
        body: JSON.stringify({ cik: Number(match[0]), payload: { symbol: target.symbol, name: match[1], sic: filer.sic, sic_description: filer.sicDescription, owner_org: filer.ownerOrg }, updated_at: new Date().toISOString() }),
      })
      if (!response.ok) throw new Error(`classification cache: ${response.status}`)
      classifications.set(String(Number(match[0])), { sic: filer.sic, sic_description: filer.sicDescription, owner_org: filer.ownerOrg })
    }
    const sector = sectorFromSec(filer.sic, filer.ownerOrg, filer.sicDescription)
    const industry = industryFromSec(filer.sicDescription)
    if (!sector) {
      if (!target.sector) sectorNotes.push(`${target.symbol} unclear (SIC ${filer.sic || '—'})`)
      continue
    }
    if (sector === target.sector && industry === target.industry) continue
    const response = await fetch(`${url}/rest/v1/instruments?id=eq.${target.id}&or=(sector.is.null,sector.eq.,sector_source.eq.sec)`, {
      method: 'PATCH', headers: { ...headers, Prefer: 'return=minimal' },
      body: JSON.stringify({ sector, industry, sector_source: 'sec', sector_updated_at: new Date().toISOString() }),
    })
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
    sectorNotes.push(`${target.symbol} ${sector}`)
  } catch (error) { // a sector is a nicety; the key figures above are already stored
    sectorNotes.push(`${target.symbol} failed`)
    console.log(`  sector ${target.symbol}: ${error instanceof Error ? error.message : error}`)
  }
}
console.log(`sectors from SEC: ${sectorNotes.join(', ') || 'no changes'}`)

console.log(`SEC requests this run: ${secRequestCount()}; distinct company histories: ${summaries.size}; retained after failure: ${reused}; filing index through: ${updates.through}`)
if (targets.length && (!rows.length || failed.length)) Deno.exit(1)
