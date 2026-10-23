#!/usr/bin/env python3
"""Exploratory portable PV/storage study; all batteries are prospective.

Run from private/verify: ../../.venv/bin/python ../../analysis/portable_solar_storage.py
Only data/portable_solar_storage.json is written. Raw interval traces remain in memory.
The public dispatch API needs no household file. The real-data builder fails closed
without staged meter/SAM inputs and the existing production reconciliation artifacts.

AC-coupled interval convention: each meter row is 0.25 elapsed hours, including
repeated DST labels. Existing import/export channels are preserved, never netted.
New AC PV offsets remaining imports, then exports; a battery can capture exports.
All new AC PV (including PV sent to a battery) and battery output share an optional
aggregate output cap. This conservative AC-coupled topology does NOT model DC
capture above the AC clipping limit. Standby is continuous AC demand, including
empty SOC. No controller charges and discharges in the same interval. Controllers
act in the supplied order on remaining flows; batteries never export intentionally.

The schedule charges from permitted solar surplus and/or super-off-peak grid,
and serves imports in off/on-peak intervals. No foresight or optimality claim.
Hardware support, circuit coverage, phase controls, and grid-parallel eligibility
remain unknown. Constant-load backup calculations assume no outage solar recharge.
Billing uses positive physical interval-net NBC, separately from export credit. NEM-ST
SC 1(f) specifies this interval-net basis. Actual bills support the numerical
choice; exact utility interval mapping, precision and grouped rounding remain open. Reproduction does
not establish tariff accuracy. See research/sb868-portable-solar-storage.md.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd
import rates as R
import nem3_grandfathering as NG

INTERVAL_HOURS = .25
SOC_TOLERANCE_KWH = 1e-8


def _finite(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    return float(value)


@dataclass(frozen=True)
class Battery:
    """Hypothetical gross DC capacity, AC directional powers, fractional reserve/RTE."""
    capacity_kwh: float
    discharge_kw: float
    charge_kw: float
    reserve: float = .2
    rte: float = .85
    standby_w: float = 10
    charging: str = 'mixed'

    def __post_init__(self):
        for key in ('capacity_kwh', 'discharge_kw', 'charge_kw', 'reserve', 'rte', 'standby_w'):
            _finite(getattr(self, key), key)
        if self.reserve >= 1 or not 0 < self.rte <= 1:
            raise ValueError('reserve must be < 1 and RTE must be in (0, 1]')
        if self.charging not in ('solar', 'grid', 'mixed'):
            raise ValueError('charging must be solar, grid, or mixed')


def _array(values, n, name):
    a = np.asarray(values, dtype=float)
    if a.shape != (n,) or not np.all(np.isfinite(a)) or np.any(a < 0):
        raise ValueError(f'{name} needs {n} finite nonnegative interval values')
    return a.copy()


def _frame(d):
    if len(d) == 0 or not {'dt', 'p', 'seas', 'ym'} <= set(d.columns):
        raise ValueError('nonempty interval frame needs dt, p, seas, ym')
    if d.dt.isna().any() or not set(d.p) <= {'sop', 'off', 'on'} or not set(d.seas) <= {'S', 'W'} or d.ym.isna().any():
        raise ValueError('invalid interval timestamps or billing buckets')


def dispatch(d, imports, exports, pv, batteries, *, export_treatment='credited',
             shared_ac_kw=None, initial_soc=None, availability=None, discharge_allowed=None):
    """Return physical interval arrays, source accounts and SOC for one pass.

    Inputs are nonnegative kWh per 15-minute meter interval. pv is available AC
    production before the shared cap. zero_credit leaves existing NEM exports
    credited and removes only new incremental exports from billing. zero_export
    curtails only new residual PV exports; it does not suppress existing rooftop
    export. Added PV can displace existing rooftop supply: any increment beyond
    the baseline meter import is treated as incremental export, regardless of
    which electrons the existing rooftop produces. Charge sources retain that
    counterfactual attribution. Exact intra-interval routing is unmeasured.
    flows.pv_ac_kwh is delivered AC after clipping and export control: direct
    use plus new-PV battery charging plus incremental export. curtailed_kwh
    includes both the initial power clip and export-control curtailment.
    """
    _frame(d)
    n = len(d)
    imp0, exp0, pv0 = (_array(x, n, name) for x, name in
                       ((imports, 'imports'), (exports, 'exports'), (pv, 'pv')))
    if export_treatment not in ('credited', 'zero_credit', 'zero_export'):
        raise ValueError('invalid export treatment')
    cap = math.inf if shared_ac_kw is None else _finite(shared_ac_kw, 'shared_ac_kw') * INTERVAL_HOURS
    if not all(isinstance(b, Battery) for b in batteries):
        raise ValueError('batteries must contain Battery configurations')
    if initial_soc is None:
        initial_soc = [b.capacity_kwh * b.reserve for b in batteries]
    soc = _array(initial_soc, len(batteries), 'initial_soc')
    for b, s in zip(batteries, soc):
        if s < b.capacity_kwh * b.reserve - 1e-12 or s > b.capacity_kwh + 1e-12:
            raise ValueError('initial SOC outside reserve/capacity')
    start = soc.copy()
    online = np.ones(n) if availability is None else _array(availability, n, "availability")
    if not np.all((online == 0) | (online == 1)):
        raise ValueError("availability must be a binary interval mask")
    standby = online * sum(b.standby_w for b in batteries) / 1000 * INTERVAL_HOURS
    active_pv = np.minimum(pv0, cap)
    imp = imp0 + standby
    direct = np.minimum(imp, active_pv)
    imp -= direct
    new_export = active_pv - direct
    old_export = exp0.copy()
    curtailed = pv0 - active_pv
    remaining_output = np.full(n, cap) - active_pv
    controllers = []
    allowed = np.ones(n) if discharge_allowed is None else _array(discharge_allowed, n, 'discharge_allowed')
    if not np.all((allowed == 0) | (allowed == 1)):
        raise ValueError('discharge_allowed must be a binary interval mask')
    periods = d.p.to_numpy()
    for k, b in enumerate(batteries):
        eta = math.sqrt(b.rte)
        floor = b.capacity_kwh * b.reserve
        log = {key: np.zeros(n) for key in ('solar_charge_kwh', 'existing_solar_charge_kwh',
               'new_solar_charge_kwh', 'grid_charge_kwh', 'served_kwh', 'loss_kwh', 'soc_kwh')}
        for j in range(n):
            if not online[j]:
                log['soc_kwh'][j] = soc[k]
                continue
            room_ac = max(0., (b.capacity_kwh - soc[k]) / eta)
            charge_limit = min(b.charge_kw * INTERVAL_HOURS, room_ac)
            new = old = grid = served = 0.
            if b.charging in ('solar', 'mixed'):
                # New surplus first avoids disposing of it in zero-credit cases;
                # this source-priority assumption is explicit, not product evidence.
                new = min(new_export[j], charge_limit)
                old = min(old_export[j], charge_limit - new)
            charged = new + old
            if b.charging in ('grid', 'mixed') and periods[j] == 'sop':
                grid = max(0., charge_limit - charged)
            charged += grid
            if charged > 0:
                soc[k] += charged * eta
            elif periods[j] != 'sop' and allowed[j]:
                served = min(imp[j], b.discharge_kw * INTERVAL_HOURS,
                             remaining_output[j], max(0., soc[k] - floor) * eta)
                soc[k] -= served / eta
            old_export[j] -= old
            new_export[j] -= new
            imp[j] += grid - served
            remaining_output[j] -= served
            log['new_solar_charge_kwh'][j] = new
            log['existing_solar_charge_kwh'][j] = old
            log['solar_charge_kwh'][j] = new + old
            log['grid_charge_kwh'][j] = grid
            log['served_kwh'][j] = served
            log['loss_kwh'][j] = charged * (1 - eta) + served * (1 / eta - 1)
            log['soc_kwh'][j] = soc[k]
        controllers.append(log)
    if export_treatment == 'zero_export':
        curtailed += new_export
        active_pv -= new_export
        new_export = np.zeros(n)
    exp = old_export + new_export
    credited = exp if export_treatment == 'credited' else old_export
    # Residual equation uses original gross channels; simultaneous import/export
    # cancel only in this energy check, NEVER before billing or dispatch.
    changes = np.zeros(n)
    losses = np.zeros(n)
    for k, log in enumerate(controllers):
        changes += np.diff(np.r_[start[k], log['soc_kwh']])
        losses += log['loss_kwh']
    error = imp + pv0 + exp0 - imp0 - exp - standby - curtailed - losses - changes
    maximum = float(np.max(np.abs(error)))
    if maximum > 1e-8 or np.any(imp < -1e-10) or np.any(exp < -1e-10):
        raise RuntimeError('dispatch energy conservation failed')
    return {'imports': imp, 'exports': exp, 'credited_exports': credited,
            'controllers': controllers, 'soc_start': start, 'soc_end': soc,
            'conservation_max_error_kwh': maximum,
            'flows': {'pv_available_kwh': pv0, 'pv_ac_kwh': active_pv,
                      'pv_direct_kwh': direct, 'new_export_kwh': new_export,
                      'existing_export_kwh': old_export, 'curtailed_kwh': curtailed,
                      'standby_kwh': standby, 'loss_kwh': losses}}


def steady_dispatch(d, imports, exports, pv, batteries, *, tolerance=SOC_TOLERANCE_KWH,
                    max_iterations=100, **kwargs):
    """Repeat the supplied interval year until every controller's SOC closes."""
    _finite(tolerance, 'tolerance')
    if tolerance == 0 or not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError('positive tolerance and iteration count needed')
    soc = kwargs.pop('initial_soc', [b.capacity_kwh * b.reserve for b in batteries])
    for iteration in range(1, max_iterations + 1):
        result = dispatch(d, imports, exports, pv, batteries, initial_soc=soc, **kwargs)
        residual = float(np.max(np.abs(result['soc_end'] - result['soc_start']), initial=0))
        if residual < tolerance:
            result['annual_soc_error_kwh'] = residual
            result['iterations'] = iteration
            return result
        soc = result['soc_end']
    raise RuntimeError(f'annual SOC did not converge within {max_iterations} passes; residual={residual}')


