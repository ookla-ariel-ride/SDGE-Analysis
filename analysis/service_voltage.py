#!/usr/bin/env python3
"""Study gateway voltage from an immutable, private CSV snapshot, never a live DB.

Run from any directory:
  .venv/bin/python analysis/service_voltage.py [--input-dir DIR] [--output FILE]

The snapshot is schema v1: manifest.json with start/end_exclusive aware instants,
America/Los_Angeles timezone, and files entries containing filename/sha256/rows.
CSV schemas are SPECS below. Empty numeric cells mean missing; no imputation.
Gateway and its PVOutput mirror form ONE instrument chain. Missing/filtered
readings cannot prove service compliance, and 5min spot samples cannot establish
sustained event duration. Sample-and-hold durations are estimates capped at 300s.

Voltage regressions condition on measured grid power + solar (= inferred gross
load, owner states no battery), local hour, and day effects. Production models use
hourly means and independent modeled POA irradiance, air temperature, clock and
season controls. Their associations do not identify causal curtailment or losses.
All confidence intervals cluster errors by local date; temporal dependence across
dates and measurement error remain limitations. The artifact contains aggregates
only: no input readings, exact acquisition timestamps, IDs, or device addresses.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

def repository_root():
    """Find the nearest repo-shaped module ancestor, never an unrelated CWD.

    Supports analysis/service_voltage.py, a root script copy, and the existing
    private/verify script copy. The canonical analysis script and data/ mark
    the root without requiring private intake at import. Unknown locations fail
    closed instead of selecting an unrelated current working directory.
    """
    directory = Path(__file__).resolve().parent
    for candidate in [directory,*directory.parents]:
        if ((candidate/'analysis').is_dir() and (candidate/'data').is_dir()
                and (candidate/'analysis'/'service_voltage.py').is_file()):
            return candidate
    raise SystemExit('Service voltage generation failed: repository root not found')


ROOT = repository_root()
DEFAULT_INPUT = ROOT / 'private' / '1-raw-data' / 'service-telemetry'
TZ = 'America/Los_Angeles'
LIMIT_SOURCE = 'https://www.pge.com/assets/pge/docs/contact-us/report-an-issue/Voltage_Tolerance.pdf'
WEATHER_SOURCE = 'https://open-meteo.com/en/docs/historical-weather-api'
VOLTAGE = ['line_v', 'leg1_v', 'leg2_v', 'freq']
SPECS = {
    'meter': ['ts', 'line_v', 'leg1_v', 'leg2_v', 'leg1_a', 'leg2_a', 'freq'],
    'mirror': ['ts', 'gen_w', 'con_w', 'line_v', 'leg1_v', 'leg2_v', 'freq', 'voltage_semantics_valid'],
    'eagle': ['ts', 'demand_w'],
    'generation': ['ts', 'src_ts', 'gen_w', 'con_w'],
    'weather': ['ts', 'temp_c', 'irradiance_wm2', 'irradiance_poa_wm2', 'provisional'],
}
# Broad physical input bounds, NOT compliance bounds. Poller acceptance itself
# already censors extreme voltages/frequency; that censoring is an explicit limit.
BOUNDS = {'line_v': (0, 350), 'leg1_v': (0, 200), 'leg2_v': (0, 200),
          'leg1_a': (0, 500), 'leg2_a': (0, 500), 'freq': (40, 80),
          'gen_w': (0, 30000), 'con_w': (-100000, 100000),
          'demand_w': (-100000, 100000), 'temp_c': (-60, 70),
          'irradiance_wm2': (0, 1500), 'irradiance_poa_wm2': (0, 1500),
          'provisional': (0, 1), 'voltage_semantics_valid':(0,1)}


def aware_timestamp(text):
    """Reject timestamps without an explicit offset; retain the actual instant."""
    if not isinstance(text, str) or not re.search(r'(Z|[+-]\d\d:\d\d)$', text):
        raise ValueError('timestamps need an explicit timezone offset')
    try:
        value = pd.Timestamp(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid timestamp') from exc
    if pd.isna(value):
        raise ValueError('invalid timestamp')
    return value.tz_convert('UTC')


def read_source(path, kind):
    """Read strict CSV without pandas' automatic NA coercion or timestamp sorting."""
    data = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(data.columns) != SPECS[kind]:
        raise ValueError(f'{kind}: unexpected columns')
    for col in ['ts'] + (['src_ts'] if kind == 'generation' else []):
        data[col] = pd.to_datetime([aware_timestamp(v) for v in data[col]], utc=True)
    if data.ts.duplicated().any() or not data.ts.is_monotonic_increasing:
        raise ValueError(f'{kind}: duplicate or unsorted timestamps')
    cadence = 3600 if kind == 'weather' else 60 if kind == 'eagle' else 300
    # pandas 3 infers datetime units; integer grid checks always use nanoseconds.
    if len(data) and np.any(data.ts.dt.as_unit('ns').astype('int64') % (cadence * 10**9)):
        raise ValueError(f'{kind}: timestamps are not on the source grid')
    if kind == 'generation':
        if np.any(data.src_ts.dt.as_unit('ns').astype('int64') % (300 * 10**9)) or (data.src_ts > data.ts).any():
            raise ValueError('generation: invalid upstream timestamp')
    if kind == 'mirror':
        flag = data.voltage_semantics_valid
        if not flag.isin(['0','1']).all():
            raise ValueError('mirror: invalid semantic quality flag')
        # Deployment date held BOTH old weather and new voltage semantics.
        # A whole-day exclusion avoids choosing observations by their voltage.
        data.loc[flag.eq('0'), VOLTAGE] = ''
    for col in SPECS[kind]:
        if col in ('ts', 'src_ts'):
            continue
        original = data[col]
        try:
            numeric = pd.to_numeric(original.where(original.ne(''), None), errors='raise').astype(float)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{kind}: invalid numeric field') from exc
        nonempty = original.ne('')
        low, high = BOUNDS[col]
        if (~np.isfinite(numeric[nonempty])).any() or (~numeric[nonempty].between(low, high)).any():
            raise ValueError(f'{kind}: nonfinite or invalid numeric field')
        if col == 'provisional' and not numeric.dropna().isin([0., 1.]).all():
            raise ValueError('weather: invalid provisional flag')
        data[col] = numeric
    return data


