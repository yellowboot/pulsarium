// Permanent SEC history shared by the scheduled public worker and company pages.
// The worker uses a scoped gateway; visitors receive summaries, never caches.
import { catchUp, filingFacts, secFetch, secGet, type FactsDoc } from './summarize.ts'

export type Report = { cik: string; accession: string; filed: string; form: string }
export type Updates = { from: string; through: string; reports: Report[] }
export interface HistoryStore {
  state(id: string): Promise<any | null>
  saveState(id: string, payload: unknown): Promise<void>
  frame(id: string): Promise<string | null>
  saveFrame(id: string, payload: string): Promise<void>
}
const REPORTS = new Set(['10-Q', '10-K', '20-F', '40-F', '10-Q/A', '10-K/A', '20-F/A', '40-F/A'])
const INDEX_VERSION = 2
const SUBMISSIONS_TTL_MS = 6 * 60 * 60 * 1000
const addDays = (date: string, n: number) => new Date(Date.parse(date + 'T12:00:00Z') + n * 86400000).toISOString().slice(0, 10)
const normalizeCik = (cik: string) => String(Number(cik)).padStart(10, '0')

export function restHistoryStore(url: string, headers: Record<string, string>): HistoryStore {
  async function read(table: string, id: string, field: string) {
    const r = await fetch(`${url}/rest/v1/${table}?id=eq.${encodeURIComponent(id)}&select=${field}`, { headers })
    if (!r.ok) throw new Error(`SEC history cache read: ${r.status}`)
    return (await r.json())[0]?.[field] ?? null
  }
  async function write(table: string, row: unknown) {
    const r = await fetch(`${url}/rest/v1/${table}?on_conflict=id`, { method: 'POST', headers: { ...headers, Prefer: 'resolution=merge-duplicates,return=minimal' }, body: JSON.stringify(row) })
    if (!r.ok) throw new Error(`SEC history cache write: ${r.status}`)
  }
  return {
    state: (id) => read('sec_market_sync_state', id, 'payload'),
    saveState: (id, payload) => write('sec_market_sync_state', { id, payload, updated_at: new Date().toISOString() }),
    frame: (id) => read('sec_market_frames', id, 'payload_gzip'),
    saveFrame: (id, payload_gzip) => write('sec_market_frames', { id, payload_gzip, fetched_at: new Date().toISOString() }),
  }
}

export async function packHistory(value: unknown): Promise<string> {
  const bytes = new Uint8Array(await new Response(new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer())
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192))
  return btoa(binary)
}
export async function unpackHistory(value: string): Promise<any> {
  const bytes = Uint8Array.from(atob(value), c => c.charCodeAt(0))
  return JSON.parse(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text())
}

export function parseReportIndex(text: string): Report[] {
  if (!/CIK\|Company Name\|Form Type\|Date Filed\|File ?Name/i.test(text)) throw new Error('Incomplete SEC filing index')
  const reports: Report[] = []
  for (const line of text.split('\n')) {
    const [cik, , form, rawDate, path] = line.trim().split('|').map(s => s.trim())
    const filed = rawDate?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')
    if (!/^\d+$/.test(cik) || !REPORTS.has(form) || !/^\d{4}-\d{2}-\d{2}$/.test(filed)) continue
    const accession = path?.match(/(\d{10}-\d{2}-\d{6})\.txt$/)?.[1]
    if (accession) reports.push({ cik: normalizeCik(cik), accession, filed, form })
  }
  return reports
}

/** Only completed SEC days are cached. A failed or late index never advances
 * the checkpoint; a small overlap also protects delayed GitHub runs. */