def _bill(d, imports, exports, *, physical_exports=None):
    _frame(d)
    f = d[['dt', 'seas', 'p', 'ym']].copy()
    f['I'] = _array(imports, len(d), 'bill imports')
    f['E'] = _array(exports, len(d), 'bill credited exports')
    f['physical_E'] = _array(exports if physical_exports is None else physical_exports, len(d), 'bill physical exports')
    bills = R.bill_nem_monthly(f, 'I', 'E', physical_exp='physical_E')
    monthly = {}
    totals = dict.fromkeys(('delivery_usd', 'generation_usd', 'pcia_usd', 'nbc_usd', 'fixed_usd'), 0.)
    for ym, group in f.groupby('ym'):
        parts = dict.fromkeys(totals, 0.)
        parts['fixed_usd'] = group.dt.dt.date.nunique() * R.BSC
        parts['nbc_usd'] = float(R.nbc_kwh(group.I, group.physical_E).sum()) * R.NBC
        for (season, period), sub in group.groupby(['seas', 'p']):
            net = float(sub.I.sum() - sub.E.sum())
            parts['delivery_usd'] += net * R.UDC[season][period]
            parts['generation_usd'] += net * R.CEA[season][period]
            parts['pcia_usd'] += max(net, 0.) * R.PCIA
        if abs(sum(parts.values()) - bills[str(ym)]) > 1e-7:
            raise RuntimeError('bill component decomposition differs from canonical engine')
        monthly[str(ym)] = dict(total_usd=float(bills[str(ym)]), components=parts,
                                imports_kwh=float(group.I.sum()), credited_exports_kwh=float(group.E.sum()),
                                physical_exports_kwh=float(group.physical_E.sum()))
        for key, value in parts.items():
            totals[key] += value
    return {'total_usd': float(sum(bills.values())), 'components': totals, 'monthly': monthly}


def bill_comparison(d, imports_before, exports_before, imports_after, credited_exports_after, *, physical_exports_after=None):
    """Rebill both complete flows at canonical June 2026 rates; no average-rate delta."""
    before = _bill(d, imports_before, exports_before)
    after = _bill(d, imports_after, credited_exports_after, physical_exports=physical_exports_after)
    return {'before': before, 'after': after, 'savings_usd': before['total_usd'] - after['total_usd'],
            'component_savings_usd': {k: before['components'][k] - after['components'][k] for k in before['components']}}



# All eight components are signed: import/fixed charges positive, export credits negative.
NBT_COMPONENTS = ('import_delivery_usd', 'import_generation_usd', 'import_pcia_usd',
                  'import_nbc_usd', 'fixed_usd', 'export_delivery_credit_usd',
                  'export_generation_credit_usd', 'export_cea_bonus_credit_usd')
NBT_ECONOMICS_REASON = 'settlement credit limits and relevant-period treatment not modeled'
NBT_FLOW_TOLERANCE_KWH = 1e-9


def load_nbt_context():
    """Load the shared public table once; derive components without another rate source."""
    full, delivery_only = NG.load_rate_table()
    bonus = NG.CEA_BONUS_USD_PER_KWH
    cases = {}
    for vintage in sorted(full):
        delivery = {k: v - bonus for k, v in delivery_only[vintage].items()}
        generation = {k: full[vintage][k] - delivery_only[vintage][k] for k in full[vintage]}
        for assumption, lookup, gen in (
                ('delivery_generation_cea_bonus', full[vintage], generation),
                ('delivery_cea_bonus_only', delivery_only[vintage], dict.fromkeys(generation, 0.))):
            cases[f'{vintage}_{assumption}'] = {
                'vintage': vintage, 'generation_credit_assumption': assumption,
                'total_lookup': lookup, 'delivery_lookup': delivery,
                'generation_lookup': gen, 'cea_bonus_usd_per_kwh': bonus}
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    return {'tariff_year': NG.TARIFF_YEAR, 'cases': cases,
            'rate_provenance': {'path': str(NG.RATE_CSV.relative_to(NG.ROOT)),
                                'sha256': digest(NG.RATE_CSV), 'owner': 'analysis/nem3_grandfathering.py --build-rates',
                                'vintages': sorted(full), 'components': ['delivery', 'generation'],
                                'cea_bonus_usd_per_kwh': bonus, 'inherited_cea_source': NG.CEA_SOURCE},
            'engine_provenance': {str(p.relative_to(NG.ROOT)): digest(p) for p in
                                  (NG.ROOT / 'analysis/nem3_grandfathering.py', NG.ROOT / 'analysis/rates.py')}}


def _nbt_pool_diagnostics(record):
    c = record['components']
    delivery = -c['export_delivery_credit_usd']
    generation = -c['export_generation_credit_usd']
    bonus = -c['export_cea_bonus_credit_usd']
    return {'delivery_import_charge_usd': c['import_delivery_usd'],
            'delivery_export_credit_usd': delivery,
            'delivery_credit_excess_usd': max(delivery-c['import_delivery_usd'], 0.),
            'generation_import_charge_usd': c['import_generation_usd'],
            'generation_export_credit_usd': generation, 'cea_bonus_credit_usd': bonus,
            'generation_credit_excess_usd': max(generation+bonus-c['import_generation_usd'], 0.),
            'import_pcia_usd': c['import_pcia_usd'], 'import_nbc_usd': c['import_nbc_usd'],
            'fixed_usd': c['fixed_usd'], 'total_signed_cost_usd': record['total_usd'],
            'imports_kwh': record['imports_kwh'], 'physical_exports_kwh': record['physical_exports_kwh'],
            'credited_exports_kwh': record['credited_exports_kwh'],
            'observed_net_export': record['physical_exports_kwh'] > record['imports_kwh']}


