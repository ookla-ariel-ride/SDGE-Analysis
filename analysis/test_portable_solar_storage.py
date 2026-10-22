#!/usr/bin/env python3
"""Synthetic physics, billing, and economics checks; no private data needed."""
import pathlib
import sys
from dataclasses import replace

import numpy as np
import pandas as pd
import rates as R
import suite_runner

CASES = []
def case(fn):
    CASES.append(fn)
    return fn


def model():
    path = pathlib.Path(__file__).with_name('portable_solar_storage.py')
    assert path.exists(), 'physical model is missing'
    import portable_solar_storage
    return portable_solar_storage


def frame(times):
    d = pd.DataFrame({'dt': pd.to_datetime(times)})
    d['p'] = [R.period_at(t) for t in d.dt]
    d['seas'] = ['S' if t.month in R.SUMMER_MONTHS else 'W' for t in d.dt]
    d['ym'] = d.dt.dt.to_period('M')
    return d


def rejects(call):
    try:
        call()
    except (ValueError, RuntimeError, FileNotFoundError):
        return
    assert False, 'invalid or unavailable input must fail closed'


@case
def case_pv_conserves_simultaneous_meter_flows_and_export_credit():
    m = model()
    d = frame(['2026-06-01 12:00'])
    for treatment, export, credited, curtailed in [('credited', .5, .5, 0), ('zero_credit', .5, .2, 0), ('zero_export', .2, .2, .3)]:
        r = m.dispatch(d, [.1], [.2], [.4], [], export_treatment=treatment, shared_ac_kw=2)
        assert np.allclose(r['imports'], [0])
        assert np.allclose(r['exports'], [export])
        assert np.allclose(r['credited_exports'], [credited])
        assert np.allclose(r['flows']['pv_direct_kwh'], [.1])
        assert np.allclose(r['flows']['curtailed_kwh'], [curtailed])
        assert r['conservation_max_error_kwh'] < 1e-10


@case
def case_zero_export_without_load_reports_zero_delivered_ac_pv():
    m = model()
    d = frame(['2026-06-01 12:00'])
    r = m.dispatch(d, [0], [0], [.25], [], export_treatment='zero_export', shared_ac_kw=1)
    assert np.allclose(r['flows']['pv_ac_kwh'], [0]), 'export control must remove undelivered AC PV'
    assert np.allclose(r['flows']['curtailed_kwh'], [.25])
    assert r['conservation_max_error_kwh'] < 1e-10


@case
def case_delivered_ac_pv_reconciles_destinations_after_clip_and_export_control():
    m = model()
    d = frame(['2026-06-01 12:00', '2026-07-01 17:00'])
    battery = m.Battery(1, 1, .4, reserve=0, rte=.81, standby_w=0, charging='solar')
    for batteries, charged, zero_export_ac in [([], 0, .05), ([battery], .1, .15)]:
        for treatment in ('credited', 'zero_credit', 'zero_export'):
            r = m.steady_dispatch(d, [.05, .25], [.2, 0], [.4, 0], batteries,
                                  shared_ac_kw=1, export_treatment=treatment)
            flows = r['flows']
            new_charge = sum((log['new_solar_charge_kwh'] for log in r['controllers']), np.zeros(len(d)))
            expected_ac = zero_export_ac if treatment == 'zero_export' else .25
            expected_curtailment = .4 - expected_ac
            assert np.allclose(new_charge, [charged, 0])
            assert np.allclose(flows['pv_ac_kwh'], [expected_ac, 0]), treatment
            assert np.allclose(flows['curtailed_kwh'], [expected_curtailment, 0])
            assert np.allclose(flows['pv_ac_kwh'], flows['pv_direct_kwh'] + new_charge + flows['new_export_kwh'])
            assert np.allclose(flows['pv_available_kwh'], flows['pv_ac_kwh'] + flows['curtailed_kwh'])
            assert r['conservation_max_error_kwh'] < 1e-10
            summary = m.scenario_summary(d, [.05, .25], [.2, 0], r, batteries)
            accounts = [summary, *summary['bill']['monthly'].values()]
            for account in accounts:
                physical = account['physical']
                destinations = physical['pv_direct_kwh'] + account['battery']['new_solar_charge_kwh'] + physical['new_export_kwh']
                assert np.isclose(physical['pv_ac_kwh'], destinations)
                assert np.isclose(physical['pv_available_kwh'], physical['pv_ac_kwh'] + physical['curtailed_kwh'])
            assert np.isclose(summary['physical']['pv_ac_kwh'], expected_ac)
            assert np.isclose(sum(row['physical']['pv_ac_kwh'] for row in summary['bill']['monthly'].values()), expected_ac)


