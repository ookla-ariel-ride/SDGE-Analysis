#!/usr/bin/env python3
"""NBC evidence guards: Decimal rows, DST occurrences, exact overlap, exclusions."""
import csv
import datetime as dt
import io
import pathlib
import sys
import tempfile

import suite_runner
import nbc_bill_reconciliation as N


def export(day, *, value='1.0000', generation='.6000'):
    rows = [['Meter Number', 'Date', 'Start Time', 'Duration', 'Consumption', 'Generation', 'Net']]
    from decimal import Decimal
    for hour in N.R.expected_day_hours(day):
        t = dt.datetime.combine(day, dt.time()) + dt.timedelta(hours=hour)
        rows.append(['synthetic', day.strftime('%m/%d/%Y'), t.strftime('%I:%M %p'), '15',
                     value, generation, str(Decimal(value)-Decimal(generation))])
    f = io.StringIO(); csv.writer(f).writerows(rows)
    return f.getvalue().encode()


def refuses(fn):
    try:
        fn()
    except (ValueError, SystemExit):
        return
    raise AssertionError('malformed evidence accepted')


def case_overlap_dst_and_decimal_quantities():
    day = dt.date(2025, 11, 2)
    a = N.parse_export(export(day))
    assert len(a) == 100
    union, overlap = N.merge_exports([a, a])
    assert len(union) == 100 and overlap == [0, 100]
    period = dict(start=day.isoformat(), end=day.isoformat(), days=1, printed_nbc_kwh=40., printed_net_kwh=40.)
    artifact = N.reconcile(union, [period])
    row = artifact['periods'][0]
    assert row['raw_consumption_kwh'] == 100.
    assert row['positive_interval_net_kwh'] == 40.
    assert artifact['summary']['positive_net_mean_absolute_residual_kwh'] == 0.
    changed = dict(a); first = next(iter(changed)); changed[first] = (N.Decimal('2'), N.Decimal('.6'), N.Decimal('1.4'))
    refuses(lambda: N.merge_exports([a, changed]))
    assert len(N.parse_export(export(dt.date(2026, 3, 8)))) == 92
    return 'DST repeats survive; exact overlaps and Decimal candidate sums agree'


def case_incomplete_periods_unbilled_days_and_bad_rows():
    day = dt.date(2026, 6, 1)
    a = N.parse_export(export(day)); b = N.parse_export(export(day + dt.timedelta(days=1)))
    series, _ = N.merge_exports([a, b])
    good = dict(start='2026-06-01', end='2026-06-01', days=1, printed_nbc_kwh=38., printed_net_kwh=38.)
    incomplete = dict(good, start='2026-05-30', end='2026-05-31', days=2)
    out = N.reconcile(series, [incomplete, good])
    assert len(out['excluded_periods']) == 1
    assert out['unbilled_interval_days'] == ['2026-06-02']
    refuses(lambda: N.reconcile(series, [good, good]))
    refuses(lambda: N.parse_export(export(day).replace(b',15,', b',30,', 1)))
    refuses(lambda: N.parse_export(export(day).replace(b'1.0000', b'NaN', 1)))
    refuses(lambda: N.parse_export(export(day).replace(b'0.4000', b'0.5000', 1)))
    refuses(lambda: N.parse_export(b'no header'))
    incomplete_rows = dict(a); incomplete_rows.pop(next(iter(incomplete_rows)))
    refuses(lambda: N.merge_exports([incomplete_rows]))
    return 'incomplete bills excluded; unmatched dates explicit; malformed intervals refused'


def case_printed_nbc_and_segmented_wildfire_agree():
    text = '''Billing Period: 12/27/25 - 1/27/26 Total Days: 32
Total Usage: 100
Non Bypassable Charges Usage: 1,772
Wildfire Fund Charge 293 kWh x $0.005 1.47
Wildfire Fund Charge 1,479 kWh x $0.006 8.87
'''
    rows = N.parse_bill_text(text)
    assert rows[0]['printed_nbc_kwh'] == 1772.
    assert rows[0]['wildfire_quantity_kwh'] == 1772.
    refuses(lambda: N.parse_bill_text(text.replace('1,479 kWh', '1,478 kWh')))
    refuses(lambda: N.parse_bill_text(text.replace('Total Days: 32', 'Total Days: 31')))
    refuses(lambda: N.parse_bill_text('unrecognized layout'))
    return 'actual printed NBC quantity agrees with all Wildfire rate segments'


def case_atomic_output_and_source_privacy():
    with tempfile.TemporaryDirectory() as directory:
        out = pathlib.Path(directory) / 'evidence.json'
        N.write_artifact(out, {'summary': {'x': 1}})
        before = out.read_bytes()
        refuses(lambda: N.write_artifact(out, {'x': float('nan')}))
        assert out.read_bytes() == before
        source = pathlib.Path(directory) / 'private-account.csv'; source.write_text('secret raw bytes')
        snapshot = N.read_source(source, 'interval_export')
        assert set(snapshot[1]) == {'role', 'sha256', 'bytes'}
        assert 'private-account' not in str(snapshot[1]) and directory not in str(snapshot[1])
    return 'atomic output rejects non-finite values; source metadata contains no private path'


def case_bill_corpus_table_requires_every_statement_period():
    periods = [dict(start='2026-06-01', end='2026-06-02', days=2,
                    printed_nbc_kwh=4., printed_net_kwh=1.)]
    raw = b'period,days,gross_kwh,net_kwh\n6/1/26 - 6/2/26,2,4.0,1.0\n'
    N.check_bill_table(periods, raw)
    refuses(lambda: N.check_bill_table([], raw))
    refuses(lambda: N.check_bill_table(periods, raw.replace(b'4.0', b'5.0')))
    refuses(lambda: N.check_bill_table(periods, raw + raw.splitlines(keepends=True)[1]))
    return 'missing, repeated or inconsistent statement periods fail before publication'


def case_each_interval_source_location_requires_exactly_one_file():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        raw = root / 'private/1-raw-data'; raw.mkdir(parents=True)
        october = raw / 'sdge-downloads-20261022'; october.mkdir()
        for canonical_count, october_count in ((2, 0), (0, 2), (1, 0), (0, 1), (2, 1)):
            for path in raw.rglob('*.csv'):
                path.unlink()
            for folder, count in ((raw, canonical_count), (october, october_count)):
                for i in range(count):
                    (folder / f'Electric_15_Minute_{i}.csv').write_text('synthetic')
            with patch.object(N, 'read_source', side_effect=AssertionError('source read before cardinality check')):
                refuses(lambda: N.main(root))
    return 'each source location needs exactly one export before any source read'


CASES = [case_each_interval_source_location_requires_exactly_one_file, case_bill_corpus_table_requires_every_statement_period, case_overlap_dst_and_decimal_quantities, case_incomplete_periods_unbilled_days_and_bad_rows,
         case_printed_nbc_and_segmented_wildfire_agree, case_atomic_output_and_source_privacy]

def main():
    failures = 0
    for case in CASES:
        try:
            print('PASS ', case())
        except suite_runner.CASE_FAILURES as exc:
            suite_runner.report_case_failure(case, exc); failures += 1
    print(f'{len(CASES)-failures}/{len(CASES)} passed')
    return int(bool(failures))

if __name__ == '__main__':
    sys.exit(main())