def _nbt_bill(d, imports, credited_exports, *, physical_exports, case):
    """Checked signed calendar-month ledger around NG.bill_nbt, never settlement.

    Accept credit <= physical export + 1e-9 kWh for floating-point residue.
    Never clip, net, resample, deduplicate or otherwise change physical arrays.
    """
    _frame(d)
    f = d[['dt', 'seas', 'p', 'ym']].copy()
    if not pd.api.types.is_datetime64_any_dtype(f.dt):
        raise ValueError('NBT timestamps must be datetime values')
    if any(str(ym) != str(dt.to_period('M')) for dt, ym in zip(f.dt, f.ym)):
        raise ValueError('NBT month bucket disagrees with observed timestamp')
    if any(seas != ('S' if dt.month in R.SUMMER_MONTHS else 'W') for dt, seas in zip(f.dt, f.seas)):
        raise ValueError('NBT season bucket disagrees with observed timestamp')
    if any(p != R.period_at(dt) for dt, p in zip(f.dt, f.p)):
        raise ValueError('NBT TOU bucket disagrees with observed timestamp')
    f['wkend'] = [R.off_peak_day(t.date()) for t in f.dt]
    f['imp'] = _array(imports, len(d), 'NBT imports')
    f['exp'] = _array(credited_exports, len(d), 'NBT credited exports')
    f['physical_exp'] = _array(physical_exports, len(d), 'NBT physical exports')
    if np.any(f.exp.to_numpy() - f.physical_exp.to_numpy() > NBT_FLOW_TOLERANCE_KWH):
        raise ValueError('NBT credited exports exceed physical exports (tolerance 1e-9 kWh)')
    keys = list(zip((NG.MONTHS[t.month-1] for t in f.dt),
                    ('Weekend' if w else 'Weekday' for w in f.wkend), f.dt.dt.hour))
    for name in ('total_lookup', 'delivery_lookup', 'generation_lookup'):
        if any(k not in case[name] for k in keys):
            raise ValueError(f'NBT {name}: missing touched lookup bucket')
        if any(not math.isfinite(case[name][k]) for k in keys):
            raise ValueError(f'NBT {name}: nonfinite touched rate')
    # Decomposition is a checked account of the existing engine, not another authority.
    f['delivery_charge'] = f.imp.to_numpy() * np.array([R.UDC[s][p] for s, p in zip(f.seas, f.p)])
    f['generation_charge'] = f.imp.to_numpy() * np.array([R.CEA[s][p] for s, p in zip(f.seas, f.p)])
    f['delivery_credit'] = f.exp.to_numpy() * np.array([case['delivery_lookup'][k] for k in keys])
    f['generation_credit'] = f.exp.to_numpy() * np.array([case['generation_lookup'][k] for k in keys])

    def record(group):
        total, credit, _ = NG.bill_nbt(group, case['total_lookup'])
        parts = dict(zip(NBT_COMPONENTS, (
            float(group.delivery_charge.sum()), float(group.generation_charge.sum()),
            float(group.imp.sum())*R.PCIA, float(group.imp.sum())*R.NBC,
            group.dt.dt.date.nunique()*R.BSC, -float(group.delivery_credit.sum()),
            -float(group.generation_credit.sum()), -float(group.exp.sum())*case['cea_bonus_usd_per_kwh'])))
        if abs(sum(parts.values())-total) > 1e-7 or abs(-sum(parts[k] for k in NBT_COMPONENTS[5:])-credit) > 1e-7:
            raise RuntimeError('NBT component decomposition disagrees with authoritative engine')
        kwh = float(group.exp.sum())
        return {'total_usd': total, 'components': parts, 'export_credit_total_usd': credit,
                'weighted_export_credit_usd_per_kwh': credit/kwh if kwh else None,
                'imports_kwh': float(group.imp.sum()), 'physical_exports_kwh': float(group.physical_exp.sum()),
                'credited_exports_kwh': kwh}

    annual = record(f)
    monthly = {str(ym): record(group) for ym, group in f.groupby('ym')}
    for key in ('total_usd', 'export_credit_total_usd'):
        if abs(sum(m[key] for m in monthly.values())-annual[key]) > 1e-7:
            raise RuntimeError(f'NBT monthly/annual disagreement: {key}')
    for key in NBT_COMPONENTS:
        if abs(sum(m['components'][key] for m in monthly.values())-annual['components'][key]) > 1e-7:
            raise RuntimeError(f'NBT monthly/annual component disagreement: {key}')
    annual.update({'monthly': monthly, 'settlement_diagnostics': {
        'annual': _nbt_pool_diagnostics(annual),
        'monthly': {ym: _nbt_pool_diagnostics(m) for ym, m in monthly.items()},
        'negative_months': [ym for ym, m in monthly.items() if m['total_usd'] < 0],
        'allocation_assumption': 'CEA bonus grouped with generation solely for diagnostics; PCIA outside delivery/generation pools pending eligibility research',
        'nsc_usd': None, 'nsc_reason': 'net-export kWh is a research trigger, not a net-surplus payment estimate'},
        'payable_bill_established': False, 'actual_tariff_accuracy_established': False,
        'settlement_credit_limits_applied': False, 'economics': None,
        'economics_unavailable_reason': NBT_ECONOMICS_REASON})
    return annual


def nbt_bill_comparison(d, imports_before, exports_before, imports_after, credited_exports_after,
                        *, physical_exports_after, case):
    """Reprice one matched behavior pair with fixed flows; never invoke dispatch."""
    before = _nbt_bill(d, imports_before, exports_before, physical_exports=exports_before, case=case)
    after = _nbt_bill(d, imports_after, credited_exports_after, physical_exports=physical_exports_after, case=case)
    return {'before': before, 'after': after, 'signed_ledger_delta_usd': before['total_usd']-after['total_usd'],
            'component_delta_usd': {k: before['components'][k]-after['components'][k] for k in NBT_COMPONENTS},
            'monthly_signed_ledger_delta_usd': {ym: before['monthly'][ym]['total_usd']-m['total_usd'] for ym, m in after['monthly'].items()}}


def _nbt_matched_row(d, row, trace, baselines, case, *, reference=False):
    baseline_id = 'unchanged_behavior' if row.get('behavior') == 'unchanged' else 'post_free_fix'
    before = baselines[baseline_id]
    after = _nbt_bill(d, trace['imports'], trace['credited_exports'], physical_exports=trace['exports'], case=case)
    delta = before['total_usd']-after['total_usd']
    result = {'baseline_id': baseline_id, 'source_scenario_id': row['id'],
              'export_treatment': row.get('export_treatment', 'credited'), 'signed_cost_ledger': after,
              'annual_hardware_signed_ledger_delta_usd': delta,
              'component_delta_usd': {k: before['components'][k]-after['components'][k] for k in NBT_COMPONENTS},
              'monthly_hardware_signed_ledger_delta_usd': {ym: before['monthly'][ym]['total_usd']-m['total_usd'] for ym, m in after['monthly'].items()},
              'nbt_ledger_delta_minus_nem_model_saving_usd': delta-row['annual_savings_usd'],
              'settlement_diagnostics': after['settlement_diagnostics'],
              'economics': None, 'economics_unavailable_reason': NBT_ECONOMICS_REASON}
    if reference:
        result.update({k: row[k] for k in ('capacity_kwh', 'discharge_kw', 'charge_kw', 'policy', 'reserve', 'rte', 'standby_w', 'scheduled_row')})
    return result