@case
def case_storage_reserve_losses_and_charge_discharge_limits():
    m = model()
    d = frame(['2026-06-01 12:00', '2026-06-01 17:00'])
    c = m.Battery(2, 1, 1, reserve=.2, rte=.81, standby_w=0, charging='solar')
    r = m.dispatch(d, [0, 1], [1, 0], [0, 0], [c], initial_soc=[.4])
    assert np.allclose(r['controllers'][0]['solar_charge_kwh'], [.25, 0])
    assert np.allclose(r['controllers'][0]['served_kwh'], [0, .2025])
    assert np.isclose(r['soc_end'][0], .4)
    assert np.isclose(sum(r['controllers'][0]['loss_kwh']), .0475)
    assert r['conservation_max_error_kwh'] < 1e-10


@case
def case_full_empty_zero_capacity_and_no_load_do_not_create_energy():
    m = model()
    d = frame(['2026-06-01 12:00', '2026-06-01 17:00'])
    for capacity, soc in [(0, 0), (1, 0), (1, 1)]:
        c = m.Battery(capacity, 1, 1, reserve=0, rte=1, standby_w=0, charging='solar')
        r = m.dispatch(d, [0, 0], [0, 0], [0, 0], [c], initial_soc=[soc])
        assert np.allclose(r['soc_end'], [soc])
        assert sum(r['controllers'][0]['served_kwh']) == 0
    r = m.dispatch(d, [0, 1], [1, 0], [0, 0], [m.Battery(1, 1, 1, reserve=0, rte=1, standby_w=0)], initial_soc=[1])
    assert r['controllers'][0]['solar_charge_kwh'][0] == 0
    assert r['controllers'][0]['served_kwh'][1] == .25


@case
def case_shared_ac_cap_includes_pv_and_all_controllers():
    m = model()
    d = frame(['2026-06-01 17:00'])
    c = m.Battery(2, 2, 2, reserve=0, rte=1, standby_w=0)
    r = m.dispatch(d, [2], [0], [.2], [c, c], shared_ac_kw=1.2, initial_soc=[2, 2])
    assert np.isclose(r['imports'][0], 1.7)
    assert np.isclose(sum(x['served_kwh'][0] for x in r['controllers']), .1)
    r = m.dispatch(d, [.1], [0], [0], [c, c], initial_soc=[2, 2])
    assert sum(x['served_kwh'][0] for x in r['controllers']) == .1


@case
def case_two_controllers_share_export_once_and_order_changes_allocation():
    m = model()
    d = frame(['2026-06-01 12:00', '2026-06-01 17:00'])
    a = m.Battery(1, 1, 1, reserve=0, rte=1, standby_w=0, charging='solar')
    b = replace(a, discharge_kw=.2)
    r = m.dispatch(d, [0, .25], [.25, 0], [0, 0], [a, b], initial_soc=[0, 0])
    s = m.dispatch(d, [0, .25], [.25, 0], [0, 0], [b, a], initial_soc=[0, 0])
    assert np.isclose(r['imports'].sum(), 0)
    assert np.isclose(s['imports'].sum(), .2)
    assert sum(x['solar_charge_kwh'].sum() for x in r['controllers']) == .25


@case
def case_standby_is_grid_load_even_at_empty_soc_and_annual_soc_balances():
    m = model()
    d = frame(['2026-06-01 00:00', '2026-06-01 17:00'])
    c = m.Battery(1, 1, 1, reserve=.2, rte=.9, standby_w=20, charging='mixed')
    r = m.steady_dispatch(d, [0, .2], [0, 0], [0, 0], [c])
    assert np.isclose(r['flows']['standby_kwh'].sum(), .01)
    assert abs(r['soc_end'][0] - r['soc_start'][0]) < 1e-8
    rejects(lambda: m.steady_dispatch(d, [0, .2], [0, 0], [0, 0], [c], max_iterations=1))


