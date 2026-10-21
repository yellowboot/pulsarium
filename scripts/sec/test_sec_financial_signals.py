import calendar
import datetime as dt
import importlib.util
import unittest
from collections import defaultdict
from pathlib import Path

import sec_financial_signals as f

spec = importlib.util.spec_from_file_location('sectors', Path(__file__).with_name('sec-sector-analytics.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
TODAY = dt.date(2026, 10, 7)
ACC = '0000000001-26-000007'


def row(value, start, end, accn=ACC):
    return {'val': value, 'start': start, 'end': end, 'accn': accn, 'filed': '2026-08-01', 'form': '10-Q'}


def doc(concepts):
    return {'facts': {'us-gaap': {key: {'units': {'shares' if key in f.BASIC else 'USD': values}}
                                for key, values in concepts.items()}}}


def meta(sector='software'):
    return {'cik': '1', 'symbol': 'TEST', 'name': 'Test Business', 'sector': sector,
            'period_end': '2026-06-30', 'quarter_period_start': '2026-04-01',
            'quarter_previous_start': '2025-04-01', 'revenue_current': 1_000_000_000,
            'quarter_revenue_current': 250_000_000, 'quarter_revenue_previous': 230_000_000}


class Signals(unittest.TestCase):
    def revenue_history(self, end):
        last = dt.date.fromisoformat(end)
        month = last.year * 12 + last.month - 1
        values = []
        for offset in range(7, -1, -1):
            at = month - 3 * offset
            year, end_month = at // 12, at % 12 + 1
            start = at - 2
            values.append(row(250_000_000, f'{start // 12:04d}-{start % 12 + 1:02d}-01',
                              f'{year:04d}-{end_month:02d}-{calendar.monthrange(year, end_month)[1]:02d}'))
        return values

    def test_latest_calendar_and_offset_fiscal_reports_replace_older_period(self):
        for end in ('2026-09-30', '2026-08-31'):
            with self.subTest(end=end):
                revenues = self.revenue_history(end)
                for r in revenues:
                    r['filed'] = '2026-10-05'
                d = doc({s.REVENUE[0]: revenues})
                pools = defaultdict(lambda: defaultdict(list))
                updated = f.latest_metrics([meta()], pools, {1: d}, TODAY, s)
                self.assertEqual(updated[0]['period_end'], end)
                self.assertEqual(updated[0]['quarter_period_start'], revenues[-1]['start'])
                self.assertEqual(updated[0]['quarter_previous_start'], revenues[-5]['start'])

    def test_newer_uncomparable_report_suppresses_older_signal(self):
        old = self.revenue_history('2026-06-30')
        newer = row(250_000_000, '2026-06-01', '2026-08-31')
        newer['filed'] = '2026-10-05'
        d = doc({s.REVENUE[0]: old + [newer]})
        pools = defaultdict(lambda: defaultdict(list))
        self.assertEqual(f.latest_metrics([meta()], pools, {1: d}, TODAY, s), [])
        newer['filed'] = '2026-10-08'
        self.assertEqual(f.latest_metrics([meta()], defaultdict(lambda: defaultdict(list)), {1: d}, TODAY, s)[0]['period_end'], '2026-06-30')

    def test_operating_recovery_is_yoy_and_material(self):
        pools = {'OperatingIncomeLoss': {1: [row(-10_000_000, '2025-04-01', '2025-06-30'),
                                                 row(20_000_000, '2026-04-01', '2026-06-30')]}}
        signals, covered = f.operating_signals(meta(), pools, s.fiscal_quarters)
        self.assertTrue(covered)
        self.assertEqual(signals[0]['code'], 'operating_turnaround')
        self.assertNotIn('change_pct', signals[0])  # No meaningless growth % across zero.
        pools['OperatingIncomeLoss'][1][-1]['val'] = 500_000
        self.assertEqual(f.operating_signals(meta(), pools, s.fiscal_quarters)[0], [])

    def test_misaligned_periods_and_missing_prior_are_excluded(self):
        pools = {'OperatingIncomeLoss': {1: [row(-10_000_000, '2025-04-01', '2025-06-30'),
                                                 row(20_000_000, '2026-03-01', '2026-06-30')]}}
        self.assertEqual(f.operating_signals(meta(), pools, s.fiscal_quarters), ([], False))
        self.assertEqual(f.operating_signals(meta(), {}, s.fiscal_quarters), ([], False))

    def test_two_quarter_persistence_requires_a_contiguous_three_quarter_run(self):
        values = [row(-10_000_000, '2025-04-01', '2025-06-30'), row(-10_000_000, '2025-10-01', '2025-12-31'),
                  row(5_000_000, '2026-01-01', '2026-03-31'), row(20_000_000, '2026-04-01', '2026-06-30')]
        signals, _ = f.operating_signals(meta(), {'OperatingIncomeLoss': {1: values}}, s.fiscal_quarters)
        self.assertIn('two quarters', signals[0]['label'])
        self.assertEqual([q['current'] for q in signals[0]['quarters']], [-10_000_000, 5_000_000, 20_000_000])
        values[-2]['start'] = '2026-01-20'
        signals, _ = f.operating_signals(meta(), {'OperatingIncomeLoss': {1: values}}, s.fiscal_quarters)
        self.assertNotIn('two quarters', signals[0]['label'])

    def test_share_comparatives_from_same_report_protect_against_split(self):
        d = doc({f.BASIC[0]: [row(100, '2025-04-01', '2025-06-30', '0000000001-25-000007'),
                               row(1000, '2025-04-01', '2025-06-30'), row(990, '2026-04-01', '2026-06-30')]})
        pair = f.report_share_pair(d, '2026-06-30', TODAY)
        self.assertEqual(pair[2]['val'], 1000)
        self.assertEqual(f.full_signals(meta(), d, TODAY, s.fiscal_quarters)[1], [])
        d['facts']['us-gaap'][f.BASIC[0]]['units']['shares'].pop(1)
        self.assertIsNone(f.report_share_pair(d, '2026-06-30', TODAY))

    def test_diluted_eps_denominator_is_not_used(self):
        d = doc({'WeightedAverageNumberOfDilutedSharesOutstanding': [row(120, '2026-04-01', '2026-06-30')]})
        self.assertIsNone(f.report_share_pair(d, '2026-06-30', TODAY))

    def test_buyback_cash_is_context_with_its_own_period(self):
        d = doc({f.BASIC[0]: [row(1000, '2025-04-01', '2025-06-30'), row(950, '2026-04-01', '2026-06-30')],
                 'PaymentsForRepurchaseOfCommonStock': [row(100_000_000, '2026-01-01', '2026-06-30')]})
        shares = f.full_signals(meta(), d, TODAY, s.fiscal_quarters)[1]
        self.assertEqual(shares[0]['change_pct'], -5)
        self.assertEqual(shares[1]['code'], 'buyback_cash')
        self.assertEqual(shares[1]['period_start'], '2026-01-01')
        self.assertEqual(shares[0]['category'], 'buybacks')

    def test_share_reduction_requires_reported_cash_to_be_labelled_buybacks(self):
        d = doc({f.BASIC[0]: [row(1000, '2025-04-01', '2025-06-30'), row(950, '2026-04-01', '2026-06-30')]})
        shares = f.full_signals(meta(), d, TODAY, s.fiscal_quarters)[1]
        self.assertEqual(shares[0]['category'], 'share_reduction')

    def test_acquisition_explanation_is_sourced_and_expires_with_comparison(self):
        d = doc({f.BASIC[0]: [row(1000, '2025-04-01', '2025-06-30'), row(1146, '2026-04-01', '2026-06-30')]})
        shares = f.full_signals({**meta(), 'cik': '93410'}, d, TODAY, s.fiscal_quarters)[1]
        self.assertEqual(shares[0]['category'], 'dilution')
        self.assertIn('Hess acquisition', shares[0]['contexts'][0]['text'])
        self.assertIn('/93410/', shares[0]['contexts'][0]['source'])
        self.assertEqual(f.share_context(93410, row(1, '2026-10-01', '2026-12-31'), row(1, '2025-10-01', '2025-12-31')), [])
        self.assertEqual(f.share_context(93410, row(1, '2025-04-01', '2025-06-30'), row(1, '2024-04-01', '2024-06-30')), [])

    def cash_rows(self, value=-20_000_000):
        return [row(value, '2025-07-01', '2025-09-30'), row(value, '2025-10-01', '2025-12-31'),
                row(value, '2026-01-01', '2026-03-31'), row(value, '2026-04-01', '2026-06-30')]

    def test_stress_requires_combined_signals_and_ignores_banks(self):
        d = doc({f.CFO[0]: self.cash_rows(), f.CASH: [row(100_000_000, None, '2025-12-31'), row(70_000_000, None, '2026-06-30')]})
        stress = f.full_signals(meta(), d, TODAY, s.fiscal_quarters)[2]
        self.assertEqual({r['code'] for r in stress}, {'negative_operating_cash', 'cash_decline'})
        self.assertEqual(f.full_signals(meta('banks'), d, TODAY, s.fiscal_quarters)[2], [])
        d['facts']['us-gaap'][f.CFO[0]]['units']['USD'] = self.cash_rows(20_000_000)
        self.assertEqual(f.full_signals(meta(), d, TODAY, s.fiscal_quarters)[2], [])

    def test_missing_debt_components_are_not_zero(self):
        d = doc({'LongTermDebtNoncurrent': [row(100_000_000, None, '2025-12-31'), row(140_000_000, None, '2026-06-30')]})
        self.assertIsNone(f.debt_pair(d, '2026-06-30', TODAY))

    def test_cash_flow_uses_standalone_quarters_and_yoy_seasonality(self):
        d = doc({f.CFO[0]: [row(-5_000_000, '2025-01-01', '2025-03-31'), row(-15_000_000, '2025-01-01', '2025-06-30'),
                              row(5_000_000, '2026-01-01', '2026-03-31'), row(20_000_000, '2026-01-01', '2026-06-30')]})
        recovery = f.full_signals(meta(), d, TODAY, s.fiscal_quarters)[0]
        self.assertEqual(recovery[0]['current'], 15_000_000)
        self.assertEqual(recovery[0]['previous'], -10_000_000)
        self.assertTrue(recovery[0]['derived'])
        self.assertEqual(len(recovery[0]['quarters']), 2)
        self.assertEqual(f.full_signals(meta('banks'), d, TODAY, s.fiscal_quarters)[0], [])

    def test_profitable_cash_flow_growth_is_not_a_turnaround(self):
        d = doc({f.CFO[0]: [row(-10_000_000, '2025-01-01', '2025-03-31'), row(0, '2025-01-01', '2025-06-30'),
                              row(10_000_000, '2026-01-01', '2026-03-31'), row(21_800_000, '2026-01-01', '2026-06-30')]})
        signals = f.full_signals(meta(), d, TODAY, s.fiscal_quarters)
        self.assertTrue(signals[3]['cash_flow'])
        self.assertEqual(signals[0], [])

    def test_operating_turnarounds_rank_by_reported_swing_before_cash_flow(self):
        large = {**meta(), 'symbol': 'LARGE'}
        small = {**meta(), 'cik': '2', 'symbol': 'SMALL', 'revenue_current': 500_000_000,
                 'quarter_revenue_current': 125_000_000, 'quarter_revenue_previous': 115_000_000}
        tiny = {**meta(), 'cik': '4', 'symbol': 'TINY', 'revenue_current': 100_000_000,
                'quarter_revenue_current': 5_000_000, 'quarter_revenue_previous': 5_000_000}
        cash = {**meta(), 'cik': '3', 'symbol': 'CASH', 'revenue_current': 10_000_000_000}
        pools = {'OperatingIncomeLoss': {1: [row(-10_000_000, '2025-04-01', '2025-06-30'), row(20_000_000, '2026-04-01', '2026-06-30')],
                                        2: [row(-15_000_000, '2025-04-01', '2025-06-30'), row(25_000_000, '2026-04-01', '2026-06-30')],
                                        4: [row(-1_100_000, '2025-04-01', '2025-06-30'), row(1_100_000, '2026-04-01', '2026-06-30')]}}
        d = doc({f.CFO[0]: [row(-500_000_000, '2025-01-01', '2025-03-31'), row(-1_000_000_000, '2025-01-01', '2025-06-30'),
                              row(500_000_000, '2026-01-01', '2026-03-31'), row(1_000_000_000, '2026-01-01', '2026-06-30')]})
        result = f.build([large, small, cash, tiny], pools, {3: d}, TODAY, s.fiscal_quarters, '2026-10-07T12:00:00Z')
        rows = result['groups']['turnaround']
        self.assertEqual([r['symbol'] for r in rows], ['SMALL', 'LARGE', 'TINY', 'CASH'])
        self.assertEqual(rows[0]['signals'][0]['swing_amount'], 40_000_000)
        self.assertGreater(rows[2]['signals'][0]['strength_pp'], rows[0]['signals'][0]['strength_pp'])
        self.assertGreater(rows[3]['signals'][0]['swing_amount'], rows[0]['signals'][0]['swing_amount'])

    def test_ttm_cash_gaps_are_excluded(self):
        values = self.cash_rows()
        values.pop(1)
        self.assertIsNone(f.quarter_run(values, '2026-06-30', 4))

    def test_business_scale_filter_and_ranking(self):
        a, b = meta(), {**meta(), 'cik': '2', 'symbol': 'SMALL', 'revenue_current': 50_000_000}
        pools = {'OperatingIncomeLoss': {i: [row(-10_000_000, '2025-04-01', '2025-06-30'), row(20_000_000, '2026-04-01', '2026-06-30')] for i in (1, 2)}}
        result = f.build([a, b], pools, {}, TODAY, s.fiscal_quarters, '2026-10-07T12:00:00Z')
        self.assertEqual(result['coverage']['eligible'], 1)
        self.assertEqual([r['symbol'] for r in result['groups']['turnaround']], ['TEST'])
        self.assertEqual(result['coverage']['shares'], 0)

    def test_instant_and_share_frames_have_distinct_units_and_freshness(self):
        class Cache:
            record = {'payload_gzip': s.pack({'data': []}), 'fetched_at': '2026-10-07T09:00:00Z'}
            paths = []
            def call(self, path): self.paths.append(path); return [self.record]
            def upsert(self, *args): pass
        class Source:
            paths = []
            def get(self, path): self.paths.append(path); return {'data': []}
        cache, src = Cache(), Source()
        now = dt.datetime(2026, 10, 7, 11, tzinfo=dt.timezone.utc)
        s.load_frame(src, cache, f.BASIC[0], 'CY2026Q3', {'CY2026Q3'}, now, unit='shares')
        s.load_frame(src, cache, f.CASH, 'CY2026Q3I', {'CY2026Q3'}, now)
        self.assertEqual(src.paths, [])
        self.assertIn('/shares/', cache.paths[0])
        self.assertIn('/USD/CY2026Q3I', cache.paths[1])


if __name__ == '__main__':
    unittest.main()