def _nbt_marginals(rows, marginals):
    result = {}
    for pair in marginals:
        before, after = (rows[pair[k]] for k in ('before', 'after'))
        if before['baseline_id'] != after['baseline_id']:
            raise ValueError('NBT marginal needs the same behavior baseline')
        identifier = f"{pair['before']}__to__{pair['after']}"
        if identifier in result:
            raise ValueError('duplicate NBT marginal pair')
        result[identifier] = {'before_id': pair['before'], 'after_id': pair['after'],
                              'baseline_id': before['baseline_id'], 'component': pair['component'],
                              'incremental_signed_ledger_delta_usd': before['signed_cost_ledger']['total_usd']-after['signed_cost_ledger']['total_usd'],
                              'economics': None, 'economics_unavailable_reason': NBT_ECONOMICS_REASON}
    return result


def _nbt_sensitivity(d, original, post, exports, scenarios, traces, references, reference_traces, marginals, context):
    """Price saved traces only. Tariff cases never call a physical dispatch function."""
    cases = {}
    for identifier, case in context['cases'].items():
        baselines = {name: _nbt_bill(d, imp, exports, physical_exports=exports, case=case) for name, imp in
                     (('unchanged_behavior', original), ('post_free_fix', post))}
        rows = {row['id']: _nbt_matched_row(d, row, traces[row['id']], baselines, case) for row in scenarios}
        refs = {row['id']: _nbt_matched_row(d, row, reference_traces[row['id']], baselines, case, reference=True) for row in references}
        cases[identifier] = {'vintage': case['vintage'], 'generation_credit_assumption': case['generation_credit_assumption'],
                             'baselines': baselines,
                             'free_behavior_signed_ledger_delta_usd': baselines['unchanged_behavior']['total_usd']-baselines['post_free_fix']['total_usd'],
                             'scenarios': rows, 'canonical_references': refs, 'marginals': _nbt_marginals(rows, marginals)}
    ranges = {}
    for row in scenarios:
        values = {key: c['scenarios'][row['id']]['annual_hardware_signed_ledger_delta_usd'] for key, c in cases.items()}
        lo, hi = min(values.values()), max(values.values())
        ranges[row['id']] = {'minimum_signed_ledger_delta_usd': lo, 'maximum_signed_ledger_delta_usd': hi,
                             'minimum_cases': [k for k, v in values.items() if v == lo], 'maximum_cases': [k for k, v in values.items() if v == hi]}
    return {'status': 'modeled fixed-flow signed-cost-ledger sensitivity',
            'observation_window': {'start': str(d.dt.min().date()), 'end': str(d.dt.max().date()),
                                   'days': d.dt.dt.date.nunique(), 'interval_count': len(d)},
            'tariff_year': context['tariff_year'], 'import_rate_basis': 'frozen June 2026 EV-TOU-5 / CEA proxy',
            'dispatch_basis': 'existing scenario traces; no NBT reoptimization',
            'future_transition_projection': False, 'actual_tariff_accuracy_established': False,
            'payable_bill_established': False, 'settlement_credit_limits_applied': False,
            'rate_provenance': context['rate_provenance'], 'engine_provenance': context['engine_provenance'],
            'assumptions': {
                'import_basis': 'gross imports at canonical frozen R.UDC + R.CEA + R.PCIA + R.NBC; distinct service dates times R.BSC',
                'nbc_basis': 'gross imports in existing NBT proxy; separate from NEM-ST positive physical interval-net NBC',
                'export_eligibility': 'whole-account hypothetical credit channels: all physical exports for credited; remaining old-export channel for zero_credit/zero_export; no SB 868 compensation determination',
                'calendar_mapping': 'observed local timestamps unchanged; saved 2026 prices by month, rates.off_peak_day holiday/weekend and hour; quarter-hour kWh sum; repeated fall-DST rows retained; missing spring hour not invented',
                'credit_settlement': 'unrestricted signed subtraction only; separate credit pools, banks, relevant periods, floors and NSC not applied; calendar months are not established billing cycles',
                'credit_retention': 'SDG&E eligible delivery credits carry forward during NBT service; CEA terms describe annual remaining generation-credit forfeiture and separate net-surplus settlement; provider rules are not interchangeable',
                'diagnostic_allocation': 'CEA bonus grouped with generation only for diagnostics; PCIA outside delivery/generation pools pending exact eligibility research',
                'flow_tolerance_kwh': NBT_FLOW_TOLERANCE_KWH},
            'cases': cases, 'scenario_ranges': ranges,
            'limitations': ['Dated 2026 snapshot applied to observed dates, not a future transition projection.',
                            'Modeled signed calendar-month cost ledgers; proxy engine does not establish payable bills or actual tariff accuracy.',
                            'Generation-credit interpretation sensitivity is not a probability or rigorous bound on settlement savings.',
                            'No NBT dispatch optimization, future tariff/equipment-price claim, purchase date, installed-price ceiling, NPV or payback.'],
            'economics': None, 'economics_unavailable_reason': NBT_ECONOMICS_REASON}


def economics(annual_saving, *, installed_cost=None, discount=.05, degradation=.01,
              availability=1., spread_factor=1., compliance_cost=0., compliance_year=4):
    """NPV and installed-price ceiling, zero real escalation; unknown cost stays null.

    Annual savings decline 1% by default: this is a financial derating assumption,
    not a physical year-by-year degradation simulation. availability/spread_factor
    are transparent cash-flow stress factors; physical availability is a separate
    dispatch row. A real future tariff needs a new billing engine/rate snapshot.
    """
    if not isinstance(annual_saving, (int, float, np.number)) or not math.isfinite(annual_saving):
        raise ValueError('annual saving must be finite')
    for value, name in ((discount, 'discount'), (degradation, 'degradation'),
                        (availability, 'availability'), (spread_factor, 'spread_factor'), (compliance_cost, 'compliance_cost')):
        _finite(value, name)
    if degradation >= 1 or availability > 1 or not isinstance(compliance_year, int) or compliance_year < 1:
        raise ValueError('invalid economics fraction or compliance year')
    if installed_cost is not None:
        _finite(installed_cost, 'installed_cost')
    benefit = annual_saving * availability * spread_factor
    out = {'installed_cost_usd': installed_cost, 'simple_payback_years': installed_cost / benefit if installed_cost is not None and benefit > 0 else None,
           'federal_25d_credit_usd': 0, 'backup_value_usd': None, 'vpp_value_usd': None,
           'assumptions': {'real_discount': discount, 'annual_savings_decline': degradation,
                           'real_tariff_escalation': 0, 'availability_cashflow_factor': availability,
                           'spread_cashflow_factor': spread_factor, 'compliance_cost_usd': compliance_cost,
                           'compliance_year': compliance_year}}
    for years, key in ((5, 'five_year'), (10, 'ten_year')):
        pv = sum(benefit * (1 - degradation)**(y-1) / (1 + discount)**y for y in range(1, years+1))
        if compliance_year <= years:
            pv -= compliance_cost / (1 + discount)**compliance_year
        out[key] = {'break_even_installed_usd': pv, 'npv_usd': None if installed_cost is None else pv - installed_cost}
    return out


