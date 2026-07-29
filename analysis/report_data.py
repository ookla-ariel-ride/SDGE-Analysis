#!/usr/bin/env python3
"""Regenerate data/report_data.json — the artifact behind the report's §5 charts.

This file previously had no committed generator. It was the output of an as-run
extended variant of analyze.py that was never committed, so every pipeline
refresh left it silently stale while the prose around it moved: after the
2026-07-27 interval correction the §5 periods chart carried corrected kWh next to
pre-correction dollars, and the paragraph above it explained a ~40 kWh gap that no
longer existed. An artifact nobody can rebuild is an artifact nobody can trust.

Basis. Everything here now comes from the canonical module. Day types come from
rates.off_peak_day, so the eight bill-confirmed tariff holidays apply, and prices
come from rates.allin / rates.energy / rates.credit. That resolves the old split
where the charts ran on published table rates while every dollar in the prose ran
on bill-derived ones.

Two different cost conventions appear below, and they are not interchangeable:

  gross import cost  imports x the marginal all-in rate, ignoring exports. This is
                     what the periods chart plots, and what the "17% of the energy
                     causes ~42% of gross import cost" headline measures.
  netted energy cost imports less exports within each (month, season, TOU) bucket,
                     priced at the energy rate when positive and the export credit
                     when negative. This is the monthly series, and it excludes the
                     non-bypassable charges and the daily fixed charge, which is
                     why it does not equal rates.bill_nem_monthly.

Dropped deliberately: the old file also carried `ev`, `battery`,
`shift_25pct_savings` and `shift_50pct_savings`. Those are superseded by
behavior_rebuild.json (session-level EV detection, physical shifting) and
battery_dispatch_policies.json (dispatch simulation), which model the same things
properly and are what the report actually cites. Keeping worse duplicates of live
figures in a second artifact is how they drift apart.

Run from the private/verify sandbox with the Green Button export as usage.csv.
"""
import datetime as dt
import json
import os
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates as R
import behavior_rebuild as br

SEASONS = ("S", "W")
PERIODS = ("sop", "off", "on")


def _repo_root():
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")


def build(d):
    """All report-facing series, from the same frame the rest of the pipeline uses."""
    d = d.copy()
    # Interval completeness is enforced in behavior_rebuild.load(), the shared
    # loader every frame here comes through; see rates.validate_interval_coverage.
    d["hour_i"] = d.dt.dt.hour
    d["gross_cost"] = [imp * R.allin(s, p)
                       for imp, s, p in zip(d.Consumption, d.seas, d.p)]

    out = {"rates_ev5": {s: {p: round(R.allin(s, p), 5) for p in PERIODS}
                         for s in SEASONS}}

    # Hour-of-day means: total kWh in that hour divided by the number of days in
    # the season, i.e. the average day the chart draws.
    for s in SEASONS:
        sub = d[d.seas == s]
        ndays = sub.dt.dt.date.nunique()
        hr = sub.groupby("hour_i")[["Consumption", "Generation"]].sum()
        hr = hr.reindex(range(24), fill_value=0.0)
        out[f"hourly_{s}"] = {"imp": [round(v / ndays, 3) for v in hr.Consumption],
                              "exp": [round(v / ndays, 3) for v in hr.Generation]}

    # Monthly: gross imports and exports, plus the netted energy cost.
    months = sorted(d.ym.unique())
    labels, mimp, mexp, mcost = [], [], [], []
    for ym in months:
        m = d[d.ym == ym]
        cost = 0.0
        for s in SEASONS:
            for p in PERIODS:
                b = m[(m.seas == s) & (m.p == p)]
                net = b.Consumption.sum() - b.Generation.sum()
                cost += net * (R.energy(s, p) if net >= 0 else R.credit(s, p))
        labels.append(str(ym))
        mimp.append(round(float(m.Consumption.sum()), 1))
        mexp.append(round(float(m.Generation.sum()), 1))
        mcost.append(round(cost))
    out["monthly"] = {"labels": labels, "imp": mimp, "exp": mexp, "cost": mcost}

    # Per season x period: gross flows, netted cost, and the gross import cost the
    # periods chart plots.
    split = []
    for s in SEASONS:
        for p in ("off", "on", "sop"):
            b = d[(d.seas == s) & (d.p == p)]
            if b.empty:
                continue
            net = b.Consumption.sum() - b.Generation.sum()
            split.append({"seas": s, "p": p,
                          "imp": round(float(b.Consumption.sum())),
                          "exp": round(float(b.Generation.sum())),
                          "cost": round(net * (R.energy(s, p) if net >= 0
                                               else R.credit(s, p)))})
    out["period_split"] = split

    # The periods chart itself, ordered as it is drawn.
    out["periods_chart"] = {
        "order": ["sop", "off", "on"],
        "import_kwh": [round(float(d[d.p == p].Consumption.sum())) for p in PERIODS],
        "import_cost": [round(float(d[d.p == p].gross_cost.sum())) for p in PERIODS],
        "import_share": [round(float(d[d.p == p].Consumption.sum()
                                     / d.Consumption.sum()), 3) for p in PERIODS]}

    on = d[d.p == "on"]
    gross_total = float(d.gross_cost.sum())
    out["onpeak"] = {"import_kwh": round(float(on.Consumption.sum()), 1),
                     "import_cost": round(float(on.gross_cost.sum())),
                     "export_kwh": round(float(on.Generation.sum()), 1),
                     "share_of_energy_cost": round(float(on.gross_cost.sum()
                                                         / gross_total), 3)}

    for s in SEASONS:
        sub = d[(d.seas == s) & (d.hour_i.between(16, 20))]
        ndays = d[d.seas == s].dt.dt.date.nunique()
        g = sub.groupby("hour_i").Consumption.sum()
        out[f"onpeak_kw_{s}"] = {str(h): round(float(g.get(h, 0.0)) / ndays, 2)
                                 for h in range(16, 21)}

    energy_cost = sum(x["cost"] for x in split)
    out["totals"] = {"imp": round(float(d.Consumption.sum())),
                     "exp": round(float(d.Generation.sum())),
                     "net": round(float(d.Consumption.sum() - d.Generation.sum())),
                     "energy_cost": energy_cost,
                     "bsc": round(d.dt.dt.date.nunique() * R.BSC)}
    return out


def main():
    root = _repo_root()
    out = build(br.load())
    path = root / "data" / "report_data.json"
    tmp = path.with_suffix(f".json.tmp{os.getpid()}")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    pc = out["periods_chart"]
    print(f"wrote {path}")
    print(f"  periods chart kWh  {pc['import_kwh']}")
    print(f"  periods chart $    {pc['import_cost']}")
    print(f"  totals             {out['totals']}")


if __name__ == "__main__":
    main()