def load_snapshot(input_dir):
    """Check hashes/schema and a complete local-date window before any public write."""
    directory = Path(input_dir)
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        if manifest['schema_version'] != 1 or manifest['timezone'] != TZ:
            raise ValueError('unsupported snapshot schema or timezone')
        start, end = aware_timestamp(manifest['start']), aware_timestamp(manifest['end_exclusive'])
        if start >= end:
            raise ValueError('invalid snapshot bounds')
        if any(bound.tz_convert(TZ) != bound.tz_convert(TZ).normalize() for bound in [start,end]):
            raise ValueError('snapshot bounds must be complete local calendar dates')
        files = manifest['files']
        if not isinstance(files,dict) or set(files) != set(SPECS):
            raise ValueError('snapshot sources must exactly match the declared schema')
        result = {}
        for kind in SPECS:
            meta = files[kind]
            if meta['filename'] != f'{kind}.csv':
                raise ValueError('unexpected snapshot filename')
            path = directory / meta['filename']
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
                raise ValueError('snapshot hash mismatch')
            data = read_source(path, kind)
            if len(data) != meta['rows']:
                raise ValueError('snapshot row count mismatch')
            mask = (data.ts > start) & (data.ts <= end) if kind == 'weather' else (data.ts >= start) & (data.ts < end)
            if not mask.all():
                raise ValueError('snapshot row outside declared bounds')
            result[kind] = data
        return result, manifest, start, end
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('missing or invalid snapshot manifest/input') from exc


def stats(values):
    """Distribution over present finite values; no missing value becomes zero."""
    values = np.asarray(pd.Series(values).dropna(), dtype=float)
    if not len(values):
        return {'samples': 0, 'min': None, 'max': None, 'mean': None,
                'std': None, 'p01': None, 'p05': None, 'p50': None, 'p95': None, 'p99': None}
    return {'samples': len(values), 'min': float(values.min()), 'max': float(values.max()),
            'mean': float(values.mean()), 'std': float(values.std()),
            **{f'p{p:02d}': float(np.percentile(values, p)) for p in (1, 5, 50, 95, 99)}}


