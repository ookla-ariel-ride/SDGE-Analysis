#!/usr/bin/env python3
"""Synthetic physics, billing, and economics checks; no private data needed."""
import pathlib
import copy
import hashlib
import json
import subprocess
import tempfile
from contextlib import contextmanager
from itertools import product
from unittest.mock import patch
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
def case_scoped_shared_cap_preserves_home_power_and_controller_order():
    m = model()
    import inspect
    assert 'shared_ac_battery_indices' in inspect.signature(m.dispatch).parameters, 'scoped shared AC membership is missing'
    d = frame(['2026-06-01 17:00'])
    home = m.Battery(2, 4, 0, reserve=0, rte=1, standby_w=0)
    small = m.Battery(1, 1, 0, reserve=0, rte=1, standby_w=0)
    for batteries, soc, membership, served, imports in [
            ([home, small], [2, 1], (1,), [1, .05], .15),
            ([small, home], [1, 2], (0,), [.05, 1], .15),
            ([home, small], [2, 1], (), [1, .2], 0),
            ([home, small], [2, 1], None, [.05, 0], 1.15)]:
        r = m.dispatch(d, [1.25], [0], [.05], batteries, initial_soc=soc,
                       shared_ac_kw=.4, shared_ac_battery_indices=membership)
        assert np.allclose([c['served_kwh'][0] for c in r['controllers']], served)
        assert np.allclose(r['imports'], [imports])
        assert r['conservation_max_error_kwh'] < 1e-10
    r = m.dispatch(d, [1.25], [0], [0], [home, small], initial_soc=[2, 1],
                   shared_ac_kw=.4, shared_ac_battery_indices=(1,))
    assert np.allclose([c['served_kwh'][0] for c in r['controllers']], [1, .1])
    pv_only = m.dispatch(d, [0], [0], [.2], [], shared_ac_kw=.4, shared_ac_battery_indices=())
    assert np.allclose(pv_only['flows']['pv_ac_kwh'], [.1])
    assert np.allclose(m.dispatch(d, [1.25], [0], [.05], [home], initial_soc=[2],
                                 shared_ac_kw=.4, shared_ac_battery_indices=())['imports'], [.2])


@case
def case_scoped_shared_cap_rejects_invalid_membership():
    m = model()
    import inspect
    assert 'shared_ac_battery_indices' in inspect.signature(m.dispatch).parameters, 'scoped shared AC membership is missing'
    d = frame(['2026-06-01 17:00'])
    b = m.Battery(1, 1, 1)
    for indices in ((True,), (False,), (-1,), (1,), (0, 0), (.0,), '0', 0):
        rejects(lambda: m.dispatch(d, [0], [0], [0], [b], shared_ac_kw=.4,
                                   shared_ac_battery_indices=indices))
    for cap in (None, 0, -1, float('inf'), float('nan')):
        for indices in ((), (0,)):
            rejects(lambda: m.dispatch(d, [0], [0], [0], [b], shared_ac_kw=cap,
                                       shared_ac_battery_indices=indices))


@case
def case_scoped_cap_keeps_pv_source_accounts_and_export_treatments():
    m = model()
    import inspect
    assert 'shared_ac_battery_indices' in inspect.signature(m.dispatch).parameters, 'scoped shared AC membership is missing'
    d = frame(['2026-06-01 12:00', '2026-06-01 17:00'])
    home = m.Battery(2, 4, .4, reserve=0, rte=1, standby_w=0, charging='solar')
    small = m.Battery(1, 1, .4, reserve=0, rte=1, standby_w=0, charging='solar')
    for batteries, member in (([home, small], (1,)), ([small, home], (0,))):
        for treatment, export, credit, curtailed in [('credited', .3, .3, .1), ('zero_credit', .3, .2, .1), ('zero_export', .2, .2, .2)]:
            r = m.dispatch(d, [0, 1], [.2, 0], [.4, 0], batteries,
                           shared_ac_kw=1.2, shared_ac_battery_indices=member, export_treatment=treatment)
            assert np.allclose(r['exports'], [export, 0])
            assert np.allclose(r['credited_exports'], [credit, 0])
            assert np.allclose(r['flows']['curtailed_kwh'], [curtailed, 0])
            assert np.allclose([c['new_solar_charge_kwh'][0] for c in r['controllers']], [.1, .1])
            assert all(c['existing_solar_charge_kwh'][0] == 0 for c in r['controllers'])
            assert r['conservation_max_error_kwh'] < 1e-10


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
    assert out['schema_version'] == 3
    assert len(rows) == 108  # Nine canonical scheduled rows need private intake.
    assert len(out['marginal_comparisons']) == 138
    for capacity in (13.5, 27):
        for ac in (.8, 1.2):
            home_id = f'large_{capacity:g}kwh_11.5kw'
            for treatment in ('credited', 'zero_credit', 'zero_export'):
                pv_id = f'home_{capacity:g}kwh_pv_{ac:g}kw_{treatment}'
                pv = rows[pv_id]
                assert pv['shared_ac_battery_indices'] == []
                assert pv['batteries'] == rows[home_id]['batteries']
                for order, member in (('home_first', [1]), ('small_first', [0])):
                    identifier = f'home_{capacity:g}kwh_combined_{ac:g}kw_{treatment}_{order}'
                    combined = rows[identifier]
                    assert combined['shared_ac_battery_indices'] == member
                    assert combined['controller_order'] == (['home', 'small'] if order == 'home_first' else ['small', 'home'])
                    control_id = ('powerwall_then_small' if order == 'home_first' else 'small_then_powerwall') if capacity == 13.5 and ac == 1.2 else f'home_{capacity:g}kwh_small_{ac:g}kw_{order}'
                    control = rows[control_id]
                    assert control['batteries'] == combined['batteries']
                    if ac == .8:
                        assert control['shared_new_pv_battery_ac_kw'] == .8
                        assert control['shared_ac_battery_indices'] == member
                    assert control['pv_dc_kw'] == 0
                    pairs = {(p['before'], p['after']): p for p in out['marginal_comparisons']}
                    for before in (home_id, f'combined_{ac:g}kw_{treatment}', pv_id, control_id):
                        pair = pairs[before, identifier]
                        difference = rows[before]['bill']['total_usd'] - combined['bill']['total_usd']
                        assert abs(pair['incremental_savings_usd'] - difference) < 1e-10
                        expected_ceiling = sum(difference*.99**(year-1)/1.05**year for year in range(1, 6))
                        assert abs(pair['economics']['five_year']['break_even_installed_usd']-expected_ceiling) < 1e-10
    costs = out['financial_sensitivities']['incremental_complete_cost_sensitivities']
    assert len(costs) == 12
    for entry in costs:
        before = out['baseline']['post_behavior'] if entry['baseline_id'] == 'post_free_fix' else rows[entry['baseline_id']]['bill']
        delta = before['total_usd'] - rows[entry['scenario_id']]['bill']['total_usd']
        assert abs(entry['incremental_savings_usd'] - delta) < 1e-10
        assert [e['installed_cost_usd'] for e in entry['cost_sensitivities']] == [1000, 2500, 5000, 7500]
        for econ in entry['cost_sensitivities']:
            cost = econ['installed_cost_usd']
            assert econ['simple_payback_years'] == (cost/delta if delta > 0 else None)
            for years, key in ((5, 'five_year'), (10, 'ten_year')):
                expected = sum(delta*.99**(y-1)/1.05**y for y in range(1, years+1))
                assert abs(econ[key]['npv_usd'] - expected + cost) < 1e-9
    negative = m.economics(-10, installed_cost=1000)
    assert negative['simple_payback_years'] is None
    assert negative['five_year']['break_even_installed_usd'] < 0


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