def load_inputs(directory):
    """Read staged private inputs and reconcile the derived hourly production."""
    directory = pathlib.Path(directory)
    paths = {name: directory / name for name in ('usage.csv', 'samA.csv', 'samB.csv')}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f'missing staged study input: {path.name}')
    if directory.resolve() != pathlib.Path.cwd().resolve():
        raise ValueError('run from the staged private input directory')
    import behavior_rebuild as br
    import battery_dispatch_policies as bd
    import marginal_capacity_value as mv
    import threeway_production_validation as tpv
    br.CSV = str(paths['usage.csv'])
    d = br.load()
    hsums = mv.hourly_meter(d)
    dates = sorted(set(d.dt.dt.date))
    excluded = tpv.dst_dates_in(dates)
    production, clipped = mv.derive_production(hsums, tpv.load_sam_hourly(), excluded)
    if not production or sum(production.values()) <= 0:
        raise ValueError('production evidence is empty')
    kw_dc, kw_ac = mv.array_nameplate()
    if max(production.values()) > kw_ac + 1e-9:
        raise ValueError('derived hourly production exceeds existing AC nameplate')
    tieout = mv.tie_out_threeway(production, dates, excluded)
    recon = mv.reconcile_with_meter_record(production, dates, excluded)
    per_kw, _, _ = mv.increment_per_interval(d, production, hsums, kw_dc, 1.)
    imp = d.Consumption.to_numpy()
    exp = d.Generation.to_numpy()
    post, moved, scenario = bd.free_fix_shift(d, imp)
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    evidence_hashes = {str(path.relative_to(mv.ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (mv.ROOT / 'analysis' / 'rates.py', mv.THREEWAY_CSV, mv.ENPHASE_CSV,
                                    mv.DATA / 'battery_sizing_curve.json')}
    public_input_parameters = {
        'solar.kw_dc': kw_dc, 'solar.kw_ac': kw_ac,
        'solar.module_count': mv.HH.get('solar.module_count'),
        'solar.inverter_model': mv.HH.get('solar.inverter_model'),
        'solar.inverter_count': mv.HH.get('solar.inverter_count'),
        'charger.kw': mv.HH.get('charger.kw')}
    public_parameter_hash = hashlib.sha256(json.dumps(public_input_parameters, sort_keys=True).encode()).hexdigest()
    return {'frame': d, 'imports': imp, 'exports': exp, 'post_imports': post,
            'pv_per_kw': np.asarray(per_kw), 'moved_kwh': moved, 'free_fix_scenario': scenario,
            'provenance': {'input_sha256': hashes, 'public_evidence_sha256': evidence_hashes,
                           'public_equipment_parameters': public_input_parameters,
                           'public_equipment_parameters_sha256': public_parameter_hash, 'window_start': str(d.dt.min().date()),
                           'window_end': str(d.dt.max().date()), 'interval_count': len(d),
                           'existing_pv_kw_dc': kw_dc, 'existing_pv_kw_ac': kw_ac,
                           'dst_pv_excluded_dates': [str(day) for day in sorted(excluded)],
                           'clipped_negative_production_kwh': clipped,
                           'production_threeway_check': tieout, 'production_meter_reconciliation': recon}}


def pv_profile(d, per_kw, dc_kw, shape='production_proxy'):
    """Estimated production shape; hypothetical delay drops energy after midnight.

    The source profile is the existing array's derived production, not measured
    south-facing portable PV. A two-hour delay is NOT a west-facing irradiance
    simulation. Shade halves energy; winter reduction multiplies Nov-May by .8.
    All shapes retain the source's zero-PV DST exclusions.
    """
    values = _array(per_kw, len(d), 'production profile') * _finite(dc_kw, 'dc_kw')
    if shape == 'production_proxy':
        return values
    if shape == 'shade_50pct':
        return values * .5
    if shape == 'winter_80pct':
        return values * np.where(d.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)), 1., .8)
    if shape == 'delayed_2h':
        mapping = {t: float(v) for t, v in zip(d.dt, values)}
        delayed = []
        for t in d.dt:
            previous = t - pd.Timedelta(hours=2)
            delayed.append(mapping.get(previous, 0.) if previous.date() == t.date() else 0.)
        return np.asarray(delayed)
    raise ValueError('unknown hypothetical production shape')


def tariff_spread_comparison(d, imports_before, exports_before, imports_after,
                             credited_exports_after, *, factor, physical_exports_after=None):
    """Rebill BOTH flows with a canonical-rate-derived TOU spread stress.

    SOP delivery/generation stay fixed. Each other period's delivery/generation
    spread above its same-season SOP rate scales by factor. NBC/PCIA/BSC stay
    fixed. This is a hypothetical rate table, not a forecast. Canonical globals
    are restored in finally; callers must not invoke this concurrently with billing.
    """
    factor = _finite(factor, 'tariff spread factor')
    old_udc, old_cea = R.UDC, R.CEA
    try:
        def scaled(table):
            return {s: {p: row['sop'] + factor * (rate - row['sop']) for p, rate in row.items()} for s, row in table.items()}
        R.UDC, R.CEA = scaled(old_udc), scaled(old_cea)
        result = bill_comparison(d, imports_before, exports_before, imports_after, credited_exports_after, physical_exports_after=physical_exports_after)
        result['hypothetical_rate_table'] = {'delivery': R.UDC, 'generation': R.CEA,
                                              'nbc': R.NBC, 'pcia': R.PCIA, 'daily_fixed': R.BSC,
                                              'spread_factor': factor}
        return result
    finally:
        R.UDC, R.CEA = old_udc, old_cea


def backup_runtime(battery, loads_w=(100, 300, 500, 1000)):
    """Illustrative constant AC loads; no circuit/surge or outage-recharge claim.

    From full respects the configured reserve. From preserved reserve assumes an
    outage overrides that floor and exhausts the reserve to zero. These cases are
    alternatives. Actual outage start SOC, control support and circuit loads unknown.
    """
    rows = []
    for load in loads_w:
        _finite(load, 'backup load')
        demand = (load + battery.standby_w) / 1000
        allowed = demand > 0 and demand <= battery.discharge_kw
        eta = math.sqrt(battery.rte)
        rows.append({'constant_load_w': load, 'standby_w': battery.standby_w,
                     'runtime_from_full_hours': battery.capacity_kwh * (1-battery.reserve) * eta / demand if allowed else None,
                     'runtime_from_preserved_reserve_hours': battery.capacity_kwh * battery.reserve * eta / demand if allowed else None,
                     'reason': None if allowed else ('constant load exceeds modeled AC output' if demand > battery.discharge_kw else 'zero total load has no finite runtime')})
    return rows


