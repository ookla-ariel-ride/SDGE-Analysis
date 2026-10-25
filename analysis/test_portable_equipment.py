#!/usr/bin/env python3
"""Synthetic equipment comparison checks; no private intake is needed."""
import copy
import json
import math
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import rates as R
import suite_runner

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def model():
    assert Path(__file__).with_name('portable_equipment.py').exists(), 'equipment model is missing'
    import portable_equipment
    return portable_equipment


def fixture():
    d = pd.DataFrame({'dt': pd.date_range('2026-06-01', periods=3 * 96, freq='15min')})
    d['p'] = [R.period_at(t) for t in d.dt]
    d['seas'] = 'S'
    d['ym'] = d.dt.dt.to_period('M')
    hour = d.dt.dt.hour.to_numpy()
    post = np.where(hour >= 16, .8, .2)
    return {'frame': d, 'imports': post + np.where(hour >= 16, .1, 0),
            'post_imports': post, 'exports': np.where((hour >= 10) & (hour < 15), .25, 0),
            'pv_per_kw': np.where((hour >= 9) & (hour < 16), .2, 0),
            'provenance': {'window_start': '2026-06-01', 'window_end': '2026-06-03',
                           'interval_count': len(d)}}


def rejects(call):
    try:
        call()
    except (ValueError, RuntimeError, FileNotFoundError):
        return
    assert False, 'invalid inputs must fail closed'


def find_case(study, capacity, output, pv):
    return next(row for row in study['cases'] if row['battery']['capacity_kwh'] == capacity
                and row['shared_ac_kw'] == output and row['pv_dc_kw'] == pv)


@case
def case_matrix_has_22_cases_and_matched_marginals():
    m = model()
    study = m.build_study(fixture())
    assert len(study['cases']) == 22
    assert len({row['id'] for row in study['cases']}) == 22
    for capacity in (7.68, 11.52, 12.288, 13.0048, 12.8):
        for output in (.8, 1.2):
            for pv in ((0, 1.6, 1.8) if capacity == 12.8 else (0, 1.6)):
                row = find_case(study, capacity, output, pv)
                assert row['id'] == f'equipment_{capacity:g}kwh_{output:g}kw_pv{pv:g}kw'
    lookup = {row['id']: row for row in study['cases']}
    assert len(study['new_pv_marginals']) == 12
    assert len(study['output_cap_marginals']) == 11
    assert len(study['capacity_marginals']) == 16
    for group in ('new_pv_marginals', 'output_cap_marginals', 'capacity_marginals'):
        for row in study[group]:
            before, after = (lookup[row[key]] for key in ('before_id', 'after_id'))
            expected = after['summary']['annual_savings_usd'] - before['summary']['annual_savings_usd']
            assert math.isclose(row['annual_savings_usd'], expected, abs_tol=1e-10)
            if group == 'new_pv_marginals':
                assert before['battery'] == {**after['battery'], 'charging': 'grid'}
                assert before['pv_dc_kw'] == 0
                assert before['shared_ac_kw'] == after['shared_ac_kw']


@case
def case_dispatch_limits_soc_and_export_allocation():
    m = model()
    inputs = fixture()
    for output in (.8, 1.2):
        row, trace = m.build_case(inputs, 12.8, output, 1.8)
        log = trace['controllers'][0]
        charge = log['solar_charge_kwh'] + log['grid_charge_kwh']
        assert charge.max() <= 1.2 * .25 + 1e-10
        assert log['served_kwh'].max() <= output * .25 + 1e-10
        assert (trace['flows']['pv_ac_kwh'] + log['served_kwh']).max() <= output * .25 + 1e-10
        assert log['soc_kwh'].min() >= 12.8 * .2 - 1e-10
        assert log['soc_kwh'].max() <= 12.8 + 1e-10
        assert abs(trace['soc_end'][0] - trace['soc_start'][0]) < 1e-8
        assert not np.any((charge > 1e-10) & (log['served_kwh'] > 1e-10))
        assert log['served_kwh'][inputs['frame'].dt.dt.hour.to_numpy() < 16].sum() == 0
        assert log['grid_charge_kwh'][inputs['frame'].p.to_numpy() != 'sop'].sum() == 0
        assert log['existing_solar_charge_kwh'].sum() == 0
        assert np.array_equal(trace['credited_exports'], inputs['exports'])
        assert np.array_equal(trace['flows']['existing_export_kwh'], inputs['exports'])
        assert np.allclose(trace['exports'], inputs['exports'] + trace['flows']['new_export_kwh'])
        assert row['checks']['restored_energy_identity_max_error_kwh'] < 1e-8
        assert row['checks']['direct_billing_max_month_error_usd'] < 1e-7
        assert row['checks']['billing_component_sum_error_usd'] < 1e-7