export async function filingUpdates(store: HistoryStore, now = new Date(), read = secFetch): Promise<Updates> {
  const secDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now)
  const yesterday = addDays(secDate, -1)
  const cursor = await store.state('fundamentals:index-cursor')
  // Revisit the deployment's previous 90 days once: v1 silently cached
  // compact daily-index dates as empty reports, so its checkpoint is unsafe.
  const from = cursor?.version === INDEX_VERSION ? addDays(cursor.through, -2) : cursor ? addDays(yesterday, -89) : addDays(yesterday, -6)
  const reports: Report[] = []
  const quarters = new Map<string, { through: string; reports: Report[] }>()
  let through = addDays(from, -1), complete = true
  // After an extended outage, continue over successive runs without deleting
  // history or skipping the unprocessed interval.
  for (let date = from, count = 0; date <= yesterday && count < 90; date = addDays(date, 1), count++) {
    let cached = await store.state(`filing-index:${date}`)
    const day = new Date(date + 'T12:00:00Z').getUTCDay()
    if (day === 0 || day === 6) cached = { reports: [], complete: true, version: INDEX_VERSION }
    if (!cached?.complete || cached.version !== INDEX_VERSION) {
      try {
        const quarter = Math.ceil(Number(date.slice(5, 7)) / 3)
        const text = await read(`https://www.sec.gov/Archives/edgar/daily-index/${date.slice(0, 4)}/QTR${quarter}/master.${date.replaceAll('-', '')}.idx`)
        cached = { reports: parseReportIndex(text), complete: true, version: INDEX_VERSION }
        await store.saveState(`filing-index:${date}`, cached)
      } catch (error) {
        // Holiday daily URLs can return 403 rather than 404. Confirm the
        // day's contents against a complete quarterly index; never infer
        // an empty filing day from a failed HTTP request.
        try {
          const quarter = Math.ceil(Number(date.slice(5, 7)) / 3)
          const key = `${date.slice(0, 4)}/QTR${quarter}`
          let full = quarters.get(key)
          if (!full) {
            const text = await read(`https://www.sec.gov/Archives/edgar/full-index/${key}/master.idx`)
            const received = text.match(/Last Data Received:\s+([A-Za-z]+ \d{1,2}, \d{4})/i)?.[1]
            const timestamp = received ? Date.parse(`${received} 12:00:00 UTC`) : NaN
            if (!Number.isFinite(timestamp)) throw new Error('Quarterly SEC index has no coverage date')
            full = { through: new Date(timestamp).toISOString().slice(0, 10), reports: parseReportIndex(text) }
            quarters.set(key, full)
          }
          if (full.through < date) throw new Error(`Quarterly SEC index only through ${full.through}`)
          cached = { reports: full.reports.filter(r => r.filed === date), complete: true, version: INDEX_VERSION }
          await store.saveState(`filing-index:${date}`, cached)
        } catch (fallback) {
          complete = false
          console.error(`SEC index ${date} deferred: ${String(error)}; fallback: ${String(fallback)}`)
          break
        }
      }
    }
    reports.push(...cached.reports)
    through = date
  }
  if (through >= from && (!cursor?.through || through > cursor.through || cursor.version !== INDEX_VERSION)) await store.saveState('fundamentals:index-cursor', { through, version: INDEX_VERSION })
  if (!complete && cursor?.through && through < cursor.through) throw new Error('SEC index check incomplete')
  return { from, through, reports }
}

export async function tickerDirectory(store: HistoryStore, now = new Date()): Promise<Record<string, [string, string]>> {
  const today = now.toISOString().slice(0, 10)
  const sector = await store.state('directory')
  if (sector?.date === today && sector.data?.fields && sector.data?.data) {
    const fields = sector.data.fields, tickers: Record<string, [string, string]> = {}
    for (const values of sector.data.data) {
      const r = Object.fromEntries(fields.map((field: string, i: number) => [field, values[i]]))
      tickers[String(r.ticker).toUpperCase()] = [normalizeCik(r.cik), r.name]
    }
    return tickers
  }
  const cached = await store.state('fundamentals:tickers')
  if (cached?.date === today) return cached.tickers
  const tickers: Record<string, [string, string]> = {}
  for (const row of Object.values(await secGet('https://www.sec.gov/files/company_tickers.json')) as any[]) tickers[String(row.ticker).toUpperCase()] = [normalizeCik(row.cik_str), row.title]
  await store.saveState('fundamentals:tickers', { date: today, tickers })
  return tickers
}

