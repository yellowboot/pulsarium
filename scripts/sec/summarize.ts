// Key figures for the company page from SEC EDGAR filings, stored in
// instrument_fundamentals by the sec-fundamentals edge function (it began as
// a port of the retired scripts/sec-fundamentals.py).
//
// Companies that report under IFRS (20-F filers such as TSMC or ASML)
// usually file annual figures only; they get annual values and no per-share
// valuation. New listings with fewer than four quarters on file and no full
// year get their latest quarter against the same quarter a year earlier.

import { XMLParser } from 'npm:fast-xml-parser@4'

export const US_MICS = new Set(['XNAS', 'XNGS', 'XNMS', 'XNCM', 'XNYS', 'ARCX', 'XASE', 'BATS', 'IEXG'])
const SEC_PAUSE_MS = 150 // stay far below SEC's 10 requests per second
// Older figures than this aren't shown: a company that stopped filing (or
// whose XBRL moved to a concept we don't read) would otherwise look current.
const MAX_AGE_DAYS: Record<string, number> = { quarters: 200, quarter: 200, annual: 480 }

// Concepts in order of preference; the first one with data wins.
const US_FLOW: Record<string, string[]> = {
  revenue: ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax',
    'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet',
    'RevenuesNetOfInterestExpense'], // banks
  gross_profit: ['GrossProfit'],
  operating_income: ['OperatingIncomeLoss'],
  net_income: ['NetIncomeLoss', 'ProfitLoss', 'NetIncomeLossAvailableToCommonStockholdersBasic'],
  eps_diluted: ['EarningsPerShareDiluted', 'EarningsPerShareBasic'],
  dividends_per_share: ['CommonStockDividendsPerShareDeclared', 'CommonStockDividendsPerShareCashPaid'],
}
const US_INSTANT: Record<string, string[]> = {
  equity: ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
  long_term_debt: ['LongTermDebt', 'LongTermDebtNoncurrent'],
  current_debt: ['LongTermDebtCurrent', 'DebtCurrent'],
  cash: ['CashAndCashEquivalentsAtCarryingValue',
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'],
  // much of a large company's cushion sits here, not in cash (Microsoft)
  short_term_investments: ['ShortTermInvestments', 'MarketableSecuritiesCurrent',
    'AvailableForSaleSecuritiesDebtSecuritiesCurrent'],
  cash_and_short_term_investments: ['CashCashEquivalentsAndShortTermInvestments'],
}
const IFRS_FLOW: Record<string, string[]> = {
  revenue: ['Revenue', 'RevenueFromContractsWithCustomers'],
  gross_profit: ['GrossProfit'],
  operating_income: ['ProfitLossFromOperatingActivities'],
  net_income: ['ProfitLossAttributableToOwnersOfParent', 'ProfitLoss'],
  eps_diluted: ['DilutedEarningsLossPerShare', 'BasicEarningsLossPerShare'],
}
// Cash-flow statement lines, reported year to date in 10-Qs (six and nine
// months), so the last twelve months are the fiscal year plus this year's
// year-to-date minus last year's.
const US_CASH_FLOW: Record<string, string[]> = {
  operating_cash_flow: ['NetCashProvidedByUsedInOperatingActivities',
    'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'],
  capex: ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'],
  // the cash-flow add-back for stock-based compensation: a real cost paid in
  // shares (dilution), read by research reports and the company page
  stock_based_compensation: ['ShareBasedCompensation', 'AllocatedShareBasedCompensationExpense'],
}
const IFRS_CASH_FLOW: Record<string, string[]> = {
  operating_cash_flow: ['CashFlowsFromUsedInOperatingActivities'],
  capex: ['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', 'PurchaseOfPropertyPlantAndEquipment'],
  stock_based_compensation: ['AdjustmentsForSharebasedPayments'],
}
const IFRS_INSTANT: Record<string, string[]> = {
  equity: ['EquityAttributableToOwnersOfParent', 'Equity'],
  cash: ['CashAndCashEquivalents'],
}

type Fact = { start?: string; end: string; val: number; filed?: string; form?: string; accn?: string }
type Concept = { units: Record<string, Fact[]> }
type Facts = Record<string, Record<string, Concept>>
export type FactsDoc = { facts?: Facts; [key: string]: unknown }

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
let requests = 0
export const secRequestCount = () => requests

export async function secFetch(url: string): Promise<string> {
  if (!['www.sec.gov', 'data.sec.gov'].includes(new URL(url).hostname)) throw new Error('SEC host required')
  const contact = (Deno.env.get('SEC_CONTACT') ?? '').trim()
  const agent = contact ? `Pulsarium portfolio tracker ${contact}` : 'Pulsarium portfolio tracker https://pulsarium.finance'
  requests++
  const response = await fetch(url, { headers: { 'User-Agent': agent, 'Accept-Encoding': 'gzip' } })
  const text = await response.text()
  await sleep(SEC_PAUSE_MS)
  if (!response.ok) throw new Error(`SEC ${response.status} for ${url}`)
  return text
}

export async function secGet(url: string): Promise<any> {
  return JSON.parse(await secFetch(url))
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

const DAY_MS = 86_400_000
const toTime = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10))
const diffDays = (later: string, earlier: string) => Math.round((toTime(later) - toTime(earlier)) / DAY_MS)
const todayIso = () => new Date().toISOString().slice(0, 10)
const addDays = (iso: string, n: number) => new Date(toTime(iso) + n * DAY_MS).toISOString().slice(0, 10)
export const age = (end: string) => diffDays(todayIso(), end)

