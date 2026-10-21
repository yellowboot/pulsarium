"""Comparable report highlights from permanently cached SEC company facts.

Numeric observations only. Causes (acquisitions, seasonality, credit policy)
require reading the filing and must never be inferred from a ratio alone.
"""
import datetime as dt

import sec_financial_signals as f

GROUPS = ('earnings_cash', 'payouts', 'working_capital')
REVENUE = ['RevenueFromContractWithCustomerExcludingAssessedTax',
           'RevenueFromContractWithCustomerIncludingAssessedTax', 'Revenues',
           'SalesRevenueNet']
MIN_CHANGE = 10_000_000
METHODS = {
    'earnings_cash': 'Non-financial companies. Compare reported net income and operating cash flow for the latest reported fiscal year-to-date or annual period of at least 170 days. Both YoY comparisons must have the same period boundaries and accession. Use ProfitLoss where disclosed, otherwise NetIncomeLoss; retain the same profit concept across both periods. Positive net income must rise at least 10% and $10 million while operating cash flow falls at least 20% and $10 million, or positive net income of at least $10 million accompanies negative operating cash flow of at least $10 million. Ranked by the net-income minus cash-flow gap as a share of TTM revenue. The figures describe a divergence; they do not establish its cause or an accounting problem.',
    'payouts': 'Non-financial companies. Use the latest reported year-to-date or annual cash-flow period of at least 170 days. Free cash flow here is reported total operating cash flow minus cash payments for property, plant and equipment. All components must have exactly matching periods and accession. Reported common-share buybacks and dividends are shown separately; an absent component is excluded from the reported subtotal, never treated as zero. The reported payout subtotal must exceed positive free cash flow by at least 20%, with a gap of at least $10 million and 1% of TTM revenue; negative free cash flow also qualifies. Ranked by payout gap / TTM revenue. This does not identify the funding source or predict a dividend cut.',
    'working_capital': 'Non-financial companies. Inventory or net current receivables must grow at least 20% YoY and at least $10 million, outpace comparable quarterly revenue growth by at least 15 percentage points, and increase by at least 1% of TTM revenue. Revenue comparatives use the same latest filing and comparable fiscal-quarter boundaries. Balance-sheet comparatives use the latest available disclosure for the matching prior-year date (within seven days of the revenue comparison), with a separate SEC source when necessary. Both balance dates are shown. Ranked by the growth-rate gap versus revenue, then the amount / TTM revenue. Changes can reflect seasonality, acquisitions and business mix; they do not establish slower collection or unsold goods.'
}


def exact(doc, tags, reference, today):
    """Select an alternative concept, never sum synonymous tags."""
    for tag in tags:
        rows = [r for r in f.facts(doc, tag, today)
                if all(r.get(k) == reference.get(k) for k in ('start', 'end', 'accn'))]
        if rows:
            return max(rows, key=f.revision)
    return None


def latest_accession(doc, end, today):
    rows = [r for tag in REVENUE + ['ProfitLoss', 'NetIncomeLoss', f.CFO[0]]
            for r in f.facts(doc, tag, today) if r['end'] == end and 70 <= f.days(r) <= 380]
    return max(rows, key=f.revision).get('accn') if rows else None


def flow_period(doc, end, today, accession):
    rows = [r for r in f.facts(doc, f.CFO[0], today)
            if r['end'] == end and r.get('accn') == accession and 170 <= f.days(r) <= 380]
    return max(rows, key=f.days) if rows else None


def prior_flow(doc, tag, current, today):
    rows = [r for r in f.facts(doc, tag, today)
            if r.get('accn') == current.get('accn')
            and 350 <= (dt.date.fromisoformat(current['end']) - dt.date.fromisoformat(r['end'])).days <= 380
            and abs(f.days(r) - f.days(current)) <= 7]
    return max(rows, key=f.revision) if rows else None


def detail(code, label, current, previous=None, **extra):
    if previous is not None:
        extra['previous_accession'] = previous.get('accn')
    return f.evidence(code, label, current, previous, extra=extra)


