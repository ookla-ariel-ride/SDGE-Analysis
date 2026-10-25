#!/usr/bin/env python3
"""Modeled equipment baskets with dated EU hardware and US price assumptions.

Run from private/verify: ../../.venv/bin/python ../../analysis/portable_equipment.py
Only data/portable_equipment.json is written. No cached results substitute for
missing household inputs. Pure builders accept synthetic frames without intake.

The canonical portable dispatch and June 2026 tariff engine supply all flows and
bills. One aggregate controller discharges only from 16:00 through midnight.
The controller's solar-charge account excludes existing rooftop exports; those
exports retain their original physical and credited amounts. This is a source
allocation convention, not physical separation behind one meter. The grid-charge
channel can coincide with rooftop exports. Added exports receive zero credit.

Hardware savings start after the free behavior fix, with no NEM home battery.
Common controller/efficiency/standby assumptions are not product specifications.
Costs are component subtotals before installation, mounts, US tax, and ownership
costs. EU prices are planning proxies, not US offers or compatibility guarantees.
"""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys
import tempfile

import numpy as np
import portable_solar_storage as P

PRICE_DATE = '2026-10-24'
FX_DATE = '2026-10-23'
USD_PER_EUR = 1.1193
US_PANEL_PRICE = 1293.99
US_PANEL_FREIGHT = 300.
US_PANEL_DC_KW = 1.6
EU_PRICE_MULTIPLIERS = (1., 1.5, 2.)
CAPACITIES_KWH = (7.68, 11.52, 12.288, 13.0048, 12.8)
OUTPUT_CAPS_KW = (.8, 1.2)
CHARGE_KW = 1.2
RESERVE = .2
RTE = .85
STANDBY_W = 10.

# Dated manufacturer evidence. Values below are the checked 2026-10-24 snapshot,
# not live prices. German 0% VAT offers and EU 19% VAT offers stay distinct.
SOURCE_URLS = {
    'stream': ('STREAM Ultra X standalone variant', 'https://de.ecoflow.com/products/stream-ultra-x?variant=56505094373757'),
    'stream_meter': ('EcoFlow Shelly meter', 'https://de.ecoflow.com/products/ecoflow-shelly-smart-meter'),
    'stream_cable': ('STREAM parallel cable', 'https://de.ecoflow.com/products/stream-parallel-cable'),
    'solarflow': ('SolarFlow 800 Pro 2', 'https://www.zendure.de/products/zendure-solarflow-800-pro-2'),
    'solarflow_battery': ('AB2000L expansion battery', 'https://www.zendure.de/products/zendure-solarflow-batterie-ab2000s-ab1000s?variant=56930921218426'),
    'solarflow_meter': ('Zendure 3CT-S meter', 'https://www.zendure.de/products/zendure-smart-meter-3ct'),
    'hub': ('BLUETTI Balco Transfer Hub', 'https://www.bluettipower.eu/products/balco-transfer-hub'),
    'meter': ('BLUETTI S Meter', 'https://www.bluettipower.eu/products/s-meter'),
    'apex': ('US APEX 300', 'https://www.bluettipower.com/products/apex-300-home-battery-backup?variant=47351562174683'),
    'b500k': ('US B500K expansion battery', 'https://www.bluettipower.com/products/b500k-expansion-battery?variant=47893756903643'),
    'dpu': ('US DELTA Pro Ultra with two batteries', 'https://us.ecoflow.com/products/delta-pro-ultra?variant=41748275626057'),
    'dpu_x': ('US DELTA Pro Ultra X with two batteries', 'https://us.ecoflow.com/products/delta-pro-ultra-x?variant=54714781171785'),
    'balco': ('Balco 260 and 1.8 kW panel bundle', 'https://www.bluettipower.eu/products/bluetti-balco-260'),
    'bc260': ('BC260 expansion battery', 'https://www.bluettipower.eu/products/bluetti-bc-260'),
    'panels': ('Four Renogy 400 W panels and US freight', 'https://www.renogy.com/products/400w-n-type-bifacial-solar-panel'),
    'fx': ('ECB EUR/USD reference, 2026-10-23', 'https://www.ecb.europa.eu/stats/shared/pdf/eurofxref.pdf?b76aa72c1bf4af718c0ae9a47c6b4704='),
}


@dataclass(frozen=True)
class Component:
    name: str
    unit_price: float
    currency: str
    included_vat_fraction: float
    source_id: str