function days(item: Fact) {
  return item.start ? diffDays(item.end, item.start) : null
}
const within = (d: number | null, low: number, high: number) => Boolean(d) && (d as number) >= low && (d as number) <= high

// ---------------------------------------------------------------------------
// Catching up with filings the company-facts summary doesn't have yet
// ---------------------------------------------------------------------------

const REPORT_FORMS = ['10-Q', '10-K', '20-F', '40-F', '10-Q/A', '10-K/A', '20-F/A', '40-F/A']
const CATCH_UP_AFTER_DAYS = 100 // companies report every ~90 days
const TAXONOMIES: Array<[string, string]> = [['fasb.org/us-gaap', 'us-gaap'], ['xbrl.sec.gov/dei', 'dei'], ['xbrl.ifrs.org', 'ifrs-full']]

const localName = (tag: string) => tag.split(':').pop() as string
const asArray = <T,>(value: T | T[] | undefined): T[] => (value == null ? [] : Array.isArray(value) ? value : [value])
const textOf = (node: any): string => (node == null ? '' : typeof node === 'object' ? String(node['#text'] ?? '') : String(node))
function child(node: any, name: string): any {
  if (!node || typeof node !== 'object') return undefined
  const key = Object.keys(node).find((k) => !k.startsWith('@_') && k !== '#text' && localName(k) === name)
  return key ? node[key] : undefined
}

/** One filing's facts, read from its own XBRL instance, in the shape of the
 * company-facts API. Only facts about the company as a whole (no segment or
 * other dimensions), as company facts has them. */
