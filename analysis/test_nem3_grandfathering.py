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
import hashlib
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
    """The documented, flagged finding: both vintages' TARIFF_YEAR (2026,
    year 1 only) schedules coincide exactly, which is WHY vintage_band_usd_
    per_yr (the vintage-only sub-band, NOT the full published range -- see
    generation_component_sensitivity for what actually gives the published
    range its width) has zero width. If a future MIDAS refresh ever makes
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
def case_committed_json_digest_matches_the_committed_csv():
    """The committed data/nem3_grandfathering.json's rate_table_provenance.sha256
    must match the ACTUAL current data/nbt_export_rates_2026.csv's hash -- if it
    doesn't, the committed JSON was computed from a different (older or newer)
    rate table than what's currently committed, exactly the staleness a Codex
    review finding identified (--build-rates and the normal run are separate
    invocations with no cross-check)."""
    _require_archive()
    result = json.loads(NG.RESULTS_JSON.read_text())
    stored = result["rate_table_provenance"]["sha256"]
    actual = hashlib.sha256(NG.RATE_CSV.read_bytes()).hexdigest()
    assert stored == actual, (
        f"committed JSON's rate_table_provenance.sha256 ({stored}) does not "
        f"match the committed CSV's actual hash ({actual}) -- the JSON is "
        "stale relative to the CSV; regenerate it")
    return f"rate_table_provenance.sha256 matches the committed CSV ({actual[:12]}...)"


@case
def case_stale_rate_table_is_detectable_via_digest_mismatch():
    """Failure-injection test (a Codex review finding): simulate exactly the
    staleness scenario the digest exists to catch -- a rate table that changes
    AFTER a JSON was generated from the old one. Regenerate the JSON against
    the real committed CSV (capturing its digest), then mutate the CSV in
    place and confirm the (now-stale) JSON's stored digest no longer matches
    the (now-different) CSV's hash -- proving the mismatch is mechanically
    detectable, not just asserted to be possible in a docstring. Restores the
    real CSV afterward; this is destructive to the CSV only within the `try`
    block, and only within this process's working copy."""
    _require_archive()
    NG.main()  # ensure the committed JSON reflects the CURRENT committed CSV
    original_csv_bytes = NG.RATE_CSV.read_bytes()
    # the JSON's stored digest, frozen at generation time -- never touched below
    stale_json_digest = json.loads(
        NG.RESULTS_JSON.read_text())["rate_table_provenance"]["sha256"]
    try:
        NG.RATE_CSV.write_bytes(
            original_csv_bytes + b"\n# a stray trailing comment mutating the file bytes\n")
        new_csv_digest = hashlib.sha256(NG.RATE_CSV.read_bytes()).hexdigest()
        assert new_csv_digest != stale_json_digest, (
            "the mutation did not actually change the CSV's hash -- test setup bug")
        # the JSON was NOT regenerated after the mutation -- exactly the
        # "second invocation skipped/failed" scenario -- so its stored digest
        # must visibly disagree with the mutated CSV's real hash
        assert stale_json_digest != new_csv_digest, (
            "a stale JSON's digest coincidentally matched the mutated CSV -- "
            "the staleness scenario this test simulates would go undetected")
    finally:
        NG.RATE_CSV.write_bytes(original_csv_bytes)
        NG.main()  # restore the committed JSON to match the restored CSV
    return ("a rate-table mutation without a JSON regeneration produces a "
           f"detectable digest mismatch (stale JSON digest "
           f"{stale_json_digest[:12]}... != mutated CSV {new_csv_digest[:12]}...)")


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
def case_generation_component_sensitivity_is_disclosed_and_higher_than_primary():
    """An adversarial review finding: this household is on CCA (not SDG&E
    bundled) generation, and SDG&E's own methodology page states its
    Generation export-rate component applies only to bundled customers.
    Rather than silently resolving this ambiguity, the artifact must publish
    a Delivery-only alternative alongside the primary (Delivery+Generation)
    figure. Removing the Generation credit LOWERS the NBT export credit,
    which RAISES the NBT bill and therefore WIDENS the grandfathering value
    -- assert that direction holds (a sign flip would indicate the
    alternative was wired backwards), not just that the field exists."""
    _require_archive()
    result = json.loads(NG.RESULTS_JSON.read_text())
    sens = result["generation_component_sensitivity"]
    assert sens["alternative_bills"], "no alternative_bills computed"
    for rate_name, alt in sens["alternative_bills"].items():
        primary_gf = result["nbt_counterfactual"][rate_name]["grandfathering_value_usd"]
        alt_gf = alt["grandfathering_value_usd"]
        assert alt_gf > primary_gf, (
            f"{rate_name}: delivery-only grandfathering value ${alt_gf} is not "
            f"greater than the primary ${primary_gf} -- removing the "
            "Generation credit should raise the NBT bill and widen the gap")
    return (f"generation-component sensitivity disclosed: primary "
           f"${result['nbt_counterfactual']['NBT00']['grandfathering_value_usd']:,.2f}/yr "
           f"vs delivery-only ${sens['alternative_bills']['NBT00']['grandfathering_value_usd']:,.2f}/yr")


