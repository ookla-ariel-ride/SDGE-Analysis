#!/usr/bin/env python3
"""
Solar-panel soiling loss quantified from rain-recovery events.

Array: 10.05 kW, SDG&E Coastal climate zone.
Window analyzed: 2025-07-24 .. 2026-07-23.

DATA PROVENANCE (all fetched or on-disk; nothing estimated):
- Daily production: pvoutput_daily.csv (kWh) and prod_daily_official.csv
  (Enphase "Energy Delivered (kWh)"), on disk in this directory.
- Daily precipitation: NOAA/RCC ACIS web service (data.rcc-acis.org/StnData),
  a nearby coastal airport gauge (station ID withheld for privacy),
  fetched 2026-07-24 via web_fetch for 2024-01-01..2024-08-12,
  2025-01-01..2025-07-23 and 2025-07-24..2026-07-23. Values below are a
  verbatim transcription of every nonzero day returned; all other days in
  range were "0.00", "T" (trace, treated as 0) or "M" (2024-03-07, missing,
  treated as 0). Inches converted to mm (x25.4).
  NOTE: the requested Open-Meteo archive API returned empty bodies through
  the sanctioned web_fetch tool for every query variant tried (full range,
  short ranges, csv/json, era5 endpoint, proxies), so ACIS station data is
  used instead. Consequently no fetched shortwave_radiation_sum exists;
  irradiance normalization uses a deterministic clear-sky GHI computation
  (Haurwitz model, pure solar geometry) plus a clear-day filter, with
  seasonal harmonics absorbing the residual GHI-to-plane-of-array mismatch.

Outputs: soiling_results.json (no PII).
"""

import csv
import json
import math
import os
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
LAT = None  # withheld
LON = None  # withheld
RAIN_CLEAN_MM = 5.0          # threshold considered "cleaning" rain
BLENDED_VALUE = 0.315        # $/kWh blended solar value (given)