export async function filingFacts(cik: string, accession: string, filed: string, form: string): Promise<Facts> {
  const folder = `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accession.replaceAll('-', '')}`
  const names: string[] = (await secGet(`${folder}/index.json`)).directory.item.map((item: any) => item.name)
  const instance = names.find((n) => n.endsWith('_htm.xml')) ?? names.find((n) => n.endsWith('.xml') && n !== 'FilingSummary.xml'
    && !['_cal.xml', '_def.xml', '_lab.xml', '_pre.xml'].some((suffix) => n.endsWith(suffix)))
  if (!instance) return {}
  // processEntities off: big filings (Citigroup, Coca-Cola) hold more
  // escaped characters than the parser's expansion limit allows, and the
  // numbers read here never contain any
  const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_', parseTagValue: false, parseAttributeValue: false, trimValues: false, processEntities: false })
  const doc = parser.parse(await secFetch(`${folder}/${instance}`))
  const rootKey = Object.keys(doc).find((k) => localName(k) === 'xbrl')
  if (!rootKey) return {}
  const root = doc[rootKey]

  // prefix -> namespace, from the root's xmlns declarations
  const namespaces: Record<string, string> = {}
  for (const [key, value] of Object.entries(root)) {
    if (key === '@_xmlns') namespaces[''] = String(value)
    else if (key.startsWith('@_xmlns:')) namespaces[key.slice(8)] = String(value)
  }

  const periods: Record<string, { start?: string; end: string }> = {}
  const units: Record<string, string> = {}
  for (const [tag, value] of Object.entries(root)) {
    if (tag.startsWith('@_')) continue
    const name = localName(tag)
    if (name === 'context') {
      for (const context of asArray(value as any)) {
        const entity = child(context, 'entity')
        if (child(entity, 'segment') !== undefined || child(context, 'scenario') !== undefined) continue
        const period = child(context, 'period')
        const instant = textOf(child(period, 'instant')).trim()
        periods[context['@_id']] = instant ? { end: instant } : {
          start: textOf(child(period, 'startDate')).trim(), end: textOf(child(period, 'endDate')).trim(),
        }
      }
    } else if (name === 'unit') {
      for (const unit of asArray(value as any)) {
        const divide = child(unit, 'divide')
        if (divide !== undefined) {
          const top = textOf(child(child(divide, 'unitNumerator'), 'measure'))
          const bottom = textOf(child(child(divide, 'unitDenominator'), 'measure'))
          units[unit['@_id']] = `${localName(top)}/${localName(bottom)}`
        } else {
          units[unit['@_id']] = localName(textOf(child(unit, 'measure')))
        }
      }
    }
  }

  const facts: Facts = {}
  for (const [tag, value] of Object.entries(root)) {
    if (tag.startsWith('@_') || tag === '#text') continue
    const [prefix, name] = tag.includes(':') ? tag.split(':') : ['', tag]
    const namespace = namespaces[prefix] ?? ''
    const taxonomy = TAXONOMIES.find(([key]) => namespace.includes(key))?.[1]
    if (!taxonomy) continue
    for (const element of asArray(value as any)) {
      if (!element || typeof element !== 'object') continue
      const period = periods[element['@_contextRef']]
      const unit = units[element['@_unitRef']]
      const text = textOf(element).trim()
      if (!period || !unit || !text) continue
      const number = Number(text)
      if (!Number.isFinite(number)) continue
      const item: Fact = { ...period, val: number, filed, form, accn: accession }
      const concept = ((facts[taxonomy] ??= {})[name] ??= { units: {} })
      ;(concept.units[unit] ??= []).push(item)
    }
  }
  return facts
}

/** Adds reports that SEC's company-facts summary hasn't taken in yet. It can
 * lag a filing by months, so when its newest report is over 100 days old,
 * newer 10-Q/10-K filings are read from their own XBRL instead. Any trouble
 * leaves `doc` as it is. */
export async function catchUp(cik: string, doc: FactsDoc): Promise<FactsDoc> {
  const facts = (doc.facts ??= {})
  let known = ''
  for (const concepts of Object.values(facts)) for (const c of Object.values(concepts)) for (const items of Object.values(c.units)) {
    for (const i of items) if (REPORT_FORMS.includes(i.form ?? '') && (i.filed ?? '') > known) known = i.filed ?? ''
  }
  if (known && age(known) <= CATCH_UP_AFTER_DAYS) return doc
  try {
    const recent = (await secGet(`https://data.sec.gov/submissions/CIK${cik}.json`)).filings.recent
    const keys = Object.keys(recent)
    const rows = (recent[keys[0]] as unknown[]).map((_, index) => Object.fromEntries(keys.map((k) => [k, recent[k][index]])))
    const newer = rows.filter((r: any) => REPORT_FORMS.includes(r.form) && r.filingDate > known && r.isXBRL).slice(0, 4)
    for (const row of newer.reverse()) { // oldest first
      const extra = await filingFacts(cik, row.accessionNumber, row.filingDate, row.form)
      for (const [taxonomy, concepts] of Object.entries(extra)) {
        for (const [name, concept] of Object.entries(concepts)) {
          const target = ((facts[taxonomy] ??= {})[name] ??= { units: {} }).units
          for (const [unit, items] of Object.entries(concept.units)) (target[unit] ??= []).push(...items)
        }
      }
    }
  } catch (error) { // the summary alone is still usable
    console.error(`CIK ${cik}: could not read newer filings (${error instanceof Error ? error.message : error})`)
  }
  return doc
}