@case
def case_hardware_savings_exclude_free_behavior():
    m = model()
    inputs = fixture()
    study = m.build_study(inputs)
    assert study['baseline']['free_behavior_saving_usd'] > 0
    row, trace = m.build_case(inputs, 12.288, 1.2, 1.6)
    d = inputs['frame'][['dt', 'seas', 'p', 'ym']].copy()
    d['post'], d['old_export'] = inputs['post_imports'], inputs['exports']
    d['after'], d['credit'], d['physical'] = trace['imports'], trace['credited_exports'], trace['exports']
    expected = sum(R.bill_nem_monthly(d, 'post', 'old_export').values()) - sum(
        R.bill_nem_monthly(d, 'after', 'credit', physical_exp='physical').values())
    assert math.isclose(row['summary']['annual_savings_usd'], expected, abs_tol=1e-10)
    changed = copy.deepcopy(inputs)
    changed['imports'] *= 5
    other, _ = m.build_case(changed, 12.288, 1.2, 1.6)
    assert other == row, 'original imports cannot credit free behavior to equipment'


@case
def case_panel_only_payback_uses_matched_battery_increment():
    m = model()
    study = m.build_study(fixture())
    row = next(row for row in study['costs']['rows'] if row['id'] == 'dpu_x')
    for item in row['cases']:
        before = find_case(study, 12.288, item['aggregate_output_kw'], 0)
        after = find_case(study, 12.288, item['aggregate_output_kw'], 1.6)
        delta = after['summary']['annual_savings_usd'] - before['summary']['annual_savings_usd']
        assert math.isclose(item['panel_increment_annual_savings_usd'], delta, abs_tol=1e-10)
        assert math.isclose(item['panel_only_simple_payback_years'], 1593.99 / delta)
        assert item['panel_only_simple_payback_years'] > 1593.99 / item['annual_savings_usd']


@case
def case_vat_and_only_eu_hardware_receive_price_sensitivity():
    m = model()
    costs = m.build_study(fixture())['costs']
    row = next(row for row in costs['rows'] if row['id'] == 'apex')
    assert row['known_us_equipment_usd'] == 1499 + 2 * 1789
    assert math.isclose(row['eu_ex_vat_eur'], (299 + 99) / 1.19)
    assert math.isclose(row['converted_eu_proxy_usd'], (299 + 99) / 1.19 * 1.1193)
    for item in row['cases']:
        sensitivities = item['price_sensitivities']
        assert [s['eu_price_multiplier'] for s in sensitivities] == [1, 1.5, 2]
        for sensitivity in sensitivities:
            total = 5077 + row['converted_eu_proxy_usd'] * sensitivity['eu_price_multiplier'] + 1593.99
            assert math.isclose(sensitivity['equipment_panel_freight_subtotal_usd'], total)
            assert math.isclose(sensitivity['simple_payback_before_unquoted_costs_years'], total / item['annual_savings_usd'])
    stream = next(row for row in costs['rows'] if row['id'] == 'stream_x2')
    assert math.isclose(stream['eu_ex_vat_eur'], 2 * 949 + 149 + 19.9)
    assert costs['date'] == '2026-10-24' and costs['reference_date'] == '2026-10-23'


@case
def case_balco_bundle_pairs_exact_capacity_and_pv_without_us_panel_allowance():
    m = model()
    study = m.build_study(fixture())
    ordinary, bundle = [next(row for row in study['costs']['rows'] if row['id'] == key)
                        for key in ('balco_260', 'balco_260_bundle')]
    assert math.isclose(ordinary['eu_ex_vat_eur'], 4544 / 1.19)
    assert math.isclose(bundle['eu_ex_vat_eur'], 4815 / 1.19)
    assert ordinary['included_pv_dc_kw'] == 0 and bundle['included_pv_dc_kw'] == 1.8
    for item in bundle['cases']:
        expected = find_case(study, 12.8, item['aggregate_output_kw'], 1.8)
        assert item['source_case'] == expected['id']
        assert item['pv_dc_kw'] == 1.8
        assert math.isclose(item['panel_increment_cost_usd'], 271 / 1.19 * 1.1193)
        assert 'configuration' in item['panel_cost_basis']
        first = item['price_sensitivities'][0]
        assert math.isclose(first['equipment_panel_freight_subtotal_usd'], 4815 / 1.19 * 1.1193)
        assert first['common_us_panel_freight_usd'] == 0
    assert all(item['pv_dc_kw'] == 1.6 for item in ordinary['cases'])