COMPONENTS = {
    'stream': Component('STREAM Ultra X, 3.84 kWh', 949, 'EUR', 0, 'stream'),
    'stream_meter': Component('Shelly meter', 149, 'EUR', 0, 'stream_meter'),
    'stream_cable': Component('Parallel cable', 19.9, 'EUR', 0, 'stream_cable'),
    'solarflow': Component('SolarFlow 800 Pro 2, 1.92 kWh', 499, 'EUR', 0, 'solarflow'),
    'solarflow_battery': Component('AB2000L, 1.92 kWh', 429, 'EUR', 0, 'solarflow_battery'),
    'solarflow_meter': Component('3CT-S meter', 119, 'EUR', 0, 'solarflow_meter'),
    'hub': Component('Future US hub priced from EU Balco Transfer Hub', 299, 'EUR', .19, 'hub'),
    'meter': Component('S Meter proxy', 99, 'EUR', .19, 'meter'),
    'apex': Component('US APEX 300, 2.7648 kWh', 1499, 'USD', 0, 'apex'),
    'b500k': Component('US B500K, 5.12 kWh, cable included', 1789, 'USD', 0, 'b500k'),
    'dpu': Component('US DELTA Pro Ultra with two batteries, 12.288 kWh', 6199, 'USD', 0, 'dpu'),
    'dpu_x': Component('US DELTA Pro Ultra X with two batteries, 12.288 kWh', 7999, 'USD', 0, 'dpu_x'),
    'balco': Component('Balco 260 base, 2.56 kWh', 1049, 'EUR', .19, 'balco'),
    'bc260': Component('BC260, 2.56 kWh', 849, 'EUR', .19, 'bc260'),
    'balco_panel_bundle': Component('Balco 260 plus one BC260 plus four 450 W panels', 2169, 'EUR', .19, 'balco'),
}


@dataclass(frozen=True)
class Basket:
    id: str
    name: str
    capacity_kwh: float
    components: tuple
    basis: str
    included_pv_dc_kw: float = 0.


CATALOG = (
    Basket('stream_x2', 'STREAM Ultra X x2', 7.68,
           (('stream', 2), ('stream_meter', 1), ('stream_cable', 1)),
           'Two units, meter, and one parallel cable; advertised German 0% VAT prices'),
    Basket('stream_x3', 'STREAM Ultra X x3', 11.52,
           (('stream', 3), ('stream_meter', 1), ('stream_cable', 2)),
           'Three units, meter, and two parallel cables; advertised German 0% VAT prices'),
    Basket('solarflow', 'SolarFlow 800 Pro 2 + five AB2000L', 11.52,
           (('solarflow', 1), ('solarflow_battery', 5), ('solarflow_meter', 1)),
           'Base, five expansions, and meter; advertised German 0% VAT prices'),
    Basket('apex', 'APEX 300 + two B500K + future hub', 13.0048,
           (('apex', 1), ('b500k', 2), ('hub', 1), ('meter', 1)),
           'US battery prices plus future hub and meter priced from EU offers after 19% VAT removal'),
    Basket('dpu', 'DELTA Pro Ultra + two batteries + future hub', 12.288,
           (('dpu', 1), ('hub', 1), ('meter', 1)),
           'US battery price plus future hub and meter after 19% VAT removal; third-party pairing assumed'),
    Basket('dpu_x', 'DELTA Pro Ultra X + two batteries + future hub', 12.288,
           (('dpu_x', 1), ('hub', 1), ('meter', 1)),
           'US battery price plus future hub and meter after 19% VAT removal; third-party pairing assumed'),
    Basket('balco_260', 'Balco 260 + four BC260 + meter', 12.8,
           (('balco', 1), ('bc260', 4), ('meter', 1)),
           'Integrated EU base, four expansions, and meter; 19% VAT removed'),
    Basket('balco_260_bundle', 'Balco 260 12.8 kWh + 1.8 kW panel bundle + meter', 12.8,
           (('balco_panel_bundle', 1), ('bc260', 3), ('meter', 1)),
           'EU base/one-expansion/four-panel bundle plus three expansions and meter; 19% VAT removed', 1.8),
)