// ---------------------------------------------------------------------------
// Facts
// ---------------------------------------------------------------------------

/** The preferred concept among `names` whose latest fact is the newest. */
function pickConcept(facts: Record<string, Concept>, names: string[]) {
  let best: { unit: string; items: Fact[]; latest: string } | null = null
  for (const name of names) {
    const concept = facts[name]
    if (!concept) continue
    for (const [unit, items] of Object.entries(concept.units)) {
      if (!items.length) continue
      const latest = items.reduce((max, i) => (i.end > max ? i.end : max), items[0].end)
      if (!best || latest > best.latest) best = { unit, items, latest }
    }
  }
  return best
}

/** One value per (start, end): the most recently filed one. */
function dedupe(items: Fact[]) {
  const chosen = new Map<string, Fact>()
  for (const item of items) {
    const key = `${item.start ?? ''}|${item.end}`
    const current = chosen.get(key)
    if (!current || (item.filed ?? '') > (current.filed ?? '')) chosen.set(key, item)
  }
  return [...chosen.values()]
}

const sortedMap = (map: Map<string, number>) => new Map([...map.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))

/** end -> value of 3-month periods. Missing quarters are derived from
 * year-to-date figures: a first quarter as the half-year minus the second
 * (new listings report it only in the prospectus), Q4 as the year minus
 * Q1-Q3. */
function quarterly(input: Fact[]) {
  const items = dedupe(input)
  const three = items.filter((i) => within(days(i), 80, 100))
  let quarters = new Map<string, number>(three.map((i) => [i.end, i.val]))
  for (const half of items.filter((i) => within(days(i), 170, 190))) {
    const second = three.find((q) => q.end === half.end)
    if (!second || [...quarters.keys()].some((end) => (half.start as string) < end && end < half.end)) continue
    quarters.set(addDays(second.start as string, -1), half.val - second.val)
  }
  quarters = sortedMap(quarters)
  for (const year of items.filter((i) => within(days(i), 350, 380))) {
    if (quarters.has(year.end)) continue
    const inside = [...quarters.entries()].filter(([end]) => (year.start as string) < end && end < year.end).map(([, v]) => v)
    if (inside.length === 3) quarters.set(year.end, year.val - inside.reduce((a, b) => a + b, 0))
  }
  return sortedMap(quarters)
}

function annual(input: Fact[]) {
  const pairs = dedupe(input).filter((i) => within(days(i), 350, 380)).map((i) => [i.end, i.val] as [string, number])
  pairs.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1]))
  return new Map(pairs) // for a repeated end the later (larger) pair wins, as dict(sorted(...))
}

/** The run of consecutive quarters that ends with the latest one. */
function backToBack(ends: string[]) {
  const run = ends.slice(-1)
  for (const end of ends.slice(0, -1).reverse()) {
    if (!within(diffDays(run[0], end), 80, 100)) break
    run.unshift(end)
  }
  return run
}

/** The run of back-to-back fiscal years that ends with the latest one. */
function consecutiveYears(ends: string[]) {
  const run = ends.slice(-1)
  for (const end of ends.slice(0, -1).reverse()) {
    if (!within(diffDays(run[0], end), 350, 380)) break
    run.unshift(end)
  }
  return run
}