@case
def case_inputs_and_prices_reject_nonfinite_invalid_or_missing_values():
    m = model()
    inputs = fixture()
    for value in (-1, float('nan'), float('inf'), True):
        for index in range(3):
            args = [12.8, 1.2, 1.6]
            args[index] = value
            rejects(lambda: m.build_case(inputs, *args))
        rejects(lambda: m.convert_eur(value, .19))
        rejects(lambda: m.convert_eur(100, value))
    for field in ('post_imports', 'exports', 'pv_per_kw'):
        for value in (-1, float('nan'), float('inf')):
            broken = copy.deepcopy(inputs)
            broken[field][0] = value
            rejects(lambda: m.build_case(broken, 12.8, 1.2, 1.6))
    cases = m.build_study(inputs)['cases']
    rejects(lambda: m.build_costs(cases[:-1]))
    rejects(lambda: m.build_costs(cases + [cases[0]]))
    broken = copy.deepcopy(cases)
    broken[0]['summary']['annual_savings_usd'] = float('nan')
    rejects(lambda: m.build_costs(broken))


@case
def case_nonpositive_savings_never_create_a_payback():
    m = model()
    cases = m.build_study(fixture())['cases']
    for row in cases:
        row['summary']['annual_savings_usd'] = 0
    for row in m.build_costs(cases)['rows']:
        for item in row['cases']:
            assert item['panel_only_simple_payback_years'] is None
            assert all(s['simple_payback_before_unquoted_costs_years'] is None
                       for s in item['price_sensitivities'])


@case
def case_artifact_write_is_deterministic_atomic_and_public_only():
    m = model()
    inputs = fixture()
    inputs['provenance']['private_path'] = '/private-household/do-not-publish'
    inputs['provenance']['input_sha256'] = {'usage.csv': 'private-fingerprint'}
    study = m.build_study(inputs)
    with tempfile.TemporaryDirectory() as temporary:
        first, second = Path(temporary) / 'first.json', Path(temporary) / 'second.json'
        m.write_artifact(study, first)
        m.write_artifact(m.build_study(inputs), second)
        subprocess.run(['cmp', str(first), str(second)], check=True)
        payload = first.read_bytes()
        assert b'private-household' not in payload and b'private-fingerprint' not in payload
        bad = copy.deepcopy(study)
        bad['not_finite'] = float('nan')
        rejects(lambda: m.write_artifact(bad, first))
        assert first.read_bytes() == payload
        with patch.object(Path, 'replace', side_effect=OSError('injected replacement failure')):
            try:
                m.write_artifact(study, first)
            except OSError:
                pass
            else:
                assert False, 'replacement failure must propagate'
        assert first.read_bytes() == payload
        assert sorted(p.name for p in Path(temporary).iterdir()) == ['first.json', 'second.json']
        assert json.loads(payload)['schema_version'] == 1


@case
def case_main_refuses_missing_private_inputs_without_cached_output_fallback():
    m = model()
    with patch.object(m.P, 'load_inputs', side_effect=FileNotFoundError('missing staged study input')), \
            patch.object(m, 'write_artifact') as write:
        rejects(m.main)
        write.assert_not_called()
    target = Path(__file__).with_name('portable_equipment.py')
    output = target.parent.parent / 'data/portable_equipment.json'
    previous = output.read_bytes() if output.exists() else None
    with tempfile.TemporaryDirectory() as temporary:
        completed = subprocess.run([sys.executable, str(target)], cwd=temporary,
                                   capture_output=True, text=True)
    assert completed.returncode != 0
    assert 'missing staged study input' in completed.stderr
    assert (output.read_bytes() if output.exists() else None) == previous


@case
def case_main_uses_canonical_root_when_census_copies_script():
    m = model()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / 'copied-root'
        root.mkdir()
        canonical_data = root / 'data'
        canonical_data.mkdir()
        script = root / 'portable_equipment.py'
        script.write_text(Path(m.__file__).read_text())
        module = SimpleNamespace(DATA=canonical_data)
        with patch.object(m.P, 'load_inputs', return_value=fixture()), \
                patch.dict(sys.modules, {'marginal_capacity_value': module}):
            copied = runpy.run_path(str(script))
            assert copied['main']() == 0
        output = canonical_data / 'portable_equipment.json'
        assert len(json.loads(output.read_text())['cases']) == 22
        assert sorted(p.relative_to(temporary).as_posix() for p in Path(temporary).rglob('*') if p.is_file()) == [
            'copied-root/data/portable_equipment.json', 'copied-root/portable_equipment.py']


def main():
    passed = 0
    for fn in CASES:
        try:
            description = fn()
        except suite_runner.CASE_FAILURES as exc:
            suite_runner.report_case_failure(fn, exc)
        else:
            passed += 1
            suffix = f': {description}' if description is not None else ''
            print(f'ok {fn.__name__}{suffix}')
    print(f'{passed}/{len(CASES)} passed')
    return int(passed != len(CASES))


if __name__ == '__main__':
    sys.exit(main())
