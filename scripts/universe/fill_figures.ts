// Own SEC figures for the Market Universe companies that our sector snapshot does not list (extra_symbols.json;
// the prototype showed them their sector's averages). For each one: its SEC company facts through the shared
// summarize(), then revenue of the latest quarter against the same quarter a year earlier, the change in operating
// margin over the same span, and revenue for the last twelve months. Output: extra_figures.json, read by
// build_data.py. A company that fails keeps its previous figures.
//
// Run by the SEC workflow; it does its work once a day (the file records the day) and is cheap to call again:
//   deno run --allow-net --allow-env --allow-read=scripts --allow-write=scripts/universe scripts/universe/fill_figures.ts
import { secGet, summarize } from '../sec/summarize.ts'

const here = (name: string) => new URL(`./${name}`, import.meta.url)
const today = new Date().toISOString().slice(0, 10)
const symbols: string[] = JSON.parse(await Deno.readTextFile(here('extra_symbols.json')))
let saved: Record<string, any> = {}
try { saved = JSON.parse(await Deno.readTextFile(here('extra_figures.json'))) } catch (_) { /* first run */ }
if (saved._updated === today && !Deno.args.includes('--force')) {
  console.log(`extra figures already refreshed on ${today}`)
  Deno.exit(0)
}

const directory = Object.values(await secGet('https://www.sec.gov/files/company_tickers.json')) as Array<{ cik_str: number; ticker: string }>
const cikOf = new Map(directory.map((row) => [row.ticker.toUpperCase(), String(row.cik_str).padStart(10, '0')]))
const round = (value: number, digits: number) => Math.round(value * 10 ** digits) / 10 ** digits
const margin = (q: { revenue: number; operating_income?: number | null }) => q.operating_income == null ? null : q.operating_income / q.revenue * 100
let fresh = 0
for (const ticker of symbols) {
  const cik = cikOf.get(ticker)
  if (!cik) { console.log(ticker, 'not in the SEC ticker directory'); continue }
  try {
    const summary: any = summarize(await secGet(`https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`))
    const quarters: any[] = summary?.quarters ?? []
    const last = quarters.at(-1), yearAgo = quarters.at(-5)
    if (!summary || summary.basis !== 'quarters' || !last?.revenue || !yearAgo?.revenue) { console.log(ticker, 'no comparable quarters'); continue }
    const now = margin(last), before = margin(yearAgo)
    saved[ticker] = {
      revYoY: round((last.revenue / yearAgo.revenue - 1) * 100, 1),
      opMarginDelta: now == null || before == null ? null : round(now - before, 2),
      ttmRevB: summary.ttm?.revenue ? round(summary.ttm.revenue / 1e9, 2) : null,
      periodEnd: last.end,
    }
    fresh++
  } catch (error) { console.log(ticker, error instanceof Error ? error.message : String(error)) }
}
if (fresh) {
  saved._updated = today
  await Deno.writeTextFile(here('extra_figures.json'), JSON.stringify(saved, null, 1) + '\n')
}
console.log(`extra figures: ${fresh} of ${symbols.length} refreshed`)