ASSUMPTIONS = {
    'capacities_kwh': list(CAPACITIES_KWH), 'aggregate_output_caps_kw': list(OUTPUT_CAPS_KW),
    'charge_kw': CHARGE_KW, 'reserve_fraction': RESERVE, 'round_trip_efficiency': RTE,
    'standby_w_total': STANDBY_W, 'controller_count': 1, 'pv_dc_kw_options': [0, 1.6, 1.8],
    'discharge_window': '16:00 <= local hour < 24:00',
    'incremental_export_credit': 'Zero; physical added exports remain',
    'existing_rooftop_charge_allocation': 'Excluded; original rooftop exports retain canonical credits',
    'economic_basis': '5% real discount, 1% annual savings decline, no real tariff escalation; unknown ownership costs excluded',
}
LIMITS = [
    '800 W and 1200 W are aggregate model caps, not guarantees of product output or future US eligibility.',
    'The common 1.2 kW charge limit, 85% round-trip efficiency, and 20% reserve are comparison assumptions, not checked product controls.',
    'One aggregate controller with 10 W total standby may understate multi-unit standby, hub losses, and control constraints.',
    'Whole-meter load access is assumed; accessible circuits, phase limits, controller coordination, and electrical matching remain unproved.',
    'Existing rooftop exports stay outside the solar-charge account; grid-channel charging can coincide with rooftop exports behind the same meter.',
    'These hardware deltas exclude free-behavior savings and a NEM home battery; do not add them to separate home-battery savings.',
    'Shared AC power clips added PV before storage; direct-DC clipping recovery is not modeled.',
    'PV follows the existing-array production proxy, not measured new-panel placement.',
    'Monthly bills use physical exports for NBC and credit only original rooftop exports; zero credit does not mean zero export.',
    'The 1.8 kW panel case belongs only to the exact Balco bundle; the common US panel allowance represents 1.6 kW.',
    'No US availability, supported pairing, complete-system certification, or purchase recommendation follows from this comparison.',
]
COST_LIMITS = [
    'EU price multipliers of 1, 1.5, and 2 are sensitivity assumptions, not predicted US premiums.',
    'Known US battery prices and the common US panel/freight allowance remain fixed; only EU-derived basket costs receive the multiplier.',
    'Subtotals exclude installation, mounts, site wiring, US sales tax, unpriced accessories, financing, maintenance, and replacement.',
    'Simple paybacks omit degradation and all unquoted costs; discounted ten-year ceilings are gross benefits before ownership costs.',
    'The Balco bundle uses EU panels inside its quoted basket; its panel increment is an advertised-configuration price difference, not a standalone panel quote.',
    'Panel electrical matching, mounting, import freight, meter compatibility, and future US connection hardware remain unresolved.',
]


def convert_eur(price, vat_fraction=0):
    """Remove included VAT before conversion; do not remove VAT from USD prices."""
    P._finite(price, 'EUR price')
    P._finite(vat_fraction, 'VAT fraction')
    if vat_fraction > 1:
        raise ValueError('VAT fraction must be <= 1')
    return price / (1 + vat_fraction) * USD_PER_EUR


def case_id(capacity, output, pv):
    return f'equipment_{capacity:g}kwh_{output:g}kw_pv{pv:g}kw'


