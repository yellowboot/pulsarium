import copy
import base64
import datetime as dt
import gzip
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('radar', Path(__file__).with_name('sec-insider-radar.py'))
radar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radar)

XML = '''<ownershipDocument><documentType>4</documentType><issuer>
<issuerCik>0000000123</issuerCik><issuerName>Sample Corp</issuerName><issuerTradingSymbol>ABC</issuerTradingSymbol>
</issuer><reportingOwner><reportingOwnerId><rptOwnerCik>0000000456</rptOwnerCik><rptOwnerName>Example CEO</rptOwnerName>
</reportingOwnerId><reportingOwnerRelationship><isOfficer>true</isOfficer><isDirector>false</isDirector>
<officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner><nonDerivativeTable>
<nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
<transactionDate><value>2026-10-05</value></transactionDate><transactionCoding><transactionCode>P</transactionCode>
<equitySwapInvolved>0</equitySwapInvolved></transactionCoding><transactionAmounts>
<transactionShares><value>20000</value></transactionShares><transactionPricePerShare><value>10</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'''


def record(owner='456', accession='0000000123-26-000001'):
    payload = radar.parse_filing(XML, accession, '2026-10-05', 'edgar/data/123/' + accession + '.txt')
    payload['owners'][0]['cik'] = owner
    return dict(accession=accession, filed_date='2026-10-05', form_type='4',
        source_path='edgar/data/123/' + accession + '.txt', status='parsed', attempts=1,
        updated_at='2026-10-06T10:00:00+00:00', payload=payload)


def summary(rows):
    return radar.snapshot(rows, {'history_from': '2026-09-29', 'sales_history_from': '2026-10-05',
        'through': '2026-10-05'}, dt.date(2026, 10, 6))


def sale_record(owner='456', accession='0000000123-26-000001', after=5000):
    raw = XML.replace('<transactionCode>P', '<transactionCode>S').replace(
        '<transactionAcquiredDisposedCode><value>A', '<transactionAcquiredDisposedCode><value>D')
    if after is not None:
        raw = raw.replace('</nonDerivativeTransaction>', '<postTransactionAmounts>'
            '<sharesOwnedFollowingTransaction><value>' + str(after) + '</value>'
            '</sharesOwnedFollowingTransaction></postTransactionAmounts></nonDerivativeTransaction>')
    row = record(owner, accession)
    row['payload'] = radar.parse_filing(raw, accession, row['filed_date'], row['source_path'])
    row['payload']['owners'][0]['cik'] = owner
    return row


