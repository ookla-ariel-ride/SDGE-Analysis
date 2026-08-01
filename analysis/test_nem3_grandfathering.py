#!/usr/bin/env python3
"""Behavioural tests for nem3_grandfathering.py (issue #9, Phase 1).

nem3_grandfathering.py imports behavior_rebuild.py at module top level, which
reads private/household.yaml at ITS OWN module top level and fails closed
(SystemExit) if that file is absent -- the same situation
test_carbon_dispatch_tradeoff.py and test_carbon_fullyear.py already solved.
Applied here too: point household.PATH at a synthetic, invented household
BEFORE importing, so this whole file imports cleanly on any checkout, private/
or not. Cases that need the REAL measured Green Button year (byte-identical
regeneration) or the REAL raw MIDAS archive (rate-table traceability) still
gate on their own precondition and SKIP rather than fail when this checkout
lacks them, matching test_carbon_dispatch_tradeoff.py's SkipCase convention.

Run from the repo root:  ./.venv/bin/python analysis/test_nem3_grandfathering.py
"""
import glob
import json
import pathlib
import re
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Same fix as test_carbon_dispatch_tradeoff.py, for the same reason: point the
# intake loader at a synthetic, invented household before the transitive import
# of behavior_rebuild fires. Values are invented; nothing here depends on them
# except the cases that explicitly load the real archive below.
import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import rates as R                     # noqa: E402
import behavior_rebuild as br         # noqa: E402
import nem3_grandfathering as NG      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button archive, or no raw MIDAS archive). Counted as
    neither pass nor fail."""


def _require_archive():
    """Only for cases that need the REAL measured year (br.load() on the
    actual data, or a byte-identical regeneration of the committed JSON built
    from it). The module import above already succeeded unconditionally using
    the synthetic household, so this gates DATA, not importability."""
    files = sorted(glob.glob(USAGE_GLOB))
    if not files or not HOUSEHOLD_YAML.is_file():
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and "
                       f"{HOUSEHOLD_YAML}, neither of which this checkout has")
    br.CSV = files[0]
    return files[0]


def _require_raw_midas():
    """Only for the rate-table traceability case: needs the raw (gitignored,
    38 MB each) MIDAS files, not just the committed condensed CSV."""
    missing = [str(p) for p in NG.RAW_FILES.values() if not p.exists()]
    if missing:
        raise SkipCase(f"needs the raw MIDAS archive(s) {missing}, which this "
                       "checkout does not have")


EPS = 1e-6


# ---------------------------------------------------------------------------
# (a) the committed rate CSV parses and has the expected RateName/RIN shape
# ---------------------------------------------------------------------------
@case
def case_committed_rate_csv_has_expected_vintages_and_shape():
    """No real archive needed: this is a property of the committed public CSV
    alone. Checks the full universe this generator relies on (2 vintages x 2
    components x 12 months x 2 day-types x 24 hours = 2,304 rows) and that
    every row is traceable back to a source RateLookupID (RIN)/ValueName, per
    the scope box's traceability requirement."""
    tab = pd.read_csv(NG.RATE_CSV)
    assert set(tab["rate_name"].unique()) == {"NBT00", "NBT26"}, tab["rate_name"].unique()
    assert set(tab["component"].unique()) == {"delivery", "generation"}, tab["component"].unique()
    assert set(tab["month"].unique()) == set(NG.MONTHS), tab["month"].unique()
    assert set(tab["day_type"].unique()) == {"Weekday", "Weekend"}, tab["day_type"].unique()
    assert set(tab["hour"].unique()) == set(range(24)), sorted(tab["hour"].unique())
    assert len(tab) == 2 * 2 * 12 * 2 * 24 == 2304, len(tab)
    # no duplicate buckets
    key = ["rate_name", "component", "month", "day_type", "hour"]
    assert not tab.duplicated(subset=key).any(), "duplicate bucket in the committed CSV"
    # traceability: every row names its source RateLookupID (RIN) and ValueName
    assert tab["rate_lookup_id"].notna().all()
    assert tab["source_value_name"].notna().all()
    rin_pat = re.compile(r"^USCA-(SDXX|XXSD)-NB(00|26)-0000$")
    assert tab["rate_lookup_id"].apply(lambda s: bool(rin_pat.match(s))).all(), \
        "a rate_lookup_id does not match the expected MIDAS RIN pattern"
    vn_pat = re.compile(r"^[A-Za-z]{3} (Weekday|Weekend) HS\d+$")
    assert tab["source_value_name"].apply(lambda s: bool(vn_pat.match(s))).all(), \
        "a source_value_name does not match the expected MIDAS ValueName pattern"
    # RIN's component substring agrees with our own component classification
    sdxx_is_delivery = tab.loc[tab["rate_lookup_id"].str.contains("SDXX"), "component"]
    xxsd_is_generation = tab.loc[tab["rate_lookup_id"].str.contains("XXSD"), "component"]
    assert (sdxx_is_delivery == "delivery").all()
    assert (xxsd_is_generation == "generation").all()
    return f"{len(tab)} rows, vintages {sorted(tab['rate_name'].unique())}, all traceable to a RIN+ValueName"