def build_case(inputs, capacity, output, dc_kw):
    """Return one public aggregate row and its in-memory interval trace."""
    for value, name in ((capacity, 'capacity'), (output, 'output'), (dc_kw, 'PV DC')):
        P._finite(value, name)
    if capacity == 0 or output == 0:
        raise ValueError('capacity and output must be positive')
    d = inputs['frame']
    P._frame(d)
    post, exports = (P._array(inputs[key], len(d), key) for key in ('post_imports', 'exports'))
    allowed = (d.dt.dt.hour.to_numpy() >= 16).astype(float)
    battery = P.Battery(capacity, output, CHARGE_KW, reserve=RESERVE, rte=RTE,
                        standby_w=STANDBY_W, charging='mixed' if dc_kw else 'grid')
    pv = P.pv_profile(d, inputs['pv_per_kw'], dc_kw)
    trace = P.steady_dispatch(d, post, np.zeros(len(d)), pv, [battery],
                              shared_ac_kw=output, export_treatment='zero_credit',
                              discharge_allowed=allowed)
    # Preserve original rooftop channels without allocating their surplus to
    # this controller. This counterfactual account is not a wiring claim.
    # The dispatch accounts can share arrays. Assign new arrays rather than
    # mutating aliases, which would add the original exports twice.
    trace['exports'] = trace['exports'] + exports
    trace['credited_exports'] = trace['credited_exports'] + exports
    trace['flows']['existing_export_kwh'] = trace['flows']['existing_export_kwh'] + exports
    log = trace['controllers'][0]
    charge = log['solar_charge_kwh'] + log['grid_charge_kwh']
    soc_change = np.diff(np.r_[trace['soc_start'][0], log['soc_kwh']])
    identity = (trace['imports'] + pv + exports - post - trace['exports']
                - trace['flows']['standby_kwh'] - trace['flows']['curtailed_kwh']
                - log['loss_kwh'] - soc_change)
    summary = P.scenario_summary(d, post, exports, trace, [battery])
    frame = d[['dt', 'seas', 'p', 'ym']].copy()
    frame['I'], frame['E'], frame['physical_E'] = trace['imports'], trace['credited_exports'], trace['exports']
    bills = P.R.bill_nem_monthly(frame, 'I', 'E', physical_exp='physical_E')
    baseline = P._bill(d, post, exports)
    checks = {
        'annual_soc_error_kwh': float(trace['annual_soc_error_kwh']),
        'model_conservation_max_error_kwh': float(trace['conservation_max_error_kwh']),
        'restored_energy_identity_max_error_kwh': float(np.abs(identity).max()),
        'peak_charge_kw': float(charge.max() / P.INTERVAL_HOURS),
        'peak_battery_output_kw': float(log['served_kwh'].max() / P.INTERVAL_HOURS),
        'peak_combined_output_kw': float((trace['flows']['pv_ac_kwh'] + log['served_kwh']).max() / P.INTERVAL_HOURS),
        'discharge_outside_evening_kwh': float(log['served_kwh'][allowed == 0].sum()),
        'existing_rooftop_charge_kwh': float(log['existing_solar_charge_kwh'].sum()),
        'grid_charge_outside_super_off_peak_kwh': float(log['grid_charge_kwh'][d.p.to_numpy() != 'sop'].sum()),
        'direct_billing_max_month_error_usd': max(abs(float(total) - summary['bill']['monthly'][str(month)]['total_usd'])
                                                  for month, total in bills.items()),
        'billing_delta_error_usd': abs(baseline['total_usd'] - sum(bills.values()) - summary['annual_savings_usd']),
        'billing_component_sum_error_usd': abs(sum(summary['component_savings_usd'].values()) - summary['annual_savings_usd']),
    }
    zero_checks = ('discharge_outside_evening_kwh', 'existing_rooftop_charge_kwh',
                   'grid_charge_outside_super_off_peak_kwh')
    error_checks = ('model_conservation_max_error_kwh', 'restored_energy_identity_max_error_kwh',
                    'direct_billing_max_month_error_usd', 'billing_delta_error_usd', 'billing_component_sum_error_usd')
    if (checks['annual_soc_error_kwh'] >= P.SOC_TOLERANCE_KWH
            or any(checks[key] > 1e-7 for key in error_checks)
            or any(checks[key] != 0 for key in zero_checks)
            or checks['peak_charge_kw'] > CHARGE_KW + 1e-8
            or checks['peak_battery_output_kw'] > output + 1e-8
            or checks['peak_combined_output_kw'] > output + 1e-8
            or log['soc_kwh'].min() < capacity * RESERVE - 1e-8
            or log['soc_kwh'].max() > capacity + 1e-8
            or np.any((charge > 1e-8) & (log['served_kwh'] > 1e-8))
            or not np.array_equal(trace['credited_exports'], exports)
            or not np.array_equal(trace['flows']['existing_export_kwh'], exports)):
        raise RuntimeError('equipment dispatch or billing check failed')
    ac_energy = capacity * (1 - RESERVE) * math.sqrt(RTE)
    return {'id': case_id(capacity, output, dc_kw), 'battery': asdict(battery),
            'pv_dc_kw': dc_kw, 'shared_ac_kw': output, 'export_treatment': 'zero_credit',
            'summary': summary, 'checks': checks,
            'full_charge_ac_before_standby_kwh': ac_energy,
            'full_charge_hours_at_output_cap_before_standby': ac_energy / output}, trace


def marginal(before, after):
    savings = after['summary']['annual_savings_usd'] - before['summary']['annual_savings_usd']
    return {'before_id': before['id'], 'after_id': after['id'], 'annual_savings_usd': savings,
            'ten_year_break_even_incremental_cost_usd': P.economics(savings)['ten_year']['break_even_installed_usd']}