# ---------------------------------------------------------------------------
# 1. Precipitation (verbatim nonzero days from ACIS gauge, inches)
# ---------------------------------------------------------------------------
PRECIP_IN = {
    # 2024-01-01..2024-08-12 (for the 8/12/2024 cleaning sanity check)
    "2024-01-03": 0.15, "2024-01-07": 0.06, "2024-01-11": 0.01,
    "2024-01-20": 0.61, "2024-01-21": 0.44, "2024-01-22": 2.17,
    "2024-01-23": 0.07, "2024-01-25": 0.01,
    "2024-02-01": 1.19, "2024-02-02": 0.12, "2024-02-04": 0.01,
    "2024-02-05": 2.01, "2024-02-06": 0.86, "2024-02-07": 0.54,
    "2024-02-08": 0.31, "2024-02-09": 0.21, "2024-02-19": 0.01,
    "2024-02-20": 0.66, "2024-02-21": 0.15,
    "2024-03-01": 0.01, "2024-03-02": 0.20, "2024-03-03": 0.04,
    "2024-03-04": 0.03, "2024-03-11": 0.01, "2024-03-12": 0.07,
    "2024-03-15": 0.18, "2024-03-18": 0.01, "2024-03-23": 0.08,
    "2024-03-24": 0.18, "2024-03-30": 1.01, "2024-03-31": 0.34,
    "2024-04-05": 0.12, "2024-04-13": 0.13, "2024-04-14": 0.12,
    "2024-04-24": 0.01, "2024-04-25": 0.04, "2024-05-05": 0.04,
    "2024-05-24": 0.01,
    # 2025-01-01..2025-07-23
    "2025-01-26": 0.41, "2025-01-27": 0.30,
    "2025-02-05": 0.01, "2025-02-06": 0.04, "2025-02-07": 0.18,
    "2025-02-12": 0.26, "2025-02-13": 1.24, "2025-02-14": 0.01,
    "2025-03-02": 0.01, "2025-03-05": 0.35, "2025-03-06": 0.78,
    "2025-03-11": 0.49, "2025-03-12": 0.05, "2025-03-13": 0.96,
    "2025-03-14": 0.28, "2025-03-17": 0.04, "2025-03-28": 0.01,
    "2025-03-31": 0.14, "2025-04-01": 0.02, "2025-04-02": 0.01,
    "2025-04-17": 0.06, "2025-04-18": 0.02, "2025-04-26": 0.11,
    "2025-05-03": 0.13, "2025-05-04": 0.08, "2025-05-05": 0.04,
    "2025-05-17": 0.02, "2025-06-03": 0.02, "2025-07-18": 0.07,
    # 2025-07-24..2026-07-23 (analysis window)
    "2025-08-28": 0.04, "2025-09-17": 0.07, "2025-09-18": 0.15,
    "2025-09-21": 0.02, "2025-10-14": 0.45,
    "2025-11-15": 0.93, "2025-11-16": 0.04, "2025-11-17": 0.83,
    "2025-11-18": 0.17, "2025-11-20": 0.23, "2025-11-21": 0.16,
    "2025-11-22": 0.01,
    "2025-12-23": 0.06, "2025-12-24": 0.50, "2025-12-26": 0.48,
    "2025-12-27": 0.01, "2025-12-31": 0.38,
    "2026-01-01": 0.87, "2026-01-02": 0.12, "2026-01-03": 0.13,
    "2026-01-04": 0.94, "2026-01-05": 0.34, "2026-01-22": 0.02,
    "2026-01-23": 0.02,
    "2026-02-11": 0.32, "2026-02-16": 1.06, "2026-02-17": 0.32,
    "2026-02-18": 0.34, "2026-02-19": 0.43,
    "2026-04-11": 0.04, "2026-04-12": 0.02, "2026-04-13": 0.07,
    "2026-04-21": 0.01, "2026-04-25": 0.16, "2026-04-26": 0.16,
    "2026-05-04": 0.01, "2026-05-05": 0.15, "2026-05-29": 0.03,
    "2026-06-28": 0.01,
}
PRECIP_MM = {date.fromisoformat(k): v * 25.4 for k, v in PRECIP_IN.items()}


def precip_mm(d):
    return PRECIP_MM.get(d, 0.0)


# ---------------------------------------------------------------------------
# 2. Production data
# ---------------------------------------------------------------------------
def load_pvoutput(path):
    out = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            out[date.fromisoformat(row["date"])] = float(row["generated_kwh"])
    return out


def load_enphase(path):
    out = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            ds = row["Date/Time"].strip()
            if ds.count("/") != 2:  # skip "Total" footer row
                continue
            m, d, y = ds.split("/")
            try:
                val = float(row["Energy Delivered (kWh)"])
            except ValueError:
                continue
            out[date(int(y), int(m), int(d))] = val
    return out


# ---------------------------------------------------------------------------
# 3. Clear-sky daily GHI (Haurwitz), pure solar geometry — deterministic
# ---------------------------------------------------------------------------
def clearsky_ghi_kwh_m2(d, lat_deg=LAT, step_s=120):
    doy = d.timetuple().tm_yday
    # Spencer declination
    g = 2 * math.pi / 365 * (doy - 1)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    lat = math.radians(lat_deg)
    total_wh = 0.0
    t = 0.0
    while t < 86400:
        hour_angle = math.radians((t / 3600.0 - 12.0) * 15.0)  # solar time
        cosz = (math.sin(lat) * math.sin(decl)
                + math.cos(lat) * math.cos(decl) * math.cos(hour_angle))
        if cosz > 0:
            ghi = 1098.0 * cosz * math.exp(-0.057 / cosz)  # Haurwitz W/m2
            total_wh += ghi * step_s / 3600.0
        t += step_s
    return total_wh / 1000.0  # kWh/m2/day