@case
def case_uncredited_physical_export_changes_nbc_only():
    m = model()
    d = frame(['2026-06-01 12:00'])
    result = m.bill_comparison(d, [1.], [.6], [1.], [0.], physical_exports_after=[.6])
    assert abs(result['after']['components']['nbc_usd'] - .4 * m.R.NBC) < 1e-12
    assert abs(result['before']['components']['nbc_usd'] - .4 * m.R.NBC) < 1e-12
    assert abs(result['savings_usd'] + .6 * m.R.energy('S', 'sop')) < 1e-12



# These fixtures catch wrong channel prices and calendar mapping, not source text.
def nbt_case(generation=True):
    keys = list(product(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
                        ['Weekday', 'Weekend'], range(24)))
    delivery = {k: .1 if k[0] == 'Jul' else .05 for k in keys}
    gen = {k: (.05 if k[0] == 'Jul' else .02) if generation else 0. for k in keys}
    return {'vintage': 'NBT00', 'generation_credit_assumption': 'delivery_generation_cea_bonus' if generation else 'delivery_cea_bonus_only',
            'delivery_lookup': delivery, 'generation_lookup': gen, 'cea_bonus_usd_per_kwh': .01,
            'total_lookup': {k: delivery[k] + gen[k] + .01 for k in keys}}


@contextmanager
def invented_import_rates():
    old = (R.UDC, R.CEA, R.PCIA, R.NBC, R.BSC)
    R.UDC = {s: dict.fromkeys(('on', 'off', 'sop'), v) for s, v in [('W', .2), ('S', .4)]}
    R.CEA = {s: dict.fromkeys(('on', 'off', 'sop'), v) for s, v in [('W', .1), ('S', .3)]}
    R.PCIA, R.NBC, R.BSC = .01, .02, .5
    try:
        yield
    finally:
        R.UDC, R.CEA, R.PCIA, R.NBC, R.BSC = old


def nbt_api():
    m = model()
    assert hasattr(m, '_nbt_bill'), 'signed NBT ledger API is missing'
    return m


@case
def case_nbt_two_month_hand_components_and_partial_date_fixed_charge():
    m = nbt_api()
    d = frame(['2026-01-07 12:00', '2026-01-07 12:15', '2026-07-04 17:00'])
    with invented_import_rates():
        bill = m._nbt_bill(d, [2, 1, 3], [.5, .5, 1], physical_exports=[1, 1, 1], case=nbt_case())
        want = dict(zip(('import_delivery_usd', 'import_generation_usd', 'import_pcia_usd', 'import_nbc_usd',
                         'fixed_usd', 'export_delivery_credit_usd', 'export_generation_credit_usd', 'export_cea_bonus_credit_usd'),
                        (1.8, 1.2, .06, .12, 1, -.15, -.07, -.02)))
        for key, value in want.items():
            assert abs(bill['components'][key] - value) < 1e-12, key
        assert abs(bill['total_usd'] - 3.94) < 1e-12
        assert abs(bill['monthly']['2026-01']['total_usd'] - 1.41) < 1e-12
        assert abs(bill['monthly']['2026-07']['total_usd'] - 2.53) < 1e-12
        only = m._nbt_bill(d, [2, 1, 3], [.5, .5, 1], physical_exports=[1, 1, 1], case=nbt_case(False))
        assert abs(only['total_usd'] - 4.01) < 1e-12