def _basket_cost(basket):
    components, us, eu = [], 0., 0.
    for key, quantity in basket.components:
        item = COMPONENTS[key]
        price = item.unit_price * quantity
        subtotal = convert_eur(price, item.included_vat_fraction) if item.currency == 'EUR' else price
        if item.currency == 'EUR':
            eu += price / (1 + item.included_vat_fraction)
        else:
            us += price
        components.append({'id': key, **asdict(item), 'quantity': quantity,
                           'quoted_subtotal': price, 'subtotal_usd_ex_vat': subtotal})
    return components, us, eu


def _payback(cost, savings):
    return cost / savings if savings > 0 else None


def build_costs(cases):
    """Match exact model rows before attaching any cost or marginal payback."""
    lookup, ids = {}, set()
    for row in cases:
        key = (row['battery']['capacity_kwh'], row['shared_ac_kw'], row['pv_dc_kw'])
        savings = row['summary']['annual_savings_usd']
        if isinstance(savings, bool) or not isinstance(savings, (int, float)) or not math.isfinite(savings):
            raise ValueError('case savings must be finite')
        if key in lookup or row['id'] in ids or row['id'] != case_id(*key):
            raise ValueError('model rows need unique, matching case IDs')
        lookup[key] = row
        ids.add(row['id'])
    rows = []
    balco_storage = next(item for item in CATALOG if item.id == 'balco_260')
    _, balco_us, balco_eu = _basket_cost(balco_storage)
    for basket in CATALOG:
        components, us, eu = _basket_cost(basket)
        converted = eu * USD_PER_EUR
        included_pv = basket.included_pv_dc_kw
        storage_subtotal = balco_us + balco_eu * USD_PER_EUR if included_pv else us + converted
        pv_kw = included_pv or US_PANEL_DC_KW
        panel_cost = us + converted - storage_subtotal if included_pv else US_PANEL_PRICE + US_PANEL_FREIGHT
        panel_basis = ('Difference between advertised Balco configurations with and without 1.8 kW panels; not a standalone quote'
                       if included_pv else 'Four 400 W US panels plus published freight; mounts and electrical matching excluded')
        cost_cases = []
        for output in OUTPUT_CAPS_KW:
            try:
                before, after = (lookup[(basket.capacity_kwh, output, pv)] for pv in (0, pv_kw))
            except KeyError as exc:
                raise ValueError('cost basket lacks an exact model case') from exc
            savings = after['summary']['annual_savings_usd']
            battery_savings = before['summary']['annual_savings_usd']
            panel_savings = savings - battery_savings
            sensitivities = []
            for multiplier in EU_PRICE_MULTIPLIERS:
                us_panels = 0. if included_pv else US_PANEL_PRICE + US_PANEL_FREIGHT
                total = us + converted * multiplier + us_panels
                sensitivities.append({'eu_price_multiplier': multiplier,
                                      'known_us_equipment_usd': us,
                                      'eu_derived_basket_usd': converted * multiplier,
                                      'common_us_panel_freight_usd': us_panels,
                                      'equipment_panel_freight_subtotal_usd': total,
                                      'simple_payback_before_unquoted_costs_years': _payback(total, savings)})
            cost_cases.append({'aggregate_output_kw': output, 'source_case': after['id'],
                               'battery_only_source_case': before['id'], 'pv_dc_kw': pv_kw,
                               'annual_savings_usd': savings, 'battery_only_annual_savings_usd': battery_savings,
                               'panel_increment_annual_savings_usd': panel_savings,
                               'panel_increment_cost_usd': panel_cost, 'panel_cost_basis': panel_basis,
                               'panel_only_simple_payback_years': _payback(panel_cost, panel_savings),
                               'price_sensitivities': sensitivities})
        source_ids = list(dict.fromkeys(component['source_id'] for component in components))
        source_ids.extend(['fx'] + ([] if included_pv else ['panels']))
        rows.append({'id': basket.id, 'name': basket.name, 'nominal_capacity_kwh': basket.capacity_kwh,
                     'known_us_equipment_usd': us, 'eu_ex_vat_eur': eu,
                     'converted_eu_proxy_usd': converted,
                     'storage_feed_in_meter_subtotal_usd': storage_subtotal,
                     'included_pv_dc_kw': included_pv, 'components': components,
                     'basis': basket.basis, 'source_ids': source_ids, 'cases': cost_cases})
    return {'date': PRICE_DATE, 'eur_usd_reference': USD_PER_EUR, 'reference_date': FX_DATE,
            'fx_source': SOURCE_URLS['fx'][1], 'panel_price_usd': US_PANEL_PRICE,
            'panel_freight_only_usd': US_PANEL_FREIGHT,
            'panel_freight_usd': US_PANEL_PRICE + US_PANEL_FREIGHT,
            'panel_dc_kw': US_PANEL_DC_KW, 'cost_limits': list(COST_LIMITS), 'rows': rows}


