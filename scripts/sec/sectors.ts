// A sector from SEC's own classification, for US companies Marketstack has
// no sector for. Every filer carries an SIC code and the SEC office that
// reviews its filings; both are mapped onto the sector names the cabinet
// uses (src/lib/sectors.ts). Used only to fill an empty sector, never to
// replace one, so a rough edge (Disney's 7990 reads as Consumer Cyclical,
// not Communication Services) costs little and users can still set their own.

// [first SIC, last SIC, sector], checked in order: narrow ranges before the
// broad ones they sit in
const SIC_RANGES: Array<[number, number, string]> = [
  // agriculture, forestry, fishing
  [100, 299, 'Consumer Defensive'], [700, 799, 'Consumer Defensive'], [800, 899, 'Basic Materials'], [900, 999, 'Consumer Defensive'],
  // mining
  [1000, 1099, 'Basic Materials'], [1200, 1399, 'Energy'], [1400, 1499, 'Basic Materials'],
  // construction: homebuilders, then contractors
  [1520, 1531, 'Consumer Cyclical'], [1500, 1799, 'Industrials'],
  // manufacturing
  [2000, 2199, 'Consumer Defensive'], // food, beverages, tobacco
  [2200, 2399, 'Consumer Cyclical'], // textiles, apparel
  [2400, 2499, 'Basic Materials'], [2500, 2599, 'Consumer Cyclical'], [2600, 2699, 'Basic Materials'],
  [2700, 2749, 'Communication Services'], [2750, 2799, 'Industrials'], // publishing, then printing
  [2830, 2836, 'Healthcare'], [2840, 2844, 'Consumer Defensive'], [2800, 2899, 'Basic Materials'], // drugs, soap and cosmetics, chemicals
  [2900, 2999, 'Energy'],
  [3011, 3011, 'Consumer Cyclical'], [3020, 3021, 'Consumer Cyclical'], [3000, 3099, 'Basic Materials'], // tires, footwear, then rubber and plastics
  [3100, 3199, 'Consumer Cyclical'], [3200, 3399, 'Basic Materials'], [3400, 3499, 'Industrials'],
  [3570, 3579, 'Technology'], [3500, 3599, 'Industrials'], // computers, then machinery
  [3630, 3639, 'Consumer Cyclical'], [3651, 3651, 'Technology'], [3660, 3679, 'Technology'], [3600, 3699, 'Industrials'],
  [3710, 3716, 'Consumer Cyclical'], [3750, 3751, 'Consumer Cyclical'], [3790, 3799, 'Consumer Cyclical'], [3700, 3799, 'Industrials'],
  [3812, 3812, 'Industrials'], [3826, 3826, 'Healthcare'], [3840, 3851, 'Healthcare'], [3873, 3873, 'Consumer Cyclical'], [3800, 3899, 'Technology'],
  [3900, 3949, 'Consumer Cyclical'], [3950, 3999, 'Industrials'],
  // transportation, communications, utilities
  [4700, 4700, 'Consumer Cyclical'], [4000, 4799, 'Industrials'], // travel services, then transport
  [4800, 4899, 'Communication Services'],
  [4922, 4923, 'Energy'], [4950, 4959, 'Industrials'], [4900, 4999, 'Utilities'], // pipelines, waste, utilities
  // wholesale
  [5045, 5045, 'Technology'], [5047, 5047, 'Healthcare'], [5065, 5065, 'Technology'], [5000, 5099, 'Industrials'],
  [5122, 5122, 'Healthcare'], [5171, 5172, 'Energy'], [5100, 5199, 'Consumer Defensive'],
  // retail
  [5331, 5331, 'Consumer Defensive'], [5400, 5499, 'Consumer Defensive'], [5912, 5912, 'Healthcare'], [5200, 5999, 'Consumer Cyclical'],
  // finance, insurance, real estate
  [6324, 6324, 'Healthcare'], [6500, 6599, 'Real Estate'], [6792, 6792, 'Energy'], [6795, 6795, 'Basic Materials'], [6798, 6798, 'Real Estate'],
  [6000, 6799, 'Financial Services'],
  // services
  [7000, 7299, 'Consumer Cyclical'],
  [7310, 7319, 'Communication Services'],
  [7320, 7329, 'Financial Services'], // credit ratings and credit bureaus: S&P Global, Moody's, Equifax
  [7370, 7379, 'Technology'], [7385, 7385, 'Communication Services'], [7389, 7389, ''], // business services NEC: too mixed
  [7300, 7399, 'Industrials'],
  [7500, 7599, 'Consumer Cyclical'], [7800, 7899, 'Communication Services'], [7900, 7999, 'Consumer Cyclical'],
  [8000, 8099, 'Healthcare'], [8100, 8199, 'Industrials'], [8200, 8299, 'Consumer Defensive'],
  [8731, 8731, 'Healthcare'], [8700, 8799, 'Industrials'],
]

// SEC offices that point at one sector. The office is broader than the SIC
// code (its Technology office also reviews Caterpillar and AT&T), so it
// only decides when the code is a catch-all.
const OFFICES: Array<[RegExp, string]> = [
  [/\bTechnology\b/, 'Technology'], [/\bLife Sciences\b/, 'Healthcare'], [/\bFinance\b/, 'Financial Services'],
]

/** The sector for an SEC filer, or null when its codes don't say clearly. */
export function sectorFromSec(sic: string | number | null | undefined, office: string | null | undefined,
  description: string | null | undefined): string | null {
  const code = Number(sic)
  const bySic = Number.isInteger(code) && code > 0 ? SIC_RANGES.find(([from, to]) => code >= from && code <= to)?.[2] || null : null
  // "NEC" (not elsewhere classified) codes are catch-alls: Ouster files under
  // "general industrial machinery, NEC" but is reviewed as Technology
  if (!bySic || /\bNEC\b/.test(description ?? '')) {
    const byOffice = OFFICES.find(([pattern]) => pattern.test(office ?? ''))?.[1]
    if (byOffice) return byOffice
  }
  return bySic
}

/** SEC's industry description without its "Services-" / "Retail-" prefixes. */
export function industryFromSec(description: string | null | undefined): string | null {
  const text = String(description ?? '').replace(/^Services-To /, 'Services to ')
    .replace(/^(Services|Retail|Wholesale)-/, '').replace(/\s+/g, ' ').trim()
  return text ? text.slice(0, 120) : null
}
