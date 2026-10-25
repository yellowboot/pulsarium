import copy
import unittest

import sec_report_checks as checks
import sec_financial_signals as f
from test_sec_financial_signals import TODAY, ACC, doc, meta, row, s


def flow(value, prior=None):
    values = [row(value, '2026-01-01', '2026-06-30')]
    if prior is not None:
        values.append(row(prior, '2025-01-01', '2025-06-30'))
    return values


def report():
    return doc({f.CFO[0]: flow(50_000_000, 100_000_000), 'NetIncomeLoss': flow(150_000_000, 100_000_000),
                'PaymentsToAcquirePropertyPlantAndEquipment': flow(20_000_000),
                'PaymentsForRepurchaseOfCommonStock': flow(100_000_000),
                'PaymentsOfDividendsCommonStock': flow(20_000_000),
                checks.REVENUE[0]: [row(250_000_000, '2026-04-01', '2026-06-30'), row(230_000_000, '2025-04-01', '2025-06-30')],
                'InventoryNet': [row(100_000_000, None, '2026-06-30'), row(50_000_000, None, '2025-06-30', '0000000001-25-000008')]})


class ReportChecks(unittest.TestCase):
    def test_large_full_histories_are_loaded_in_bounded_pages(self):
        from sec_read_cache import ReadCache, FRAME_CACHE
        from urllib.parse import parse_qs, urlsplit
        class Store:
            paths = []
            def cached_frames(self, prefix):
                metadata = [{'id': f'companyfacts/{i:010d}', 'fetched_at': 'v1'} for i in range(1, 14)]
                return ReadCache().rows(self.call, lambda _: metadata, 'sec_market_frames', '',
                                        FRAME_CACHE, 'id,payload_gzip,fetched_at')
            def call(self, path):
                self.paths.append(path)
                self_outer.assertIn('limit=12', path)
                ids = parse_qs(urlsplit(path).query)['id'][0][4:-1].split(',')
                return [{'id': key, 'payload_gzip': int(key.split('/')[1]), 'fetched_at': 'v1'} for key in ids]
        self_outer = self
        store = Store()
        result = f.histories(store, lambda value: {'doc': {'cik': value}})
        self.assertEqual(len(result), 13)
        self.assertEqual(len(store.paths), 2)
        self.assertEqual(result[13]['cik'], 13)

    def test_profitable_cash_divergence_uses_exact_ytd_yoy(self):
        groups, coverage = checks.analyze(meta(), report(), TODAY)
        self.assertTrue(all(coverage.values()))
        self.assertEqual(groups['earnings_cash'][0]['current'], 150_000_000)
        self.assertEqual(groups['earnings_cash'][0]['previous'], 100_000_000)
        self.assertEqual(groups['earnings_cash'][1]['change_pct'], -50)
        self.assertEqual(groups['earnings_cash'][0]['period_start'], '2026-01-01')

    def test_mixed_income_cash_periods_and_accessions_are_excluded(self):
        for key, value in [('start', '2026-04-01'), ('accn', '0000000001-26-000006')]:
            with self.subTest(key=key):
                d = report();d['facts']['us-gaap']['NetIncomeLoss']['units']['USD'][0][key] = value
                groups, coverage = checks.analyze(meta(), d, TODAY)
                self.assertFalse(coverage['earnings_cash'])
                self.assertEqual(groups['earnings_cash'], [])

    def test_first_quarter_and_stale_period_do_not_fall_back_to_old_report(self):
        for end in ['2026-03-31', '2026-09-30']:
            m = {**meta(), 'period_end': end}
            groups, coverage = checks.analyze(m, report(), TODAY)
            self.assertEqual(groups['earnings_cash'], [])
            self.assertEqual(groups['payouts'], [])

    def test_small_divergence_does_not_qualify(self):
        d = report();d['facts']['us-gaap'][f.CFO[0]]['units']['USD'][0]['val'] = 95_000_000
        self.assertEqual(checks.analyze(meta(), d, TODAY)[0]['earnings_cash'], [])

    def test_positive_profit_negative_cash_does_not_require_profit_growth(self):
        d = report();d['facts']['us-gaap'][f.CFO[0]]['units']['USD'][0]['val'] = -50_000_000
        d['facts']['us-gaap']['NetIncomeLoss']['units']['USD'][0]['val'] = 20_000_000
        first = checks.analyze(meta(), d, TODAY)[0]['earnings_cash'][0]
        self.assertIn('negative', first['label'])
        self.assertLess(first['change_pct'], 0)

    def test_payout_subtotal_capex_and_gap(self):
        first, free, *_ = checks.analyze(meta(), report(), TODAY)[0]['payouts']
        self.assertEqual(first['current'], 120_000_000)
        self.assertEqual(free['current'], 30_000_000)
        self.assertEqual(first['gap_amount'], 90_000_000)
        self.assertEqual(first['components'], ['buybacks', 'dividends'])

    def test_missing_payout_component_is_disclosed_and_missing_capex_excluded(self):
        d = report();del d['facts']['us-gaap']['PaymentsOfDividendsCommonStock']
        first = checks.analyze(meta(), d, TODAY)[0]['payouts'][0]
        self.assertEqual(first['current'], 100_000_000)
        self.assertEqual(first['components'], ['buybacks'])
        del d['facts']['us-gaap']['PaymentsToAcquirePropertyPlantAndEquipment']
        groups, coverage = checks.analyze(meta(), d, TODAY)
        self.assertFalse(coverage['payouts'])
        self.assertEqual(groups['payouts'], [])

    def test_dividend_alternative_tags_are_never_double_counted(self):
        d = report();d['facts']['us-gaap']['PaymentsOfDividends'] = {'units': {'USD': flow(30_000_000)}}
        self.assertEqual(checks.analyze(meta(), d, TODAY)[0]['payouts'][0]['current'], 120_000_000)

    def test_payout_cash_must_have_identical_boundaries(self):
        d = report();d['facts']['us-gaap']['PaymentsForRepurchaseOfCommonStock']['units']['USD'][0]['start'] = '2026-04-01'
        self.assertEqual(checks.analyze(meta(), d, TODAY)[0]['payouts'], [])

    def test_working_capital_yoy_has_separate_prior_source_and_revenue(self):
        values = checks.analyze(meta(), report(), TODAY)[0]['working_capital']
        self.assertEqual(values[0]['change_pct'], 100)
        self.assertEqual(values[0]['previous_end'], '2025-06-30')
        self.assertNotEqual(values[0]['previous_accession'], values[0]['accession'])
        self.assertAlmostEqual(values[0]['revenue_growth_pct'], 100 * (250 / 230 - 1))
        self.assertEqual(values[-1]['code'], 'report_revenue')

    def test_year_end_balance_cannot_replace_prior_year_quarter(self):
        d = report();d['facts']['us-gaap']['InventoryNet']['units']['USD'][1]['end'] = '2025-12-31'
        groups, coverage = checks.analyze(meta(), d, TODAY)
        self.assertEqual(groups['working_capital'], [])
        self.assertFalse(coverage['working_capital'])

    def test_matching_revenue_growth_is_not_a_working_capital_flag(self):
        d = report();d['facts']['us-gaap'][checks.REVENUE[0]]['units']['USD'][0]['val'] = 460_000_000
        self.assertEqual(checks.analyze(meta(), d, TODAY)[0]['working_capital'], [])

    def test_financial_businesses_and_future_facts_excluded(self):
        for sector in ('banks', 'insurance'):
            groups, coverage = checks.analyze(meta(sector), report(), TODAY)
            self.assertFalse(any(coverage.values()))
            self.assertFalse(any(groups.values()))
        d = report()
        for fact in d['facts']['us-gaap'].values():
            for r in fact['units']['USD']:
                r['filed'] = '2026-10-08'
        self.assertFalse(any(checks.analyze(meta(), d, TODAY)[0].values()))

    def test_relative_ranking_precedes_company_size(self):
        large, small = meta(), {**meta(), 'cik': '2', 'symbol': 'SMALL', 'revenue_current': 500_000_000}
        result = f.build([large, small], {}, {1: report(), 2: copy.deepcopy(report())}, TODAY, s.fiscal_quarters, '2026-10-07T12:00:00Z')
        for key in checks.GROUPS:
            self.assertEqual([r['symbol'] for r in result['groups'][key]], ['SMALL', 'TEST'])


if __name__ == '__main__':
    unittest.main()
