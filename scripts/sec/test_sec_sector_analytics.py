import datetime as dt
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("analytics", Path(__file__).with_name("sec-sector-analytics.py"))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class Cache:
    def __init__(self, frame, stamp):
        self.record = {"payload_gzip": s.pack(frame), "fetched_at": stamp}
        self.writes = []
    def call(self, path): return [self.record]
    def upsert(self, *args): self.writes.append(args)


class SEC:
    def __init__(self): self.requests = []
    def get(self, url):
        self.requests.append(url)
        return {"data": [{"cik": 1, "val": 2}]}


def fact(value, start, end):
    return {"val": value, "start": start, "end": end, "accn": "0000000001-26-000001"}


class Analytics(unittest.TestCase):
    def setUp(self):
        self.cur, self.prev, self.annual = "CY2026Q2", "CY2025Q2", "CY2025"
        self.frames = {(c, f): {} for c in s.REVENUE + ["OperatingIncomeLoss"] for f in (self.cur, self.prev)}
        self.frames.update({(c, self.annual): {} for c in s.CFO + s.CAPEX})
        self.frames[s.REVENUE[0], self.cur][1] = fact(120, "2026-04-01", "2026-06-30")
        self.frames[s.REVENUE[0], self.prev][1] = fact(100, "2025-04-01", "2025-06-30")

    def test_historical_frame_is_never_refetched(self):
        cache = Cache({"data": []}, "2020-01-01T00:00:00+00:00")
        sec = SEC()
        s.load_frame(sec, cache, "Revenues", self.prev, self.cur)
        self.assertEqual(sec.requests, [])
        self.assertEqual(cache.writes, [])

    def test_only_latest_reporting_frame_refreshes_after_ttl(self):
        cache = Cache({"data": []}, "2026-10-06T00:00:00+00:00")
        sec = SEC()
        now = dt.datetime(2026, 10, 6, 5, tzinfo=dt.timezone.utc)
        s.load_frame(sec, cache, "Revenues", self.cur, self.cur, now)
        self.assertFalse(sec.requests)
        s.load_frame(sec, cache, "Revenues", self.cur, self.cur, now + dt.timedelta(hours=2))
        self.assertEqual(len(sec.requests), 1)
        self.assertEqual(len(cache.writes), 1)

    def test_reporting_windows_include_current_quarter_without_wait(self):
        self.assertEqual(s.reporting_window(dt.date(2026, 10, 6)), (2026, 4, 2025))
        self.assertEqual(s.quarter_frames(dt.date(2027, 1, 6))[-2:], ['CY2026Q4', 'CY2027Q1'])
        self.assertEqual(s.closed_quarter(dt.date(2027, 1, 6)), 'CY2026Q4')

    def test_refresh_appends_corrections_and_retains_absent_issuers(self):
        cache = Cache({'data': [{'cik': 1, 'val': 1}, {'cik': 2, 'val': 9}]}, '2026-10-06T00:00:00Z')
        doc = s.load_frame(SEC(), cache, 'Revenues', self.cur, {self.cur}, dt.datetime(2026, 10, 6, 8, tzinfo=dt.timezone.utc))
        self.assertEqual(doc['data'], [{'cik': 1, 'val': 1}, {'cik': 2, 'val': 9}, {'cik': 1, 'val': 2}])
        self.assertEqual(s.fact_index(doc)[1]['val'], 2)

    def test_annual_refresh_survives_year_turn_and_leaves_future_windows_cached(self):
        self.assertEqual(s.refresh_windows(dt.date(2027, 1, 6)), {'CY2026Q4', 'CY2026Q3', 'CY2026'})
        self.assertEqual(s.refresh_windows(dt.date(2026, 10, 7)), {'CY2026Q3', 'CY2026Q2', 'CY2026', 'CY2025'})
        self.assertEqual(s.refresh_windows(dt.date(2026, 12, 6)), {'CY2026Q4', 'CY2026Q3', 'CY2026'})

    def ttm_pools(self):
        rows = []
        for year in (2024, 2025, 2026):
            for quarter in range(1, 5):
                if year == 2026 and quarter > 3: continue
                month = quarter * 3
                end = dt.date(year, month, s.calendar.monthrange(year, month)[1]).isoformat()
                rows.append(fact(100 if year < 2026 else 120, f'{year}-{month-2:02d}-01', end))
        return {s.REVENUE[0]: {1: rows}}

    def test_latest_report_is_used_in_early_october(self):
        pools = self.ttm_pools()
        meta = {'sic': 7372, 'symbol': 'TEST', 'name': 'Test'}
        m = s.latest_company_metrics(1, meta, pools, dt.date(2026, 10, 7))
        self.assertEqual(m['period_end'], '2026-09-30')
        self.assertEqual(m['revenue_current'], 460)
        self.assertEqual(m['revenue_previous'], 400)
        self.assertAlmostEqual(m['revenue_yoy'], 15)
        self.assertEqual(m['quarter_revenue_current'], 120)
        self.assertAlmostEqual(m['quarter_revenue_yoy'], 20)

    def test_ttm_gaps_ytd_and_concept_switch_do_not_make_comparison(self):
        pools = self.ttm_pools()
        pools[s.REVENUE[0]][1] = [r for r in pools[s.REVENUE[0]][1] if r['end'] != '2025-12-31']
        self.assertIsNone(s.latest_revenue(1, pools, dt.date(2026, 10, 7)))
        pools = self.ttm_pools()
        for row in pools[s.REVENUE[0]][1]:
            if row['end'].endswith('06-30'): row['start'] = row['end'][:4] + '-01-01'
        self.assertIsNone(s.latest_revenue(1, pools, dt.date(2026, 10, 7)))
        rows = self.ttm_pools()[s.REVENUE[0]][1]
        self.assertIsNone(s.latest_revenue(1, {s.REVENUE[0]: {1: rows[-4:]}, s.REVENUE[1]: {1: rows[:-4]}}, dt.date(2026, 10, 7)))

    def test_fiscal_q4_and_annual_correction_for_september_year_end(self):
        rows = [fact(110, '2024-10-01', '2024-12-31'), fact(120, '2025-01-01', '2025-03-31'),
                fact(130, '2025-04-01', '2025-06-30'), fact(500, '2024-10-01', '2025-09-30'),
                fact(510, '2024-10-01', '2025-09-30')]
        quarter = s.fiscal_quarters(rows)[-1]
        self.assertEqual((quarter['start'], quarter['end'], quarter['val']), ('2025-07-01', '2025-09-30', 150))
        rows[1]['start'] = '2024-09-01'
        self.assertEqual(len(s.fiscal_quarters(rows)), 2)

    def test_missing_q1_is_recovered_from_ytd_without_switching_concept(self):
        pools = self.ttm_pools()
        pools[s.REVENUE[0]][1] = [r for r in pools[s.REVENUE[0]][1] if r['end'] not in ('2024-03-31', '2024-12-31')]
        pools[s.REVENUE[0]][1].append(fact(400, '2024-01-01', '2024-12-31'))
        self.assertIsNone(s.latest_revenue(1, pools, dt.date(2026, 7, 7)))
        pools[s.REVENUE[0]][1].append(fact(200, '2024-01-01', '2024-06-30'))
        pair = s.latest_revenue(1, pools, dt.date(2026, 7, 7))
        self.assertEqual(pair[1][0]['val'], 100)
        self.assertEqual(pair[1][0]['end'], '2024-09-30')
        quarters = s.fiscal_quarters(pools[s.REVENUE[0]][1])
        q1 = next(r for r in quarters if r['end'] == '2024-03-31')
        self.assertEqual((q1['start'], q1['val'], q1['derived']), ('2024-01-01', 100, True))
        # A cumulative fact under another concept cannot fill this gap.
        pools[s.REVENUE[1]] = {1: [pools[s.REVENUE[0]][1].pop()]}
        self.assertFalse(any(r['end'] == '2024-03-31' for r in s.fiscal_quarters(pools[s.REVENUE[0]][1])))

    def test_cumulative_differences_recover_missing_quarters_and_keep_direct_values(self):
        rows = [fact(100, '2025-01-01', '2025-03-31'), fact(220, '2025-01-01', '2025-06-30'),
                fact(350, '2025-01-01', '2025-09-30'), fact(500, '2025-01-01', '2025-12-31')]
        self.assertEqual([r['val'] for r in s.fiscal_quarters(rows)], [100, 120, 130, 150])
        rows.append(fact(125, '2025-04-01', '2025-06-30'))
        self.assertEqual(s.fiscal_quarters(rows)[1]['val'], 125)
        rows = [fact(300, '2025-01-02', '2025-09-30'), fact(200, '2025-01-01', '2025-06-30')]
        self.assertEqual(s.fiscal_quarters(rows), [])

    def test_ttm_aggregation_ranks_quarterly_revenue_and_shows_new_quarter_coverage(self):
        m = s.latest_company_metrics(1, {'sic': 7372, 'symbol': 'NEW', 'name': 'New'}, self.ttm_pools(), dt.date(2026, 10, 7))
        older = {**m, 'cik': '2', 'symbol': 'OLD', 'reporting_frame': 'CY2026Q2', 'period_end': '2026-06-30',
                 'revenue_previous': 1600, 'revenue_current': 1800, 'quarter_revenue_current': 110}
        result = s.aggregate([m, older], {}, {}, 'Latest reported', 'Latest annual', {}, dt.date(2026, 10, 7))
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['coverage']['latest_report_count'], 1)
        self.assertEqual(result['coverage']['latest_report_revenue_pct'], 20)
        self.assertEqual(result['coverage']['reporting_target'], 'Q3 2026')
        self.assertEqual(result['sectors'][0]['companies'][0]['symbol'], 'NEW')
        self.assertEqual(result['sectors'][0]['companies'][0]['revenue_current'], 120)
        self.assertAlmostEqual(result['sectors'][0]['companies'][0]['revenue_yoy'], 20)
        self.assertEqual(result['sectors'][0]['revenue_current'], 2260)

    def test_ttm_margin_and_latest_matching_annual_cash_flow(self):
        pools = self.ttm_pools()
        pools['OperatingIncomeLoss'] = {1: [{**r, 'val': r['val'] * (.2 if r['end'].startswith('2026') else .1)} for r in pools[s.REVENUE[0]][1]]}
        pools[s.CFO[0]] = {1: [fact(60, '2025-04-01', '2026-03-31')]}
        pools[s.CAPEX[0]] = {1: [fact(70, '2025-04-01', '2026-03-31')]}
        m = s.latest_company_metrics(1, {'sic': 7372, 'symbol': 'TEST', 'name': 'Test'}, pools, dt.date(2026, 10, 7))
        self.assertAlmostEqual(m['margin_change'], 100 * (82 / 460 - .1))
        self.assertEqual((m['fcf'], m['fcf_period_end']), (-10, '2026-03-31'))

    def test_twelve_and_sixteen_week_fiscal_quarters_make_valid_ttm(self):
        rows = []
        for start, ends, value in [('2023-09-04', ['2023-11-26', '2024-02-18', '2024-05-12', '2024-09-01'], 100),
                                   ('2024-09-02', ['2024-11-24', '2025-02-16', '2025-05-11', '2025-08-31'], 120),
                                   ('2025-09-01', ['2025-11-23', '2026-02-15', '2026-05-10'], 130)]:
            cursor = start
            for end in ends[:3]:
                rows.append(fact(value, cursor, end))
                cursor = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
            if len(ends) == 4: rows.append(fact(value * 4, start, ends[-1]))
        pair = s.latest_revenue(1, {s.REVENUE[0]: {1: rows}}, dt.date(2026, 10, 7))
        self.assertIsNotNone(pair)
        self.assertEqual(sum(r['val'] for r in pair[0]), 510)
        self.assertEqual(sum(r['val'] for r in pair[1]), 460)
        self.assertEqual(s.duration(pair[0][0]), 112)

    def test_significant_missing_issuer_uses_one_permanent_company_history(self):
        class HistoryCache:
            def __init__(self): self.records = {}
            def call(self, path):
                key = path.split('id=eq.')[1].split('&')[0]
                return [self.records[key]] if key in self.records else []
            def upsert(self, table, key, row): self.records[row['id']] = row
        class Source:
            def __init__(self, doc): self.requests = []; self.doc = doc
            def get(self, url): self.requests.append(url); return self.doc
        from collections import defaultdict
        pools = defaultdict(lambda: defaultdict(list))
        rows = self.ttm_pools()[s.REVENUE[0]][1]
        doc = {'facts': {'us-gaap': {s.REVENUE[0]: {'units': {'USD': rows}}}}}
        pools[s.REVENUE[0]][1] = [{**rows[-1], 'val': 6e9}]
        source, cache, eligible = Source(doc), HistoryCache(), {}
        for _ in range(2):
            s.supplement_large_issuers(source, cache, {1: {'symbol': 'LARGE'}}, pools, eligible, dt.date(2026, 10, 7))
        self.assertEqual(len(source.requests), 1)
        self.assertIn(1, eligible)
        self.assertEqual(s.unpack(cache.records['companyfacts/0000000001']['payload_gzip'])['doc'], doc)

    def test_large_issuer_with_older_comparison_checks_latest_report(self):
        from collections import defaultdict
        rows = [{**r, 'val': r['val'] * 60_000_000} for r in self.ttm_pools()[s.REVENUE[0]][1]]
        pools = defaultdict(lambda: defaultdict(list))
        pools[s.REVENUE[0]][1] = rows[:-1]
        eligible = {1: s.latest_revenue(1, pools, dt.date(2026, 10, 7))}
        pools[s.REVENUE[1]][1] = [rows[-1]]
        class Source:
            def __init__(self): self.requests = []
            def get(self, url):
                self.requests.append(url)
                return {'facts': {'us-gaap': {s.REVENUE[0]: {'units': {'USD': rows}}}}}
        class EmptyCache:
            def call(self, path): return []
            def upsert(self, *args): pass
        sec = Source()
        s.supplement_large_issuers(sec, EmptyCache(), {1: {'symbol': 'BIG'}}, pools, eligible, dt.date(2026, 10, 7))
        self.assertEqual(len(sec.requests), 1)
        self.assertEqual(eligible[1][0][-1]['end'], '2026-09-30')

    def test_security_filter_preserves_businesses_and_excludes_instruments(self):
        cases = [
            ('Our Bond, Inc. - Common Stock', 'N', 'equity'),
            ('Visa Inc.', 'N', 'equity'),
            ('Logitech - Registered Shares', 'N', 'equity'),
            ('Energy Transfer LP Common Units', 'N', 'equity'),
            ('Real Estate Investment Trust', 'N', 'equity'),
            ('Company American Depositary Shares, each representing one unit', 'N', 'equity'),
            ('Empire State Realty OP, L.P. Units Representing Limited Partnership Interests', 'N', 'equity'),
            ('Belpointe PREP, LLC Class A Units', 'N', 'equity'),
            ('United States Oil Fund', 'Y', 'fund'),
            ('Closed-End Fund Common Shares', 'N', 'fund'),
            ('Exchange-Traded Note', 'N', 'fund'),
            ('Trust Preference Securities', 'N', 'preferred'),
            ('Company Preferred Stock', 'N', 'preferred'),
            ('Company 6% Notes due 2049', 'N', 'debt'),
            ('Company First Mortgage Bonds', 'N', 'debt'),
            ('Company - Warrants', 'N', 'warrant'),
            ('Company - Rights', 'N', 'rights'),
            ('Acquisition Corp. - Unit', 'N', 'units'),
            ('Company Tangible Equity Unit', 'N', 'units'),
            ('Company Units, each consisting of Common Stock and Warrants', 'N', 'warrant'),
        ]
        for name, etf, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(s.listing_kind({'Security Name': name, 'ETF': etf}), expected)
        self.assertEqual(s.listing_kind({'Security Name': 'Test Common Stock', 'Test Issue': 'Y'}), 'test')
        self.assertEqual(s.listing_kind({'ACT Symbol': 'SCE$L', 'Security Name': 'SCE TRUST VI', 'ETF': 'N'}), 'preferred')

    def test_security_aliases_do_not_merge_preferred_suffix_with_common_ticker(self):
        nasdaq = 'Symbol|Security Name|ETF\nBCPC|Balchem - Common Stock|N\n'
        other = 'ACT Symbol|Security Name|CQS Symbol|NASDAQ Symbol|ETF\nBC$C|Brunswick Notes due 2049|BCpC|BC-C|N\nBRK.B|Berkshire Common Stock|BRK.B|BRK-B|N\n'
        listings = {**s.parse_listings(nasdaq), **s.parse_listings(other)}
        self.assertEqual(listings['BCPC'], 'equity')
        self.assertEqual(listings['BC-PC'], 'debt')
        self.assertEqual(listings['BRK-B'], 'equity')
        directory = {'fields': ['cik', 'ticker', 'name', 'exchange'], 'data': [
            [1, 'BC-PC', 'Brunswick', 'NYSE'], [1, 'BCPC', 'Business', 'Nasdaq'],
            [1, 'BRK-B', 'Second common class', 'NYSE'], [2, 'UNKNOWN', 'Unknown', 'NYSE']]}
        self.assertEqual(s.listed_issuers(directory, listings), {1: {'symbol': 'BCPC', 'name': 'Business', 'exchange': 'Nasdaq'}})

    def test_daily_security_metadata_cache_prevents_visitor_or_repeat_fetches(self):
        class DirectoryCache:
            def call(self, path):
                return [{'payload': {'date': '2026-10-06', 'symbols': {'MOVE': 'equity', 'USO': 'fund'}}}]
        with patch.object(s.urllib.request, 'urlopen') as request:
            self.assertEqual(s.load_listing_directory(DirectoryCache(), dt.date(2026, 10, 6), 'Test'), {'MOVE': 'equity', 'USO': 'fund'})
            request.assert_not_called()

    def test_no_ytd_or_different_revenue_concept_comparison(self):
        self.assertIsNotNone(s.revenue_pair(1, self.frames, self.cur, self.prev))
        self.frames[s.REVENUE[0], self.cur][1]["start"] = "2026-01-01"
        self.assertIsNone(s.revenue_pair(1, self.frames, self.cur, self.prev))
        self.frames[s.REVENUE[0], self.cur][1]["start"] = "2026-04-01"
        self.frames[s.REVENUE[1], self.prev] = self.frames[s.REVENUE[0], self.prev]
        self.frames[s.REVENUE[0], self.prev] = {}
        self.assertIsNone(s.revenue_pair(1, self.frames, self.cur, self.prev))

    def test_margin_period_matching_and_annual_fcf(self):
        self.frames["OperatingIncomeLoss", self.cur][1] = fact(24, "2026-04-01", "2026-06-30")
        self.frames["OperatingIncomeLoss", self.prev][1] = fact(10, "2025-04-01", "2025-06-30")
        self.frames[s.CFO[0], self.annual][1] = fact(30, "2025-01-01", "2025-12-31")
        self.frames[s.CAPEX[0], self.annual][1] = fact(20, "2025-01-01", "2025-12-31")
        m = s.company_metrics(1, {"sic": 7372, "symbol": "TEST", "name": "Test"}, self.frames, self.cur, self.prev, self.annual)
        self.assertAlmostEqual(m["revenue_yoy"], 20)
        self.assertEqual(m["revenue_current"], 120)
        self.assertEqual(m["revenue_previous"], 100)
        self.assertEqual(m["revenue_previous_end"], "2025-06-30")
        self.assertAlmostEqual(m["margin_change"], 10)
        self.assertEqual(m["fcf"], 10)
        self.frames["OperatingIncomeLoss", self.cur][1]["start"] = "2026-01-01"
        m = s.company_metrics(1, {"sic": 6021, "symbol": "BANK", "name": "Bank"}, self.frames, self.cur, self.prev, self.annual)
        self.assertIsNone(m["margin_change"])
        self.assertIsNone(m["fcf"])

    def test_industry_uses_sic_without_guessing_unknowns(self):
        self.assertEqual(s.industry("3674"), "semiconductors")
        self.assertEqual(s.industry(2834), "pharmaceuticals")
        self.assertEqual(s.industry(4922), "energy")
        self.assertEqual(s.industry(6324), "healthcare")
        self.assertIsNone(s.industry(9999))

    def test_fourth_quarter_uses_contiguous_periods_only(self):
        concept = s.REVENUE[0]
        frames = {(concept, 'CY2025'): {1: fact(500, '2025-01-01', '2025-12-31')},
                  (concept, 'CY2025Q1'): {1: fact(100, '2025-01-01', '2025-03-31')},
                  (concept, 'CY2025Q2'): {1: fact(110, '2025-04-01', '2025-06-30')},
                  (concept, 'CY2025Q3'): {1: fact(120, '2025-07-01', '2025-09-30')},
                  (concept, 'CY2025Q4'): {}}
        s.fourth_quarter(frames, concept, 2025)
        self.assertEqual(frames[concept, 'CY2025Q4'][1]['val'], 170)
        self.assertEqual(frames[concept, 'CY2025Q4'][1]['start'], '2025-10-01')
        frames[concept, 'CY2025Q4'] = {}
        frames[concept, 'CY2025Q2'][1]['start'] = '2025-01-01'
        s.fourth_quarter(frames, concept, 2025)
        self.assertEqual(frames[concept, 'CY2025Q4'], {})

    def test_pressure_mixed_and_small_samples(self):
        rows = [{"cik": str(i), "symbol": "TEST", "name": "Test", "sector": "retail", "revenue_yoy": -5,
                 "revenue_current": 95, "revenue_previous": 100,
                 "margin_change": -1, "fcf": None, "period_end": "2026-06-30", "source": None} for i in range(10)]
        def direction():
            return s.aggregate(rows, {}, {}, "Q2 2026", "2025", {}, dt.date(2026, 10, 6))["sectors"][0]
        self.assertEqual(direction()["direction"], "pressure")
        self.assertIsNone(direction()["fcf_positive_pct"])
        for r in rows: r["margin_change"] = 1
        self.assertEqual(direction()["direction"], "mixed")
        rows.pop()
        self.assertEqual(direction()["direction"], "limited")

    def test_revenue_totals_weight_size_and_retain_equal_company_breadth(self):
        # Nine tiny issuers grow quickly while one large issuer contracts.
        rows = [{"cik": str(i), "symbol": "SMALL", "name": "Small", "sector": "automotive",
                 "revenue_current": 836_000, "revenue_previous": 54_000,
                 "revenue_yoy": 100 * (836_000 / 54_000 - 1), "margin_change": -1,
                 "fcf": None, "period_end": "2026-06-30", "source": None} for i in range(9)]
        rows.append({**rows[0], "cik": "10", "symbol": "LARGE", "revenue_current": 90_000_000_000,
                     "revenue_previous": 100_000_000_000, "revenue_yoy": -10})
        result = s.aggregate(rows, {}, {}, "Q2 2026", "2025", {}, dt.date(2026, 10, 6))["sectors"][0]
        self.assertEqual(result["direction"], "pressure")
        self.assertEqual(result["revenue_total_yoy"], -9.99)
        self.assertEqual(result["revenue_yoy"], 1448.15)
        self.assertEqual(result["growing_count"], 9)
        self.assertEqual(result["declining_count"], 1)
        self.assertEqual(result["revenue_current"], sum(r["revenue_current"] for r in rows))
        self.assertEqual(result["revenue_previous"], sum(r["revenue_previous"] for r in rows))

    def test_low_base_flag_uses_prior_year_revenue_and_preserves_dollars(self):
        self.frames[s.REVENUE[0], self.cur][1]["val"] = 836_000
        self.frames[s.REVENUE[0], self.prev][1]["val"] = 54_000
        meta = {"sic": 3711, "symbol": "FFAI", "name": "Faraday"}
        result = s.company_metrics(1, meta, self.frames, self.cur, self.prev, self.annual)
        self.assertTrue(result["low_revenue_base"])
        self.assertEqual(result["revenue_current"], 836_000)
        self.assertEqual(result["revenue_previous"], 54_000)
        self.assertAlmostEqual(result["revenue_yoy"], 1448.148148148148)
        self.frames[s.REVENUE[0], self.prev][1]["val"] = 1_000_000
        self.assertFalse(s.company_metrics(1, meta, self.frames, self.cur, self.prev, self.annual)["low_revenue_base"])

    def test_unmatched_issuer_is_excluded_from_both_revenue_totals(self):
        self.frames[s.REVENUE[0], self.cur][2] = fact(100_000_000, "2026-04-01", "2026-06-30")
        meta = {"sic": 7372, "symbol": "TEST", "name": "Test"}
        rows = [s.company_metrics(cik, meta, self.frames, self.cur, self.prev, self.annual) for cik in (1, 2)]
        result = s.aggregate([r for r in rows if r], {}, {}, "Q2 2026", "2025", {}, dt.date(2026, 10, 6))["sectors"][0]
        self.assertEqual(result["company_count"], 1)
        self.assertEqual(result["revenue_current"], 120)
        self.assertEqual(result["revenue_previous"], 100)
        self.assertEqual(result["revenue_total_yoy"], 20)

    def test_company_ranking_uses_revenue_and_includes_large_decliners(self):
        values = [("LARGE_DOWN", 90e9, 100e9), ("LARGE_UP", 70e9, 60e9),
                  ("THIRD", 50e9, 50e9), ("FOURTH", 40e9, 38e9),
                  ("FIFTH", 30e9, 33e9), ("SIXTH", 20e9, 20e9),
                  ("SMALL_UP", 836_000, 54_000), ("SMALL_DOWN", 1_000, 2_000)]
        rows = [{"cik": str(i), "symbol": symbol, "name": symbol, "sector": "automotive",
                 "revenue_current": current, "revenue_previous": previous,
                 "revenue_yoy": 100 * (current / previous - 1), "margin_change": None,
                 "fcf": None, "period_end": "2026-06-30", "source": None}
                for i, (symbol, current, previous) in enumerate(reversed(values))]
        result = s.aggregate(rows, {}, {}, "Q2 2026", "2025", {}, dt.date(2026, 10, 6))
        self.assertEqual(result["company_ranking"], "revenue")
        companies = result["sectors"][0]["companies"]
        self.assertEqual([c["symbol"] for c in companies], [symbol for symbol, _, _ in values[:6]])
        self.assertEqual(companies[0]["revenue_yoy"], -10)
        self.assertEqual(result["coverage"]["included"], 8)

    def test_insider_plans_duplicates_and_amendments(self):
        t = {"date": "2026-10-05", "direct": True, "private": False, "currency": "USD", "planned": True,
             "amount": 1000, "security": "Common", "shares": 10, "price": 100}
        f = {"form": "4", "issuer_cik": "1", "owners": [{"cik": "9", "officer": True}],
             "filed_date": "2026-10-06", "purchases": [t], "sales": [t], "notes": {}}
        state = {"sales_history_from": "2026-10-06"}
        totals = s.insider_totals([f, f], state, {1: {"sic": 7372}}, dt.date(2026, 10, 6))["software"]
        self.assertEqual(totals["purchases"], 0)
        self.assertEqual(totals["sales"], 1000)
        self.assertEqual(totals["planned_sales"], 1000)
        amendment = {**f, "form": "4/A", "original_date": "2026-10-06"}
        self.assertFalse(s.insider_totals([f, amendment], state, {1: {"sic": 7372}}, dt.date(2026, 10, 6)))


if __name__ == "__main__": unittest.main()