export function appendFacts(doc: FactsDoc, extra: FactsDoc['facts']) {
  for (const [taxonomy, concepts] of Object.entries(extra ?? {})) for (const [name, concept] of Object.entries(concepts)) {
    const units = (((doc.facts ??= {})[taxonomy] ??= {})[name] ??= { units: {} }).units
    for (const [unit, items] of Object.entries(concept.units)) {
      const old = units[unit] ??= [], seen = new Set(old.map(r => JSON.stringify(r)))
      old.push(...items.filter(r => !seen.has(JSON.stringify(r))))
    }
  }
}
function knownAccessions(doc: FactsDoc) {
  const known = new Set<string>()
  for (const concepts of Object.values(doc.facts ?? {})) for (const c of Object.values(concepts)) for (const rows of Object.values(c.units)) for (const r of rows) if (r.accn) known.add(r.accn)
  return known
}
export type HistoryDeps = { company: (cik: string) => Promise<FactsDoc>; catchUp: typeof catchUp; filing: typeof filingFacts; submissions: typeof secGet }
const defaults: HistoryDeps = { company: cik => secGet(`https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`), catchUp, filing: filingFacts, submissions: secGet }

export async function companyHistory(store: HistoryStore, cik: string, updates: Updates, deps = defaults, now = new Date()): Promise<FactsDoc> {
  cik = normalizeCik(cik)
  const key = `companyfacts/${cik}`
  const saved = await store.frame(key)
  const baseline = saved ? await unpackHistory(saved) : { doc: await deps.catchUp(cik, await deps.company(cik)), through: '', pending: [] }
  const doc: FactsDoc = baseline.doc
  const known = knownAccessions(doc)
  for (const accession of baseline.loaded_accessions ?? []) known.add(accession)
  const reports: Report[] = [...baseline.pending, ...updates.reports.filter(r => normalizeCik(r.cik) === cik)]
  let checkedAt = baseline.submissions_checked_at
  if (baseline.index_version !== INDEX_VERSION || !checkedAt || now.getTime() - Date.parse(checkedAt) >= SUBMISSIONS_TTL_MS || baseline.through && baseline.through < updates.from) {
    // Submissions update during the filing day, before the nightly index.
    // Recheck metadata between the three daily runs and after parser repairs;
    // download only unseen accessions, retaining the permanent full history.
    const recent = (await deps.submissions(`https://data.sec.gov/submissions/CIK${cik}.json`)).filings.recent
    for (let i = 0; i < recent.accessionNumber.length; i++) if (REPORTS.has(recent.form[i]) && !known.has(recent.accessionNumber[i]) && recent.isXBRL[i]) {
      reports.push({ cik, accession: recent.accessionNumber[i], filed: recent.filingDate[i], form: recent.form[i] })
    }
    checkedAt = now.toISOString()
  }
  const pending: Report[] = []
  for (const report of [...new Map(reports.map(r => [r.accession, r])).values()].sort((a, b) => a.filed.localeCompare(b.filed) || a.accession.localeCompare(b.accession))) {
    if (known.has(report.accession)) continue
    try {
      const filingKey = `filing/${report.accession}`
      const cached = await store.frame(filingKey)
      const extra = cached ? await unpackHistory(cached) : await deps.filing(cik, report.accession, report.filed, report.form)
      // An accepted filing can contain no standard XBRL figures (e.g. a
      // Part III-only 10-K/A). Its empty result is retained once as well.
      if (!cached) await store.saveFrame(filingKey, await packHistory(extra))
      appendFacts(doc, extra)
      known.add(report.accession)
    } catch (error) {
      pending.push(report)
      console.error(`SEC filing ${report.accession} deferred: ${error instanceof Error ? error.message : error}`)
    }
  }
  await store.saveFrame(key, await packHistory({ doc, loaded_accessions: [...known], through: updates.through > (baseline.through || '') ? updates.through : baseline.through, pending, submissions_checked_at: checkedAt, index_version: INDEX_VERSION }))
  if (pending.length) throw new Error(`${pending.length} new SEC reports await retry; saved history retained`)
  return doc
}