def coverage(data, start, end, cadence, required):
    """Whole-window coverage on a UTC slot grid, daily denominators on local dates.

    Bounds must be ordered aware instants on the UTC source grid; partial
    off-grid intervals are unsupported and fail closed. Input timestamps follow
    read_source's sorted, unique, on-grid contract. No observation bridges a
    missing slot. Complete local-date bounds are enforced by load_snapshot. Gaps are
    gaps between valid timestamps; edge missing slots stay in missing_seconds.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if (start.tzinfo is None or end.tzinfo is None or pd.isna(start) or pd.isna(end)
            or start >= end or not isinstance(cadence,int) or cadence <= 0):
        raise ValueError('coverage needs ordered aware bounds and a positive integer cadence')
    start, end = start.tz_convert('UTC'), end.tz_convert('UTC')
    if start.value % (cadence*10**9) or end.value % (cadence*10**9):
        raise ValueError('coverage bounds must align with the source grid')
    valid = data.loc[data[required].notna().all(axis=1) & (data.ts >= start) & (data.ts < end), 'ts']
    expected = pd.date_range(start, end, freq=f'{cadence}s', inclusive='left')
    occupied = pd.DatetimeIndex(valid).intersection(expected)
    gaps = np.diff(occupied.as_unit('ns').astype('int64')) / 1e9
    elapsed = (end - start).total_seconds()
    # Interval weights, including the final sample only until end, never across gaps.
    represented = sum(min(cadence, (end - ts).total_seconds()) for ts in occupied)
    expected_dates = expected.tz_convert(TZ).strftime('%Y-%m-%d')
    observed_dates = occupied.tz_convert(TZ).strftime('%Y-%m-%d')
    daily = []
    for date, count in pd.Series(expected_dates).value_counts(sort=False).items():
        n = int(np.count_nonzero(observed_dates == date))
        daily.append({'date': date, 'occupied_slots': n, 'possible_slots': int(count),
                      'coverage_pct': 100 * n / count})
    return {'rows': len(data), 'valid_rows': len(valid), 'occupied_slots': len(occupied),
            'possible_slots': len(expected), 'coverage_pct': 100*len(occupied)/len(expected),
            'represented_seconds_estimate': represented, 'missing_seconds': elapsed-represented,
            'dates_with_samples': len(set(observed_dates)),
            'dates_without_samples': [d['date'] for d in daily if not d['occupied_slots']],
            'max_gap_seconds': float(gaps.max()) if len(gaps) else None,
            'interval_seconds': stats(gaps),
            'gaps_above_1_5_cadence': int(sum(gaps > 1.5*cadence)),
            'gap_counts': {'at_or_below_cadence': int(sum(gaps <= cadence)),
                           'above_cadence_to_15min': int(sum((gaps > cadence) & (gaps <= 900))),
                           'above_15min_to_1h': int(sum((gaps > 900) & (gaps <= 3600))),
                           'above_1h': int(sum(gaps > 3600))},
            'daily': daily}


def voltage_summary(data, end=None):
    """Classify each available field and estimate capped sample-and-hold time.

    Data follow read_source's ordered, unique, aware timestamp contract. An
    explicit aware end must follow every sample and caps each represented span.
    Without an end, the final sample represents zero seconds; earlier samples
    stop at the next timestamp or 300s. Estimates never prove sustained events.
    """
    if end is not None:
        end = pd.Timestamp(end)
        if end.tzinfo is None or pd.isna(end) or (data.ts >= end).any():
            raise ValueError('voltage duration end must be aware and follow all samples')
    result = {}
    for col in ['leg1_v', 'leg2_v', 'line_v']:
        if col not in data:
            continue
        low, high = (228, 252) if col == 'line_v' else (114, 126)
        present = data[col].notna()
        outside = present & ~data[col].between(low, high)
        summary = stats(data[col])
        # Duplicate/unsorted/on-grid safety belongs to read_source, not this pure summary.
        ts = data.ts
        next_gap = (ts.shift(-1) - ts).dt.total_seconds().fillna(300 if end is not None else 0).clip(lower=0, upper=300)
        if end is not None:
            next_gap = np.minimum(next_gap,(end-ts).dt.total_seconds())
        summary.update({'range_a_v': [low, high], 'range_a_outside_samples': int(outside.sum()),
                        'below_range_a_samples': int((data[col] < low).sum()),
                        'above_range_a_samples': int((data[col] > high).sum()),
                        'outside_sample_pct': 100*outside.sum()/present.sum() if present.sum() else None,
                        'outside_represented_seconds_estimate': float(next_gap[outside].sum())})
        result[col] = summary
    if {'leg1_v', 'leg2_v'}.issubset(data):
        result['leg_difference_absolute_v'] = stats(abs(data.leg1_v-data.leg2_v))
    if {'leg1_v', 'leg2_v', 'line_v'}.issubset(data):
        result['line_minus_leg_sum_v'] = stats(data.line_v-data.leg1_v-data.leg2_v)
    return result


def frequency_summary(data):
    values = data.freq.dropna()
    return {**stats(values), 'unique_reported_values': int(values.nunique()),
            'constant_at_reported_resolution': bool(len(values) and values.nunique() == 1),
            'interpretation': 'Measurement-chain sanity only; rounded 0.01 Hz, uncalibrated, and poller filters 55 < Hz < 65. No grid-frequency compliance claim.'}


def combine_voltage(local, mirror):
    """Retain every available channel; local wins independently for each field.

    Missing companion fields never remove a voltage observation. Mirror fields
    fill only missing local fields at the same timestamp, with channel source
    tags. Complete pairs can gate models after this evidence-preserving merge.
    """
    local = local.dropna(subset=VOLTAGE, how='all').copy()
    mirror = mirror.dropna(subset=VOLTAGE, how='all').copy()
    both = local[['ts']+VOLTAGE].merge(mirror[['ts']+VOLTAGE], on='ts', suffixes=('_local', '_mirror'))
    overlap = {'matched_rows': len(both), 'independent_instruments': False,
               'matched_rows_definition': 'Matching timestamps with any available channel per source; field comparisons use finite pairs only.'}
    for col in VOLTAGE:
        diff = (both[col+'_local']-both[col+'_mirror']).dropna()
        overlap[col] = {'compared_samples':len(diff),
                        'max_absolute_difference': float(abs(diff).max()) if len(diff) else None,
                        'different_samples': int((abs(diff) > 1e-6).sum()),
                        'difference': stats(diff)}
    local = local.set_index('ts')
    mirror = mirror.set_index('ts')
    times = local.index.union(mirror.index).sort_values()
    result = pd.DataFrame(index=times)
    result.index.name = 'ts'
    for col in VOLTAGE:
        local_values, mirror_values = local[col].reindex(times), mirror[col].reindex(times)
        result[col] = local_values.where(local_values.notna(), mirror_values)
        result[col+'_source'] = np.where(local_values.notna(), 'local_gateway',
                                        np.where(mirror_values.notna(), 'same_chain_mirror', 'missing'))
    sources = result[[col+'_source' for col in VOLTAGE]]
    has_local = sources.eq('local_gateway').any(axis=1)
    has_mirror = sources.eq('same_chain_mirror').any(axis=1)
    result['source'] = np.where(has_local & has_mirror, 'local_gateway_with_mirror_fields',
                                np.where(has_local, 'local_gateway', 'same_chain_mirror'))
    return result.reset_index(), overlap


def join_context(voltage, mirror, eagle, tolerance_seconds=30):
    """Solar joins exact slots; EAGLE joins nearest within stated tolerance, no fill."""
    joined = voltage.merge(mirror[['ts', 'gen_w', 'con_w']], on='ts', how='left', validate='one_to_one')
    grid = eagle[['ts', 'demand_w']].rename(columns={'ts': 'grid_ts', 'demand_w': 'net_grid_w'})
    joined = pd.merge_asof(joined.sort_values('ts'), grid.sort_values('grid_ts'),
                           left_on='ts', right_on='grid_ts', direction='nearest',
                           tolerance=pd.Timedelta(seconds=tolerance_seconds))
    joined['grid_offset_seconds'] = (joined.grid_ts-joined.ts).dt.total_seconds()
    joined['gross_load_w'] = joined.gen_w+joined.net_grid_w
    # Negative inferred gross load flags incompatible timing/instrument values.
    joined['negative_inferred_load'] = joined.gross_load_w < 0
    return joined


def hourly_context(joined, weather, weather_shift_hours=0):
    """Aggregate the hour (t-1h,t] for weather END label t. Need >=11 common slots.

    Temperature is the source's instantaneous hour-label value. Irradiance is the
    modeled preceding-hour mean. The join is exact at UTC hour END boundaries.
    """
    columns = ['leg1_v', 'leg2_v', 'gen_w', 'gross_load_w', 'net_grid_w']
    valid = joined.dropna(subset=columns).copy()
    valid = valid[valid.gross_load_w >= 0]
    valid['ts_hour'] = valid.ts.dt.ceil('h')
    grouped = valid.groupby('ts_hour')
    hourly = grouped[columns].mean()
    hourly['valid_slots'] = grouped.size()
    hourly = hourly[hourly.valid_slots >= 11].reset_index().rename(columns={'ts_hour': 'ts'})
    weather = weather.copy()
    weather['ts'] = weather.ts+pd.Timedelta(hours=weather_shift_hours)
    return hourly.merge(weather, on='ts', how='inner', validate='one_to_one')


def cluster_regression(y, x, names, groups):
    """OLS with intercept and CR1 daily-cluster sandwich; t interval with G-1 df.

    Center and scale design before solving for numerical stability. Rank failure
    yields 'unidentified', never a pseudo-precise voltage coefficient. No p-value
    or significance claim is a causal test.
    """
    y, x, groups = np.asarray(y, float), np.asarray(x, float), np.asarray(groups)
    n, k = x.shape
    unique = np.unique(groups)
    if n <= k+2 or len(unique) < 8:
        return {'status': 'insufficient_evidence', 'samples': n, 'days': len(unique)}
    mean, scale = x.mean(axis=0), x.std(axis=0)
    if (scale < 1e-10).any():
        return {'status': 'unidentified', 'samples': n, 'days': len(unique)}
    z = np.column_stack([np.ones(n), (x-mean)/scale])
    beta, _, rank, _ = np.linalg.lstsq(z, y, rcond=None)
    if rank != z.shape[1]:
        return {'status': 'unidentified', 'samples': n, 'days': len(unique)}
    residual = y-np.einsum('ij,j->i',z,beta)
    bread = np.linalg.inv(np.einsum('ij,ik->jk',z,z))
    meat = np.zeros((k+1, k+1))
    for group in unique:
        score = np.einsum('ij,i->j',z[groups == group],residual[groups == group])
        meat += np.outer(score, score)
    covariance = np.einsum('ij,jk->ik',np.einsum('ij,jk->ik',bread,meat),bread) * len(unique)/(len(unique)-1) * (n-1)/(n-k-1)
    error = np.sqrt(np.maximum(0, np.diag(covariance)[1:]))/scale
    coefficients = beta[1:]/scale
    critical = student_t.ppf(.975, len(unique)-1)
    total = ((y-y.mean())**2).sum()
    return {'status': 'estimated_association', 'samples': n, 'days': len(unique),
            'r_squared': float(1-np.sum(residual**2)/total) if total else None,
            'residual_rmse': float(np.sqrt(np.mean(residual**2))),
            'design_condition_number': float(np.linalg.cond(z)),
            'ci_method': 'OLS CR1 sandwich clustered by local date; Student t, dates minus one degrees of freedom',
            'coefficients': {name: {'estimate': float(b), 'ci95': [float(b-critical*s), float(b+critical*s)]}
                             for name, b, s in zip(names, coefficients, error)}}


def time_controls(data, daily_effects=False):
    """Local-clock harmonics plus day fixed effects or quadratic season trend."""
    local = data.ts.dt.tz_convert(TZ)
    hour = local.dt.hour+local.dt.minute/60
    controls = {}
    for harmonic in (1, 2, 3):
        controls[f'hour_sin_{harmonic}'] = np.sin(2*np.pi*harmonic*hour/24)
        controls[f'hour_cos_{harmonic}'] = np.cos(2*np.pi*harmonic*hour/24)
    date = local.dt.strftime('%Y-%m-%d')
    if daily_effects:
        for number, day in enumerate(sorted(date.unique())[1:]):
            controls[f'day_effect_{number+1}'] = (date == day).astype(float)
    else:
        days = (data.ts-data.ts.min()).dt.total_seconds()/86400
        controls['season_day'] = days
        controls['season_day_squared'] = days**2
    return pd.DataFrame(controls, index=data.index), date.to_numpy()


def voltage_association(joined):
    """Conditional slopes per leg in V/kW; effects need not originate at the utility."""
    data = joined.dropna(subset=['gen_w', 'gross_load_w', 'leg1_v', 'leg2_v'])
    data = data[data.gross_load_w >= 0].copy()
    if len(data) < 100:
        return {'status': 'insufficient_evidence'}
    controls, dates = time_controls(data, daily_effects=True)
    design = pd.DataFrame({'gross_load_kw': data.gross_load_w/1000, 'solar_kw': data.gen_w/1000})
    design = pd.concat([design, controls], axis=1)
    results = {}
    for col in ['leg1_v', 'leg2_v']:
        model = cluster_regression(data[col], design.values, design.columns.tolist(), dates)
        if 'coefficients' in model:
            model['coefficients'] = {k: model['coefficients'][k] for k in ('gross_load_kw', 'solar_kw')}
        results[col] = model
    # Nighttime reduces the solar-change confound without inventing subminute PV.
    night = data[data.gen_w <= 10]
    if len(night) >= 100:
        control, date = time_controls(night, daily_effects=True)
        design = pd.concat([pd.DataFrame({'net_import_kw': night.net_grid_w/1000}), control], axis=1)
        results['nighttime_leg_mean_v'] = cluster_regression((night.leg1_v+night.leg2_v)/2,
                                                            design.values, design.columns.tolist(), date)
        if 'coefficients' in results['nighttime_leg_mean_v']:
            results['nighttime_leg_mean_v']['coefficients'] = {'net_import_kw': results['nighttime_leg_mean_v']['coefficients']['net_import_kw']}
    results['interpretation'] = ('Associations condition on same-day effects and local-clock harmonics. Solar and inferred gross load share generation data. '
                                 'Nighttime check reduces that coupling. These slopes do not separate upstream variation from premise conductor drop/rise. '
                                 'Synchronized service-point and inverter-side readings with known accuracy could help answer that location question.')
    return results


def production_association(hourly):
    """Hourly conditional relation and leave-one-date-out expected-power reference.

    Modeled POA is an independent weather reference, not measured irradiance at
    the modules. Holdout prediction errors measure reference weakness. The same
    observational system supplies all training days, so this cannot reveal a
    persistent loss shared by every day. Causal kWh and dollars remain unknown.
    """
    base = {'causal_loss_kwh': None, 'causal_cost_usd': None,
            'status': 'insufficient_evidence', 'confidence': 'low for causal attribution',
            'interpretation': 'Conditional association only. No calibrated service-point voltage, inverter event trace, or verified unconstrained production reference. Missing or sub-five-minute curtailment remains possible.'}
    columns = ['gen_w','leg1_v','leg2_v','gross_load_w','irradiance_poa_wm2','temp_c']
    if not len(hourly) or not set(columns+['ts']).issubset(hourly):
        return base
    data = hourly.dropna(subset=columns).copy()
    data = data[data.irradiance_poa_wm2 >= 100]
    base.update({'matched_daylight_hours': len(data), 'irradiance_floor_wm2': 100,
                 'provisional_weather_hours': int(data.provisional.eq(1).sum()) if 'provisional' in data else None,
                 'unknown_weather_provenance_hours': int(data.provisional.isna().sum()) if 'provisional' in data else None})
    if len(data) < 100 or data.ts.dt.tz_convert(TZ).dt.date.nunique() < 20:
        return base
    controls, dates = time_controls(data)
    baseline = pd.DataFrame({'poa_kw_m2': data.irradiance_poa_wm2/1000,
                             'poa_squared': (data.irradiance_poa_wm2/1000)**2,
                             'air_temp_c': data.temp_c,
                             'poa_temperature': data.irradiance_poa_wm2/1000*(data.temp_c-25)})
    baseline = pd.concat([baseline, controls], axis=1)
    voltage = (data.leg1_v+data.leg2_v)/2
    design = pd.concat([pd.DataFrame({'mean_leg_v': voltage, 'gross_load_kw': data.gross_load_w/1000}), baseline],axis=1)
    model = cluster_regression(data.gen_w, design.values, design.columns.tolist(), dates)
    sensitivity = pd.concat([pd.DataFrame({'mean_leg_v':voltage}),baseline],axis=1)
    no_load = cluster_regression(data.gen_w,sensitivity.values,sensitivity.columns.tolist(),dates)
    # A reference fit on every OTHER local date: no own-day response enters prediction.
    x = np.column_stack([np.ones(len(data)), baseline.values])
    y = data.gen_w.to_numpy()
    prediction = np.full(len(data), np.nan)
    for date in sorted(set(dates)):
        train, test = dates != date, dates == date
        beta, _, rank, _ = np.linalg.lstsq(x[train],y[train],rcond=None)
        if rank == x.shape[1]:
            prediction[test] = np.einsum('ij,j->i',x[test],beta)
    valid = np.isfinite(prediction)
    reference = {'method': 'Leave-one-local-date-out OLS: modeled POA, POA squared, air temperature, POA-temperature interaction, three local-clock harmonics, quadratic season trend; no voltage or load in reference',
                 'scored_hours': int(valid.sum()),
                 'rmse_w': float(np.sqrt(np.mean((y[valid]-prediction[valid])**2))) if valid.any() else None,
                 'mean_observed_w': float(y.mean()),
                 'mean_prediction_error_w': float(np.mean(y[valid]-prediction[valid])) if valid.any() else None,
                 'negative_predictions': int(sum(prediction[valid]<0)),
                 'warning': 'Empirical expected power is not verified uncurtailed output. Hourly sampled means are not energy meters. Do not integrate residuals as losses.'}
    if 'coefficients' in model:
        interval = model['coefficients']['mean_leg_v']['ci95']
        base['inverse_association_at_95pct'] = bool(interval[1] < 0)
        base['finding'] = ('The conditional model estimates an inverse association within observed voltage support; causation and lost energy remain unproved.'
                           if interval[1] < 0 else 'The conditional model does not establish an inverse voltage-production association at the stated uncertainty level. This does not prove no relationship or zero production loss.')
    for result in [model, no_load]:
        if 'coefficients' in result:
            result['voltage_w_per_v'] = result['coefficients']['mean_leg_v']
            result.pop('coefficients')
    base.update({'status': model['status'], 'conditioned_model': model,
                 'sensitivity_without_inferred_load': no_load, 'expected_power_reference': reference,
                 'voltage_support_mean_leg_v': stats(voltage),
                 'hourly_mean_outside_range_count': int(((data.leg1_v<114)|(data.leg1_v>126)|(data.leg2_v<114)|(data.leg2_v>126)).sum()),
                 'model_controls': 'Mean leg V, inferred gross load kW, modeled POA, POA squared, instantaneous hourly air temperature, POA-temperature interaction, local-hour harmonics 1-3, quadratic elapsed-day trend.',
                 'identifiability': 'Voltage responds to generation as well as potentially affecting it. Weather resolution, changing shade/soiling, inferred-load coupling, omitted upstream conditions, and shared persistent losses limit interpretation. A coefficient or its interval does not establish a loss mechanism.'})
    return base


def disposition(data):
    """Consider every present voltage field; neither missing legs nor short windows hide excursions."""
    columns = [col for col in ['leg1_v','leg2_v','line_v'] if col in data]
    valid = data.loc[data[columns].notna().any(axis=1)]
    span = (valid.ts.max()-valid.ts.min()).total_seconds()/86400 if len(valid) else 0
    leg_outside = pd.Series(False,index=valid.index)
    for col in ['leg1_v','leg2_v']:
        if col in valid:
            leg_outside |= valid[col].lt(114) | valid[col].gt(126)
    line_outside = valid.line_v.lt(228) | valid.line_v.gt(252) if 'line_v' in valid else pd.Series(False,index=valid.index)
    outside = leg_outside | line_outside
    observed_dates = valid.ts.dt.tz_convert(TZ).dt.date.nunique()
    paired = valid.dropna(subset=['leg1_v','leg2_v']) if {'leg1_v','leg2_v'}.issubset(valid) else valid.iloc[:0]
    sufficient = span >= 60 and observed_dates >= 60
    status = ('investigate_observed_outside_range' if outside.any() else
              'monitored_samples_within_range_a' if sufficient else 'insufficient_window')
    if outside.any():
        action = ('Investigate the recorded excursion and measurement-chain consistency. SDG&E accepts suspected voltage problems and can supply service-entrance measurements. '
                  'Customer calibration is not a prerequisite to request investigation. Calibration, sensing location, and event duration matter before asserting a proved compliance breach.')
    elif not sufficient:
        action = 'Collect at least 60 local observation dates over a 60-day elapsed span before a within-range study disposition. No formal service-compliance closure follows from the current samples.'
    else:
        action = ('Continue monitoring. This window documents no Range A excursion to support alleging a utility voltage violation. Formal within-specification closure remains unsupported. '
                  'SDG&E accepts suspected voltage problems and can supply service-entrance measurements. Customer calibration is not a prerequisite to request investigation.')
    return {'status': status, 'elapsed_observation_days': span,
            'local_dates_with_paired_leg_samples':int(paired.ts.dt.tz_convert(TZ).dt.date.nunique()),
            'local_dates_with_any_voltage_samples':int(observed_dates),
            'observation_window_sufficient':bool(sufficient),
            'any_leg_outside_samples': int(leg_outside.sum()),
            'line_outside_samples':int(line_outside.sum()),
            'any_voltage_outside_samples':int(outside.sum()),
            'confidence': 'moderate for rounded monitored samples; low for service-point compliance and causal loss',
            'service_compliance_proved': False, 'causal_loss_proved': False,
            'issue_binary_closure_proved': False, 'action':action,
            'reason': 'Gateway sensing point and accuracy are not established. Collector filters extremes, spot samples miss events and missing time. Within-range readings cannot rule out unobserved curtailment.'}


def binned_characterization(joined):
    """Aggregate hourly/monthly and fixed kW strata; never expose individual readings."""
    data = joined.copy()
    local = data.ts.dt.tz_convert(TZ)
    data['local_hour'] = local.dt.hour
    data['month'] = local.dt.strftime('%Y-%m')
    result = {}
    for key in ['local_hour','month']:
        result[key] = [{'bin': int(label) if key=='local_hour' else label,
                        'samples':len(rows),'leg1_v':stats(rows.leg1_v),'leg2_v':stats(rows.leg2_v),
                        'outside_any_leg_samples':int(((rows.leg1_v<114)|(rows.leg1_v>126)|(rows.leg2_v<114)|(rows.leg2_v>126)).sum())}
                       for label,rows in data.groupby(key,observed=True)]
    bins = {'gross_load_w':[-np.inf,0,1000,3000,6000,10000,np.inf],
            'net_grid_w':[-np.inf,-6000,-3000,0,3000,6000,np.inf],
            'gen_w':[-np.inf,10,1000,3000,6000,np.inf]}
    for column, edges in bins.items():
        labels = pd.cut(data[column],edges,right=False)
        result[column] = [{'bin_w':str(label),'samples':len(rows),'leg1_v':stats(rows.leg1_v),'leg2_v':stats(rows.leg2_v)}
                          for label,rows in data.groupby(labels,observed=True)]
    return result


def build_study(input_dir):
    data, manifest, start, end = load_snapshot(input_dir)
    combined, overlap = combine_voltage(data['meter'], data['mirror'])
    if not combined[['line_v','leg1_v','leg2_v']].notna().any(axis=None):
        raise ValueError('no usable voltage observations')
    joined = join_context(combined,data['mirror'],data['eagle'])
    common = ['leg1_v','leg2_v','gen_w','gross_load_w']
    hourly = hourly_context(joined,data['weather'])
    coverages = {}
    for name, fields, cadence in [('meter',VOLTAGE,300),('mirror',VOLTAGE,300),
                                  ('eagle',['demand_w'],60),('generation',['gen_w'],300)]:
        coverages[name] = coverage(data[name],start,end,cadence,fields)
    # Weather labels are hour ends; shift for window coverage, not for model matching.
    weather_intervals = data['weather'].copy()
    weather_intervals['ts'] = weather_intervals.ts-pd.Timedelta(hours=1)
    coverages['weather_poa'] = coverage(weather_intervals,start,end,3600,['irradiance_poa_wm2'])
    coverages['combined_voltage'] = coverage(combined,start,end,300,VOLTAGE)
    coverages['joined_voltage_solar_grid'] = coverage(joined,start,end,300,common)
    # Calendar subtraction preserves 60 whole local dates through DST changes.
    last60 = (end.tz_convert(TZ)-pd.DateOffset(days=60)).tz_convert('UTC')
    latest60 = {name:coverage(frame,last60,end,step,cols) for name,frame,step,cols in
                [('meter',data['meter'],300,VOLTAGE),('mirror',data['mirror'],300,VOLTAGE),
                 ('combined_voltage',combined,300,VOLTAGE),('eagle',data['eagle'],60,['demand_w']),
                 ('joined_voltage_solar_grid',joined,300,common)]}
    # generation source timestamps are intentionally allowed to repeat. Compare
    # upstream power at that physical slot, not at the poller's later observation.
    generation = data['generation']
    check = generation.merge(data['mirror'][['ts','gen_w']],left_on='src_ts',right_on='ts',suffixes=('_poll','_mirror'))
    lag = (generation.ts-generation.src_ts).dt.total_seconds()
    finite_pairs = np.isfinite(check.gen_w_poll) & np.isfinite(check.gen_w_mirror)
    delta = (check.gen_w_poll-check.gen_w_mirror).loc[finite_pairs]
    if not len(delta):
        reconciliation_status = 'insufficient_comparison_evidence'
        reconciliation_detail = 'No finite paired source-slot power comparisons exist; agreement or disagreement is not determined.'
    elif abs(delta).gt(1e-6).any():
        reconciliation_status = 'unresolved_disagreement'
        reconciliation_detail = 'Finite exact src_ts comparisons disagree. Revision/timing histories do not establish the cause.'
    else:
        reconciliation_status = 'consistent_at_source_slot'
        reconciliation_detail = 'Available finite exact src_ts comparisons agree within 0.000001 W; this is not independent calibration.'
    # con_w remains a reported field; compare two hypotheses rather than assume gross/net.
    consumption_mask = joined[['con_w','net_grid_w','gross_load_w']].notna().all(axis=1)
    consumption = joined.loc[consumption_mask]
    manifest_bytes = (Path(input_dir)/'manifest.json').read_bytes()
    result = {
        'schema_version':1,
        'study':{'first_local_date':start.tz_convert(TZ).date().isoformat(),
                 'last_local_date':(end-pd.Timedelta(seconds=1)).tz_convert(TZ).date().isoformat(),
                 'timezone':TZ,'snapshot_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
                 'input_sha256':{kind:manifest['files'][kind]['sha256'] for kind in SPECS},
                 'private_raw_only':True,'generator_reads_live_database':False},
        'service_voltage_reference':{'nominal_v':[120,240],'range_a_leg_v':[114,126],
                                     'range_a_line_v':[228,252],'bounds_inclusive':True,
                                     'source':LIMIT_SOURCE,'source_location':'PG&E Voltage Tolerance Boundary, July 2018, pages 1-2, table 1',
                                     'scope':'ANSI C84.1 service voltage Range A; utilization limits differ. Sustained levels, not momentary transients. Gateway sensing point is unverified.'},
        'measurement_chain':{'sensor':'Enphase IQ Gateway selected first meter with numeric voltage; first two channels for legs',
                             'physical_sensing_point':'not established from stored records',
                             'accuracy':'No calibration record, calibrated comparison, or manufacturer accuracy bound established for selected channel.',
                             'resolution':{'voltage_v':0.1,'frequency_hz':0.01},
                             'collector_acceptance_masks':{'line_v':'180 < V < 300','legs_v':'100 < V < 150','frequency_hz':'55 < Hz < 65'},
                             'censoring_warning':'Poller drops values outside acceptance masks. Missing values/time include filtered and failed acquisitions; extreme events cannot be ruled out.',
                             'mirror_independent':False,'ambiguous_mirror_deployment_date':'2026-07-25; all voltage channels excluded for this date because old humidity/pressure coexist with new voltage meanings. Solar/grid fields retained.', 'battery':'Owner states no installed battery.',
                             'clocks_and_units':{'voltage':'V, poller local clock floored to 5min; same chain in mirror',
                                                 'grid':'W, signed net import positive/export negative; EAGLE collector minute clock, device freshness unknown',
                                                 'solar':'W, main mirror physical source slot; exact join to voltage slot, actual read instants not established',
                                                 'load':'Inferred gross W = simultaneous signed grid W + solar W, no installed battery; measurement/timing error remains',
                                                 'weather':'Modeled POA/GHI W/m2 average over preceding hour; UTC end-of-hour label. Air temperature C is instantaneous at label.',
                                                 'production':'Hourly mean of >=11 common five-minute sampled powers, not integrated energy'}},
        'coverage':coverages,'latest_60_local_dates':latest60,
        'duration_method':'Sample-and-hold represented time capped at scheduled cadence (300s voltage), stops before gaps; estimates only, not measured sustained event durations. Missing time is separate from in-range time.',
        'voltage':voltage_summary(combined,end=end),
        'voltage_by_source':{name:voltage_summary(data[name],end=end) for name in ['meter','mirror']},
        'source_field_completeness':{name:{col:int(data[name][col].notna().sum()) for col in SPECS[name] if col not in ['ts','src_ts']} for name in SPECS},
        'frequency':frequency_summary(combined),
        'source_overlap':overlap,'source_selection':combined.source.value_counts().to_dict(),
        'source_field_selection':{col:combined[col+'_source'].value_counts().to_dict() for col in VOLTAGE},
        'source_selection_method':'Retain every available channel. Prefer local values independently per field; fill only missing local values from the same-slot mirror. Row and channel source tags distinguish mixed rows. Complete field masks gate models and complete-slot coverage, not per-field distributions or excursion evidence.',
        'alignment':{'solar':'Exact timestamp only; no interpolation or forward fill',
                     'grid_tolerance_seconds':30,'grid_offset_seconds':stats(joined.grid_offset_seconds),
                     'rows_with_solar':int(joined.gen_w.notna().sum()),
                     'rows_with_grid':int(joined.net_grid_w.notna().sum()),
                     'rows_with_solar_and_grid':int(joined[['gen_w','net_grid_w']].notna().all(axis=1).sum()),
                     'negative_inferred_load_rows_excluded_from_models':int(joined.negative_inferred_load.sum()),
                     'mirror_consumption_semantics_check':{'compared_rows':len(consumption),
                         'reported_minus_inferred_gross_w':stats(consumption.con_w-consumption.gross_load_w),
                         'reported_minus_signed_grid_w':stats(consumption.con_w-consumption.net_grid_w),
                         'use':'Reported con_w is not used as gross load; inferred load uses EAGLE + solar.'},
                     'generation_poller_lag_seconds':stats(lag),
                     'repeated_generation_source_slots':int(generation.src_ts.duplicated().sum()),
                     'generation_source_slot_overlap_rows':len(check),
                     'generation_source_slot_minus_mirror_w':stats(delta),
                     'generation_source_reconciliation':{
                         'status':reconciliation_status,
                         'finite_paired_comparison_rows':len(delta),
                         'different_source_slot_rows':int(abs(delta).gt(1e-6).sum()),
                         'poller_zero_later_mirror_positive_rows':int(((check.gen_w_poll==0)&(check.gen_w_mirror>0)).sum()),
                         'series_used_for_production':'historical_main_mirror',
                         'poller_tee_used_as_production_truth':False,
                         'limitation':reconciliation_detail+' The tee records the then-current upstream view; the historical mirror is a later snapshot. The tee is excluded from production modeling; later mirror values remain observational, without independent production calibration.'},
                     'matched_hourly_rows':len(hourly),'hourly_minimum_common_slots':11},
        'characterization':binned_characterization(joined),
        'voltage_conditioned_associations':voltage_association(joined),
        'production':production_association(hourly),
        'production_sensitivities':{
            'final_weather_only':production_association(hourly[hourly.provisional.eq(0)]),
            'weather_shift_minus_one_hour':production_association(hourly_context(joined,data['weather'],-1)),
            'weather_shift_plus_one_hour':production_association(hourly_context(joined,data['weather'],1))},
        'disposition':disposition(combined),
        'missing_evidence':['Service compliance: physical sensing point, voltage accuracy, and sustained event duration remain unestablished. SDG&E can supply service-entrance measurements for suspected problems; customer calibration is not a prerequisite to request investigation.',
                            'Event-related production response: inverter trip/curtailment records and faster synchronized voltage/production could test suspected events that five-minute samples miss.',
                            'Causal loss: on-site irradiance/module temperature or another independently checked production reference could strengthen attribution. The production-source disagreement needs resolution before assigning it a loss mechanism.',
                            'Location of voltage variation: synchronized service-point and inverter-side readings with known accuracy could help separate upstream variation from premise conductor drop/export rise. These follow-ups answer specific questions; they are not requirements for the current sampled finding.'],
        'causal_loss_kwh':None,'causal_cost_usd':None,
        'pricing':'No identified loss or aligned counterfactual exists; no kWh or dollar estimate. Any future counterfactual needs canonical tariff re-billing of aligned interval flows.',
        'sources':{'service_limits':LIMIT_SOURCE,'modeled_weather_time_semantics':WEATHER_SOURCE}}
    return result


def clean_json(value):
    """Round aggregates consistently; refuse any nonfinite JSON output."""
    if isinstance(value, dict):
        return {str(k):clean_json(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (float,np.floating)):
        if not np.isfinite(value):
            raise ValueError('nonfinite public output')
        return round(float(value),6)
    if isinstance(value,np.integer):
        return int(value)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,default=DEFAULT_INPUT)
    parser.add_argument('--output',type=Path,default=ROOT/'data/service_voltage.json')
    args=parser.parse_args(argv)
    try:
        text=json.dumps(clean_json(build_study(args.input_dir)),indent=2,sort_keys=True,allow_nan=False)+'\n'
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',dir=args.output.parent,prefix='.service-voltage-',delete=False) as f:
            temporary=Path(f.name)
            f.write(text)
        temporary.replace(args.output)
    except (ValueError,OSError) as exc:
        # Input exception content can include private data; print class only.
        raise SystemExit(f'Service voltage generation failed: {type(exc).__name__}') from None
    print('Service voltage aggregate written.')


if __name__=='__main__':
    main()