@case
def case_invalid_inputs_and_missing_sources_fail_closed():
    m = model()
    d = frame(['2026-06-01 00:00'])
    for x in ([-1], [float('nan')], [float('inf')], [], [1, 2]):
        rejects(lambda: m.dispatch(d, x, [0], [0], []))
    for kwargs in ({'reserve': 1}, {'rte': 0}, {'standby_w': -1}, {'charging': 'unknown'}, {'discharge_kw': -1}):
        rejects(lambda: m.Battery(**dict(dict(capacity_kwh=1, discharge_kw=1, charge_kw=1), **kwargs)))
    rejects(lambda: m.dispatch(d, [0], [0], [0], [], export_treatment='unknown'))
    rejects(lambda: m.dispatch(d, [0], [0], [0], [m.Battery(1, 1, 1)], initial_soc=[2]))
    rejects(lambda: m.load_inputs(pathlib.Path('/nonexistent/portable-study-input')))


@case
def case_dst_rows_use_meter_interval_duration_not_wall_clock_subtraction():
    m = model()
    d = frame(['2026-11-01 01:00', '2026-11-01 01:00', '2026-03-08 01:45', '2026-03-08 03:00'])
    r = m.dispatch(d, [0]*4, [0]*4, [1]*4, [], shared_ac_kw=.4)
    assert np.allclose(r['exports'], [.1]*4)
    assert np.isclose(r['flows']['curtailed_kwh'].sum(), 3.6)


@case
def case_monthly_rebill_crosses_credit_bucket_and_preserves_nbc():
    m = model()
    d = frame(['2026-06-01 17:00', '2026-07-01 17:00'])
    a = m.bill_comparison(d, [1, 1], [0, 0], [0, 2], [1, 0])
    # June flips +1 import to -1 export; July adds a gross import.
    expected = R.credit('S', 'on')
    assert np.isclose(a['savings_usd'], expected)
    assert np.isclose(a['after']['components']['nbc_usd'], 2 * R.NBC)
    assert np.isclose(a['before']['components']['fixed_usd'], a['after']['components']['fixed_usd'])
    assert len(a['after']['monthly']) == 2


@case
def case_economics_unknown_cost_and_explicit_transition_cost():
    m = model()
    a = m.economics(100, discount=0, degradation=0)
    assert a['installed_cost_usd'] is None and a['simple_payback_years'] is None
    assert a['five_year']['break_even_installed_usd'] == 500
    assert a['five_year']['npv_usd'] is None
    b = m.economics(100, installed_cost=200, discount=0, degradation=0, compliance_cost=50, compliance_year=4)
    assert b['five_year']['npv_usd'] == 250
    assert b['ten_year']['break_even_installed_usd'] == 950
    assert b['simple_payback_years'] == 2
    rejects(lambda: m.economics(float('nan')))


@case
def case_production_shapes_delay_within_day_and_apply_shade():
    m = model()
    assert hasattr(m, 'pv_profile'), 'production sensitivity helper is missing'
    d = frame(['2026-06-01 12:00', '2026-06-01 14:00', '2026-06-01 23:00', '2026-06-02 01:00'])
    assert np.allclose(m.pv_profile(d, [1, 0, 3, 0], 2, 'delayed_2h'), [0, 2, 0, 0])
    assert np.allclose(m.pv_profile(d, [1, 0, 3, 0], 2, 'shade_50pct'), [1, 0, 3, 0])
    rejects(lambda: m.pv_profile(d, [1]*4, 1, 'measured_west'))


@case
def case_availability_removes_standby_charge_and_discharge_when_offline():
    m = model()
    d = frame(['2026-06-01 00:00', '2026-06-01 17:00'])
    c = m.Battery(1, 1, 1, standby_w=20)
    r = m.dispatch(d, [0, 1], [0, 0], [0, 0], [c], availability=[0, 0])
    assert np.allclose(r['imports'], [0, 1])
    assert r['flows']['standby_kwh'].sum() == 0
    rejects(lambda: m.dispatch(d, [0, 1], [0, 0], [0, 0], [c], availability=[.5, 1]))