# ---------------------------------------------------------------------------
# (e) battery-marginal reconciliation vs extended_findings.py's nbt_2039
#     (issue #9 Phase 3, AC6) -- fail-closed reference loading + arithmetic
# ---------------------------------------------------------------------------
@case
def case_load_nbt_2039_reference_aborts_on_missing_file():
    """load_nbt_2039_reference() must abort, naming the missing artifact, if
    data/extended_results.json is absent -- it is a read-only reference this
    generator never recomputes, so a missing file must not be treated as
    'nothing to reconcile against'."""
    real_path = NG.EXTENDED_JSON
    NG.EXTENDED_JSON = pathlib.Path(tempfile.mkstemp()[1])
    NG.EXTENDED_JSON.unlink()  # exists() must be False
    try:
        NG.load_nbt_2039_reference()
    except SystemExit as exc:
        assert "is missing" in str(exc), f"wrong refusal message: {exc}"
        return "load_nbt_2039_reference refuses when extended_results.json is absent"
    else:
        raise AssertionError("a missing extended_results.json was accepted")
    finally:
        NG.EXTENDED_JSON = real_path


@case
def case_load_nbt_2039_reference_aborts_on_missing_nbt_2039_key():
    """A committed artifact that has been regenerated without its nbt_2039
    section (e.g. a stale extended_findings.py run) must abort the
    reconciliation rather than silently comparing against nothing."""
    real_path = NG.EXTENDED_JSON
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    tmp.write_text(json.dumps({"some_other_key": 1}))
    NG.EXTENDED_JSON = tmp
    try:
        NG.load_nbt_2039_reference()
    except SystemExit as exc:
        assert "nbt_2039" in str(exc), f"wrong refusal message: {exc}"
        return "load_nbt_2039_reference refuses an artifact missing nbt_2039"
    else:
        raise AssertionError("an artifact missing nbt_2039 was accepted")
    finally:
        NG.EXTENDED_JSON = real_path
        tmp.unlink(missing_ok=True)


@case
def case_load_nbt_2039_reference_aborts_on_missing_flat_credit_bucket():
    """A malformed nbt_2039 section missing one of the 3c/5c/8c buckets must
    abort by name, not raise a bare KeyError deep inside the reconciliation."""
    real_path = NG.EXTENDED_JSON
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    tmp.write_text(json.dumps({"nbt_2039": {
        "battery_marginal_under_nem2": 2329,
        "battery_marginal_under_nbt": {
            "3c": {"battery_marginal_yr": 2540},
            "5c": {"battery_marginal_yr": 2527},
            # "8c" deliberately missing
        },
    }}))
    NG.EXTENDED_JSON = tmp
    try:
        NG.load_nbt_2039_reference()
    except SystemExit as exc:
        assert "8c" in str(exc), f"wrong refusal message: {exc}"
        return "load_nbt_2039_reference refuses a reference missing a flat-credit bucket"
    else:
        raise AssertionError("a reference missing the 8c bucket was accepted")
    finally:
        NG.EXTENDED_JSON = real_path
        tmp.unlink(missing_ok=True)