# ---------------------------------------------------------------------------
# 4. Statistics helpers (pure python: t-dist p-value via incomplete beta)
# ---------------------------------------------------------------------------
def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-9, 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delt = d * c
        h *= delt
        if abs(delt - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_pvalue(t, dof):
    return _betai(dof / 2.0, 0.5, dof / (dof + t * t))


def ols(X, y):
    """Multiple OLS via normal equations (small k). Returns beta, se, resid."""
    n, k = len(y), len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)]
           for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    # Gauss-Jordan inverse
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(k)]
           for i, row in enumerate(XtX)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(aug[r][col]))
        aug[col], aug[piv] = aug[piv], aug[col]
        pv = aug[col][col]
        aug[col] = [v / pv for v in aug[col]]
        for r in range(k):
            if r != col:
                f = aug[r][col]
                aug[r] = [v - f * w for v, w in zip(aug[r], aug[col])]
    inv = [row[k:] for row in aug]
    beta = [sum(inv[a][b] * Xty[b] for b in range(k)) for a in range(k)]
    resid = [y[i] - sum(X[i][b] * beta[b] for b in range(k)) for i in range(n)]
    dof = n - k
    s2 = sum(r * r for r in resid) / dof if dof > 0 else float("nan")
    se = [math.sqrt(s2 * inv[a][a]) for a in range(k)]
    return beta, se, resid, dof


def median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# ---------------------------------------------------------------------------
# 5. Build daily table for the analysis window
# ---------------------------------------------------------------------------
def build_table(prod, start, end):
    rows = []
    d = start
    while d <= end:
        if d in prod and prod[d] > 5.0:  # drop outage/near-zero days
            cs = clearsky_ghi_kwh_m2(d)
            rows.append({"date": d, "kwh": prod[d], "cs": cs,
                         "perf": prod[d] / cs, "rain": precip_mm(d)})
        d += timedelta(days=1)
    return rows


def flag_clear_days(rows, half_window=10, frac=0.95, pctl=0.90):
    """Clear day = perf within `frac` of the local (±window) 90th pctile."""
    by_date = {r["date"]: r for r in rows}
    for r in rows:
        window = []
        for k in range(-half_window, half_window + 1):
            rr = by_date.get(r["date"] + timedelta(days=k))
            if rr:
                window.append(rr["perf"])
        window.sort()
        ref = window[min(len(window) - 1, int(pctl * len(window)))]
        r["clear"] = r["perf"] >= frac * ref
    return rows


def days_since_rain(d, threshold=RAIN_CLEAN_MM, search_back=500):
    for k in range(1, search_back + 1):
        if precip_mm(d - timedelta(days=k)) >= threshold:
            return k
    return None