def scenario_summary(d, baseline_imports, baseline_exports, result, batteries):
    """Aggregate dispatch accounts and complete monthly bills; publish no raw traces."""
    comparison = bill_comparison(d, baseline_imports, baseline_exports, result['imports'], result['credited_exports'], physical_exports_after=result['exports'])
    days = d.dt.dt.date.nunique()
    accounts = []
    for b, log, start, end in zip(batteries, result['controllers'], result['soc_start'], result['soc_end']):
        aggregate = {key: float(values.sum()) for key, values in log.items() if key != 'soc_kwh'}
        aggregate.update({'soc_start_kwh': float(start), 'soc_end_kwh': float(end),
                          'soc_min_kwh': float(min(start, log['soc_kwh'].min())),
                          'soc_max_kwh': float(max(start, log['soc_kwh'].max())),
                          'dc_discharge_throughput_kwh': aggregate['served_kwh'] / math.sqrt(b.rte),
                          'dc_charge_throughput_kwh': (aggregate['solar_charge_kwh'] + aggregate['grid_charge_kwh']) * math.sqrt(b.rte)})
        aggregate['equivalent_full_cycles'] = aggregate['dc_discharge_throughput_kwh'] / b.capacity_kwh if b.capacity_kwh else 0.
        aggregate['cycles_per_day'] = aggregate['equivalent_full_cycles'] / days
        accounts.append(aggregate)
    total = {key: sum(account[key] for account in accounts) for key in ('solar_charge_kwh', 'existing_solar_charge_kwh', 'new_solar_charge_kwh', 'grid_charge_kwh', 'served_kwh', 'loss_kwh', 'dc_charge_throughput_kwh', 'dc_discharge_throughput_kwh')}
    on = d.p.to_numpy() == 'on'
    physical = {key: float(values.sum()) for key, values in result['flows'].items()}
    physical.update({'imports_kwh': float(result['imports'].sum()), 'exports_kwh': float(result['exports'].sum()),
                     'credited_exports_kwh': float(result['credited_exports'].sum()),
                     'peak_import_kw': float(result['imports'].max() / INTERVAL_HOURS),
                     'baseline_peak_import_kw': float(np.max(baseline_imports) / INTERVAL_HOURS),
                     'peak_import_reduction_kw': float((np.max(baseline_imports) - result['imports'].max()) / INTERVAL_HOURS),
                     'onpeak_served_kwh': sum(float(log['served_kwh'][on].sum()) for log in result['controllers'])})
    monthly = comparison['after']['monthly']
    for month, mask in ((str(ym), (d.ym == ym).to_numpy()) for ym in d.ym.unique()):
        monthly[month]['physical_exports_kwh'] = float(result['exports'][mask].sum())
        monthly[month]['physical'] = {key: float(values[mask].sum()) for key, values in result['flows'].items()}
        monthly[month]['physical']['peak_import_kw'] = float(result['imports'][mask].max() / INTERVAL_HOURS)
        monthly[month]['battery'] = {key: sum(float(log[key][mask].sum()) for log in result['controllers'])
                                      for key in ('solar_charge_kwh', 'existing_solar_charge_kwh', 'new_solar_charge_kwh', 'grid_charge_kwh', 'served_kwh', 'loss_kwh')}
        monthly[month]['battery']['equivalent_full_cycles_by_controller'] = [float(log['served_kwh'][mask].sum()) / math.sqrt(b.rte) / b.capacity_kwh if b.capacity_kwh else 0.
                                                                           for b, log in zip(batteries, result['controllers'])]
    return {'annual_savings_usd': comparison['savings_usd'], 'bill': comparison['after'],
            'component_savings_usd': comparison['component_savings_usd'], 'physical': physical,
            'battery': total, 'controllers': accounts, 'annual_soc_error_kwh': result['annual_soc_error_kwh'],
            'conservation_max_error_kwh': result['conservation_max_error_kwh'], 'soc_iterations': result['iterations'],
            'economics': economics(comparison['savings_usd']),
            'conditional_backup_runtime': [backup_runtime(b) for b in batteries]}