def build_study(inputs):
    """Run 22 matched cases without loading files or publishing interval traces."""
    d = inputs['frame']
    before = P._bill(d, inputs['imports'], inputs['exports'])
    post = P._bill(d, inputs['post_imports'], inputs['exports'])
    cases = [build_case(inputs, capacity, output, pv)[0]
             for capacity in CAPACITIES_KWH for output in OUTPUT_CAPS_KW
             for pv in ((0., 1.6, 1.8) if capacity == 12.8 else (0., 1.6))]
    lookup = {(r['battery']['capacity_kwh'], r['shared_ac_kw'], r['pv_dc_kw']): r for r in cases}
    pv_marginals = [marginal(lookup[(capacity, output, 0)], lookup[(capacity, output, pv)])
                    for capacity in CAPACITIES_KWH for output in OUTPUT_CAPS_KW
                    for pv in ((1.6, 1.8) if capacity == 12.8 else (1.6,))]
    output_marginals = [marginal(lookup[(capacity, .8, pv)], lookup[(capacity, 1.2, pv)])
                        for capacity in CAPACITIES_KWH
                        for pv in ((0, 1.6, 1.8) if capacity == 12.8 else (0, 1.6))]
    capacities = sorted(CAPACITIES_KWH)
    capacity_marginals = [marginal(lookup[(lower, output, pv)], lookup[(upper, output, pv)])
                          for lower, upper in zip(capacities, capacities[1:])
                          for output in OUTPUT_CAPS_KW for pv in (0, 1.6)]
    sources = {key: {'title': title, 'url': url, 'checked_date': PRICE_DATE}
               for key, (title, url) in SOURCE_URLS.items()}
    sources.update({'portable_model': {'path': 'analysis/portable_solar_storage.py'},
                    'canonical_rates': {'path': 'analysis/rates.py'}})
    return {'schema_version': 1, 'status': 'Modeled engineering and cost proxies; no installed-product forecast',
            'rate_basis': 'Frozen June 2026 EV-TOU-5/CEA Clean Impact Plus NEM model',
            'load_basis': 'Post-free-fix whole-meter access; no home battery; no selected-circuit claim',
            'window': {'window_start': str(d.dt.min().date()), 'window_end': str(d.dt.max().date()),
                       'interval_count': len(d)},
            'assumptions': {**ASSUMPTIONS, 'capacities_kwh': list(CAPACITIES_KWH),
                            'aggregate_output_caps_kw': list(OUTPUT_CAPS_KW), 'pv_dc_kw_options': [0, 1.6, 1.8]},
            'limits': list(LIMITS), 'sources': sources,
            'baseline': {'pre_behavior': before, 'post_behavior': post,
                         'free_behavior_saving_usd': before['total_usd'] - post['total_usd']},
            'cases': cases, 'new_pv_marginals': pv_marginals,
            'output_cap_marginals': output_marginals, 'capacity_marginals': capacity_marginals,
            'costs': build_costs(cases)}


def write_artifact(study, destination):
    """Serialize before touching output, then replace atomically in its directory."""
    payload = json.dumps(P._rounded(study), indent=2, allow_nan=False) + '\n'
    destination = Path(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.portable_equipment-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    inputs = P.load_inputs(Path.cwd())
    study = build_study(inputs)
    # The census and documented sandbox can copy scripts beside their inputs.
    # Use the loader's canonical root rather than inferring it from __file__.
    import marginal_capacity_value as mv
    output = mv.DATA / 'portable_equipment.json'
    write_artifact(study, output)
    print(f'Wrote {output.name}: {len(study["cases"])} modeled cases and {len(study["costs"]["rows"])} cost baskets')
    return 0


if __name__ == '__main__':
    sys.exit(main())