@case
def case_nbt_physical_credit_channels_gross_nbc_and_zero_export():
    m = nbt_api()
    d = frame(['2026-01-07 12:00'])
    with invented_import_rates():
        full = m._nbt_bill(d, [1], [.5], physical_exports=[2], case=nbt_case())
        none = m._nbt_bill(d, [1], [0], physical_exports=[2], case=nbt_case())
        assert full['components']['import_nbc_usd'] == none['components']['import_nbc_usd'] == .02
        assert full['physical_exports_kwh'] == none['physical_exports_kwh'] == 2
        assert abs(none['total_usd'] - full['total_usd'] - .04) < 1e-12
        assert none['weighted_export_credit_usd_per_kwh'] is None
        assert none['total_usd'] == m._nbt_bill(d, [1], [0], physical_exports=[2], case=nbt_case(False))['total_usd']


@case
def case_nbt_quarter_hours_holiday_dst_rows_and_missing_spring_hour():
    m = nbt_api()
    d = frame(['2026-07-03 12:00', '2025-07-04 12:00', '2025-07-04 12:15', '2025-07-04 12:30',
               '2025-07-04 12:45', '2025-11-02 01:15', '2025-11-02 01:15', '2026-03-08 01:45', '2026-03-08 03:00'])
    case_ = nbt_case(False)
    for key in case_['total_lookup']:
        case_['delivery_lookup'][key] = .2 if key[1] == 'Weekend' else .1
        case_['total_lookup'][key] = case_['delivery_lookup'][key] + .01
    before = d.copy(deep=True)
    b = m._nbt_bill(d, np.zeros(9), [.25]*9, physical_exports=[.25]*9, case=case_)
    # July 3 weekday is .11; all eight remaining holiday/Sunday rows are .21.
    assert abs(b['export_credit_total_usd'] - .4475) < 1e-12
    assert b['credited_exports_kwh'] == 2.25
    assert b['monthly']['2025-11']['credited_exports_kwh'] == .5
    assert b['monthly']['2026-03']['credited_exports_kwh'] == .5
    pd.testing.assert_frame_equal(d, before)


@case
def case_nbt_invalid_flows_dates_and_missing_touched_bucket_fail_closed():
    m = nbt_api()
    d = frame(['2026-01-07 12:00'])
    for imp, credit, physical in [([-1], [0], [0]), ([float('nan')], [0], [0]),
                                  ([1, 2], [0], [0]), ([1], [.1], [0]),
                                  ([1], [float('inf')], [1]), ([1], [0], [-1])]:
        rejects(lambda: m._nbt_bill(d, imp, credit, physical_exports=physical, case=nbt_case()))
    physical = np.array([1.])
    m._nbt_bill(d, [1], [1 + 5e-10], physical_exports=physical, case=nbt_case())
    assert physical[0] == 1., 'tolerance must not change physical exports'
    rejects(lambda: m._nbt_bill(d, [1], [1+2e-9], physical_exports=physical, case=nbt_case()))
    bad = d.copy(); bad['ym'] = pd.Period('2026-02')
    rejects(lambda: m._nbt_bill(bad, [1], [0], physical_exports=[0], case=nbt_case()))
    accepted_buckets = []
    for field, value in (('seas', 'S'), ('p', 'on')):
        bad = d.copy(); bad[field] = value
        try:
            m._nbt_bill(bad, [1], [0], physical_exports=[0], case=nbt_case())
        except ValueError:
            pass
        else:
            accepted_buckets.append(field)
    assert not accepted_buckets, f'timestamp-inconsistent buckets accepted: {accepted_buckets}'
    for name in ('total_lookup', 'delivery_lookup', 'generation_lookup'):
        bad_case = nbt_case(); bad_case[name].pop(('Jan', 'Weekday', 12))
        try:
            m._nbt_bill(d, [1], [0], physical_exports=[0], case=bad_case)
        except (ValueError, SystemExit):
            pass
        else:
            assert False, name


@case
def case_nbt_generation_sensitivity_changes_storage_and_pv_in_opposite_directions():
    m = nbt_api()
    d = frame(['2026-01-07 12:00'])
    with invented_import_rates(), patch.object(m, 'steady_dispatch', side_effect=AssertionError('reprice invoked dispatch')):
        # Storage loses 1 kWh of credited rooftop export; PV adds 1 kWh.
        for after_import, after_export, full_delta, only_delta in [(0, 0, .25, .27), (1, 2, .08, .06), (2, 1, -.33, -.33)]:
            got = []
            for case_ in (nbt_case(), nbt_case(False)):
                out = m.nbt_bill_comparison(d, [1], [1], [after_import], [after_export], physical_exports_after=[after_export], case=case_)
                got.append(out['signed_ledger_delta_usd'])
            assert np.allclose(got, [full_delta, only_delta], atol=1e-12), got