@case
def case_tariff_spread_rebill_changes_both_counterfactuals_and_restores_rates():
    m = model()
    assert hasattr(m, 'tariff_spread_comparison'), 'tariff stress rebill is missing'
    d = frame(['2026-06-01 00:00', '2026-06-01 17:00'])
    original = R.UDC['S']['on']
    a = m.tariff_spread_comparison(d, [0, 1], [0, 0], [1, 0], [0, 0], factor=0)
    assert abs(a['savings_usd']) < 1e-9
    assert R.UDC['S']['on'] == original
    b = m.tariff_spread_comparison(d, [0, 1], [0, 0], [1, 0], [0, 0], factor=1)
    assert np.isclose(b['savings_usd'], R.energy('S', 'on') - R.energy('S', 'sop'))


@case
def case_scenario_summary_runtime_and_marginal_economics():
    m = model()
    assert hasattr(m, 'scenario_summary'), 'scenario accounting is missing'
    d = frame(['2026-06-01 12:00', '2026-06-01 17:00'])
    c = m.Battery(2, 1, 1, reserve=.2, rte=1, standby_w=0, charging='solar')
    r = m.steady_dispatch(d, [0, .25], [.25, 0], [0, 0], [c])
    summary = m.scenario_summary(d, [0, .25], [.25, 0], r, [c])
    assert summary['battery']['served_kwh'] == .25
    assert summary['battery']['existing_solar_charge_kwh'] == .25
    assert summary['physical']['peak_import_kw'] == 0
    runtime = m.backup_runtime(c, [100, 2000])
    assert runtime[0]['runtime_from_full_hours'] == 16
    assert runtime[0]['runtime_from_preserved_reserve_hours'] == 4
    assert runtime[1]['runtime_from_full_hours'] is None
    assert runtime[1]['reason'] == 'constant load exceeds modeled AC output'

@case
def case_matrix_integrates_each_pair_and_keeps_free_behavior_out_of_hardware():
    m = model()
    assert hasattr(m, 'build_study'), 'study builder is missing'
    d = frame(pd.date_range('2026-06-01', periods=96, freq='15min'))
    imp = np.where(d.p.to_numpy() == 'on', .3, .1)
    exp = np.where((d.dt.dt.hour >= 10) & (d.dt.dt.hour < 14), .4, 0)
    inputs = {'frame': d, 'imports': imp + .01, 'post_imports': imp, 'exports': exp,
              'pv_per_kw': np.where(exp > 0, .1, 0), 'moved_kwh': .96,
              'free_fix_scenario': 'synthetic', 'provenance': {}}
    out = m.build_study(inputs, include_references=False)
    rows = {r['id']: r for r in out['scenarios']}
    assert out['baseline']['free_behavior_saving_usd'] > 0
    assert 'stream_single_default_360w' in rows, 'manual default output case is missing'
    default = rows['stream_single_default_360w']
    assert default['batteries'][0]['capacity_kwh'] == 1.92
    assert default['batteries'][0]['discharge_kw'] == .36
    assert default['physical']['onpeak_served_kwh'] <= .36 * 5
    small = rows['battery_3.84kwh']
    assert np.isclose(small['annual_savings_usd'], out['baseline']['post_behavior']['total_usd'] - small['bill']['total_usd'])
    for comparison in out['marginal_comparisons']:
        assert np.isclose(comparison['incremental_savings_usd'], rows[comparison['after']]['annual_savings_usd'] - rows[comparison['before']]['annual_savings_usd'])
    assert out['unavailable_scenarios']['selected_loads']['annual_savings_usd'] is None
    assert rows['powerwall_then_small']['annual_soc_error_kwh'] < 1e-8
    assert rows['small_then_powerwall']['annual_soc_error_kwh'] < 1e-8
    assert all(r['economics']['installed_cost_usd'] is None for r in rows.values())
    assert len(out['financial_sensitivities']['tariff_spread_rebills']) == 3

@case
def case_fractional_solar_charge_never_creates_negative_grid_import():
    m = model()
    d = frame(['2026-06-01 12:00'])
    r = m.dispatch(d, [.03], [.8], [.06499999999999995], [m.Battery(3.84, 1.2, 1.2)], shared_ac_kw=.4, initial_soc=[2.8])
    assert r['imports'][0] >= 0, 'roundoff must not produce negative physical imports'
    assert r['controllers'][0]['grid_charge_kwh'][0] >= 0
    m.bill_comparison(d, [.03], [.8], r['imports'], r['credited_exports'])