# ---------------------------------------------------------------------------
# 6. Main analysis
# ---------------------------------------------------------------------------
def main():
    start, end = date(2025, 7, 24), date(2026, 7, 23)
    pv = load_pvoutput(os.path.join(HERE, "pvoutput_daily.csv"))
    en = load_enphase(os.path.join(HERE, "prod_daily_official.csv"))

    results = {
        "meta": {
            "array_kw": 10.05,
            "location": "SDG&E Coastal climate zone",
            "window": [str(start), str(end)],
            "precip_source": ("NOAA/RCC ACIS data.rcc-acis.org StnData, "
                              "nearby coastal airport gauge (station ID withheld), fetched 2026-07-24"),
            "open_meteo_note": ("Open-Meteo archive API returned empty bodies "
                                "via the permitted web_fetch tool for all "
                                "attempted query variants; shortwave_radiation"
                                "_sum therefore NOT fetched. Normalization "
                                "uses deterministic Haurwitz clear-sky GHI + "
                                "clear-day filter + seasonal harmonics."),
            "rain_clean_threshold_mm": RAIN_CLEAN_MM,
            "blended_value_usd_per_kwh": BLENDED_VALUE,
        }
    }

    # cross-check the two production sources
    common = sorted(set(pv) & set(en))
    diffs = [pv[d] - en[d] for d in common]
    mpv = sum(pv[d] for d in common) / len(common)
    men = sum(en[d] for d in common) / len(common)
    results["production_crosscheck"] = {
        "n_common_days": len(common),
        "pvoutput_mean_kwh": round(mpv, 2),
        "enphase_mean_kwh": round(men, 2),
        "mean_abs_diff_kwh": round(sum(abs(x) for x in diffs) / len(diffs), 3),
    }

    tables = {}
    for name, prod in (("pvoutput", pv), ("enphase", en)):
        rows = flag_clear_days(build_table(prod, start, end))
        tables[name] = rows

    # ---- rain events (>=5mm day after >=30 dry days; wet clusters merged)
    # derived from the fetched precip record
    events = [
        {"id": "E1_2025-10-14", "wet_start": date(2025, 10, 14),
         "wet_end": date(2025, 10, 14), "event_mm": round(0.45 * 25.4, 1)},
        {"id": "E2_2025-11-15_22", "wet_start": date(2025, 11, 15),
         "wet_end": date(2025, 11, 22),
         "event_mm": round(sum(precip_mm(date(2025, 11, 15) + timedelta(k))
                               for k in range(8)), 1)},
        {"id": "E3_2025-12-23_2026-01-05", "wet_start": date(2025, 12, 23),
         "wet_end": date(2026, 1, 5),
         "event_mm": round(sum(precip_mm(date(2025, 12, 23) + timedelta(k))
                               for k in range(14)), 1)},
        {"id": "E4_2026-02-11_19", "wet_start": date(2026, 2, 11),
         "wet_end": date(2026, 2, 19),
         "event_mm": round(sum(precip_mm(date(2026, 2, 11) + timedelta(k))
                               for k in range(9)), 1)},
    ]
    for ev in events:
        ev["dry_days_before"] = days_since_rain(ev["wet_start"])

    # ---- per-event pre/post comparison (clear days, 10-day windows)
    def event_recovery(rows, ev, use_resid=None):
        by_date = {r["date"]: r for r in rows}
        pre, post = [], []
        for k in range(1, 11):
            r = by_date.get(ev["wet_start"] - timedelta(days=k))
            if r and r["clear"]:
                if use_resid is None:
                    pre.append(r["perf"])
                elif r["date"] in use_resid:
                    pre.append(use_resid[r["date"]])
            r = by_date.get(ev["wet_end"] + timedelta(days=k))
            if r and r["clear"]:
                if use_resid is None:
                    post.append(r["perf"])
                elif r["date"] in use_resid:
                    post.append(use_resid[r["date"]])
        if len(pre) >= 2 and len(post) >= 2:
            if use_resid is not None:  # residuals are in log space
                rec = math.exp(median(post) - median(pre)) - 1
            else:
                rec = median(post) / median(pre) - 1
            return rec, len(pre), len(post)
        return None, len(pre), len(post)

    # ---- seasonal-harmonic + days-since-rain regression on clear days
    def run_regression(rows):
        X, y, dates_used = [], [], []
        for r in rows:
            if not r["clear"]:
                continue
            dsr = days_since_rain(r["date"])
            if dsr is None:
                continue
            doy = r["date"].timetuple().tm_yday
            w = 2 * math.pi * doy / 365.25
            X.append([1.0, math.sin(w), math.cos(w),
                      math.sin(2 * w), math.cos(2 * w), float(dsr)])
            y.append(math.log(r["perf"]))
            dates_used.append(r["date"])
        beta, se, resid, dof = ols(X, y)
        c, sec = beta[5], se[5]
        tstat = c / sec
        p = t_pvalue(abs(tstat), dof)
        r_partial = tstat / math.sqrt(tstat * tstat + dof)
        rate_day = -(math.exp(c) - 1)             # fractional loss/day
        rate_month = -(math.exp(c * 30.44) - 1)   # fractional loss/month
        # simple (unadjusted) regression for transparency
        Xs = [[row[0], row[5]] for row in X]
        bs, ses, _, dofs = ols(Xs, y)
        ts = bs[1] / ses[1]
        simple = {
            "rate_pct_per_month": -(math.exp(bs[1] * 30.44) - 1) * 100,
            "r": ts / math.sqrt(ts * ts + dofs),
            "p": t_pvalue(abs(ts), dofs),
        }
        # log-perf with harmonic seasonality removed, dsr effect RETAINED
        resid_keep_dsr = {}
        for i, d in enumerate(dates_used):
            harm = beta[0] + sum(b * x for b, x in zip(beta[1:5], X[i][1:5]))
            resid_keep_dsr[d] = y[i] - harm
        # collinearity diagnostic: VIF of dsr vs the harmonic terms
        Xh = [row[:5] for row in X]
        yd = [row[5] for row in X]
        bh, _, rh, _ = ols(Xh, yd)
        ssr = sum(v * v for v in rh)
        mu = sum(yd) / len(yd)
        sst = sum((v - mu) ** 2 for v in yd)
        r2 = 1 - ssr / sst
        vif = 1.0 / (1 - r2) if r2 < 1 else float("inf")
        return {
            "n": len(y), "dof": dof,
            "coef_ln_per_day": c, "se": sec, "t": tstat, "p": p,
            "r_partial": r_partial,
            "rate_pct_per_day": rate_day * 100,
            "rate_pct_per_month": rate_month * 100,
            "simple_unadjusted": simple,
            "vif_dsr_vs_harmonics": vif,
        }, resid_keep_dsr

    for name in ("pvoutput", "enphase"):
        rows = tables[name]
        reg, resid_map = run_regression(rows)
        ev_out = []
        for ev in events:
            rec, npre, npost = event_recovery(rows, ev)
            rec_adj, _, _ = event_recovery(rows, ev, use_resid=resid_map)
            implied = None
            if rec_adj is not None and ev["dry_days_before"]:
                implied = (rec_adj / (1 + rec_adj)
                           / ev["dry_days_before"] * 30.44 * 100)
            ev_out.append({
                **{k: str(v) if isinstance(v, date) else v
                   for k, v in ev.items()},
                "n_clear_pre": npre, "n_clear_post": npost,
                "recovery_pct_raw": None if rec is None else round(rec * 100, 2),
                "recovery_pct_seasonal_adj": (None if rec_adj is None
                                              else round(rec_adj * 100, 2)),
                "implied_soiling_rate_pct_per_month": (
                    None if implied is None else round(implied, 2)),
            })
        monthly = {}
        for r in rows:
            if r["clear"]:
                monthly.setdefault(r["date"].strftime("%Y-%m"), []).append(
                    r["perf"])
        results[name] = {
            "n_days_used": len(rows),
            "n_clear_days": sum(1 for r in rows if r["clear"]),
            "monthly_median_clearday_perf_kwh_per_kwhm2": {
                k: round(median(v), 3) for k, v in sorted(monthly.items())},
            "events": ev_out,
            "regression": {
                "n": reg["n"], "dof": reg["dof"],
                "vif_dsr_vs_harmonics": round(reg["vif_dsr_vs_harmonics"], 1),
                "rate_pct_per_month": round(reg["rate_pct_per_month"], 3),
                "rate_pct_per_day": round(reg["rate_pct_per_day"], 4),
                "t": round(reg["t"], 2), "p": float(f'{reg["p"]:.3g}'),
                "r_partial": round(reg["r_partial"], 3),
                "simple_unadjusted_rate_pct_per_month":
                    round(reg["simple_unadjusted"]["rate_pct_per_month"], 3),
                "simple_unadjusted_r":
                    round(reg["simple_unadjusted"]["r"], 3),
                "simple_unadjusted_p":
                    float(f'{reg["simple_unadjusted"]["p"]:.3g}'),
            },
        }

    # ---- sanity check vs verified 8/12/2024 cleaning (+11.8% diff-in-diff)
    clean_date = date(2024, 8, 12)
    dsr_2024 = days_since_rain(clean_date)  # from fetched 2024 gauge record
    rate = results["pvoutput"]["regression"]["rate_pct_per_month"]
    predicted = rate / 30.44 * dsr_2024 if dsr_2024 else None
    cleaning_gain_known = 11.8  # % gain after cleaning (verified prior work)
    soiling_known = cleaning_gain_known / (100 + cleaning_gain_known) * 100
    results["sanity_check_2024_cleaning"] = {
        "cleaning_date": str(clean_date),
        "known_cleaning_gain_pct": cleaning_gain_known,
        "known_implied_soiling_loss_pct": round(soiling_known, 1),
        "dry_days_before_cleaning_ge5mm": dsr_2024,
        "last_ge5mm_rain_before": "2024-03-31 (8.6 mm)",
        "predicted_soiling_from_2025_26_rate_pct":
            None if predicted is None else round(predicted, 1),
        "known_rate_equiv_pct_per_month":
            round(soiling_known / dsr_2024 * 30.44, 2) if dsr_2024 else None,
    }

    # ---- annual economics: day-by-day soiling model s(t)=rate*dsr (capped)
    def annual_loss(rate_pct_month, cap_loss_frac):
        rate_frac_day = rate_pct_month / 100.0 / 30.44
        lost = tot = 0.0
        d = start
        while d <= end:
            if d in pv:
                tot += pv[d]
                dsr = days_since_rain(d)
                if dsr is not None:
                    s = max(min(rate_frac_day * dsr, cap_loss_frac), 0.0)
                    if s < 1:
                        lost += pv[d] * s / (1 - s)
            d += timedelta(days=1)
        return lost, tot

    caps = [ev["recovery_pct_seasonal_adj"]
            for ev in results["pvoutput"]["events"]
            if ev["recovery_pct_seasonal_adj"] is not None]
    cap_a = max(c / (100 + c) for c in caps) if caps else None
    lost_a, total_kwh = annual_loss(rate, cap_a if cap_a else 1.0)
    # Scenario B: rate & cap implied by the verified 2024 cleaning
    rate_b = results["sanity_check_2024_cleaning"]["known_rate_equiv_pct_per_month"]
    cap_b = results["sanity_check_2024_cleaning"]["known_implied_soiling_loss_pct"] / 100.0
    lost_b, _ = annual_loss(rate_b, cap_b)
    results["annual_economics"] = {
        "annual_generation_kwh": round(total_kwh, 0),
        "soiling_model": ("loss(t) = rate x days-since-rain(>=5mm), capped; "
                          "lost kWh = prod x s/(1-s) summed daily"),
        "scenario_A_this_years_evidence": {
            "rate_pct_per_month": rate,
            "cap_loss_pct": round(cap_a * 100, 2) if cap_a else None,
            "annual_lost_kwh": round(lost_a, 0),
            "annual_lost_usd_at_0.315": round(lost_a * BLENDED_VALUE, 0),
        },
        "scenario_B_2024_cleaning_evidence": {
            "rate_pct_per_month": rate_b,
            "cap_loss_pct": round(cap_b * 100, 1),
            "annual_lost_kwh": round(lost_b, 0),
            "annual_lost_usd_at_0.315": round(lost_b * BLENDED_VALUE, 0),
        },
        "caveat": ("Scenario A assumes no manual cleaning occurred in the "
                   "2025-26 window; whether one occurred before/within the "
                   "window is not determined from available data. The small "
                   "recovery at E1 (after 214 dry days) vs the 10.6% soiling "
                   "verified at the 2024-08-12 cleaning (134 dry days) "
                   "suggests either a 2025 cleaning, partial cleaning by the "
                   "11 mm Oct rain, or soiling saturation."),
    }

    out = os.path.join(HERE, "soiling_results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
