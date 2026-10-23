#!/usr/bin/env python3
"""Reproduce the actual-bill evidence for the fifteen-minute NBC model basis.

Owns only data/nbc_bill_reconciliation.json. Read the preserved canonical export
and October 2026 download plus actual statement PDFs. Raw identifiers, rows and
paths never enter the artifact. Bill dates are inclusive; repeated DST labels
keep occurrence numbers. Decimal arithmetic preserves the exported precision.
This compares numerical candidates, not undocumented register mechanisms.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import tempfile

import pdfplumber
import rates as R

NUM = r'[-−]?[\d,]+(?:\.\d+)?'
HEADER = ['Meter Number', 'Date', 'Start Time', 'Duration', 'Consumption', 'Generation', 'Net']


def read_source(path, role):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('source changed during read')
    return raw, {'role': role, 'sha256': digest, 'bytes': len(raw)}


def parse_export(raw):
    rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
    headers = [i for i, row in enumerate(rows) if row == HEADER]
    if len(headers) != 1:
        raise ValueError('expected exactly one known interval header')
    result, occurrences = {}, Counter()
    for row in rows[headers[0]+1:]:
        if not row:
            continue
        if len(row) != len(HEADER) or row[3] != '15':
            raise ValueError('malformed fifteen-minute row')
        try:
            day = dt.datetime.strptime(row[1], '%m/%d/%Y').date()
            clock = dt.datetime.strptime(row[2], '%I:%M %p').time()
            values = tuple(Decimal(v) for v in row[4:7])
        except (ValueError, InvalidOperation) as exc:
            raise ValueError('invalid date, time or quantity') from exc
        if not all(v.is_finite() for v in values) or min(values[:2]) < 0 or values[0]-values[1] != values[2]:
            raise ValueError('invalid channel values or Net != Consumption - Generation')
        occurrences[(day, clock)] += 1
        result[(day, clock, occurrences[(day, clock)])] = values
    if not result:
        raise ValueError('empty interval export')
    return result


def merge_exports(exports):
    series, overlaps = {}, []
    for current in exports:
        if not current:
            raise ValueError('empty interval export')
        days = sorted({key[0] for key in current})
        R.validate_interval_coverage(((day, clock.hour + clock.minute/60) for day, clock, _ in current),
                                     days[0], days[-1])
        common = current.keys() & series.keys()
        if any(current[key] != series[key] for key in common):
            raise ValueError('overlapping interval occurrences disagree')
        overlaps.append(len(common))
        series.update(current)
    if not series:
        raise ValueError('no interval evidence')
    days = sorted({key[0] for key in series})
    R.validate_interval_coverage(((day, clock.hour + clock.minute/60) for day, clock, _ in series),
                                 days[0], days[-1])
    return series, overlaps


def _decimal(text):
    return Decimal(text.replace(',', '').replace('−', '-'))


def parse_bill_text(text):
    marks = list(re.finditer(r'Billing Period:\s*(\d+/\d+/\d+)\s*-\s*(\d+/\d+/\d+)\s*Total Days:\s*(\d+)', text))
    if not marks:
        raise ValueError('no bill service periods')
    result = []
    for index, mark in enumerate(marks):
        start, end = [dt.datetime.strptime(mark[i], '%m/%d/%y').date() for i in (1, 2)]
        days = int(mark[3])
        if days <= 0 or (end-start).days+1 != days:
            raise ValueError('bill dates disagree with printed days')
        chunk = text[mark.end():marks[index+1].start() if index+1 < len(marks) else len(text)]
        nbc = re.findall(r'Non Bypassable Charges Usage:\s*('+NUM+')', chunk)
        net = re.findall(r'Total Usage:\s*('+NUM+')', chunk)
        wildfire = re.findall(r'Wildfire Fund Charge\s+('+NUM+r')\s*kWh\s*x', chunk)
        if len(nbc) != 1 or len(net) != 1 or not wildfire:
            raise ValueError('missing or ambiguous bill quantities')
        printed = _decimal(nbc[0]); fire = sum(map(_decimal, wildfire), Decimal(0))
        if printed < 0 or fire != printed:
            raise ValueError('printed NBC and Wildfire quantities disagree')
        result.append(dict(start=start.isoformat(), end=end.isoformat(), days=days,
                           printed_nbc_kwh=float(printed), printed_net_kwh=float(_decimal(net[0])),
                           wildfire_quantity_kwh=float(fire), wildfire_segments=len(wildfire)))
    return result


def check_bill_table(periods, raw):
    """Check PDF coverage against the existing parser-owned statement corpus.

    gross_kwh is printed NBC usage in this table, not raw Consumption.
    Neither missing PDFs nor a stale table may silently shrink this evidence.
    """
    actual = {(p['start'], p['end']): (p['days'], p['printed_nbc_kwh'], p['printed_net_kwh'])
              for p in periods}
    expected = {}
    for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        start, end = [dt.datetime.strptime(v.strip(), '%m/%d/%y').date().isoformat()
                      for v in row['period'].split(' - ')]
        if (start, end) in expected:
            raise ValueError('duplicate bill table period')
        expected[start, end] = (int(row['days']), float(row['gross_kwh']), float(row['net_kwh']))
    if not expected or actual != expected or len(actual) != len(periods):
        raise ValueError('statement PDFs and parser-owned bill corpus disagree')


def reconcile(series, periods):
    by_day = defaultdict(list)
    for (day, _, _), values in series.items():
        by_day[day].append(values)
    covered, excluded, claimed = [], [], set()
    for period in sorted(periods, key=lambda row: row['start']):
        start, end = [dt.date.fromisoformat(period[k]) for k in ('start', 'end')]
        if (end-start).days+1 != period['days']:
            raise ValueError('period date/count mismatch')
        days = {start + dt.timedelta(days=i) for i in range(period['days'])}
        if claimed & days:
            raise ValueError('duplicate or overlapping service periods')
        claimed |= days
        missing = days - by_day.keys()
        if missing:
            excluded.append(dict(period, reason='incomplete interval coverage', missing_days=len(missing)))
            continue
        values = [v for day in days for v in by_day[day]]
        imports = sum((v[0] for v in values), Decimal(0))
        positive = sum((max(v[2], Decimal(0)) for v in values), Decimal(0))
        signed = sum((v[2] for v in values), Decimal(0))
        row = dict(period, interval_rows=len(values), raw_consumption_kwh=float(imports),
                   positive_interval_net_kwh=float(positive), signed_interval_net_kwh=float(signed),
                   raw_consumption_minus_printed_nbc_kwh=float(imports-Decimal(str(period['printed_nbc_kwh']))),
                   positive_net_minus_printed_nbc_kwh=float(positive-Decimal(str(period['printed_nbc_kwh']))),
                   signed_net_minus_printed_total_usage_kwh=float(signed-Decimal(str(period['printed_net_kwh']))),
                   candidate_difference_kwh=float(imports-positive))
        covered.append(row)
    if not covered:
        raise ValueError('no fully covered billed periods')
    summary = {key: round(sum(row[field] for row in covered), 9) for key, field in {
        'covered_days': 'days', 'covered_rows': 'interval_rows', 'billed_nbc_kwh': 'printed_nbc_kwh',
        'raw_consumption_kwh': 'raw_consumption_kwh', 'positive_interval_net_kwh': 'positive_interval_net_kwh',
        'candidate_difference_kwh': 'candidate_difference_kwh'}.items()}
    summary['covered_period_count'] = len(covered)
    for name, field in [('raw_consumption', 'raw_consumption_minus_printed_nbc_kwh'),
                         ('positive_net', 'positive_net_minus_printed_nbc_kwh'),
                         ('signed_net', 'signed_net_minus_printed_total_usage_kwh')]:
        residuals = [row[field] for row in covered]
        summary[name+'_sum_residual_kwh'] = round(sum(residuals), 9)
        summary[name+'_mean_absolute_residual_kwh'] = round(sum(map(abs, residuals))/len(residuals), 9)
        summary[name+'_max_absolute_residual_kwh'] = max(map(abs, residuals))
    return {'summary': summary, 'periods': covered, 'excluded_periods': excluded,
            'unbilled_interval_days': [day.isoformat() for day in sorted(by_day.keys()-claimed)],
            'interval_union': {'days': len(by_day), 'rows': len(series),
                               'first_date': min(by_day).isoformat(), 'last_date': max(by_day).isoformat()},
            'dst_days': [{'date': day.isoformat(), 'rows': len(values)} for day, values in sorted(by_day.items()) if len(values) != 96]}


def write_artifact(path, result):
    payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique adjacent staging file avoids both partial writes and shared .tmp names.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def main(root=None):
    root = Path(root) if root is not None else next(p for p in [Path.cwd(), *Path.cwd().parents]
                                                   if (p/'analysis/rates.py').is_file() and (p/'data').is_dir())
    raw_dir = root/'private/1-raw-data'
    canonical = sorted(raw_dir.glob('Electric_15_Minute_*.csv'))
    october = sorted((raw_dir/'sdge-downloads-20261022').glob('Electric_15_Minute_*.csv'))
    if len(canonical) != 1 or len(october) != 1:
        raise ValueError('need preserved canonical and October interval exports, exactly one each')
    exports = canonical + october
    sources, checks, channels = [], [], []
    for path in exports:
        raw, metadata = read_source(path, 'interval_export')
        sources.append(metadata); checks.append((path, metadata['sha256'])); channels.append(parse_export(raw))
    series, overlap = merge_exports(channels)
    bills = sorted((raw_dir/'electric-bills').glob('*.pdf'))
    if not bills:
        raise ValueError('no actual electric statements')
    periods = []
    for path in bills:
        raw, metadata = read_source(path, 'electric_statement')
        sources.append(metadata); checks.append((path, metadata['sha256']))
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            periods.extend(parse_bill_text('\n'.join(page.extract_text() or '' for page in pdf.pages)))
    table_path = root/'data/bill_periods_electric.csv'
    table_raw, table_meta = read_source(table_path, 'printed_bill_quantity_table')
    sources.append(table_meta); checks.append((table_path, table_meta['sha256']))
    check_bill_table(periods, table_raw)
    result = reconcile(series, periods)
    result.update(sources=sources, overlap_rows_by_export=overlap,
                  method={'nbc_model_basis': 'Sum max(Consumption - Generation, 0) within each exported fifteen-minute interval.',
                          'bill_quantity': 'Printed Non Bypassable Charges Usage, checked against summed Wildfire Fund Charge kWh. The legacy bill table gross_kwh field denotes this printed quantity, not raw Consumption.',
                          'coverage': 'Inclusive printed service dates; exact local-time multiplicities and repeated DST occurrences retained; exact overlap agreement required.',
                          'arithmetic': 'Decimal source arithmetic; no grouped rounding or historical TOU assumption enters these interval sums.',
                          'interpretation': 'Compare candidate residuals as evidence for the numerical NBC basis. This does not establish exact utility replication or physical channel-register mechanisms.',
                          'limits': 'Utility billing-interval mapping, source precision, grouped rounding, historical rate segments and exact midnight cutoffs remain unresolved.',
                          'tariff': 'SDGE NEM-ST SC 1(f), sheet 6, revised CPUC sheet 37390-E, effective 2023-03-01; consumption net of exports in each metered interval.',
                          'tariff_url': 'https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE'})
    for path, digest in checks:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('source changed before publication')
    write_artifact(root/'data/nbc_bill_reconciliation.json', result)
    print(json.dumps(result['summary'], sort_keys=True))
    return result


if __name__ == '__main__':
    main()