def build_study(inputs, *, include_references=True, nbt_context=None):
    """Generate targeted one-variable sweeps; only aggregate financial/energy outputs."""
    from dataclasses import replace
    d, original, post, exp = (inputs[k] for k in ('frame', 'imports', 'post_imports', 'exports'))
    zeros = np.zeros(len(d))
    baseline = _bill(d, post, exp)
    unchanged = _bill(d, original, exp)
    small = Battery(3.84, 1.2, 1.2)
    pw = Battery(13.5, 11.5, 5)
    scenarios, traces = [], {}

    def add(identifier, family, batteries=(), dc_kw=0., ac_kw=None, shape='production_proxy',
            treatment='credited', behavior='post_free_fix', availability=None, note=None, discharge_allowed=None):
        imports = original if behavior == 'unchanged' else post
        pv = pv_profile(d, inputs['pv_per_kw'], dc_kw, shape)
        r = steady_dispatch(d, imports, exp, pv, batteries, shared_ac_kw=ac_kw,
                            export_treatment=treatment, availability=availability, discharge_allowed=discharge_allowed)
        row = {'id': identifier, 'family': family, 'evidence': 'modeled hypothetical configuration',
               'behavior': behavior, 'batteries': [asdict(b) for b in batteries],
               'pv_dc_kw': dc_kw, 'shared_new_pv_battery_ac_kw': ac_kw,
               'production_shape': shape, 'export_treatment': treatment,
               'note': note, 'availability_fraction': float(np.mean(availability)) if availability is not None else 1.}
        row.update(scenario_summary(d, imports, exp, r, batteries))
        scenarios.append(row)
        traces[identifier] = r
        return row

    for ac in (.4, .8, 1.2):
        for treatment in ('credited', 'zero_credit', 'zero_export'):
            add(f'pv_{ac:g}kw_{treatment}', 'solar_only', dc_kw=ac*1.25, ac_kw=ac, treatment=treatment)
            add(f'combined_{ac:g}kw_{treatment}', 'pv_plus_storage', [small], dc_kw=ac*1.25, ac_kw=ac, treatment=treatment,
                note='3.84 kWh capacity fixed; PV and battery output share this one AC cap')
    for shape in ('delayed_2h', 'shade_50pct', 'winter_80pct'):
        add(f'pv_{shape}', 'solar_shape_sensitivity', dc_kw=1., ac_kw=.8, shape=shape)
        add(f'combined_{shape}', 'combined_shape_sensitivity', [small], dc_kw=1., ac_kw=1.2, shape=shape)
    for power, identifier in ((.36, 'stream_single_default_360w'), (.8, 'stream_single_conditional_800w')):
        add(identifier, 'manual_output_limit_sensitivity', [Battery(1.92, power, 1.05)], ac_kw=power,
            note='US STREAM EF-EA-HD-U2K-1K2U manual: 360W default; 800W needs supported circuit conditions. Eligibility, actual load access and control schedule remain unproved; reserve/RTE/standby assumed.')
    for capacity in (1.92, 3.84, 5.76, 7.68, 11.52):
        add(f'battery_{capacity:g}kwh', 'battery_capacity_sweep', [replace(small, capacity_kwh=capacity)], ac_kw=1.2,
            note='Power fixed at 1.2 kW; generic controlled system, not single-unit product capability')
    for power in (.8, 1., 1.2):
        add(f'battery_power_{power:g}kw', 'battery_power_sweep', [replace(small, discharge_kw=power)], ac_kw=power,
            note='Capacity 3.84 kWh and charge power 1.2 kW fixed')
    for reserve in (.1, .2, .3):
        add(f'battery_reserve_{reserve:g}', 'reserve_sweep', [replace(small, reserve=reserve)], ac_kw=1.2)
    for rte in (.8, .85, .9):
        add(f'battery_rte_{rte:g}', 'efficiency_sweep', [replace(small, rte=rte)], ac_kw=1.2)
    for standby in (0, 10, 30):
        add(f'battery_standby_{standby}w', 'standby_sweep', [replace(small, standby_w=standby)], ac_kw=1.2,
            note='Assumed AC standby, not measured product consumption')
    for source in ('solar', 'grid', 'mixed'):
        add(f'battery_charging_{source}', 'charge_source_sweep', [replace(small, charging=source)], ac_kw=1.2)
        add(f'combined_charging_{source}', 'combined_charge_source_sweep', [replace(small, charging=source)], dc_kw=1., ac_kw=1.2)
    for retention in (.8, .9):
        add(f'battery_retention_{retention:g}', 'physical_capacity_degradation', [replace(small, capacity_kwh=small.capacity_kwh*retention)], ac_kw=1.2)
    mask = (d.dt.dt.day.to_numpy() > 3).astype(float)
    add('battery_first3days_month_offline', 'physical_availability', [small], ac_kw=1.2, availability=mask,
        note='Hypothetical outage/control downtime: battery and its standby off during first three dates each calendar month; PV unaffected')
    large_configs = [(4.096, 4, 1.8), (6.144, 7.2, 1.8), (12.288, 7.2, 1.8),
                     (12.288, 12, 1.8), (13.5, 11.5, 5), (27, 11.5, 8)]
    for capacity, power, charge in large_configs:
        add(f'large_{capacity:g}kwh_{power:g}kw', 'prospective_home_storage', [Battery(capacity, power, charge)],
            note='Generic storage parameters; charge/connection assumptions, not equipment eligibility or manufacturer performance')
    add('powerwall_then_small', 'two_sequential_controllers', [pw, small],
        note='Hypothetical first home battery dispatches first; each retains own output cap; no shared plug-in cap assumed across separate systems')
    add('small_then_powerwall', 'two_sequential_controllers', [small, pw],
        note='Hypothetical small battery dispatches first; ordering sensitivity, no independent savings addition')
    add('unchanged_small', 'unchanged_behavior', [small], ac_kw=1.2, behavior='unchanged')
    add('unchanged_pv', 'unchanged_behavior', dc_kw=1., ac_kw=.8, behavior='unchanged')
    add('unchanged_combined', 'unchanged_behavior', [small], dc_kw=1., ac_kw=1.2, behavior='unchanged')
    add('combined_commoncap_0.5kwdc', 'panel_increment', [small], dc_kw=.5, ac_kw=1.2)
    add('combined_commoncap_1kwdc', 'panel_increment', [small], dc_kw=1., ac_kw=1.2)
    add('combined_commoncap_1.5kwdc', 'panel_increment', [small], dc_kw=1.5, ac_kw=1.2)

    rows = {row['id']: row for row in scenarios}
    marginals = []
    def marginal(before, after, what, assumed_extra_cost=None):
        save = rows[after]['annual_savings_usd'] - rows[before]['annual_savings_usd']
        marginals.append({'before': before, 'after': after, 'component': what,
                          'incremental_savings_usd': save, 'economics': economics(save),
                          'assumed_extra_equipment_only_cost_usd': assumed_extra_cost,
                          'equipment_only_simple_marginal_payback_years': assumed_extra_cost/save if assumed_extra_cost is not None and save > 0 else None})
    capacities = (1.92, 3.84, 5.76, 7.68, 11.52)
    for a, b in zip(capacities, capacities[1:]):
        marginal(f'battery_{a:g}kwh', f'battery_{b:g}kwh', 'added storage at fixed power', (b-a)/1.92*1399)
    for before, after in [('battery_3.84kwh', 'combined_commoncap_0.5kwdc'),
                           ('combined_commoncap_0.5kwdc', 'combined_commoncap_1kwdc'),
                           ('combined_commoncap_1kwdc', 'combined_commoncap_1.5kwdc')]:
        marginal(before, after, 'added 0.5 kW DC panels at fixed 1.2 kW shared AC cap')
    for order in ('powerwall_then_small', 'small_then_powerwall'):
        marginal('large_13.5kwh_11.5kw', order, 'additional small battery after hypothetical home battery, specified dispatch order')

    reference_rows, nbt_references, reference_traces = [], [], {}
    if include_references:
        import battery_sizing_curve as bs
        import battery_dispatch_policies as bd
        import marginal_capacity_value as mv
        for capacity, power, charge in [(1.92, .8, 1.05), (3.84, 1.2, 1.2), (5.76, 1.2, 1.2),
                                        (7.68, 1.2, 1.2), (11.52, 1.2, 1.2)] + large_configs[:3] + [(13.5, 11.5, 5)]:
            i, e, served, thru, start, end, iterations = bs._steady_state_run(d, post, exp, capacity, power, charge_kw=charge)
            bs._check_conservation(d, post, exp, i, e, served, thru, capacity)
            comparison = bill_comparison(d, post, exp, i, e)
            same = Battery(capacity, power, charge, reserve=0, rte=.9, standby_w=0)
            scheduled = add(f'scheduled_reference_{capacity:g}kwh', 'schedule_vs_published_policy', [same],
                            discharge_allowed=bd.value_charge_gate(d, post, exp)['disch_win'],
                            note='Same capacity, charge/discharge limits, RTE, reserve, standby, baseline and eligible discharge intervals as canonical policy; charge decisions and simultaneous-flow priority differ')
            solar = float(np.maximum(exp-e, 0).sum())
            grid = float(np.maximum(i-post, 0).sum())
            reference_rows.append({'capacity_kwh': capacity, 'discharge_kw': power, 'charge_kw': charge,
                                   'policy': bd.PUBLISHED_POLICY, 'reserve': 0, 'rte': .9, 'standby_w': 0,
                                   'annual_savings_usd': comparison['savings_usd'], 'bill': comparison['after'],
                                   'component_savings_usd': comparison['component_savings_usd'],
                                   'imports_kwh': float(i.sum()), 'exports_kwh': float(e.sum()),
                                   'peak_import_kw': float(i.max() / INTERVAL_HOURS),
                                   'served_kwh': served, 'grid_charge_kwh': grid, 'existing_solar_charge_kwh': solar,
                                   'conversion_loss_kwh': grid+solar-served-(end-start),
                                   'equivalent_full_cycles_per_day': thru/capacity/d.dt.dt.date.nunique(),
                                   'annual_soc_error_kwh': abs(end-start), 'soc_start_kwh': start, 'soc_end_kwh': end,
                                   'soc_iterations': iterations, 'scheduled_row': scheduled['id'],
                                   'scheduled_minus_canonical_savings_usd': scheduled['annual_savings_usd'] - comparison['savings_usd'],
                                   'economics': economics(comparison['savings_usd'])})
            reference_id = f'canonical_{capacity:g}kwh_{power:g}kw_{charge:g}kw_{bd.PUBLISHED_POLICY}'
            nbt_references.append(dict(reference_rows[-1], id=reference_id))
            reference_traces[reference_id] = {'imports': i, 'exports': e, 'credited_exports': e}
        artifact = json.loads((mv.DATA / 'battery_sizing_curve.json').read_text())
        expected = artifact['post_behavior']['shipping_products_on_curve'][0]['save_usd']
        difference = reference_rows[-1]['annual_savings_usd'] - expected
        if abs(difference) > .02:
            raise RuntimeError(f'published 13.5kWh reference differs by ${difference:.4f}')
        baseline['canonical_reference_difference_usd'] = difference

    for treatment in ('credited', 'zero_credit', 'zero_export'):
        for a, b in ((.4, .8), (.8, 1.2)):
            marginal(f'pv_{a:g}kw_{treatment}', f'pv_{b:g}kw_{treatment}', 'added 0.5kW DC panels and 0.4kW AC output capacity')
    marginal('large_6.144kwh_7.2kw', 'large_12.288kwh_7.2kw', 'additional 6.144kWh at fixed 7.2kW discharge / 1.8kW charge')
    marginal('large_13.5kwh_11.5kw', 'large_27kwh_11.5kw', 'additional 13.5kWh; charge power changes 5kW to assumed 8kW')
    marginal('stream_single_default_360w', 'stream_single_conditional_800w', 'conditional output change at fixed single-unit capacity and charge power')
    target = rows['battery_3.84kwh']
    target_trace = traces['battery_3.84kwh']
    financial = {'target_scenario': target['id'],
                 'cost_sensitivities': [economics(target['annual_savings_usd'], installed_cost=cost) for cost in (1000, 2500, 5000, 7500)],
                 'cost_label': 'Assumed complete installed costs; not quotes. Cost overrun is represented by higher ladder rungs.',
                 'availability_cashflow_stress': [economics(target['annual_savings_usd'], availability=a) for a in (.8, .9)],
                 'availability_note': 'Cashflow-only derating; physical downtime has a separate re-dispatched scenario.',
                 'continued_operation_after_2030': economics(target['annual_savings_usd']),
                 '2030_compliance_cost_sensitivities': [economics(target['annual_savings_usd'], compliance_cost=cost, compliance_year=4) for cost in (500, 1500)],
                 '2030_note': 'Hypothetical 2027 commissioning, year 4 cost in 2030; operation continues. Neither automatic shutdown nor permanent exemption assumed.',
                 'tariff_spread_rebills': []}
    for factor in (.5, .75, 1.25):
        comp = tariff_spread_comparison(d, post, exp, target_trace['imports'], target_trace['credited_exports'], factor=factor, physical_exports_after=target_trace['exports'])
        financial['tariff_spread_rebills'].append({'spread_factor': factor, 'annual_savings_usd': comp['savings_usd'],
                                                  'bill_before': comp['before'], 'bill_after': comp['after'],
                                                  'hypothetical_rate_table': comp['hypothetical_rate_table'],
                                                  'economics': economics(comp['savings_usd']),
                                                  'dispatch': 'fixed simple schedule; same physical flows, both baseline and hardware re-billed'})
    nbt = _nbt_sensitivity(d, original, post, exp, scenarios, traces, nbt_references, reference_traces,
                           marginals, load_nbt_context() if nbt_context is None else nbt_context)
    return {'schema_version': 2, 'present_2026_nbt_sensitivity': nbt, 'issue': 304, 'study_date': '2026-10-22',
            'status': 'exploratory; no purchase recommendation or authorization for household wiring',
            'provenance': dict(inputs['provenance'], rate_snapshot='June 2026 EV-TOU-5 / CEA Clean Impact Plus; canonical analysis/rates.py',
                               baseline_repository_commit='05f4c8c', confidence='modeled deltas; estimated portable PV shape'),
            'installed_baseline': {'battery_count': 0, 'battery_ownership_evidence': 'owner confirmation 2026-10-22',
                                   'existing_rooftop': '30 Panasonic 335W modules, 30 Enphase IQ7X (IQ7X-96-2-US), 10.05 kW DC / 9.45 kW AC',
                                   'ev_charger': 'Tesla Wall Connector; project intake charger.kw = 11.5',
                                   'powerwall': 'hypothetical only; no installed home battery'},
            'baseline': {'unchanged_behavior': unchanged, 'post_behavior': baseline,
                         'free_behavior_saving_usd': unchanged['total_usd'] - baseline['total_usd'],
                         'shifted_kwh': inputs['moved_kwh'], 'free_fix_scenario': inputs['free_fix_scenario'],
                         'hardware_savings_exclude_free_fix': True},
            'assumptions': {
                'policy': 'simple schedule: solar surplus charge any period, grid charge only SOP; discharge only off/on-peak; no simultaneous charge/discharge per controller',
                'access': 'total-meter load access assumed; no real circuit eligibility established; all installed costs unknown',
                'pv': 'separate hypothetical DC size = 1.25 times AC cap; AC-coupled PV; no DC recovery above AC cap; hourly proxy spread evenly across meter rows',
                'shape': 'production_proxy follows existing rooftop, not measured south portable placement; delayed_2h is hypothetical west/delayed proxy, not irradiance model; shade_50pct and winter_80pct are assumed energy reductions',
                'interval': '15-minute gross import/export can reflect changes inside the interval. Offset-first allocation is a modeled assumption, not proof of simultaneous instantaneous flows.',
                'export_credit': 'credited grants canonical NEM to incremental meter exports. zero_credit removes credit for ALL incremental meter exports, including displaced existing rooftop exports. Existing baseline rooftop exports retain canonical credits unless a battery consumes them. Electron-origin and legal eligibility unknown.',
                'zero_export': 'perfect control curtails only new residual PV; existing rooftop exports remain. This is incremental zero-export, not whole-site zero-export; device/control feasibility unknown.',
                'controllers': 'supplied sequence shares remaining imports and both solar source accounts; no intentional battery export; new surplus charges first; both dispatch orders reported',
                'shared_ac': 'one stated aggregate AC cap includes all new PV AC plus battery discharge; larger separately connected home-storage scenarios use their individual power limits',
                'base_battery': '20% reserve, 85% RTE split symmetrically, 10W continuous AC standby; assumptions, not measured product behavior',
                'annual_soc': {'tolerance_kwh': SOC_TOLERANCE_KWH, 'maximum_passes': 100, 'nonconvergence': 'fail closed'},
                'economics': '5% real discount, 1% annual savings decline, zero real tariff escalation; no replacement/subscription/maintenance cost assumed because quote unknown; ceilings before those costs',
                'equipment_price': '$1399 per 1.92kWh unit from supplied issue snapshot, observation date 2026-10-22; equipment-only marginal illustration, not installed price or available-stock claim',
                'backup': 'constant-load conditional runtime only; known product/circuit surge limits absent; no existing Enphase outage production or solar recharge assumed',
                'bill_limitations': 'calendar-month model differs from statement windows; frozen rate vintage differs from historical bills; documented historical residual remains unexplained. NBC sums positive physical import minus physical export within each fifteen-minute interval. Actual bills support this basis; exact utility interval mapping, precision, and grouped rounding remain unresolved. Uncredited exports still enter physical NBC netting as a modeling assumption; compensation and interconnection eligibility remain separate unresolved questions. Absolute modeled bills are not actual bills.',
                'fixed_costs': 'electric daily charge unchanged; gas unchanged and excluded from electric model',
                'incentives': 'zero homeowner federal25D credit; other incentive/VPP eligibility and monetary backup value unknown'},
            'canonical_no_reserve_benchmarks': reference_rows, 'scenarios': scenarios,
            'marginal_comparisons': marginals, 'financial_sensitivities': financial,
            'unavailable_scenarios': {
                'selected_loads': {'annual_savings_usd': None, 'reason': 'No real selected-circuit/appliance interval energy, surge or supported wiring evidence. Illustrative runtime is not household selected-load savings.'},
                'daylight_ev_or_flexible_load': {'annual_savings_usd': None, 'reason': 'Daylight vehicle presence and appliance flexibility unmeasured; existing free overnight EV shift already in baseline.'},
                'post_nem_adoption': {'annual_savings_usd': None, 'reason': 'Future post-grandfathering tariff, equipment price and supported configuration remain unknown. The present-2026 NBT signed-ledger sensitivity keeps physical dispatch fixed and does not establish delayed-adoption value.', 'sensitivity_pointer': 'present_2026_nbt_sensitivity'},
                'measured_standby_and_controls': {'annual_savings_usd': None, 'reason': 'Exact US hardware manual/firmware, standby, minimum-load thresholds, cloud-independent schedules and control responses unverified.'},
                'whole_home_or_multiday_backup': {'runtime_hours': None, 'reason': 'Essential-circuit energy/startup records, transfer isolation, grid-forming compatibility and outage weather evidence absent.'}},
            'unresolved_inputs': ['exact U.S. certification and connection eligibility', 'NEM/CEA incremental and displaced rooftop export treatment',
                                  'conventional expansion only: original capacity for the percentage limit, prior expansion and onsite-load evidence; not SB 868 eligibility conditions', 'phase/CT layout and controllable circuit coverage',
                                  'site placement/shading and permitted mounting', 'full installed quote with panels/mounts/wiring/transfer/permits/tax/labor/subscriptions/replacement',
                                  'measured standby, usable capacity, supported reserve/charge control and warranty throughput',
                                  '2030 transition and future tariff scenarios; no known extra approval or compliance cost established']}


def _rounded(value):
    """Stable public precision; preserve SOC errors in scientific notation."""
    if isinstance(value, dict):
        return {str(k): _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(value):
            raise ValueError('nonfinite artifact value')
        if abs(value) < 1e-7:
            return float(f'{value:.4g}') if value else 0.
        return round(float(value), 6)
    if isinstance(value, np.integer):
        return int(value)
    return value


def main():
    inputs = load_inputs(pathlib.Path.cwd())
    artifact = _rounded(build_study(inputs))
    import marginal_capacity_value as mv
    out = mv.DATA / 'portable_solar_storage.json'
    payload = json.dumps(artifact, indent=2, allow_nan=False) + '\n'
    temporary = out.with_suffix('.json.tmp')
    temporary.write_text(payload)
    temporary.replace(out)
    print(f'Wrote {out.name}: {len(artifact["scenarios"])} scenarios; free behavior saving ${artifact["baseline"]["free_behavior_saving_usd"]:.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