@case
def case_new_pv_and_rooftop_charge_accounts_preserve_remaining_old_credit():
    m = model()
    d = frame(['2026-06-01 12:00'])
    b = m.Battery(1, 1, .4, reserve=0, rte=1, standby_w=0, charging='solar')
    r = m.dispatch(d, [0], [.2], [.3], [b], export_treatment='zero_credit')
    assert np.allclose(r['controllers'][0]['new_solar_charge_kwh'], [.1])
    assert np.allclose(r['controllers'][0]['existing_solar_charge_kwh'], [0])
    assert np.allclose(r['credited_exports'], [.2])
    assert np.allclose(r['exports'], [.4])
    assert np.isclose(r['soc_end'][0], .1)


@case
def case_source_modes_are_real_constraints_and_not_accounting_labels():
    m = model()
    d = frame(['2026-06-01 12:00'])
    base = m.Battery(1, 1, 1, reserve=0, rte=1, standby_w=0)
    solar = m.dispatch(d, [0], [.1], [0], [replace(base, charging='solar')])
    grid = m.dispatch(d, [0], [.1], [0], [replace(base, charging='grid')])
    assert solar['imports'][0] == 0 and solar['exports'][0] == 0
    assert np.isclose(solar['soc_end'][0], .1)
    assert grid['imports'][0] == .25 and grid['exports'][0] == .1
    assert grid['soc_end'][0] == .25


@case
def case_frame_and_remaining_input_validation_fail_closed():
    m = model()
    d = frame(['2026-06-01 12:00'])
    for bad in (d.iloc[:0], d.drop(columns=['ym']), d.assign(p='other'), d.assign(seas='other'), d.assign(dt=pd.NaT)):
        rejects(lambda: m.dispatch(bad, [0], [0], [0], []))
    for value in (-1, float('nan'), float('inf')):
        rejects(lambda: m.dispatch(d, [0], [0], [0], [], shared_ac_kw=value))
    rejects(lambda: m.dispatch(d, [0], [0], [0], ['battery']))
    rejects(lambda: m.dispatch(d, [0], [0], [0], [m.Battery(1, 1, 1)], initial_soc=[0]))
    rejects(lambda: m.steady_dispatch(d, [0], [0], [0], [], tolerance=0))
    rejects(lambda: m.steady_dispatch(d, [0], [0], [0], [], max_iterations=0))
    rejects(lambda: m.economics(100, installed_cost=-1))
    rejects(lambda: m.economics(100, availability=1.1))
    rejects(lambda: m.economics(100, degradation=1))
    before = R.UDC
    rejects(lambda: m.tariff_spread_comparison(d, [-1], [0], [0], [0], factor=.5))
    assert R.UDC is before


@case
def case_multiple_month_scenario_accounts_reconcile_to_annual():
    m = model()
    d = frame(['2026-06-01 12:00', '2026-07-01 17:00'])
    b = m.Battery(1, 1, 1, reserve=0, rte=1, standby_w=0, charging='solar')
    r = m.steady_dispatch(d, [0, .25], [.25, 0], [0, 0], [b])
    s = m.scenario_summary(d, [0, .25], [.25, 0], r, [b])
    monthly = s['bill']['monthly']
    assert 'physical' in monthly['2026-06'], 'monthly energy and source accounts are missing'
    assert monthly['2026-06']['battery']['solar_charge_kwh'] == .25
    assert monthly['2026-07']['battery']['served_kwh'] == .25
    assert sum(v['physical']['loss_kwh'] for v in monthly.values()) == s['physical']['loss_kwh']

@case
def case_discharge_mask_matches_comparator_load_access_without_stopping_charge():
    m = model()
    d = frame(['2026-06-01 12:00', '2026-06-01 17:00'])
    b = m.Battery(1, 1, 1, reserve=0, rte=1, standby_w=0)
    r = m.dispatch(d, [0, 1], [0, 0], [0, 0], [b], discharge_allowed=[1, 0])
    assert r['imports'][0] == .25
    assert r['imports'][1] == 1
    assert r['soc_end'][0] == .25
    rejects(lambda: m.dispatch(d, [0, 1], [0, 0], [0, 0], [b], discharge_allowed=[.5, 0]))

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