class RadarTests(unittest.TestCase):
    def test_awards_and_option_exercises_are_not_purchases(self):
        for code in ['A', 'M', 'F', 'S', 'G']:
            filing = radar.parse_filing(XML.replace('<transactionCode>P', '<transactionCode>' + code),
                '0000000123-26-000001', '2026-10-05', 'edgar/data/123/0000000123-26-000001.txt')
            self.assertEqual(filing['purchases'], [])

    def test_groups_count_people_not_purchase_rows(self):
        one = record()
        one['payload']['purchases'].append(copy.deepcopy(one['payload']['purchases'][0]))
        self.assertEqual(summary([one])['clusters'], [])
        two = record('789', '0000000123-26-000002')
        result = summary([one, two])['clusters'][0]
        self.assertEqual(result['buyers'], 2)
        self.assertEqual(result['amount'], 600000)

    def test_trading_plans_private_indirect_and_foreign_currency_are_stored_but_not_ranked(self):
        for field, value in [('planned', True), ('private', True), ('direct', False), ('currency', 'CAD')]:
            row = record()
            row['payload']['purchases'][0][field] = value
            self.assertEqual(summary([row])['large'], [])
            self.assertEqual(len(row['payload']['purchases']), 1)

    def test_joint_reporting_is_not_counted_as_a_group(self):
        row = record()
        row['payload']['owners'].append(dict(row['payload']['owners'][0], cik='999'))
        self.assertEqual(summary([row])['clusters'], [])
        self.assertEqual(summary([row])['large'], [])

    def test_retained_footnotes_exclude_reinvestments_and_ipo_programs(self):
        notes = [
            'Shares acquired through the reinvestment of dividends on restricted stock.',
            'Dividends were reinvested pursuant to the stock incentive plan.',
            'Shares purchased pursuant to a directed share program in the initial public offering.',
            'Acquired under an employee stock purchase plan through payroll deductions.',
        ]
        for note in notes:
            row = record()
            row['payload']['notes'] = {'F1': note}
            self.assertEqual(summary([row])['large'], [], note)
            self.assertEqual(len(row['payload']['purchases']), 1)
            self.assertEqual(row['attempts'], 1)
        # An unrelated grant note must not exclude a separately priced buy.
        row = record()
        row['payload']['notes'] = {'F1': 'Restricted stock units granted as compensation.'}
        self.assertEqual(len(summary([row])['large']), 1)

    def test_amendment_suppresses_affected_original_without_double_count(self):
        original, amended = record(), record(accession='0000000123-26-000003')
        amended['payload']['form'] = '4/A'
        amended['payload']['original_date'] = '2026-10-05'
        amended['payload']['purchases'][0]['amount'] = 100000
        self.assertEqual(summary([original, amended])['large'], [])

    def test_index_deduplicates_issuer_owner_accessions_and_rejects_paths(self):
        text = '\n'.join(['123|Company|4|20261005|edgar/data/123/0000000123-26-000001.txt',
            '456|Owner|4|20261005|edgar/data/456/0000000123-26-000001.txt',
            '123|Bad|4|20261005|../secrets', '123|Annual|10-K|20261005|edgar/data/123/0000000123-26-000002.txt'])
        self.assertEqual(len(radar.index_filings(text, {123, 456})), 1)

    def test_repeat_refresh_keeps_history_and_makes_no_filing_or_index_downloads(self):
        class FakeSEC:
            calls = []
            def get(self, url):
                self.calls.append(url)
                if url.endswith('company_tickers.json'):
                    return json.dumps({'0': {'cik_str': 123}})
                raise AssertionError('Stored history requested again: ' + url)
        with tempfile.TemporaryDirectory() as directory:
            store = radar.LocalStore(Path(directory) / 'history.sqlite')
            store.save_state({'history_from': '2026-10-05', 'through': '2026-10-05', 'indexes': ['2026-10-05']})
            store.save([record()])
            sec = FakeSEC()
            with patch.object(store, 'records', wraps=store.records) as reads:
                radar.run(sec, store, 5000, dt.date(2026, 10, 6))
                self.assertEqual(reads.call_count, 1)
            self.assertEqual(len(sec.calls), 1)
            self.assertEqual(store.records()[0]['attempts'], 1)
            self.assertEqual(len(store.records()), 1)
            store.db.close()
    def test_unknown_price_and_stale_transaction_are_not_ranked(self):
        row = record()
        row['payload']['purchases'][0]['date'] = '2026-09-01'
        self.assertEqual(summary([row])['large'], [])
        filing = radar.parse_filing(XML.replace('<value>10</value>', '<value></value>'),
            '0000000123-26-000001', '2026-10-05', 'edgar/data/123/0000000123-26-000001.txt')
        self.assertEqual(filing['purchases'], [])

    def test_non_us_transaction_currency_is_read_from_footnotes(self):
        raw = XML.replace('</ownershipDocument>', '<footnotes><footnote id="F1">Prices in Brazilian reais.</footnote></footnotes></ownershipDocument>')
        filing = radar.parse_filing(raw, '0000000123-26-000001', '2026-10-05', 'edgar/data/123/0000000123-26-000001.txt')
        self.assertEqual(filing['purchases'][0]['currency'], 'BRL')
        self.assertIn('F1', filing['notes'])

    def test_sales_have_their_own_direction_and_count_distinct_sellers(self):
        one, two = sale_record(), sale_record('789', '0000000123-26-000002')
        result = summary([one, two])
        self.assertEqual(result['large'], [])
        self.assertEqual(result['sales']['clusters'][0]['sellers'], 2)
        self.assertEqual(result['sales']['clusters'][0]['amount'], 400000)
        self.assertEqual(result['sales']['large'][0]['holding_reduction']['percent'], 80)

    def test_tax_withholding_exercises_gifts_and_acquisitions_are_not_sales(self):
        for code in ['F', 'M', 'G', 'A', 'P']:
            row = sale_record()
            raw = gzip.decompress(base64.b64decode(row['payload']['ownership_xml_gzip'])).decode()
            raw = raw.replace('<transactionCode>S', '<transactionCode>' + code)
            payload = radar.parse_filing(raw, row['accession'], row['filed_date'], row['source_path'])
            self.assertEqual(payload['sales'], [])

    def test_planned_sales_are_included_and_marked(self):
        row = sale_record()
        row['payload']['sales'][0]['planned'] = True
        card = summary([row])['sales']['large'][0]
        self.assertEqual(card['planned_amount'], card['amount'])

    def test_absent_zero_and_ambiguous_holdings_are_handled(self):
        self.assertIsNone(summary([sale_record(after=None)])['sales']['large'][0]['holding_reduction'])
        self.assertEqual(summary([sale_record(after=0)])['sales']['large'][0]['holding_reduction']['percent'], 100)
        row = sale_record()
        raw = gzip.decompress(base64.b64decode(row['payload']['ownership_xml_gzip'])).decode()
        transaction = raw[raw.index('<nonDerivativeTransaction>'):raw.index('</nonDerivativeTransaction>') + len('</nonDerivativeTransaction>')]
        for code in ['M', 'F', 'P']:
            mixed = raw.replace('</nonDerivativeTable>', transaction.replace('<transactionCode>S', '<transactionCode>' + code) + '</nonDerivativeTable>')
            payload = radar.parse_filing(mixed, row['accession'], row['filed_date'], row['source_path'])
            self.assertNotIn('holding_reduction_pct', payload['sales'][0])

    def test_multiple_sale_rows_use_consistent_balances_only(self):
        row = sale_record()
        raw = gzip.decompress(base64.b64decode(row['payload']['ownership_xml_gzip'])).decode()
        transaction = raw[raw.index('<nonDerivativeTransaction>'):raw.index('</nonDerivativeTransaction>') + len('</nonDerivativeTransaction>')]
        repeated = raw.replace('</nonDerivativeTable>', transaction + '</nonDerivativeTable>')
        payload = radar.parse_filing(repeated, row['accession'], row['filed_date'], row['source_path'])
        self.assertAlmostEqual(payload['sales'][0]['holding_reduction_pct'], 88.9)
        inconsistent = raw.replace('</nonDerivativeTable>', transaction.replace('<value>5000</value>', '<value>4000</value>') + '</nonDerivativeTable>')
        payload = radar.parse_filing(inconsistent, row['accession'], row['filed_date'], row['source_path'])
        self.assertNotIn('holding_reduction_pct', payload['sales'][0])

    def test_sales_filters_and_amendments(self):
        for field, value in [('private', True), ('direct', False), ('currency', 'CAD'), ('date', '2026-09-01')]:
            row = sale_record()
            row['payload']['sales'][0][field] = value
            self.assertEqual(summary([row])['sales']['large'], [])
        original, amended = sale_record(), sale_record(accession='0000000123-26-000003')
        amended['payload']['form'] = '4/A'
        amended['payload']['original_date'] = original['filed_date']
        self.assertEqual(summary([original, amended])['sales']['large'], [])

    def test_original_ownership_xml_is_retained_for_local_reprocessing(self):
        payload = record()['payload']
        self.assertEqual(gzip.decompress(base64.b64decode(payload['ownership_xml_gzip'])).decode(), XML)

    def test_legacy_purchase_only_cache_is_never_redownloaded_for_sales(self):
        class FakeSEC:
            def __init__(self):
                self.calls = []
            def get(self, url):
                self.calls.append(url)
                if url.endswith('company_tickers.json'):
                    return json.dumps({'0': {'cik_str': 123}})
                raise AssertionError('Stored history requested again: ' + url)
        with tempfile.TemporaryDirectory() as directory:
            store = radar.LocalStore(Path(directory) / 'history.sqlite')
            state = {'history_from': '2026-10-05', 'through': '2026-10-05', 'indexes': ['2026-10-05']}
            store.save_state(state)
            legacy = record()
            for key in ['schema_version', 'sales', 'ownership_xml_gzip']:
                legacy['payload'].pop(key)
            store.save([legacy])
            sec = FakeSEC()
            result = radar.run(sec, store, 5000, dt.date(2026, 10, 6))
            self.assertEqual(len(sec.calls), 1)
            self.assertEqual(store.records()[0], legacy)
            self.assertEqual(result['sales']['history_from'], '2026-10-06')
            self.assertFalse(result['sales']['coverage']['complete'])
            self.assertEqual(len(result['large']), 1)
            store.db.close()
    def test_new_sale_is_downloaded_once_and_appended_to_legacy_history(self):
        class FakeSEC:
            def __init__(self):
                self.calls = []
            def get(self, url):
                self.calls.append(url)
                if url.endswith('company_tickers.json'):
                    return json.dumps({'0': {'cik_str': 123}})
                if url.endswith('master.20261006.idx'):
                    return 'CIK|Company Name|Form Type|Date Filed|File Name\n' \
                        '123|Sample|4|20261006|edgar/data/123/0000000123-26-000002.txt'
                if url.endswith('0000000123-26-000002.txt'):
                    return gzip.decompress(base64.b64decode(sale_record()['payload']['ownership_xml_gzip'])).decode()
                raise AssertionError('Unexpected download: ' + url)
        with tempfile.TemporaryDirectory() as directory:
            store = radar.LocalStore(Path(directory) / 'history.sqlite')
            store.save_state({'history_from': '2026-10-05', 'through': '2026-10-05', 'indexes': ['2026-10-05']})
            legacy = record()
            for key in ['schema_version', 'sales', 'ownership_xml_gzip']:
                legacy['payload'].pop(key)
            store.save([legacy])
            sec = FakeSEC()
            first = radar.run(sec, store, 5000, dt.date(2026, 10, 7))
            second = radar.run(sec, store, 5000, dt.date(2026, 10, 7))
            self.assertEqual(first['sales']['large'][0]['amount'], 200000)
            self.assertTrue(second['sales']['coverage']['complete'])
            self.assertEqual(len(store.records()), 2)
            self.assertEqual(store.records()[0], legacy)
            self.assertEqual(store.records()[1]['attempts'], 1)
            self.assertEqual(sum(u.endswith('.txt') for u in sec.calls), 1)
            self.assertEqual(sum(u.endswith('.idx') for u in sec.calls), 1)
            store.db.close()


if __name__ == '__main__':
    unittest.main()
