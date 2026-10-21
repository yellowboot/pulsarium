import assert from 'node:assert/strict'
import { appendFacts, companyHistory, filingUpdates, packHistory, parseReportIndex, unpackHistory, type HistoryStore, type HistoryDeps, type Report } from './history.ts'

class Store implements HistoryStore {
  states = new Map<string, any>()
  frames = new Map<string, string>()
  async state(id: string) { return this.states.get(id) ?? null }
  async saveState(id: string, payload: unknown) { this.states.set(id, payload) }
  async frame(id: string) { return this.frames.get(id) ?? null }
  async saveFrame(id: string, payload: string) { this.frames.set(id, payload) }
}
const accession = '0000000001-26-000001'
const report: Report = { cik: '0000000001', accession, form: '10-Q', filed: '2026-10-06' }
const factDoc = (accn: string, val: number) => ({ facts: { 'us-gaap': { Revenues: { units: { USD: [{ accn, val, start: '2026-07-01', end: '2026-09-30', filed: '2026-10-06', form: '10-Q' }] } } } } })
function dependencies() {
  const counts = { company: 0, filing: 0, submissions: 0 }
  const deps: HistoryDeps = {
    company: async () => { counts.company++; return factDoc('0000000001-25-000001', 100) },
    catchUp: async (_cik, doc) => doc,
    filing: async (_cik, accn) => { counts.filing++; return factDoc(accn, 120).facts },
    submissions: async () => { counts.submissions++; return { filings: { recent: { accessionNumber: [accession], filingDate: ['2026-10-06'], form: ['10-Q'], isXBRL: [1] } } } },
  }
  return { deps, counts }
}
const index = 'CIK|Company Name|Form Type|Date Filed|File Name\n1|Example|10-Q|2026-10-06|edgar/data/1/0000000001-26-000001.txt\n2|Plan|4|2026-10-06|edgar/data/2/0000000002-26-000001.txt\n1|Example|10-Q/A|2026-10-06|edgar/data/1/0000000001-26-000002.txt\n'