def analyze(meta, doc, today):
    groups = {key: [] for key in GROUPS}
    coverage = {key: False for key in GROUPS}
    if meta['sector'] in ('banks', 'insurance'):
        return groups, coverage
    end, revenue = meta['period_end'], meta['revenue_current']
    accession = latest_accession(doc, end, today)
    cash = flow_period(doc, end, today, accession)
    if cash:
        profit = None
        for profit_tag in ('ProfitLoss', 'NetIncomeLoss'):
            profit = exact(doc, [profit_tag], cash, today)
            if profit is not None:
                break
        old_cash = prior_flow(doc, f.CFO[0], cash, today)
        old_profit = exact(doc, [profit_tag], old_cash, today) if old_cash and profit is not None else None
        if profit and old_cash and old_profit:
            coverage['earnings_cash'] = True
            rises = old_profit['val'] > 0 and profit['val'] >= 1.1 * old_profit['val'] and profit['val'] - old_profit['val'] >= MIN_CHANGE
            falls = old_cash['val'] > 0 and cash['val'] <= .8 * old_cash['val'] and old_cash['val'] - cash['val'] >= MIN_CHANGE
            negative = profit['val'] >= MIN_CHANGE and cash['val'] <= -MIN_CHANGE
            if (profit['val'] >= MIN_CHANGE and rises and falls) or negative:
                label = 'Profit rose while operating cash flow fell YoY' if rises and falls else 'Positive profit with negative operating cash flow'
                groups['earnings_cash'] = [
                    detail('profit_cash_gap', label, profit, old_profit,
                           strength=(profit['val'] - cash['val']) / revenue,
                           gap_amount=profit['val'] - cash['val']),
                    detail('report_operating_cash', 'Operating cash flow', cash, old_cash)]
        capex = exact(doc, ['PaymentsToAcquirePropertyPlantAndEquipment'], cash, today)
        buyback = exact(doc, ['PaymentsForRepurchaseOfCommonStock'], cash, today)
        dividends = exact(doc, ['PaymentsOfDividendsCommonStock', 'PaymentsOfDividends'], cash, today)
        payments = [r for r in (buyback, dividends) if r is not None]
        if capex and capex['val'] >= 0 and payments and all(r['val'] >= 0 for r in payments):
            coverage['payouts'] = True
            paid = sum(r['val'] for r in payments)
            free = cash['val'] - capex['val']
            gap = paid - free
            if paid >= MIN_CHANGE and gap >= max(MIN_CHANGE, .01 * revenue) and (free <= 0 or paid >= 1.2 * free):
                total = {**cash, 'val': paid, 'derived': True}
                groups['payouts'] = [
                    detail('payout_gap', 'Reported payouts exceeded free cash flow', total,
                           strength=gap / revenue, gap_amount=gap,
                           components=[name for name, r in [('buybacks', buyback), ('dividends', dividends)] if r is not None]),
                    detail('report_free_cash', 'Operating cash flow minus capital spending', {**cash, 'val': free, 'derived': True}),
                    detail('report_operating_cash', 'Operating cash flow', cash),
                    detail('report_capex', 'Cash paid for property, plant and equipment', capex)]
                if buyback is not None:
                    groups['payouts'].append(detail('report_buyback', 'Reported common-share buyback cash', buyback))
                if dividends is not None:
                    groups['payouts'].append(detail('report_dividends', 'Reported dividend cash', dividends))
    quarter = None
    for tag in REVENUE:
        rows = [r for r in f.facts(doc, tag, today) if r['end'] == end and r.get('accn') == accession
                and r.get('start') == meta.get('quarter_period_start') and 70 <= f.days(r) <= 125]
        if rows:
            quarter = max(rows, key=f.revision)
            prior = prior_flow(doc, tag, quarter, today)
            if prior and prior.get('start') == meta.get('quarter_previous_start') and prior['val'] > 0:
                break
            quarter = None
    if quarter:
        sales_growth = 100 * (quarter['val'] / prior['val'] - 1)
        for tag, code, label in [('InventoryNet', 'inventory_growth', 'Inventories grew faster than revenue YoY'),
                                 ('AccountsReceivableNetCurrent', 'receivables_growth', 'Receivables grew faster than revenue YoY')]:
            rows = f.facts(doc, tag, today)
            current = [r for r in rows if r['end'] == end and r.get('accn') == accession and r['val'] >= 0]
            previous = [r for r in rows if abs((dt.date.fromisoformat(r['end']) - dt.date.fromisoformat(prior['end'])).days) <= 7
                        and r['val'] > 0 and 350 <= (dt.date.fromisoformat(end) - dt.date.fromisoformat(r['end'])).days <= 380]
            if not current or not previous:
                continue
            a, b = max(current, key=f.revision), max(previous, key=f.revision)
            coverage['working_capital'] = True
            growth = 100 * (a['val'] / b['val'] - 1)
            gap = growth - sales_growth
            if growth >= 20 and gap >= 15 and a['val'] - b['val'] >= max(MIN_CHANGE, .01 * revenue):
                groups['working_capital'].append(detail(code, label, a, b, strength=gap,
                                                       gap_amount=a['val'] - b['val'], revenue_growth_pct=sales_growth))
        if groups['working_capital']:
            groups['working_capital'].sort(key=lambda r: (-r['strength'], -r['gap_amount']))
            groups['working_capital'].append(detail('report_revenue', 'Comparable quarterly revenue', quarter, prior))
    return groups, coverage