@case
def case_nbt_credit_excess_remains_visible_in_positive_and_negative_ledgers():
    m = nbt_api()
    d = frame(['2026-01-07 12:00', '2026-07-04 17:00'])
    with invented_import_rates():
        case_ = nbt_case()
        for k in case_['total_lookup']:
            case_['generation_lookup'][k] = .2
            case_['total_lookup'][k] = case_['delivery_lookup'][k] + .21
        positive = m._nbt_bill(d, [1, 10], [2, 0], physical_exports=[2, 0], case=case_)
        diag = positive['settlement_diagnostics']
        assert positive['total_usd'] > 0
        assert abs(diag['monthly']['2026-01']['generation_credit_excess_usd'] - .32) < 1e-12
        assert diag['annual']['generation_credit_excess_usd'] == 0
        negative = m._nbt_bill(d.iloc[:1], [1], [10], physical_exports=[10], case=case_)
        assert negative['total_usd'] < 0
        assert negative['settlement_diagnostics']['negative_months'] == ['2026-01']
        assert negative['settlement_diagnostics']['annual']['observed_net_export']
        assert negative['economics'] is None
        assert negative['payable_bill_established'] is False
        assert negative['actual_tariff_accuracy_established'] is False
        assert negative['settlement_credit_limits_applied'] is False
        assert negative['settlement_diagnostics']['nsc_usd'] is None


@case
def case_nbt_matrix_maps_every_scenario_and_marginal_to_its_behavior_baseline():
    m = nbt_api()
    d = frame(pd.date_range('2026-01-07', periods=96, freq='15min'))
    imp = np.where(d.p.to_numpy() == 'on', .3, .1)
    exp = np.where((d.dt.dt.hour >= 10) & (d.dt.dt.hour < 14), .4, 0)
    inputs = {'frame': d, 'imports': imp+.1, 'post_imports': imp, 'exports': exp,
              'pv_per_kw': np.where(exp > 0, .1, 0), 'moved_kwh': 9.6, 'free_fix_scenario': 'synthetic', 'provenance': {}}
    context = m.load_nbt_context()
    snapshot = copy.deepcopy(inputs)
    traces = []
    real_dispatch = m.steady_dispatch
    def capture(*args, **kwargs):
        result = real_dispatch(*args, **kwargs)
        traces.append(result)
        return result
    with patch.object(m, 'steady_dispatch', side_effect=capture):
        out = m.build_study(inputs, include_references=False, nbt_context=context)
    for row, trace in zip(out['scenarios'], traces):
        assert trace['annual_soc_error_kwh'] < 1e-8
        if 'shared_ac_battery_indices' in row:
            capped = trace['flows']['pv_ac_kwh'].copy()
            for index in row['shared_ac_battery_indices']:
                capped += trace['controllers'][index]['served_kwh']
            assert np.all(capped <= row['shared_new_pv_battery_ac_kw']*.25 + 1e-12)
        assert abs(row['component_savings_usd']['fixed_usd']) < 1e-12
    saved = copy.deepcopy(traces)
    altered = copy.deepcopy(context)
    for case_ in altered['cases'].values():
        case_['delivery_lookup'] = dict(case_['delivery_lookup'])
        for k in case_['total_lookup']:
            case_['total_lookup'][k] += .1
            case_['delivery_lookup'][k] += .1
    with patch.object(m, 'steady_dispatch', side_effect=AssertionError('tariff cases reran dispatch')):
        reprice = m._nbt_sensitivity(d, inputs['imports'], imp, exp, out['scenarios'],
                                    {r['id']: t for r, t in zip(out['scenarios'], traces)},
                                    [], {}, out['marginal_comparisons'], altered)
    def unchanged(a, b):
        if isinstance(a, dict):
            assert a.keys() == b.keys()
            for k in a:
                unchanged(a[k], b[k])
        elif isinstance(a, list):
            for x, y in zip(a, b):
                unchanged(x, y)
        elif isinstance(a, np.ndarray):
            np.testing.assert_array_equal(a, b)
        else:
            assert a == b
    unchanged(traces, saved)  # Includes source accounts, controllers, SOC, and all physical flows.
    sensitivity = out['present_2026_nbt_sensitivity']
    first_case = next(iter(sensitivity['cases']))
    assert reprice['cases'][first_case]['baselines']['post_free_fix']['total_usd'] != sensitivity['cases'][first_case]['baselines']['post_free_fix']['total_usd']
    rows = {r['id']: r for r in out['scenarios']}
    assert len(sensitivity['cases']) == 4
    for c in sensitivity['cases'].values():
        assert set(c['scenarios']) == set(rows)
        assert c['canonical_references'] == {}
        assert c['free_behavior_signed_ledger_delta_usd'] > 0
        assert {r['export_treatment'] for r in c['scenarios'].values()} == {'credited', 'zero_credit', 'zero_export'}
        for identifier, r in c['scenarios'].items():
            source = rows[identifier]
            baseline = 'unchanged_behavior' if source['behavior'] == 'unchanged' else 'post_free_fix'
            assert r['baseline_id'] == baseline
            assert r['source_scenario_id'] == identifier
            assert abs(r['annual_hardware_signed_ledger_delta_usd'] - (c['baselines'][baseline]['total_usd']-r['signed_cost_ledger']['total_usd'])) < 1e-10
            assert abs(r['nbt_ledger_delta_minus_nem_model_saving_usd'] - (r['annual_hardware_signed_ledger_delta_usd']-source['annual_savings_usd'])) < 1e-10
            assert r['economics'] is None
        assert len(c['marginals']) == len(out['marginal_comparisons'])
        for pair in c['marginals'].values():
            before, after = (c['scenarios'][pair[k]] for k in ('before_id', 'after_id'))
            assert abs(pair['incremental_signed_ledger_delta_usd'] - (before['signed_cost_ledger']['total_usd']-after['signed_cost_ledger']['total_usd'])) < 1e-10
        assert {'powerwall_then_small', 'small_then_powerwall'} <= set(c['scenarios'])
        bad = copy.deepcopy(c['scenarios']); bad['battery_3.84kwh']['baseline_id'] = 'unchanged_behavior'
        rejects(lambda: m._nbt_marginals(bad, out['marginal_comparisons']))
    assert set(sensitivity['scenario_ranges']) == set(rows)
    pd.testing.assert_frame_equal(inputs['frame'], snapshot['frame'])
    for key in ('imports', 'post_imports', 'exports', 'pv_per_kw'):
        np.testing.assert_array_equal(inputs[key], snapshot[key])