Deno.test('financial filing index retains amendments and excludes unrelated forms', () => {
  const rows = parseReportIndex(index)
  assert.equal(rows.length, 2)
  assert.equal(rows[1].form, '10-Q/A')
  assert.equal(rows[0].cik, '0000000001')
  assert.throws(() => parseReportIndex('<html>Unavailable</html>'))
})
Deno.test('real daily compact dates and quarterly Filename header are accepted', () => {
  const compact = index.replaceAll('2026-10-06', '20261006')
  assert.deepEqual(parseReportIndex(compact), parseReportIndex(index))
  assert.deepEqual(parseReportIndex(index.replace('File Name', 'Filename')), parseReportIndex(index))
})
Deno.test('a holiday 403 falls back to a quarterly index covering the requested day', async () => {
  const store = new Store()
  store.states.set('fundamentals:index-cursor', { through: '2026-10-13', version: 2 })
  const urls: string[] = []
  const updates = await filingUpdates(store, new Date('2026-10-14T12:00:00Z'), async url => {
    urls.push(url)
    if (url.includes('full-index')) return 'Last Data Received: October 13, 2026\n' + index.replace('2026-10-06', '2026-10-13')
    throw new Error('SEC 403')
  })
  assert.equal(updates.through, '2026-10-13')
  assert.equal(updates.reports.length, 1)
  assert.equal(urls.filter(u => u.includes('full-index')).length, 1)
  assert.equal(store.states.get('filing-index:2026-10-12').reports.length, 0)
})
Deno.test('an incomplete quarterly fallback cannot advance the checkpoint', async () => {
  const store = new Store()
  store.states.set('fundamentals:index-cursor', { through: '2026-10-11', version: 2 })
  const updates = await filingUpdates(store, new Date('2026-10-14T12:00:00Z'), async url => {
    if (url.includes('full-index')) return 'Last Data Received: October 11, 2026\n' + index
    throw new Error('SEC 403')
  })
  assert.equal(updates.through, '2026-10-11')
  assert(!store.states.has('filing-index:2026-10-12'))
})
Deno.test('v1 empty indices are reparsed instead of retained forever', async () => {
  const store = new Store()
  store.states.set('fundamentals:index-cursor', { through: '2026-10-06' })
  store.states.set('filing-index:2026-10-06', { complete: true, reports: [] })
  const updates = await filingUpdates(store, new Date('2026-10-07T12:00:00Z'), async () => index.replaceAll('2026-10-06', '20261006'))
  assert.equal(updates.through, '2026-10-06')
  assert.equal(store.states.get('filing-index:2026-10-06').reports.length, 2)
  assert.equal(store.states.get('fundamentals:index-cursor').version, 2)
})
Deno.test('completed daily indices are shared and never fetched again', async () => {
  const store = new Store(); let requests = 0
  const read = async () => { requests++; return index }
  const now = new Date('2026-10-07T12:00:00Z')
  const first = await filingUpdates(store, now, read)
  assert.equal(first.through, '2026-10-06')
  assert.equal(requests, 5)
  await filingUpdates(store, now, read)
  assert.equal(requests, 5)
})
Deno.test('UTC midnight cannot freeze the SEC trading day still in progress', async () => {
  const updates = await filingUpdates(new Store(), new Date('2026-10-07T00:05:00Z'), async url => {
    assert(!url.includes('20261006'))
    return index
  })
  assert.equal(updates.through, '2026-10-05')
})
Deno.test('failed index keeps checkpoint before the gap for the next run', async () => {
  const store = new Store()
  const updates = await filingUpdates(store, new Date('2026-10-07T12:00:00Z'), async url => {
    if (url.includes('20261001')) throw new Error('SEC 503')
    return index
  })
  assert.equal(updates.through, '2026-09-30')
  assert.equal(store.states.get('fundamentals:index-cursor').through, '2026-09-30')
  const retry = await filingUpdates(store, new Date('2026-10-07T12:00:00Z'), async () => index)
  assert.equal(retry.through, '2026-10-06')
})
Deno.test('one baseline, then only unseen filings; repeat callers use permanent history', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  const updates = { from: '2026-10-01', through: '2026-10-06', reports: [report] }
  const first = await companyHistory(store, '1', updates, deps)
  assert.deepEqual(first.facts?.['us-gaap'].Revenues.units.USD.map(r => r.val), [100, 120])
  await companyHistory(store, '0000000001', updates, deps)
  assert.deepEqual(counts, { company: 1, filing: 1, submissions: 1 })
  assert(store.frames.has(`filing/${accession}`))
})
Deno.test('a filing already in company facts never needs a separate download', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  deps.company = async () => { counts.company++; return factDoc(accession, 120) }
  await companyHistory(store, '1', { from: '2026-10-01', through: '2026-10-06', reports: [report] }, deps)
  assert.equal(counts.filing, 0)
})
Deno.test('a nonfinancial amendment is remembered without repeated downloads', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  deps.filing = async () => { counts.filing++; return {} }
  const updates = { from: '2026-10-01', through: '2026-10-06', reports: [{ ...report, form: '10-K/A' }] }
  await companyHistory(store, '1', updates, deps)
  await companyHistory(store, '1', updates, deps)
  assert.equal(counts.filing, 1)
})
Deno.test('amended values append to history; originals and cached filing survive', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  const updates = { from: '2026-10-01', through: '2026-10-06', reports: [report] }
  await companyHistory(store, '1', updates, deps)
  const amendment = { ...report, accession: '0000000001-26-000002', form: '10-Q/A' }
  const doc = await companyHistory(store, '1', { ...updates, reports: [report, amendment] }, deps)
  assert.equal(doc.facts?.['us-gaap'].Revenues.units.USD.length, 3)
  assert.equal(counts.filing, 2)
  const old = await unpackHistory(store.frames.get(`filing/${accession}`)!)
  assert.equal(old['us-gaap'].Revenues.units.USD[0].accn, accession)
})
Deno.test('temporary filing failure persists pending metadata and retries without full history', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  const updates = { from: '2026-10-01', through: '2026-10-06', reports: [report] }
  const original = deps.filing
  deps.filing = async () => { throw new Error('SEC 503') }
  await assert.rejects(() => companyHistory(store, '1', updates, deps))
  const saved = await unpackHistory(store.frames.get('companyfacts/0000000001')!)
  assert.equal(saved.pending.length, 1)
  assert.equal(saved.doc.facts['us-gaap'].Revenues.units.USD[0].val, 100)
  deps.filing = original
  const doc = await companyHistory(store, '1', { ...updates, reports: [] }, deps)
  assert.equal(doc.facts?.['us-gaap'].Revenues.units.USD.length, 2)
  assert.equal(counts.company, 1)
})
Deno.test('inactive companies supplement the index gap from submissions once', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  await store.saveFrame('companyfacts/0000000001', await packHistory({ doc: factDoc('old', 100), through: '2026-06-30', pending: [] }))
  await companyHistory(store, '1', { from: '2026-10-01', through: '2026-10-06', reports: [] }, deps)
  assert.deepEqual(counts, { company: 0, filing: 1, submissions: 1 })
})
Deno.test('re-appending identical facts cannot double-count a report', () => {
  const doc = factDoc(accession, 120)
  appendFacts(doc, structuredClone(doc.facts))
  assert.equal(doc.facts['us-gaap'].Revenues.units.USD.length, 1)
})
Deno.test('submissions metadata is reused for 6 hours between daily checks', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  const updates = { from: '2026-10-01', through: '2026-10-05', reports: [] }
  deps.submissions = async () => { counts.submissions++; return { filings: { recent: { accessionNumber: [], filingDate: [], form: [], isXBRL: [] } } } }
  await companyHistory(store, '1', updates, deps, new Date('2026-10-06T06:00:00Z'))
  deps.submissions = dependencies().deps.submissions
  await companyHistory(store, '1', updates, deps, new Date('2026-10-06T11:59:00Z'))
  assert.equal(counts.filing, 0)
  await companyHistory(store, '1', updates, deps, new Date('2026-10-06T12:00:00Z'))
  assert.equal(counts.filing, 1)
  assert.equal(counts.company, 1)
})
Deno.test('parser repair recovers a report older than the incorrect company checkpoint', async () => {
  const store = new Store(), { deps, counts } = dependencies()
  await store.saveFrame('companyfacts/0000000001', await packHistory({ doc: factDoc('old', 100), through: '2026-10-09', pending: [] }))
  await companyHistory(store, '1', { from: '2026-10-08', through: '2026-10-10', reports: [] }, deps)
  assert.equal(counts.filing, 1)
  assert.equal(counts.submissions, 1)
})