@case
def case_nbt26_and_nbt00_are_identical_for_this_tariff_year():
    """The documented, flagged finding: both vintages' TARIFF_YEAR schedules
    coincide exactly, which is WHY the grandfathering-value band this
    generator publishes has zero width. If a future MIDAS refresh ever makes
    them diverge, this case (not just prose) will catch it."""
    tab = pd.read_csv(NG.RATE_CSV)
    piv = tab.pivot_table(index=["component", "month", "day_type", "hour"],
                          columns="rate_name", values="value_usd_per_kwh")
    diff = (piv["NBT00"] - piv["NBT26"]).abs()
    assert diff.max() < EPS, f"vintages diverge by up to {diff.max()}"
    return f"NBT00 vs NBT26: max|diff| = {diff.max()} across {len(piv)} cells"


# ---------------------------------------------------------------------------
# (b) hand-built synthetic export series with a hand-computed expected cost
# ---------------------------------------------------------------------------
@case
def case_bill_nbt_matches_hand_computation():
    """Two rows, two months, two TOU periods, two seasons -- picked so every
    term in bill_nbt() (BSC per distinct day per ym, gross import at
    rates.allin(season, period), and the export credit aggregated by (month,
    day_type, hour) bucket) is exercised and independently hand-computed."""
    rows = pd.DataFrame({
        "dt": pd.to_datetime(["2026-01-05 08:00", "2026-08-15 18:00"]),  # Mon, Sat
        "wkend": [False, True],
        "seas": ["W", "S"],
        "imp": [10.0, 5.0],
        "exp": [2.0, 3.0],
    })
    # period(hour_frac=8, weekday) -> "off" (not 16-21, not weekend, not
    # <6 or 10<=h<14); period(hour_frac=18, weekend) -> "on" (16<=18<21, checked
    # before the weekend branch) -- cross-checked against rates.period_at below.
    rows["p"] = [R.period_at(ts) for ts in rows["dt"]]
    assert list(rows["p"]) == ["off", "on"], rows["p"].tolist()
    rows["ym"] = rows["dt"].dt.to_period("M")

    credit_lookup = {("Jan", "Weekday", 8): 0.05, ("Aug", "Weekend", 18): 0.10}
    bill, credit_total, by_bucket = NG.bill_nbt(rows, credit_lookup)

    expected_credit = 2.0 * 0.05 + 3.0 * 0.10
    assert abs(credit_total - expected_credit) < EPS, (credit_total, expected_credit)
    assert by_bucket.sum() == rows["exp"].sum()

    expected_bill = (
        2 * R.BSC                       # one distinct day in each of 2 ym groups
        + 10.0 * R.allin("W", "off")    # row 0's gross import
        + 5.0 * R.allin("S", "on")      # row 1's gross import
        - expected_credit)
    assert abs(bill - expected_bill) < EPS, (bill, expected_bill)
    return (f"bill=${bill:.4f} matches hand computation ${expected_bill:.4f} "
           f"(export credit ${credit_total:.2f})")


# ---------------------------------------------------------------------------
# (c) fail-closed: a missing bucket aborts, never interpolates
# ---------------------------------------------------------------------------
@case
def case_load_rate_table_aborts_on_incomplete_vintage():
    """Delete one row from a temp copy of the committed CSV and confirm
    load_rate_table() refuses rather than silently treating the missing
    (month, day_type, hour) bucket as zero or interpolated."""
    real_path = NG.RATE_CSV
    tab = pd.read_csv(real_path)
    truncated = tab[~((tab["rate_name"] == "NBT00") & (tab["component"] == "delivery")
                       & (tab["month"] == "Jan") & (tab["day_type"] == "Weekday")
                       & (tab["hour"] == 0))]
    assert len(truncated) == len(tab) - 1
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".csv")[1])
    truncated.to_csv(tmp, index=False)
    NG.RATE_CSV = tmp
    try:
        NG.load_rate_table()
    except SystemExit as exc:
        # Dropping one row leaves that (month, day_type, hour) index entry in
        # place (the OTHER component's row for the same bucket still supplies
        # it) but with a NaN delivery value -- a different, still-correct
        # fail-closed message than an outright missing bucket count.
        assert "NaN" in str(exc) or "missing" in str(exc), f"wrong refusal message: {exc}"
        return "a truncated rate table aborts load_rate_table(), naming the gap"
    else:
        raise AssertionError("a rate table missing a bucket was accepted")
    finally:
        NG.RATE_CSV = real_path
        tmp.unlink(missing_ok=True)