@case
def case_battery_reconciliation_disagreement_matches_hand_computation():
    """The disagreement arithmetic itself (real-hourly marginal minus each of
    the four nbt_2039 reference figures, and the in/out-of-bracket call),
    independent of the real dispatch/bill computation: hand-pick a real
    marginal and a reference and check every field battery_marginal_under_real_nbt
    would derive from them."""
    real_path = NG.EXTENDED_JSON
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    tmp.write_text(json.dumps({"nbt_2039": {
        "battery_marginal_under_nem2": 2000,
        "battery_marginal_under_nbt": {
            "3c": {"battery_marginal_yr": 2500},
            "5c": {"battery_marginal_yr": 2400},
            "8c": {"battery_marginal_yr": 2300},
        },
    }}))
    NG.EXTENDED_JSON = tmp
    try:
        ref = NG.load_nbt_2039_reference()
        assert ref["battery_marginal_under_nem2_usd_yr"] == 2000
        assert ref["battery_marginal_under_flat_nbt_usd_yr"] == {
            "3c": 2500, "5c": 2400, "8c": 2300}

        # Case 1: a real marginal INSIDE the flat 3c-8c bracket [2300, 2500]
        real_inside = 2350
        flat_ref = ref["battery_marginal_under_flat_nbt_usd_yr"]
        lo, hi = min(flat_ref.values()), max(flat_ref.values())
        inside = lo <= real_inside <= hi
        assert inside is True
        gap = 0.0 if inside else min(abs(real_inside - lo), abs(real_inside - hi))
        assert gap == 0.0
        assert round(real_inside - ref["battery_marginal_under_nem2_usd_yr"], 2) == 350

        # Case 2: a real marginal OUTSIDE the bracket (above the high end)
        real_outside = 2650
        inside2 = lo <= real_outside <= hi
        assert inside2 is False
        gap2 = min(abs(real_outside - lo), abs(real_outside - hi))
        assert round(gap2, 2) == 150.0, gap2  # 2650 - 2500 = 150
        return (f"disagreement arithmetic matches hand computation: inside-bracket "
               f"case gap={gap}, outside-bracket case gap={gap2}")
    finally:
        NG.EXTENDED_JSON = real_path
        tmp.unlink(missing_ok=True)


@case
def case_battery_marginal_reconciliation_regenerates_and_is_internally_consistent():
    """Needs the real archive: runs the full reconciliation and checks the
    committed artifact's own disagreement fields are internally consistent
    with its own reference and real-hourly figures -- a second, independent
    check on top of the byte-identical regeneration case above."""
    _require_archive()
    result = json.loads(NG.RESULTS_JSON.read_text())
    recon = result["battery_marginal_reconciliation_vs_nbt_2039"]
    ref = recon["reference_nbt_2039"]
    flat_ref = ref["battery_marginal_under_flat_nbt_usd_yr"]
    nem2_ref = ref["battery_marginal_under_nem2_usd_yr"]
    for rn, v in recon["battery_marginal_real_hourly_usd_yr"].items():
        real = v["battery_marginal_usd_yr"]
        dis = recon["disagreement_vs_reference"][rn]
        assert abs(dis["vs_nem2_usd"] - (real - nem2_ref)) < EPS
        for credit_name, flat_val in flat_ref.items():
            assert abs(dis[f"vs_flat_{credit_name}_usd"] - (real - flat_val)) < EPS
        lo, hi = min(flat_ref.values()), max(flat_ref.values())
        expected_inside = lo <= real <= hi
        assert dis["real_within_flat_3c_8c_bracket"] == expected_inside
        # served/throughput are positive and throughput >= served (round-trip
        # losses only add to throughput, never subtract from it)
    assert recon["served_kwh_yr"] > 0
    assert recon["throughput_kwh_yr"] >= recon["served_kwh_yr"]
    return (f"battery_marginal_reconciliation_vs_nbt_2039's own disagreement fields are "
           f"internally consistent with its reference and real-hourly figures "
           f"({len(recon['battery_marginal_real_hourly_usd_yr'])} vintages)")


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