function latestInstant(items: Fact[]) {
  const instants = items.filter((i) => !i.start)
  if (!instants.length) return null
  return instants.reduce((best, i) => (i.end > best.end || (i.end === best.end && (i.filed ?? '') > (best.filed ?? '')) ? i : best))
}

/** Sum of all share classes on the latest cover-page date; without a
 * cover-page count, the latest quarter's diluted weighted average - not for
 * new listings, whose average still counts the weeks before the IPO. */
function sharesOutstanding(dei: Record<string, Concept>, source: Record<string, Concept>, weightedOk = true) {
  const concept = dei.EntityCommonStockSharesOutstanding
  if (!concept || !('shares' in concept.units)) {
    if (!weightedOk) return null
    const diluted = source.WeightedAverageNumberOfDilutedSharesOutstanding
    const items = diluted?.units.shares ?? []
    const quarters = dedupe(items).filter((i) => within(days(i), 80, 100))
    return quarters.length ? quarters.reduce((best, i) => (i.end > best.end ? i : best)).val : null
  }
  const items = concept.units.shares
  const latest = items.reduce((max, i) => (i.end > max ? i.end : max), items[0].end)
  const rows = items.filter((i) => i.end === latest)
  const newestFiling = rows.reduce((max, i) => ((i.filed ?? '') > max ? i.filed ?? '' : max), '')
  return rows.filter((i) => (i.filed ?? '') === newestFiling).reduce((sum, i) => sum + i.val, 0)
}

const SPLIT_FACTORS = [2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50]
const round6 = (value: number) => Math.round(value * 1e6) / 1e6

/** Dividends per share by fiscal year (the last ten back-to-back years),
 * adjusted for stock splits, with the year the current run of increases
 * began and the year the current run of payments began. Conservative: the
 * run of increases stops at the first year that isn't higher. */
function dividendHistory(source: Record<string, Concept>) {
  const candidates: Array<Map<string, number>> = []
  for (const name of ['CommonStockDividendsPerShareDeclared', 'CommonStockDividendsPerShareCashPaid']) {
    const concept = source[name]
    const years = concept ? annual(concept.units['USD/shares'] ?? []) : new Map<string, number>()
    if (years.size) candidates.push(years)
  }
  if (!candidates.length) return null
  const lastKey = (m: Map<string, number>) => [...m.keys()].at(-1) as string
  candidates.sort((a, b) => (lastKey(a) < lastKey(b) ? 1 : lastKey(a) > lastKey(b) ? -1 : 0)) // most recent first (stable)
  const dps = new Map(candidates[0])
  for (const other of candidates.slice(1)) {
    const shared = [...dps.keys()].filter((end) => other.has(end))
    if (shared.length && shared.every((end) => Math.abs((dps.get(end) as number) - (other.get(end) as number)) <= 0.01 * Math.abs(dps.get(end) as number))) {
      for (const [end, value] of other) if (!dps.has(end)) dps.set(end, value)
    }
  }

  const ends = [...dps.keys()].sort()
  if (age(ends.at(-1) as string) > MAX_AGE_DAYS.annual || (dps.get(ends.at(-1) as string) as number) <= 0) return null
  const run = consecutiveYears(ends)
  const values = run.map((end) => dps.get(end) as number)

  // A split shows as the share count jumping by a whole factor while the
  // per-share dividend drops by about the same factor (or the reverse);
  // earlier years are then restated to today's shares.
  const diluted = source.WeightedAverageNumberOfDilutedSharesOutstanding
  const shares = diluted ? annual(diluted.units.shares ?? []) : new Map<string, number>()
  for (let i = run.length - 1; i > 0; i--) {
    const before = run[i - 1], after = run[i]
    if (!(shares.get(before) && shares.get(after) && values[i - 1] > 0 && values[i] > 0)) continue
    const ratio = (shares.get(after) as number) / (shares.get(before) as number)
    for (const factor of [...SPLIT_FACTORS, ...SPLIT_FACTORS.map((k) => 1 / k)]) {
      if (Math.abs(ratio / factor - 1) < 0.15 && values[i] * factor / values[i - 1] > 0.6 && values[i] * factor / values[i - 1] < 1.8) {
        for (let j = 0; j < i; j++) values[j] = values[j] / factor
        break
      }
    }
  }

  let raisedSince: string | null = null
  for (let i = run.length - 1; i > 0; i--) {
    if (values[i] > values[i - 1] * 1.002) raisedSince = run[i]
    else break
  }
  let firstPaid = values.length - 1
  while (firstPaid > 0 && values[firstPaid - 1] > 0) firstPaid--
  let start = Math.max(0, run.length - 10)
  if (raisedSince && run.length - run.indexOf(raisedSince) >= 3) start = Math.max(start, run.indexOf(raisedSince) - 1)
  return {
    years: run.slice(start).map((end, k) => ({ end, dps: round6(values[start + k]) })),
    raised_since: raisedSince,
    paid_since: run[firstPaid],
  }
}

