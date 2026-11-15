// Own SEC figures for the Market Universe companies that our sector snapshot does not carry (the prototype showed
// them their sector's averages). Reads each company's SEC company facts and works out, with the shared summarize():
// revenue of the latest quarter against the same quarter a year earlier, the change in operating margin over the
// same span, and revenue for the last twelve months. Output: scripts/universe/extra_figures.json, read by
// build_universe.py.
//
// summarize.ts imports an npm: module that only Deno resolves; under Node run it against a copy whose import line
// is replaced by a stub (the XML parser is not used here):
//   node fill_figures.ts <path to that copy> "<contact email for the SEC User-Agent>" UCTT COLM ...
import { writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const [modulePath, contact, ...tickers] = process.argv.slice(2)
const { summarize } = await import(pathToFileURL(modulePath).href)
const headers = { 'User-Agent': `Pulsarium universe figures ${contact}`, 'Accept-Encoding': 'gzip' }
const get = async (url: string) => {
  const response = await fetch(url, { headers })
  if (!response.ok) throw new Error(`SEC ${response.status} for ${url}`)
  return response.json()
}
const directory = Object.values(await get('https://www.sec.gov/files/company_tickers.json')) as Array<{ cik_str: number; ticker: string }>
const cikOf = new Map(directory.map((row) => [row.ticker.toUpperCase(), String(row.cik_str).padStart(10, '0')]))
const round = (value: number, digits: number) => Math.round(value * 10 ** digits) / 10 ** digits
const out: Record<string, unknown> = {}
for (const ticker of tickers) {
  const cik = cikOf.get(ticker)
  if (!cik) { console.log(ticker, 'no CIK'); continue }
  try {
    const summary = summarize(await get(`https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`))
    const quarters = summary?.quarters ?? []
    const last = quarters.at(-1), yearAgo = quarters.at(-5)
    if (!summary || summary.basis !== 'quarters' || !last?.revenue || !yearAgo?.revenue) { console.log(ticker, 'no comparable quarters'); continue }
    const margin = (q: { revenue: number; operating_income?: number | null }) => q.operating_income == null ? null : q.operating_income / q.revenue * 100
    const now = margin(last), before = margin(yearAgo)
    out[ticker] = {
      revYoY: round((last.revenue / yearAgo.revenue - 1) * 100, 1),
      opMarginDelta: now == null || before == null ? null : round(now - before, 2),
      ttmRevB: summary.ttm?.revenue ? round(summary.ttm.revenue / 1e9, 2) : null,
      periodEnd: last.end,
    }
    console.log(ticker, JSON.stringify(out[ticker]))
  } catch (error) { console.log(ticker, String(error)) }
  await new Promise((resolve) => setTimeout(resolve, 150))
}
writeFileSync(new URL('./extra_figures.json', import.meta.url), JSON.stringify(out, null, 1) + '\n')
console.log(Object.keys(out).length, 'of', tickers.length)