@case
def case_bill_nbt_aborts_on_a_bucket_the_household_data_touches_but_the_table_lacks():
    """Same fail-closed contract, exercised at the bill_nbt() call site
    directly: a complete-looking credit_lookup that is simply missing the ONE
    bucket this household's synthetic data actually needs must abort bill_nbt,
    not silently price that export at zero."""
    rows = pd.DataFrame({
        "dt": pd.to_datetime(["2026-03-10 10:00"]),   # Tue -> weekday
        "wkend": [False],
        "seas": ["W"],
        "imp": [1.0],
        "exp": [1.0],
        "p": ["sop"],
        "ym": pd.to_datetime(["2026-03-10"]).to_period("M"),
    })
    empty_lookup = {}   # deliberately missing ("Mar", "Weekday", 10)
    try:
        NG.bill_nbt(rows, empty_lookup)
    except SystemExit as exc:
        assert "not in the rate table" in str(exc), f"wrong refusal message: {exc}"
        return "bill_nbt refuses a bucket absent from the credit lookup"
    else:
        raise AssertionError("bill_nbt priced a bucket absent from the lookup")


# ---------------------------------------------------------------------------
# (d) byte-identical regeneration (CLAUDE.md 9) -- needs the real archive
# ---------------------------------------------------------------------------
@case
def case_artifact_regenerates_byte_identically():
    _require_archive()
    path = NG.RESULTS_JSON
    before = path.read_bytes()
    NG.main()
    after = path.read_bytes()
    assert after == before, "data/nem3_grandfathering.json is not reproducible"
    return "data/nem3_grandfathering.json regenerates byte-identically"


@case
def case_grandfathering_value_is_in_the_right_ballpark_vs_the_old_bracket():
    """Sanity check (not a tautology): the real hourly-priced figure should be
    a plausible number for this household, not off by an order of magnitude
    from the old flat-cent bracket it is replacing. A >=5x gap would indicate
    a units or aggregation bug, not a genuine finding (per the issue brief)."""
    _require_archive()
    result = json.loads(NG.RESULTS_JSON.read_text())
    lo = result["grandfathering_value_range_usd_per_yr"]["low"]
    hi = result["grandfathering_value_range_usd_per_yr"]["high"]
    old_lo = result["old_bracket_for_context"]["low_usd_yr"]
    old_hi = result["old_bracket_for_context"]["high_usd_yr"]
    assert 0 < lo <= hi, (lo, hi)
    assert lo > old_lo / 5 and hi < old_hi * 5, (
        f"new range ${lo}-${hi} is >5x away from the old ${old_lo}-${old_hi} "
        "bracket -- investigate before trusting this as a genuine finding")
    return f"new range ${lo:,.0f}-${hi:,.0f}/yr vs old ${old_lo:,}-${old_hi:,}/yr -- same order of magnitude"


@case
def case_build_rate_table_reproduces_the_committed_csv():
    """Traceability from the raw MIDAS files to the committed CSV: rebuild
    into a temp path (never touching the real committed file) and confirm it
    matches byte-for-byte. Needs the raw (gitignored) MIDAS archive."""
    _require_raw_midas()
    real_path = NG.RATE_CSV
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".csv")[1])
    NG.RATE_CSV = tmp
    try:
        NG.build_rate_table()
        rebuilt = tmp.read_bytes()
        committed = real_path.read_bytes()
        assert rebuilt == committed, (
            "rebuilding from the raw MIDAS files does not reproduce the "
            "committed data/nbt_export_rates_2026.csv byte-for-byte")
    finally:
        NG.RATE_CSV = real_path
        tmp.unlink(missing_ok=True)
    return "rebuilding from the raw MIDAS archive reproduces the committed CSV byte-for-byte"


def main():
    listed = [c.__name__ for c in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