/** The chart's Cash view: operating cash flow, capex and free cash flow with
 * the revenue of the same span, for the twelve months to each quarter end
 * (cash-flow statements run year-to-date, so a lone quarter isn't on file)
 * and for each fiscal year. Points without operating cash flow are left out;
 * banks have no capex line, so their free cash flow stays null. */
function cashFlowSeries(source: Record<string, Concept>, cashFlowMap: Record<string, string[]>,
  revenue: { q: Map<string, number>; y: Map<string, number> }, quarterEnds: string[], yearEnds: string[]) {
  const items = (key: string) => pickConcept(source, cashFlowMap[key] ?? [])?.items ?? []
  const ocfItems = items('operating_cash_flow')
  const capexItems = items('capex')
  if (!ocfItems.length) return {}
  const revQ = [...revenue.q.keys()]
  const revenueTtm = (end: string) => {
    const at = revQ.indexOf(end)
    if (at < 3 || !within(diffDays(revQ[at], revQ[at - 3]), 250, 300)) return null
    return revQ.slice(at - 3, at + 1).reduce((sum, e) => sum + (revenue.q.get(e) as number), 0)
  }
  const point = (end: string, ocf: number | null, capex: number | null, rev: number | null) =>
    ocf == null ? [] : [{ end, operating_cash_flow: ocf, capex, free_cash_flow: capex == null ? null : ocf - capex, revenue: rev }]
  const ocfYears = annual(ocfItems)
  const capexYears = annual(capexItems)
  return {
    quarters: quarterEnds.flatMap((end) => point(end, ttmFromYtd(ocfItems, end), capexItems.length ? ttmFromYtd(capexItems, end) : null, revenueTtm(end))),
    years: yearEnds.slice(-6).flatMap((end) => point(end, ocfYears.get(end) ?? null, capexYears.get(end) ?? null, revenue.y.get(end) ?? null)),
  }
}

/** Twelve months to `end` of a year-to-date flow (cash-flow lines): the
 * fiscal year plus this year's year-to-date minus the same span a year
 * earlier; the fiscal year itself when `end` closes one. */
function ttmFromYtd(input: Fact[], end: string): number | null {
  const items = dedupe(input).filter((i) => i.start)
  const atEnd = items.filter((i) => i.end === end).sort((a, b) => (days(b) ?? 0) - (days(a) ?? 0))[0]
  if (!atEnd) return null
  const span = days(atEnd) as number
  if (within(span, 350, 380)) return atEnd.val
  const year = items.filter((i) => within(days(i), 350, 380) && i.end < end && within(diffDays(end, i.end), span - 15, span + 15))[0]
  const before = items.find((i) => within(days(i), span - 15, span + 15) && within(diffDays(end, i.end), 350, 380))
  return year && before ? year.val + atEnd.val - before.val : null
}