@case
def case_nbt_public_import_and_synthetic_bill_need_no_intake_or_dispatch():
    source = str(pathlib.Path(__file__).resolve().parent)
    code = f"""
import sys, importlib.abc
sys.path.insert(0, {source!r})
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('household', 'behavior_rebuild', 'battery_dispatch_policies'):
            raise AssertionError('private intake/dispatch import: '+fullname)
sys.meta_path.insert(0, Block())
import portable_solar_storage as m
import nem3_grandfathering as ng
import pandas as pd
d=pd.DataFrame({{'dt': pd.to_datetime(['2026-01-07 12:00']), 'seas':['W'], 'p':['sop'], 'ym':pd.PeriodIndex(['2026-01'], freq='M')}})
c=next(iter(m.load_nbt_context()['cases'].values()))
b=m._nbt_bill(d, [1], [0], physical_exports=[0], case=c)
assert b['weighted_export_credit_usd_per_kwh'] is None
"""
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run([sys.executable, '-c', code], cwd=directory, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@case
def case_nbt_public_artifact_provenance_semantics_and_all_reference_mappings():
    root = pathlib.Path(__file__).resolve().parent.parent
    out = json.loads((root/'data/portable_solar_storage.json').read_text())
    assert out['schema_version'] == 3, 'public artifact lacks scoped joint cases'
    assert len(out['scenarios']) == 117
    assert len(out['marginal_comparisons']) == 138
    assert len(out['canonical_no_reserve_benchmarks']) == 9
    b = out['present_2026_nbt_sensitivity']
    assert b['tariff_year'] == 2026
    assert b['observation_window']['start'] == out['provenance']['window_start']
    assert b['observation_window']['end'] == out['provenance']['window_end']
    for flag in ('future_transition_projection', 'payable_bill_established', 'actual_tariff_accuracy_established', 'settlement_credit_limits_applied'):
        assert b[flag] is False
    rate_bytes = (root/b['rate_provenance']['path']).read_bytes()
    assert b['rate_provenance']['sha256'] == hashlib.sha256(rate_bytes).hexdigest()
    assert b['rate_provenance']['sha256'] != hashlib.sha256(rate_bytes + b'\n').hexdigest(), 'changed CSV bytes must expose a stale digest'
    extension = out['provenance']['joint_extension']
    assert extension['generator_sha256'] == hashlib.sha256((root/extension['generator']).read_bytes()).hexdigest()
    for path, digest in b['engine_provenance'].items():
        assert digest == hashlib.sha256((root/path).read_bytes()).hexdigest()
    assert out['unavailable_scenarios']['post_nem_adoption']['annual_savings_usd'] is None
    assert out['unavailable_scenarios']['post_nem_adoption']['sensitivity_pointer'] == 'present_2026_nbt_sensitivity'
    for c in b['cases'].values():
        assert set(c['scenarios']) == {r['id'] for r in out['scenarios']}
        assert len(c['canonical_references']) == len(out['canonical_no_reserve_benchmarks'])
        assert {r['scheduled_row'] for r in c['canonical_references'].values()} == {r['scheduled_row'] for r in out['canonical_no_reserve_benchmarks']}
        for r in c['canonical_references'].values():
            source = next(x for x in out['canonical_no_reserve_benchmarks'] if x['scheduled_row'] == r['scheduled_row'])
            assert (r['capacity_kwh'], r['discharge_kw'], r['charge_kw'], r['policy']) == (source['capacity_kwh'], source['discharge_kw'], source['charge_kw'], source['policy'])
            assert r['baseline_id'] == 'post_free_fix'
            assert abs(r['annual_hardware_signed_ledger_delta_usd']-(c['baselines']['post_free_fix']['total_usd']-r['signed_cost_ledger']['total_usd'])) < 2e-6
            assert abs(r['nbt_ledger_delta_minus_nem_model_saving_usd']-(r['annual_hardware_signed_ledger_delta_usd']-source['annual_savings_usd'])) < 2e-6
        for pair in c['marginals'].values():
            before, after = (c['scenarios'][pair[k]] for k in ('before_id', 'after_id'))
            assert before['baseline_id'] == after['baseline_id']
            assert abs(pair['incremental_signed_ledger_delta_usd']-(before['signed_cost_ledger']['total_usd']-after['signed_cost_ledger']['total_usd'])) < 2e-6
        ledgers = list(c['baselines'].values()) + [r['signed_cost_ledger'] for r in list(c['scenarios'].values())+list(c['canonical_references'].values())]
        for ledger in ledgers:
            assert abs(sum(x['total_usd'] for x in ledger['monthly'].values())-ledger['total_usd']) < 2e-5
            assert abs(sum(ledger['components'].values())-ledger['total_usd']) < 1e-5
            assert ledger['economics'] is None
    assert 'private/' not in json.dumps(b)



@case
def case_ownership_allowance_hand_math_and_discount_timing():
    m = model()
    assert hasattr(m, 'ownership_allowance'), 'ownership allowance function is missing'
    five = m.ownership_allowance(100, 200, years=5, annual_cost=10,
                                 service_cost=50, service_year=3, discount=0, degradation=0)
    assert five['discounted_savings_usd'] == 500
    assert five['discounted_ownership_costs_usd'] == 100
    assert five['total_affordable_initial_cost_usd'] == 400
    assert five['remaining_allowance_usd'] == 200
    two = m.ownership_allowance(100, 200, years=2, annual_cost=10,
                                service_cost=50, service_year=3, discount=0, degradation=0)
    assert two['remaining_allowance_usd'] == -20
    assert two['discounted_service_cost_usd'] == 0
    timed = m.ownership_allowance(110, 200, years=2, annual_cost=11,
                                  service_cost=121, service_year=2, discount=.1, degradation=.1)
    assert abs(timed['discounted_savings_usd'] - (100 + 99 / 1.21)) < 1e-10
    assert abs(timed['discounted_ownership_costs_usd'] - (10 + 11 / 1.21 + 100)) < 1e-10
    assert m.ownership_allowance(-100, 0, years=1)['remaining_allowance_usd'] < 0
    for key in ('installed_cost_usd', 'actual_installed_payback_years', 'actual_installed_npv_usd'):
        assert five[key] is None


@case
def case_ownership_allowance_rejects_invalid_finance_inputs():
    m = model()
    assert hasattr(m, 'ownership_allowance'), 'ownership allowance function is missing'
    for name in ('years', 'service_year'):
        for value in (True, False, 0, -1, 1.5, float('inf')):
            kwargs = dict(years=5); kwargs[name] = value
            rejects(lambda kwargs=kwargs: m.ownership_allowance(100, 200, **kwargs))
    for name in ('equipment_price', 'annual_cost', 'service_cost', 'discount', 'degradation', 'annual_saving'):
        for value in (float('inf'), float('-inf'), float('nan')):
            kwargs = dict(annual_saving=100, equipment_price=200, years=5); kwargs[name] = value
            rejects(lambda kwargs=kwargs: m.ownership_allowance(**kwargs))
    for name in ('equipment_price', 'annual_cost', 'service_cost', 'discount', 'degradation'):
        kwargs = dict(annual_saving=100, equipment_price=200, years=5); kwargs[name] = -1
        rejects(lambda kwargs=kwargs: m.ownership_allowance(**kwargs))
    rejects(lambda: m.ownership_allowance(100, 200, years=5, degradation=1))
    assert m.ownership_allowance(100, 200, years=5, discount=1e308)['remaining_allowance_usd'] == -200


def decision_fixture():
    return json.loads((pathlib.Path(__file__).resolve().parents[1] / 'data/portable_solar_storage.json').read_text())


@case
def case_decision_support_preserves_sources_and_reconciles_months():
    m = model()
    assert hasattr(m, 'build_decision_support'), 'decision support builder is missing'
    study = decision_fixture()
    study.pop('decision_support', None)
    before = copy.deepcopy(study)
    out = m.build_decision_support(study)
    assert study == before, 'derived builder changed an inherited object'
    assert out['schema_version'] == 1
    assert len(out['common_options']) == 5
    pv = out['common_options'][0]
    assert pv['family'] == 'pv_0.8kw' and pv['battery_capacity_kwh'] == 0
    assert len(out['operating_cases']) == 5
    for row in out['operating_cases']:
        assert len(row['monthly_savings_usd']) == 13
        assert '2025-07' in row['monthly_savings_usd'] and '2026-07' in row['monthly_savings_usd']
        assert abs(sum(row['monthly_savings_usd'].values()) - row['annual_savings_usd']) < 2e-5
        assert abs(sum(s['annual_savings_usd'] for s in row['seasons'].values()) - row['annual_savings_usd']) < 2e-5
    assert len(out['purchase_increments']) == 12
    assert len(out['ownership_stresses']['cases']) == 72
    assert len(out['policy_bridge']['cases']) == 3
    no_refs = copy.deepcopy(study); no_refs['canonical_no_reserve_benchmarks'] = []
    assert m.build_decision_support(no_refs)['policy_bridge']['status'] == 'references not requested'
    assert len(study['scenarios']) == 117 and len(study['marginal_comparisons']) == 138


@case
def case_decision_support_candidate_catalog_and_matched_increments():
    m = model()
    assert hasattr(m, 'build_decision_support'), 'decision support builder is missing'
    out = m.build_decision_support(decision_fixture())
    candidates = {r['id']: r for r in out['candidate_allowances']}
    expected = {
        'stream_default': ('EFSTREAMULTRA800W-US', 1399, 'stream_single_default_360w'),
        'stream_conditional': ('EFSTREAMULTRA800W-US', 1399, 'stream_single_conditional_800w'),
        'dp3_base': ('EFDELTAPRO3-US', 2799, 'large_4.096kwh_4kw'),
        'dpu_base': ('DPU-US', 4199, 'large_6.144kwh_7.2kw'),
        'dpu_shp2_bundle': ('SHP2-DPU', 5499, 'large_6.144kwh_7.2kw'),
        'dpu_two_battery': ('DPU-EB', 6199, 'large_12.288kwh_7.2kw'),
        'ultrax_base': ('DPUX-US', 7999, 'large_12.288kwh_12kw'),
        'ultrax_shp3_bundle': ('DPUX-SHP3', 10999, 'large_12.288kwh_12kw')}
    assert set(candidates) == set(expected)
    for key, (sku, price, scenario) in expected.items():
        row = candidates[key]
        assert (row['sku'], row['equipment_price_usd'], row['scenario_id']) == (sku, price, scenario)
        assert row['observation_date'] == '2026-10-23'
        assert row['warranty_years'] == (10 if key.startswith('stream') else 5)
        assert row['warranty_scope'] == ('STREAM unit' if key.startswith('stream') else 'inverter and batteries only; bundled panel accessories have separate warranty terms')
        assert row['five_year']['remaining_allowance_usd'] < row['ten_year']['remaining_allowance_usd']
    assert candidates['stream_default']['available'] is False
    assert candidates['ultrax_base']['five_year']['remaining_allowance_usd'] < 0
    assert out['ownership_stresses']['dpu_battery_increment']['equipment_price_usd'] == 2000
    panels = [r for r in out['purchase_increments'] if r['kind'] == 'panels_after_portable']
    assert len(panels) == 4
    assert panels[0]['before'] == 'battery_power_0.8kw'
    assert abs(panels[0]['annual_savings_usd'] - 455.811821) < 2e-5


@case
def case_decision_support_fails_closed_on_bad_sources_and_controls():
    m = model()
    assert hasattr(m, 'build_decision_support'), 'decision support builder is missing'
    source = decision_fixture()
    mutations = [
        lambda s: s['baseline']['unchanged_behavior'].__setitem__('total_usd', float('nan')),
        lambda s: s['baseline']['unchanged_behavior']['components'].__setitem__('fixed_usd', float('inf')),
        lambda s: s['baseline']['unchanged_behavior']['monthly']['2025-07']['components'].__setitem__('fixed_usd', float('-inf')),
        lambda s: s['baseline']['unchanged_behavior']['monthly']['2025-07'].__setitem__('total_usd', 123),
        lambda s: s['baseline'].__setitem__('free_behavior_saving_usd', float('nan')),
        lambda s: s['scenarios'].append(copy.deepcopy(s['scenarios'][0])),
        lambda s: s['scenarios'].pop(next(i for i,r in enumerate(s['scenarios']) if r['id'] == 'battery_power_0.8kw')),
        lambda s: s['scenarios'][0]['physical'].__setitem__('imports_kwh', float('nan')),
        lambda s: s['scenarios'][0]['bill']['components'].__setitem__('fixed_usd', 123),
        lambda s: next(r for r in s['scenarios'] if r['id']=='combined_0.8kw_credited')['bill']['monthly'].pop('2025-07'),
        lambda s: next(r for r in s['scenarios'] if r['id']=='combined_0.8kw_credited')['batteries'][0].__setitem__('reserve', .3),
        lambda s: s['marginal_comparisons'].append(copy.deepcopy(s['marginal_comparisons'][0])),
        lambda s: next(r for r in s['scenarios'] if r['id']=='large_12.288kwh_7.2kw')['batteries'][0].__setitem__('charge_kw', 2),
        lambda s: s['financial_sensitivities']['tariff_spread_rebills'][0].__setitem__('spread_factor', float('nan')),
        lambda s: next(r for r in s['scenarios'] if r['id']=='battery_power_0.8kw')['bill']['monthly']['2025-07']['battery'].__setitem__('served_kwh', 999),
    ]
    for mutate in mutations:
        bad = copy.deepcopy(source); mutate(bad)
        rejects(lambda bad=bad: m.build_decision_support(bad))



@case
def case_evening_output_example_uses_tariff_windows_and_hand_math():
    m = model()
    out = m.build_decision_support(decision_fixture())
    assert 'evening_output_example' in out, 'fixed evening-output example is missing'
    row = out['evening_output_example']
    assert (row['output_kw'], row['start_hour'], row['end_hour']) == (1.2, 16, 24)
    assert abs(row['delivered_kwh'] - 9.6) < 1e-12
    assert abs(row['period_delivered_kwh']['on'] - 6) < 1e-12
    assert abs(row['period_delivered_kwh']['off'] - 3.6) < 1e-12
    assert row['period_delivered_kwh']['sop'] == 0
    for weekend in (False, True):
        assert R.period(16, weekend) == 'on'
        assert R.period(20.75, weekend) == 'on'
        assert R.period(21, weekend) == 'off'
        assert R.period(23.75, weekend) == 'off'
    assert abs(row['grid_charge_kwh'] - 9.6/.85) < 1e-12
    assert abs(row['nominal_capacity_kwh'] - 13.015827469) < 1e-9
    for season, gross, charge, net in (('summer', 7.048716, 1.4112, 5.637516),
                                      ('winter', 5.499744, 1.436724705882353, 4.063019294117647)):
        result = row['seasons'][season]
        assert abs(result['gross_avoided_import_usd_per_day'] - gross) < 1e-12
        assert abs(result['grid_charge_cost_usd_per_day'] - charge) < 1e-12
        assert abs(result['net_before_standby_and_equipment_usd_per_day'] - net) < 1e-12
        assert abs(result['thirty_day_repeat']['delivered_kwh'] - 288) < 1e-10
        assert abs(result['thirty_day_repeat']['net_before_standby_and_equipment_usd'] - 30*net) < 1e-10
    assert row['annual_forecast_usd'] is None
    assert row['physical_dispatch_run'] is False
    for bad_rate in (float('nan'), float('inf'), -1):
        with patch.dict(R.UDC['S'], {'on': bad_rate}):
            rejects(lambda: m.build_decision_support(decision_fixture()))



def chart_fixture():
    # Removing a repeated fall hour or counting nonexistent spring rows breaks this fixture.
    spring = list(pd.date_range('2026-03-08', periods=96, freq='15min'))
    spring = [t for t in spring if t.hour != 2]
    fall = list(pd.date_range('2025-11-02', periods=96, freq='15min'))
    fall += list(pd.date_range('2025-11-02 01:00', periods=4, freq='15min'))
    d = frame(spring + fall + list(pd.date_range('2026-06-01', periods=96, freq='15min')))
    ids = ['pv_0.8kw_zero_credit', 'battery_power_0.8kw', 'combined_0.8kw_zero_credit']
    for cap in ('13.5', '27'):
        ids += [f'large_{cap}kwh_11.5kw', f'home_{cap}kwh_pv_0.8kw_zero_credit',
                f'home_{cap}kwh_small_0.8kw_home_first', f'home_{cap}kwh_combined_0.8kw_zero_credit_home_first']
    traces, rows = {}, []
    for i, identifier in enumerate(['post_free_fix'] + ids):
        factor = i + 1
        trace = {'imports': np.full(len(d), .25 * factor),
                 'exports': np.full(len(d), .5 * factor),
                 'credited_exports': np.full(len(d), .125 * factor)}
        if i == 0:
            trace['credited_exports'] = trace['exports'].copy()
        months = {str(ym): {'imports_kwh': float(trace['imports'][d.ym == ym].sum()),
                            'physical_exports_kwh': float(trace['exports'][d.ym == ym].sum()),
                            'credited_exports_kwh': float(trace['credited_exports'][d.ym == ym].sum())}
                  for ym in d.ym.unique()}
        if i == 0:
            baseline = {'monthly': months}
        else:
            rows.append({'id': identifier, 'bill': {'monthly': months}})
            traces[identifier] = trace
    study = {'baseline': {'post_behavior': baseline}, 'scenarios': rows,
             'provenance': {'rate_snapshot': 'synthetic'}, 'assumptions': {'policy': 'synthetic'}}
    return d, np.full(len(d), .25), np.full(len(d), .5), traces, study


@case
def case_chart_profiles_count_dst_intervals_and_reconcile_seasonal_energy():
    m = model()
    assert hasattr(m, 'build_chart_data'), 'matched hourly aggregate builder is missing'
    chart = m.build_chart_data(*chart_fixture())
    assert chart['schema_version'] == 1
    assert len(chart['profiles']) == 12
    winter = chart['basis']['seasons']['W']
    assert winter['interval_counts_by_hour'][1] == 12
    assert winter['interval_counts_by_hour'][2] == 4
    assert winter['elapsed_hours_by_hour'][1] == 3
    assert winter['elapsed_hours_by_hour'][2] == 1
    assert sum(winter['interval_counts_by_hour']) == 192
    for key, factor in [('post_free_fix', 1), ('pv_0.8kw_zero_credit', 2),
                        ('home_27kwh_combined_0.8kw_zero_credit_home_first', 12)]:
        profile = chart['profiles'][key]
        for season, total in [('S', 24), ('W', 48)]:
            row = profile['seasons'][season]
            assert row['imports_avg_kw'] == [factor] * 24
            assert row['physical_exports_avg_kw'] == [2 * factor] * 24
            assert row['totals_kwh']['imports'] == total * factor
            hours = chart['basis']['seasons'][season]['elapsed_hours_by_hour']
            assert abs(sum(a*b for a, b in zip(row['imports_avg_kw'], hours)) - total*factor) < 1e-10
    assert chart['profiles']['post_free_fix']['source_path'] == 'baseline.post_behavior'
    assert chart['profiles']['pv_0.8kw_zero_credit']['seasons']['W']['credited_exports_avg_kw'] == [1] * 24


@case
def case_chart_profiles_reject_wrong_accounts_and_do_not_mutate_traces():
    m = model()
    assert hasattr(m, 'build_chart_data'), 'matched hourly aggregate builder is missing'
    args = chart_fixture()
    saved = copy.deepcopy(args)
    chart = m.build_chart_data(*args)
    chart['profiles']['pv_0.8kw_zero_credit']['seasons']['S']['imports_avg_kw'][0] = 999
    for key in args[3]:
        for field in args[3][key]:
            assert np.array_equal(args[3][key][field], saved[3][key][field])
    assert args[4] == saved[4]
    bad = chart_fixture()
    bad[4]['scenarios'][0]['bill']['monthly']['2026-06']['imports_kwh'] += 1
    rejects(lambda: m.build_chart_data(*bad))
    bad = chart_fixture()
    bad[3]['battery_power_0.8kw']['exports'][0] = -1
    rejects(lambda: m.build_chart_data(*bad))


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