export function summarize(factsDoc: FactsDoc) {
  const facts = factsDoc.facts ?? {}
  let taxonomy: string, flowMap: Record<string, string[]>, instantMap: Record<string, string[]>, cashFlowMap: Record<string, string[]>
  if (facts['us-gaap']) [taxonomy, flowMap, instantMap, cashFlowMap] = ['us-gaap', US_FLOW, US_INSTANT, US_CASH_FLOW]
  else if (facts['ifrs-full']) [taxonomy, flowMap, instantMap, cashFlowMap] = ['ifrs-full', IFRS_FLOW, IFRS_INSTANT, IFRS_CASH_FLOW]
  else return null
  const source = facts[taxonomy]

  const series: Record<string, { q: Map<string, number>; y: Map<string, number> }> = {}
  let currency: string | null = null
  for (const [key, names] of Object.entries(flowMap)) {
    const picked = pickConcept(source, names)
    if (!picked) continue
    if (key === 'revenue') currency = picked.unit
    series[key] = { q: quarterly(picked.items), y: annual(picked.items) }
  }
  if (!series.revenue) return null

  const revQ = [...series.revenue.q.keys()]
  const revY = [...series.revenue.y.keys()]
  let yearAgo: string | null = null
  let basis: string
  let ends: string[]
  if (revQ.length >= 8) {
    basis = 'quarters'; ends = revQ.slice(-8)
  } else if (revY.length && age(revY.at(-1) as string) <= MAX_AGE_DAYS.annual) {
    basis = 'annual'; ends = []
  } else if (revQ.length) {
    // a new listing: a few quarters on file and no recent full year
    const run = backToBack(revQ)
    if (run.length >= 4) {
      basis = 'quarters'; ends = run.slice(-8)
    } else {
      const latest = revQ.at(-1) as string
      basis = 'quarter'
      ends = revQ.filter((e) => diffDays(latest, e) < 730)
      yearAgo = ends.find((e) => within(diffDays(latest, e), 350, 380)) ?? null
    }
  } else return null

  const ttm = (key: string, offset = 0): number | null => {
    const s = series[key]
    if (!s) return null
    if (basis === 'quarter') { // the latest quarter and the same one a year earlier
      const end = offset ? yearAgo : ends.at(-1)
      return end ? s.q.get(end) ?? null : null
    }
    if (basis === 'quarters') {
      const stop = ends.length - offset
      const window = ends.slice(Math.max(0, stop - 4), stop)
      const values = window.map((end) => s.q.get(end))
      return window.length < 4 || values.some((v) => v == null) ? null : (values as number[]).reduce((a, b) => a + b, 0)
    }
    const years = [...s.y.values()]
    const index = offset ? years.length - 2 : years.length - 1
    return index >= 0 ? years[index] : null
  }

  const keys = Object.keys(flowMap).filter((k) => k !== 'dividends_per_share')
  const result: Record<string, any> = {
    taxonomy,
    currency,
    basis,
    period_end: basis === 'annual' ? revY.at(-1) : ends.at(-1),
    ttm: Object.fromEntries(keys.map((k) => [k, ttm(k)])),
    ttm_prev: Object.fromEntries(keys.map((k) => [k, ttm(k, 4)])),
    quarters: ends.map((end) => ({
      end,
      ...Object.fromEntries(['revenue', 'gross_profit', 'operating_income', 'net_income', 'eps_diluted', 'dividends_per_share']
        .filter((k) => series[k]).map((k) => [k, series[k].q.get(end) ?? null])),
    })),
  }
  if (series.dividends_per_share && basis !== 'quarter') result.dividends_per_share_ttm = ttm('dividends_per_share')
  // diluted EPS for the twelve months to each quarter end (up to three
  // years of back-to-back quarters), for P/E against the company's own range
  if (series.eps_diluted && series.net_income && basis === 'quarters') {
    const epsQ = series.eps_diluted.q
    const incomeQ = series.net_income.q
    let run = backToBack([...epsQ.keys()]).slice(-15)
    // a quarter left at its pre-split figure (or a fourth quarter worked out
    // from one) shows as net income over EPS, the implied share count,
    // jumping away from its neighbours' or taking the wrong sign; the run
    // keeps only the quarters after the latest such break
    let later: number | null = null
    for (let i = run.length - 1; i >= 0; i--) {
      const eps = epsQ.get(run[i]) as number
      const income = incomeQ.get(run[i])
      const shares = income != null && Math.abs(eps) >= 0.1 ? income / eps : null
      if ((income != null && income * eps < 0) || (shares != null && later != null && (shares / later > 1.25 || shares / later < 0.8))) {
        run = run.slice(i + 1)
        break
      }
      if (shares != null) later = shares
    }
    result.eps_ttm = run.flatMap((end, k) => k < 3 ? []
      : [{ end, eps: round6(run.slice(k - 3, k + 1).reduce((sum, e) => sum + (epsQ.get(e) as number), 0)) }])
  }
  // up to six back-to-back fiscal years, for the chart's multi-year view
  const yearRun = consecutiveYears(revY)
  if (yearRun.length >= 3 && age(yearRun.at(-1) as string) <= MAX_AGE_DAYS.annual) {
    result.years = yearRun.slice(-6).map((end) => ({
      end,
      ...Object.fromEntries(['revenue', 'net_income'].filter((k) => series[k]).map((k) => [k, series[k].y.get(end) ?? null])),
    }))
  }
  if (taxonomy === 'us-gaap') {
    const history = dividendHistory(source)
    if (history) result.dividend_history = history
  }

  const balance: Record<string, any> = {}
  for (const [key, names] of Object.entries(instantMap)) {
    const picked = pickConcept(source, names)
    if (!picked) continue
    const fact = latestInstant(picked.items)
    if (fact) {
      balance[key] = fact.val
      balance.as_of ??= fact.end
    }
  }
  if (taxonomy === 'us-gaap') balance.shares_outstanding = sharesOutstanding(facts.dei ?? {}, source, basis !== 'quarter')
  if (balance.cash_and_short_term_investments == null && balance.cash != null && balance.short_term_investments != null) {
    balance.cash_and_short_term_investments = balance.cash + balance.short_term_investments
  }
  result.balance = balance

  // last twelve months of the cash-flow statement, and the twelve before
  const cashEnd = result.period_end as string
  const yearBefore = (revY.length && basis === 'annual') ? revY.at(-2) ?? null
    : (ends.length ? ends.find((e) => within(diffDays(cashEnd, e), 350, 380)) ?? null : null)
  const cashFlow: Record<string, number | string | null> = { period_end: cashEnd }
  for (const [key, names] of Object.entries(cashFlowMap)) {
    const picked = pickConcept(source, names)
    if (!picked || !cashEnd) continue
    cashFlow[`${key}_ttm`] = ttmFromYtd(picked.items, cashEnd)
    cashFlow[`${key}_ttm_prev`] = yearBefore ? ttmFromYtd(picked.items, yearBefore) : null
  }
  const fcf = (ocf: unknown, capex: unknown) => typeof ocf === 'number' && typeof capex === 'number' ? ocf - capex : null
  cashFlow.free_cash_flow_ttm = fcf(cashFlow.operating_cash_flow_ttm, cashFlow.capex_ttm)
  cashFlow.free_cash_flow_ttm_prev = fcf(cashFlow.operating_cash_flow_ttm_prev, cashFlow.capex_ttm_prev)
  if (basis !== 'quarter' && Object.keys(cashFlow).length > 1) {
    result.cash_flow = { ...cashFlow, ...cashFlowSeries(source, cashFlowMap, series.revenue, ends, yearRun) }
  }

  if (!result.period_end || age(result.period_end) > MAX_AGE_DAYS[basis]) return null

  let latestFiling: Fact | null = null
  for (const c of Object.values(source)) for (const items of Object.values(c.units)) for (const i of items) {
    if (i.filed && (!latestFiling || i.filed > (latestFiling.filed as string))) latestFiling = i
  }
  result.filed = latestFiling?.filed ?? null
  result.form = latestFiling?.form ?? null
  return result
}

export function secTicker(symbol: string) {
  return symbol.toUpperCase().replaceAll('.', '-') // BRK.B -> BRK-B, as SEC writes it
}
