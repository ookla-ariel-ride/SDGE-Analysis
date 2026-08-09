#!/usr/bin/env python3
"""The mechanical half of report generation (issue #39, part 1): resolve every
{{TOKEN}} in report-template.html to a real value, with no model involved.

WHAT THIS DOES
  1. Parses report-template.html itself (never a hand-transcribed list) to find
     every token, split into LIVE (outside any <!-- --> comment) and
     COMMENT-ONLY (appears only inside a <!-- TODO ... --> block, as a worked
     example of a value a later LLM prose pass may reference). Two comment-only
     strings are generic instructional text, not real tokens, and are excluded:
     TOKEN and DOUBLE_BRACE_TOKENS.
  2. Declares TOKENS: a committed, explicit map of token name -> source record
     (which of data_json / data_csv / rates_module / household_yaml /
     cited_constant / derived, the exact path/key/column, and a format spec).
  3. Resolves the whole map against the real committed archive: data/*.json,
     data/*.csv, analysis/rates.py, and private/household.yaml (public-ok
     fields only, enforced at every resolution -- see _hh_value below).

FAIL-CLOSED, WITH ONE NAMED EXCEPTION CLASS
  Every token that CAN be sourced from something committed in this repo IS
  sourced, and resolve_token() raises SystemExit naming the token and what was
  tried if its source ever goes missing. A small number of live template
  tokens have NO committed source anywhere in this repo today (not in data/,
  not in household.yaml, not in analysis/*.py, not in TECHNICAL.md) -- these
  are listed in KNOWN_GAPS with the reason, kept deliberately small, and their
  TOKENS entries have kind="gap": calling resolve_token on one of them raises
  SystemExit naming the gap and the reason, which IS the fail-closed behavior
  (it never silently returns an empty or invented string). resolve_all(...)
  defaults to raising if the gap set ever changes shape unexpectedly, and
  omits them from its returned map unless include_gaps=True is passed.

PRIVACY
  Every household_yaml-sourced token is checked against
  analysis/privacy_tiers.py's cheatsheet-derived tier map at RESOLUTION TIME,
  not just at authoring time: a path with no tier at all, or a tier other than
  public-ok, raises SystemExit rather than returning the value. This is the
  runtime assertion CLAUDE.md section 4 requires -- it protects a future
  token addition from silently reaching a private-only or secret field.

Run standalone to print every resolved token:
  ./.venv/bin/python analysis/report_tokens.py
"""
import csv
import datetime as dt
import json
import pathlib
import re
import sys


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:  # pragma: no cover - reachable only outside a checkout
                break
            p = p.parent
    raise SystemExit(  # pragma: no cover - the module's own path always resolves in-repo
        "repo root not found: no ancestor of the CWD or of this "
        "script contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"
TEMPLATE = ROOT / "report-template.html"
sys.path.insert(0, str(ROOT / "analysis"))
import household as hh          # noqa: E402
import privacy_tiers as pt      # noqa: E402
import rates as R               # noqa: E402


# ---------------------------------------------------------------------------
# 1. Parse the template for its own token inventory (never hand-transcribed).
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\{\{([A-Z0-9_]*)\}\}")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
GENERIC_META_TOKENS = {"TOKEN", "DOUBLE_BRACE_TOKENS"}


def template_tokens(html=None):
    """(live, comment_only) token-name sets from report-template.html.

    live = appears at least once OUTSIDE any <!-- --> span (static markup,
    table cells, JS chart config -- resolved and rendered directly).
    comment_only = appears ONLY inside <!-- --> spans (worked examples inside
    TODO blocks for a later prose-generation pass to reference), minus the two
    generic instructional strings TOKEN and DOUBLE_BRACE_TOKENS.
    """
    text = TEMPLATE.read_text() if html is None else html
    comment_spans = [m.span() for m in _COMMENT_RE.finditer(text)]

    def in_comment(pos):
        return any(s <= pos < e for s, e in comment_spans)

    live, comment_only = set(), set()
    for m in _TOKEN_RE.finditer(text):
        name = m.group(1)
        if not name:
            continue
        (comment_only if in_comment(m.start()) else live).add(name)
    comment_only -= live
    comment_only -= GENERIC_META_TOKENS
    return live, comment_only


# ---------------------------------------------------------------------------
# Cached loaders for the committed archive.
# ---------------------------------------------------------------------------
_json_cache = {}


def _json(name):
    if name not in _json_cache:
        path = DATA / name
        if not path.is_file():
            raise SystemExit(f"report_tokens: missing committed data file data/{name}")
        _json_cache[name] = json.loads(path.read_text())
    return _json_cache[name]


_csv_cache = {}


def _csv_rows(name):
    if name not in _csv_cache:
        path = DATA / name
        if not path.is_file():
            raise SystemExit(f"report_tokens: missing committed data file data/{name}")
        with open(path, newline="") as f:
            _csv_cache[name] = list(csv.DictReader(f))
    return _csv_cache[name]


def _dig(obj, path):
    node = obj
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            raise SystemExit(f"report_tokens: path {path!r} not found in the source "
                              f"data (stuck at {key!r})")
    return node


def _rates_src():
    return pathlib.Path(R.__file__).read_text()


# ---------------------------------------------------------------------------
# household.yaml access, gated on the public-ok tier at every call.
# ---------------------------------------------------------------------------
_TIERS_CACHE = None


def _hh_tiers():
    global _TIERS_CACHE
    if _TIERS_CACHE is None:
        _TIERS_CACHE = pt.path_tiers()
    return _TIERS_CACHE


def _hh_node():
    """The raw parsed private/household.yaml dict, via household.py's own
    fail-closed loader (so a missing file/key fails exactly the way every
    other analysis script's household access fails)."""
    hh.get("household.utility")  # forces the load / the standard fail-closed message
    return hh._load()


def _hh_value(path):
    """[values] at a cheatsheet-contract path (privacy_tiers.resolve's `[]`
    list notation), after asserting the path is tiered public-ok.

    This is the runtime privacy gate CLAUDE.md section 4 requires: a token
    whose spec ever names a path this cheatsheet has not tiered, or has tiered
    private-only/secret, fails closed here -- it is never silently read.
    """
    tiers = _hh_tiers()
    tier = tiers.get(path)
    if tier is None:
        raise SystemExit(
            f"report_tokens: refusing to read household.yaml path {path!r}: it is "
            "not a field DATA-SOURCES-CHEATSHEET.md tiers at all (privacy_tiers."
            "path_tiers() has no entry for it) -- an untiered household key must "
            "never be published")
    if tier != "public-ok":
        raise SystemExit(
            f"report_tokens: refusing to read household.yaml path {path!r}: its "
            f"cheatsheet tier is {tier!r}, not public-ok -- CLAUDE.md section 4 "
            "forbids putting a private-only or secret household answer into any "
            "committed artifact, and a rendered report token is one")
    values, found = pt.resolve(_hh_node(), path)
    if not found:
        raise SystemExit(f"report_tokens: household.yaml has no value at path {path!r} "
                          "(private/household.yaml is missing this key)")
    return values


def hh1(path):
    """The single scalar value at a household.yaml path (asserts exactly one)."""
    values = _hh_value(path)
    if len(values) != 1:
        raise SystemExit(f"report_tokens: household.yaml path {path!r} resolved to "
                          f"{len(values)} values, expected exactly 1")
    return values[0]


# ---------------------------------------------------------------------------
# Format specs. Numeric leaf tokens (data_json/data_csv/rates_module/
# household_yaml) go through one of these by name. "derived" and
# "cited_constant" tokens usually build their own final string and pass
# fmt=None (str() passthrough).
# ---------------------------------------------------------------------------
def _usd0(v):
    return f"${v:,.0f}"


def _usd0_tilde(v):
    return f"~${v:,.0f}"


def _usd2(v):
    return f"${v:,.2f}"


def _usd3(v):
    return f"${v:,.3f}"


def _num0(v):
    return f"{v:,.0f}"


def _num1(v):
    return f"{v:,.1f}"


def _num2(v):
    return f"{v:,.2f}"


def _pct0(v):
    return f"{round(v)}%"


def _pct1(v):
    return f"{v:.1f}%"


def _yr1(v):
    return f"{v:.1f} yr"


def _cents1(v):
    return f"{v * 100:.1f}¢"


def _raw(v):
    return str(v)


def _year(v):
    return f"{int(v):d}"


FORMATTERS = {
    None: _raw, "raw": _raw,
    "usd0": _usd0, "usd0_tilde": _usd0_tilde, "usd2": _usd2, "usd3": _usd3,
    "num0": _num0, "num1": _num1, "num2": _num2, "year": _year,
    "pct0": _pct0, "pct1": _pct1, "yr1": _yr1, "cents1": _cents1,
}


# ---------------------------------------------------------------------------
# Small TOU-boundary oracle: derives clock-hour windows by CALLING
# rates.period(), never by re-declaring the hours. This is what lets
# PEAK_WINDOW / CHEAP_WINDOW / the day-band prices stay correct if rates.py's
# windows ever change, and keeps this module out of
# test_scripts_runnable.py's TOU_EXEMPT concern (it reads the canonical
# module's own output, it does not re-implement TOU assignment).
# ---------------------------------------------------------------------------
def _weekday_runs():
    """[(start_hour, end_hour, period_label), ...] for a canonical (non-
    holiday) weekday, derived by sampling rates.period() every 15 minutes."""
    runs = []
    start, cur = 0.0, R.period(0.0, False)
    h = 0.25
    while h <= 24.0:
        lab = R.period(h, False) if h < 24.0 else None
        if lab != cur:
            runs.append((start, h, cur))
            start, cur = h, lab
        h += 0.25
    return runs


def _hour_label(h):
    h = int(h)
    if h == 0:
        return "12am"
    if h == 12:
        return "12pm"
    return f"{h}am" if h < 12 else f"{h - 12}pm"


def _fmt_hour_range(h1, h2):
    l1, l2 = _hour_label(h1), _hour_label(h2)
    if l1[-2:] == l2[-2:] and h1 not in (0, 12):
        return f"{l1[:-2]}–{l2}"
    return f"{l1}–{l2}"


def _peak_window():
    runs = [r for r in _weekday_runs() if r[2] == "on"]
    if len(runs) != 1:
        raise SystemExit(f"report_tokens: expected exactly one on-peak run in "
                          f"rates.period()'s weekday schedule, found {runs}")
    return _fmt_hour_range(runs[0][0], runs[0][1])


def _cheap_window():
    """The daytime (not overnight) weekday super-off-peak run -- the
    'structural gift' window highlighted in the Bottom line / Monday appendix."""
    runs = [r for r in _weekday_runs() if r[2] == "sop" and r[0] > 0]
    if len(runs) != 1:
        raise SystemExit(f"report_tokens: expected exactly one daytime weekday "
                          f"super-off-peak run, found {runs}")
    return _fmt_hour_range(runs[0][0], runs[0][1])


_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _season_label():
    months = sorted(R.SUMMER_MONTHS)
    if months != list(range(months[0], months[-1] + 1)):
        raise SystemExit(f"report_tokens: rates.SUMMER_MONTHS {sorted(R.SUMMER_MONTHS)} "
                          "is not a contiguous range; the season-label formatter "
                          "assumes a single contiguous summer season")
    return f"{_MONTH_ABBR[months[0]]}–{_MONTH_ABBR[months[-1]]}"


def _rates_effective_date():
    m = re.search(r"effective (\d{1,2})/(\d{1,2})/(\d{4})", _rates_src())
    if not m:
        raise SystemExit("report_tokens: could not find an 'effective M/D/YYYY' date "
                          "in analysis/rates.py's module docstring")
    mo, day, yr = map(int, m.groups())
    return dt.date(yr, mo, day)


def _season_for_month(month):
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer",
            9: "fall", 10: "fall", 11: "fall"}[month]


def _fraction_to_month(fraction):
    month = max(1, min(12, round(fraction * 12) or 1))
    return month


# ---------------------------------------------------------------------------
# KNOWN GAPS: live template tokens with no committed source anywhere in this
# repo today (checked: data/*.json, data/*.csv, private/household.yaml,
# analysis/*.py constants, TECHNICAL.md). Kept small and named, per the brief:
# resolving these mechanically would require inventing a figure, which
# CLAUDE.md section 0 forbids. A later human/LLM pass can fill them once a
# script exists; until then resolve_token() raises SystemExit naming them.
# ---------------------------------------------------------------------------
KNOWN_GAPS = {
    "UTILITY_TOOL_BEST_PLAN_FIGURE": (
        "the utility plan-comparison tool's own dollar figure comes only from "
        "DATA-SOURCES-CHEATSHEET.md's plan_comparison_capture field, which is "
        "private-only (a screenshot of an account-specific page); no committed "
        "data/*.json or *.csv extracts the dollar figure from it, so there is no "
        "public-ok source for the exact number the tool quoted"),
    "EXPANSION_PAYBACK_YEARS": (
        "no committed generator computes a marginal-panel-expansion payback; the "
        "figure quoted in the hand-authored index.html (13-16 yr) is disclosed "
        "there as coming from unarchived workpaper arithmetic, not a script or a "
        "data/*.json artifact"),
    "MARGINAL_EXPORT_VALUE": (
        "companion figure to EXPANSION_PAYBACK_YEARS (the marginal new-panel kWh's "
        "export value) -- same gap, no committed script or artifact computes it"),
    "ELECTRIFICATION_VERDICT_SHORT": (
        "data/heat_pump_conversion.json now prices the space-heating side "
        "(install cost, three COP scenarios, real per-interval-billed "
        "midpoint net savings positive at every COP tested -- from barely "
        "so at COP 2.8 (within this model's own precision) to modestly so "
        "at COP 3.5/4.2 -- not the flat gas_decomposition:"
        "hp_heating_saving_yr figure the pre-issue-#1 comparison used), but "
        "hpwh_saving_yr (205) still has no committed install-cost source -- "
        "'which appliance pencils' needs BOTH sides costed the same way, "
        "and only one is, so the comparison still cannot be made honestly "
        "from what is committed"),
    "INCENTIVE_STATUS": (
        "DATA-SOURCES-CHEATSHEET.md's incentive_status field is an explicit "
        "'research task' (current federal ITC / CA SGIP status, deliberately not "
        "assumed to still exist) with no household.yaml storage location and no "
        "data/*.json artifact; nothing in this repo's committed archive records "
        "today's program status"),
}


# ---------------------------------------------------------------------------
# 2. THE TOKEN MAP. One entry per sourceable token (127 live + 16 legitimate
#    comment-only examples = 143; see test_report_tokens.py for the exact
#    count check against a fresh template parse).
# ---------------------------------------------------------------------------
TOKENS = {}


def _tok(name, **spec):
    if name in TOKENS:
        raise SystemExit(f"report_tokens: token {name!r} declared twice in TOKENS")
    TOKENS[name] = spec


for _gap_name, _gap_reason in KNOWN_GAPS.items():
    _tok(_gap_name, kind="gap", reason=_gap_reason)


# ---- household / plan / utility identity -----------------------------------
_tok("CLIMATE_ZONE", kind="household_yaml", path="household.climate_zone")
_tok("UTILITY_NAME", kind="household_yaml", path="household.utility")
_tok("BEST_PLAN", kind="household_yaml", path="household.plan")
_tok("NEM_STATUS", kind="household_yaml", path="household.nem_version")
_tok("PTO_DATE", kind="household_yaml", path="household.pto_date", fmt="raw")
_tok("SYSTEM_SIZE_KW_DC", kind="household_yaml", path="solar.kw_dc", fmt="num2")
_tok("AC_CEILING_KW", kind="household_yaml", path="solar.kw_ac", fmt="num2")
_tok("PANEL_COUNT", kind="household_yaml", path="solar.module_count", fmt="num0")
_tok("SYSTEM_GROSS_COST", kind="household_yaml", path="solar.install_invoice_usd", fmt="usd0")


def _install_payment_date(ctx):
    raw = hh1("solar.install_paid_date")           # e.g. "2019-12" or a date
    s = str(raw)
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m:
        yr, mo = int(m.group(1)), int(m.group(2))
        return f"{_MONTH_FULL[mo]} {yr}"
    d = dt.date.fromisoformat(s)
    return f"{_MONTH_FULL[d.month]} {d.day}, {d.year}"


_tok("INSTALL_PAYMENT_DATE", kind="derived", get=_install_payment_date,
     sources=["private/household.yaml:solar.install_paid_date"])


def _generation_provider_short(ctx):
    cca = hh1("household.cca")
    head = re.split(r"\s+[—–-]\s+|\(", cca)[0].strip()
    initials = "".join(w[0] for w in head.split() if w[:1].isupper())
    if not initials:
        raise SystemExit(f"report_tokens: could not derive an acronym from "
                          f"household.cca {cca!r}")
    return initials


_tok("GENERATION_PROVIDER_SHORT", kind="derived", get=_generation_provider_short,
     sources=["private/household.yaml:household.cca"])
_tok("GENERATION_PROVIDER", kind="derived",
     get=lambda ctx: f"{hh1('household.cca')} ({_generation_provider_short(ctx)})",
     sources=["private/household.yaml:household.cca"])


def _other_major_loads(ctx):
    # "vehicles" has no trailing "[]" in its cheatsheet contract path, so it
    # resolves (like cleaning_history) to [the whole list] -- one found node
    # holding every entry, not one node per vehicle.
    lists = _hh_value("vehicles")
    if len(lists) != 1 or not isinstance(lists[0], list):
        raise SystemExit("report_tokens: private/household.yaml:vehicles did not "
                          "resolve to a single list")
    n = len(lists[0])
    return f"{n} EV{'s' if n != 1 else ''}"


_tok("OTHER_MAJOR_LOADS", kind="derived", get=_other_major_loads,
     sources=["private/household.yaml:vehicles"])

_tok("HOME_DESCRIPTION", kind="cited_constant", value="Single-family home",
     source="TECHNICAL.md's stated subject system ('a single-family home in the "
            "SDG&E Coastal climate zone')")

def _as_date(v):
    """household.yaml date fields parse as datetime.date via PyYAML's implicit
    date resolver, but a fabricated/synthetic archive may hand this a plain
    'YYYY-MM-DD' string instead -- accept either."""
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


_tok("NEM_EXPIRY_YEAR", kind="derived",
     get=lambda ctx: _as_date(hh1("household.pto_date")).year + 20,
     sources=["private/household.yaml:household.pto_date (+20 yr NEM 2.0 term)"], fmt="year")

_tok("INVERTER_DESCRIPTION", kind="derived",
     get=lambda ctx: (
         f"{int(hh1('solar.inverter_count'))} × {hh1('solar.inverter_model')} "
         f"(~{hh1('solar.kw_ac') * 1000 / hh1('solar.inverter_count'):.0f} VA each)"),
     sources=["private/household.yaml:solar.inverter_count/inverter_model/kw_ac"])

_tok("PANEL_MODEL_WATTS", kind="derived",
     get=lambda ctx: f"{hh1('solar.kw_dc') * 1000 / hh1('solar.module_count'):.0f} W",
     sources=["private/household.yaml:solar.kw_dc/module_count"])


def _size_verification_source(ctx):
    sources = _hh_value("monitoring[].source")
    measures = _hh_value("monitoring[].measures")
    for s, m in zip(sources, measures):
        if "production" in m.lower():
            return f"the {s} device registration"
    raise SystemExit("report_tokens: no monitoring[] entry measures production")


_tok("SIZE_VERIFICATION_SOURCE", kind="derived", get=_size_verification_source,
     sources=["private/household.yaml:monitoring[].source/measures"])


# ---- day-band / TOU-derived tokens -----------------------------------------
_tok("PEAK_WINDOW", kind="derived", get=lambda ctx: _peak_window(),
     sources=["analysis/rates.py:period() (sampled)"])
_tok("CHEAP_WINDOW", kind="derived", get=lambda ctx: _cheap_window(),
     sources=["analysis/rates.py:period() (sampled)"])
_tok("DAYBAND_SEASON_LABEL", kind="derived", get=lambda ctx: _season_label(),
     sources=["analysis/rates.py:SUMMER_MONTHS"])
_tok("DAYBAND_SOP_PRICE", kind="derived", get=lambda ctx: _cents1(R.allin("S", "sop")),
     sources=["analysis/rates.py:allin('S','sop')"])
_tok("DAYBAND_OFFPEAK_PRICE", kind="derived",
     get=lambda ctx: "~" + _cents1(R.allin("S", "off")),
     sources=["analysis/rates.py:allin('S','off')"])
_tok("DAYBAND_ONPEAK_PRICE", kind="derived",
     get=lambda ctx: (lambda lo, hi: f"{round(lo * 100)}–{round(hi * 100)}¢")(
         min(R.allin("S", "on"), R.allin("W", "on")),
         max(R.allin("S", "on"), R.allin("W", "on"))),
     sources=["analysis/rates.py:allin('S'/'W','on')"])
_tok("SUPER_OFF_PEAK_RATE", kind="derived", get=lambda ctx: _cents1(R.allin("S", "sop")),
     sources=["analysis/rates.py:allin('S','sop')"])
_tok("SUMMER_ONPEAK_IMPORT_RATE", kind="derived",
     get=lambda ctx: _usd3(R.allin("S", "on")),
     sources=["analysis/rates.py:allin('S','on')"])
_tok("SUMMER_ONPEAK_EXPORT_RATE", kind="derived",
     get=lambda ctx: _usd3(R.credit("S", "on")),
     sources=["analysis/rates.py:credit('S','on')"])
_tok("RATES_EFFECTIVE_DATE", kind="derived",
     get=lambda ctx: _rates_effective_date().isoformat(),
     sources=["analysis/rates.py module docstring ('effective M/D/YYYY')"])
_tok("BILLED_GENERATION_RATES", kind="derived",
     get=lambda ctx: (f"${R.CEA['S']['on']} on-peak / ${R.CEA['S']['off']} off-peak / "
                       f"${R.CEA['S']['sop']} super-off-peak (summer)"),
     sources=["analysis/rates.py:CEA['S']"])


# ---- report_data.json -------------------------------------------------------
_tok("ANNUAL_IMPORT_KWH", kind="data_json", file="report_data.json",
     path=("totals", "imp"), fmt="num0")
_tok("ANNUAL_EXPORT_KWH", kind="data_json", file="report_data.json",
     path=("totals", "exp"), fmt="num0")


def _annual_production_kwh(ctx):
    rows = _csv_rows("enphase_daily_production.csv")
    for r in rows:
        if r["Date/Time"] == "Total":
            return float(r["Energy Delivered (kWh)"].replace(",", ""))
    raise SystemExit("report_tokens: enphase_daily_production.csv has no 'Total' footer row")


_tok("ANNUAL_PRODUCTION_KWH", kind="derived", get=_annual_production_kwh,
     sources=["data/enphase_daily_production.csv (Total footer row)"], fmt="num0")


def _pvoutput_annual_kwh(ctx):
    return sum(float(r["generated_kwh"]) for r in _csv_rows("pvoutput_daily.csv"))


def _production_agreement_pct(ctx):
    a, b = _annual_production_kwh(ctx), _pvoutput_annual_kwh(ctx)
    return abs(a - b) / ((a + b) / 2) * 100


_tok("PRODUCTION_SOURCE_COUNT", kind="cited_constant", value=2, fmt="num0",
     source="two production totals are backed by committed artifacts (Enphase CT "
            "meter via data/enphase_daily_production.csv's Total row, PVOutput via "
            "data/pvoutput_daily.csv summed); a third independently-derived series "
            "the hand-authored report also cites is disclosed there as an "
            "unarchived workpaper, not artifact-backed, so it is not counted here")
_tok("PRODUCTION_AGREEMENT_PCT", kind="derived",
     get=lambda ctx: f"±{round(_production_agreement_pct(ctx))}%",
     sources=["data/enphase_daily_production.csv", "data/pvoutput_daily.csv"])


def _annual_load_kwh(ctx):
    rd = _json("report_data.json")
    return rd["totals"]["imp"] + (_annual_production_kwh(ctx) - rd["totals"]["exp"])


_tok("ANNUAL_LOAD_KWH", kind="derived", get=lambda ctx: round(_annual_load_kwh(ctx)),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="num0")
_tok("SOLAR_COVERAGE_PCT", kind="derived",
     get=lambda ctx: round(_annual_production_kwh(ctx) / _annual_load_kwh(ctx) * 100),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="num0")
_tok("SELF_CONSUMED_SHARE", kind="derived",
     get=lambda ctx: (lambda p, e: round((p - e) / p * 100))(
         _annual_production_kwh(ctx), _json("report_data.json")["totals"]["exp"]),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="pct0")
_tok("EXPORTED_SHARE", kind="derived",
     get=lambda ctx: (lambda p, e: round(e / p * 100))(
         _annual_production_kwh(ctx), _json("report_data.json")["totals"]["exp"]),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="pct0")
_tok("CAPACITY_FACTOR", kind="derived",
     get=lambda ctx: f"~{_annual_production_kwh(ctx) / (hh1('solar.kw_dc') * 8760) * 100:.1f}%",
     sources=["data/enphase_daily_production.csv", "private/household.yaml:solar.kw_dc"])
_tok("SPECIFIC_YIELD", kind="derived",
     get=lambda ctx: round(_annual_production_kwh(ctx) / hh1("solar.kw_dc")),
     sources=["data/enphase_daily_production.csv", "private/household.yaml:solar.kw_dc"], fmt="num0")

_tok("ONPEAK_IMPORT_SHARE_PCT", kind="derived",
     get=lambda ctx: round(_json("report_data.json")["periods_chart"]["import_share"][
         _json("report_data.json")["periods_chart"]["order"].index("on")] * 100),
     sources=["data/report_data.json:periods_chart"], fmt="num0")
_tok("OFFPEAK_IMPORT_SHARE_PCT", kind="derived",
     get=lambda ctx: round(_json("report_data.json")["periods_chart"]["import_share"][
         _json("report_data.json")["periods_chart"]["order"].index("off")] * 100),
     sources=["data/report_data.json:periods_chart"], fmt="num0")
_tok("SOP_IMPORT_SHARE_PCT", kind="derived",
     get=lambda ctx: round(_json("report_data.json")["periods_chart"]["import_share"][
         _json("report_data.json")["periods_chart"]["order"].index("sop")] * 100),
     sources=["data/report_data.json:periods_chart"], fmt="num0")

_tok("CHART_TITLE_PERIODS", kind="derived",
     get=lambda ctx: (lambda pc, i: (
         f"{round(pc['import_share'][i] * 100)}% of imports happen on-peak, driving "
         f"{round(pc['import_cost'][i] / sum(pc['import_cost']) * 100)}% of gross "
         "import cost"))(_json("report_data.json")["periods_chart"],
                          _json("report_data.json")["periods_chart"]["order"].index("on")),
     sources=["data/report_data.json:periods_chart"])


def _chart_title_hourly(ctx):
    kws = _json("report_data.json")["onpeak_kw_S"]
    peak_h, peak_kw = max(kws.items(), key=lambda kv: kv[1])
    return (f"Average demand by hour of day — on-peak ({_peak_window()}) reaches "
            f"{peak_kw:.1f} kW average summer draw at {_hour_label(int(peak_h))}")


_tok("CHART_TITLE_HOURLY", kind="derived", get=_chart_title_hourly,
     sources=["data/report_data.json:onpeak_kw_S"])


def _chart_title_battery(ctx):
    dp = _json("battery_dispatch_policies.json")
    return (f"Projected grid import with a battery — on-peak imports fall from "
            f"{dp['inputs']['onpeak_import_kwh']:,} to {dp['pw3']['onpeak_after_greedy']:,} "
            "kWh/yr with one battery")


_tok("CHART_TITLE_BATTERY", kind="derived", get=_chart_title_battery,
     sources=["data/battery_dispatch_policies.json:inputs, pw3"])


def _chart_title_carbon(ctx):
    wm = _json("carbon_fullyear_results.json")["intensity_kg_per_mwh"]["window_means_annual"]
    return (f"Grid carbon by hour (measured) — overnight super-off-peak averages "
            f"{wm['sop_overnight_00_06']:.0f} kg CO₂/MWh vs {wm['solar_midday_10_14']:.0f} "
            "at solar midday")


_tok("CHART_TITLE_CARBON", kind="derived", get=_chart_title_carbon,
     sources=["data/carbon_fullyear_results.json:intensity_kg_per_mwh.window_means_annual"])


def _chart_title_spread(ctx):
    ds = _json("tou_spread.json")["delivery_spread"]
    return (f"On-peak minus super-off-peak delivery spread by rate segment — "
            f"{ds['summer']['n']} summer and {ds['winter']['n']} winter priced segments")


_tok("CHART_TITLE_SPREAD", kind="derived", get=_chart_title_spread,
     sources=["data/tou_spread.json:delivery_spread"])


def _sec9_teaser(ctx):
    dr = _json("deep_results.json")
    return (f"{dr['ev_sessions']['count']} EV charging sessions logged; overnight "
            f"phantom baseload {dr['phantom']['annual_kwh']:,} kWh/yr "
            f"(~${dr['phantom']['annual_cost_at_blend']:,}/yr)")


_tok("SEC9_TEASER", kind="derived", get=_sec9_teaser,
     sources=["data/deep_results.json:ev_sessions, phantom"])


def _sec12_teaser(ctx):
    sc = _json("soiling_results.json")["sanity_check_2024_cleaning"]
    return f"the {sc['cleaning_date']} cleaning measured a {sc['known_cleaning_gain_pct']}% production gain"


_tok("SEC12_TEASER", kind="derived", get=_sec12_teaser,
     sources=["data/soiling_results.json:sanity_check_2024_cleaning"])


def _sec13_teaser(ctx):
    nem = _json("nem3_grandfathering.json")["grandfathering_value_range_usd_per_yr"]
    swing = _json("carbon_fullyear_results.json")["footprints_kg_co2_per_yr"]["detail"][
        "midday_cleaner_than_overnight_by"]
    return (f"NEM 2.0 worth ${nem['low']:,.0f}–{nem['high']:,.0f}/yr; overnight grid "
            f"power runs {swing:.0f} kg CO₂/yr dirtier than midday for the same load")


_tok("SEC13_TEASER", kind="derived", get=_sec13_teaser,
     sources=["data/nem3_grandfathering.json", "data/carbon_fullyear_results.json"])


# ---- bottom line / bills ----------------------------------------------------
_tok("ACTUAL_ANNUAL_BILL", kind="data_json", file="behavior_rebuild.json",
     path=("baseline", "actual_billed"), fmt="usd0")
_tok("ACTUAL_MONTHLY_BILL", kind="derived",
     get=lambda ctx: _json("behavior_rebuild.json")["baseline"]["actual_billed"] / 12,
     sources=["data/behavior_rebuild.json:baseline.actual_billed"], fmt="usd0")
_tok("ANALYSIS_DAYS", kind="data_json", file="behavior_rebuild.json",
     path=("window", "days"), fmt="num0")
_tok("ANALYSIS_START_DATE", kind="derived",
     get=lambda ctx: _json("behavior_rebuild.json")["window"]["start"].split(" ")[0],
     sources=["data/behavior_rebuild.json:window.start"])
_tok("ANALYSIS_END_DATE", kind="derived",
     get=lambda ctx: _json("behavior_rebuild.json")["window"]["end"].split(" ")[0],
     sources=["data/behavior_rebuild.json:window.end"])


def _analysis_window_short(ctx):
    w = _json("behavior_rebuild.json")["window"]
    s = dt.date.fromisoformat(w["start"].split(" ")[0])
    e = dt.date.fromisoformat(w["end"].split(" ")[0])
    return f"{_MONTH_ABBR[s.month]} {s.year} – {_MONTH_ABBR[e.month]} {e.year}"


_tok("ANALYSIS_WINDOW_SHORT", kind="derived", get=_analysis_window_short,
     sources=["data/behavior_rebuild.json:window"])
_tok("REPORT_DATE", kind="derived", get=lambda ctx: dt.date.today().isoformat(),
     sources=["system clock at generation time"])

_tok("BEHAVIOR_MODEL_SCRIPT", kind="cited_constant", value="analysis/behavior_rebuild.py",
     source="the generator that writes data/behavior_rebuild.json")
_tok("DISPATCH_SCRIPT", kind="cited_constant", value="analysis/battery_dispatch_policies.py",
     source="the generator that writes data/battery_dispatch_policies.json")
_tok("CARBON_SCRIPT", kind="cited_constant", value="analysis/carbon_fullyear.py",
     source="the generator that writes data/carbon_fullyear_results.json")
_tok("LIFETIME_SCRIPT", kind="cited_constant", value="analysis/lifetime_payback.py",
     source="the generator that writes data/lifetime_payback.json")
_tok("SOILING_SCRIPT", kind="cited_constant", value="analysis/soiling_analysis.py",
     source="the generator that writes data/soiling_results.json")
_tok("BILLING_MODEL_SCRIPT", kind="cited_constant", value="analysis/rates.py",
     source="the canonical bill_nem netting engine every absolute dollar in this "
            "report is validated against")


def _bill_count_and_period_count(ctx):
    rows = _csv_rows("electric_bill_summary.csv")
    periods = len(rows)
    # A PDF statement occasionally splits into two billing periods at a
    # mid-cycle rate change (CLAUDE.md 1: "one PDF can contain multiple billing
    # periods"); the split leaves a short stub period behind. A normal SDG&E
    # monthly cycle runs ~28-32 days, so any period under 15 days is treated as
    # a stub half of a statement rather than a statement of its own.
    stubs = sum(1 for r in rows if float(r["days"]) < 15)
    return periods, periods - stubs


_tok("BILLING_PERIOD_COUNT", kind="derived",
     get=lambda ctx: _bill_count_and_period_count(ctx)[0],
     sources=["data/electric_bill_summary.csv"], fmt="num0")
_tok("BILL_COUNT", kind="derived",
     get=lambda ctx: _bill_count_and_period_count(ctx)[1],
     sources=["data/electric_bill_summary.csv"], fmt="num0")


_tok("EV_FIX_SAVINGS_100", kind="data_json", file="behavior_rebuild.json",
     path=("scenarios", "a", "saved"), fmt="usd0_tilde")
_tok("EV_FIX_SAVINGS_80", kind="data_json", file="behavior_rebuild.json",
     path=("scenarios", "b", "saved"), fmt="usd0")
_tok("OVERLAP_DEDUCTION", kind="data_json", file="behavior_rebuild.json",
     path=("battery", "double_count_avoided"), fmt="usd0")

_tok("BATTERY_SAVINGS_PRICE_AWARE", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "greedy", "save"), fmt="usd0")
_tok("BATTERY_SAVINGS_EVENING_ONLY", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "evening", "save"), fmt="usd0")
_tok("BATTERY_EXP_SAVINGS_PRICE_AWARE", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3x", "greedy", "save"), fmt="usd0")
_tok("KWH_SERVED_PRICE_AWARE", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "greedy", "kwh_served"), fmt="num0")
_tok("KWH_SERVED_EXP", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3x", "greedy", "kwh_served"), fmt="num0")
_tok("CYCLES_PER_DAY", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "greedy", "cycles_per_day"), fmt="num2")
_tok("STORED_KWH_COST_SOLAR", kind="cited_constant", value="~8.4¢",
     source="analysis/battery_dispatch_policies.py module docstring: 'a stored kWh "
            "costs ~8.4c (midday surplus: forgone super-off-peak export credit / RTE)'")
_tok("STORED_KWH_COST_GRID", kind="cited_constant", value="~13.9¢",
     source="analysis/battery_dispatch_policies.py module docstring: '~13.9c "
            "(super-off-peak grid top-up / RTE)'")

_tok("BATTERY_COST", kind="data_json", file="package_results.json",
     path=("packages", "MID", "cost"), fmt="usd0_tilde")
_tok("BATTERY_EXPANDED_COST", kind="data_json", file="package_results.json",
     path=("packages", "HIGH", "cost"), fmt="usd0_tilde")


def _battery_sim_row(name):
    for row in _json("battery_sim.json"):
        if row["config"] == name:
            return row
    raise SystemExit(f"report_tokens: no battery_sim.json row named {name!r}")


_tok("BATTERY_MODEL", kind="cited_constant", value="Tesla Powerwall 3 (PW3)",
     source="analysis/battery_dispatch_policies.py module docstring / TECHNICAL.md "
            "section 6 (the shipping 13.5 kWh / 11.5 kW config); matches "
            "data/battery_sim.json's '1x Tesla Powerwall 3' row")
_tok("BATTERY_EXPANDED_MODEL", kind="cited_constant", value="PW3 + Expansion",
     source="analysis/battery_dispatch_policies.py module docstring / TECHNICAL.md "
            "section 6 (the shipping 27 kWh / 11.5 kW config); matches "
            "data/battery_sim.json's 'PW3 + 1 Expansion' row")
_tok("BATTERY_KWH", kind="derived",
     get=lambda ctx: _battery_sim_row("1x Tesla Powerwall 3")["usable_kwh"],
     sources=["data/battery_sim.json"], fmt="num1")
_tok("BATTERY_KW", kind="derived",
     get=lambda ctx: _battery_sim_row("1x Tesla Powerwall 3")["power_kw"],
     sources=["data/battery_sim.json"], fmt="num1")
_tok("BATTERY_EXPANDED_KWH", kind="derived",
     get=lambda ctx: _battery_sim_row("PW3 + 1 Expansion")["usable_kwh"],
     sources=["data/battery_sim.json"], fmt="num0")
_tok("BATTERY_CHARGE_KW", kind="cited_constant", value=5.0, fmt="num1",
     source="analysis/battery_dispatch_policies.py's CHARGE_KW constant (issue #40: "
            "the bare PW3 unit's nameplate CHARGE power, distinct from its 11.5 kW "
            "DISCHARGE rating in BATTERY_KW); data/battery_sim.json has no charge_kw "
            "field to derive this from, so it is cited directly from the source module")
_tok("BATTERY_EXPANDED_CHARGE_KW", kind="cited_constant", value=8.0, fmt="num1",
     source="analysis/battery_dispatch_policies.py's CHARGE_KW_WITH_EXPANSION constant "
            "(issue #40: the PW3 + Expansion config's nameplate CHARGE power; discharge "
            "stays 11.5 kW same as the bare unit, see BATTERY_EXPANDED_MODEL)")
_tok("BATTERY_SAVINGS_BASE", kind="derived",
     get=lambda ctx: _battery_sim_row("1x Tesla Powerwall 3")["net_annual_savings"],
     sources=["data/battery_sim.json"], fmt="usd0")


def _payback_range(ctx):
    pk = _json("package_results.json")["packages"]["MID"]
    lo, hi = sorted((pk["battery_alone_payback_yr"], pk["battery_alone_payback_post_fix_yr"]))
    return f"{lo:.1f}–{hi:.1f} yr"


_tok("BATTERY_PAYBACK_RANGE", kind="derived", get=_payback_range,
     sources=["data/package_results.json:packages.MID"])
_tok("BATTERY_PAYBACK_EVENING_ONLY", kind="data_json", file="package_results.json",
     path=("packages", "MID", "battery_alone_payback_evening_only_yr"), fmt="yr1")


_tok("BATTERY_MARGINAL_SAVINGS", kind="data_json", file="package_results.json",
     path=("packages", "MID", "battery_alone_yr"), fmt="usd0")

_tok("ENDURANCE_ESSENTIALS", kind="derived",
     get=lambda ctx: (lambda e: f"{e['median_h']} h (p10 {e['p10_h']} h)")(
         _json("backup_endurance.json")["PW3|t1"]),
     sources=["data/backup_endurance.json:'PW3|t1'"])
_tok("ENDURANCE_HOUSE", kind="derived",
     get=lambda ctx: (lambda e: f"{e['median_h']} h (p10 {e['p10_h']} h)")(
         _json("backup_endurance.json")["PW3|t2"]),
     sources=["data/backup_endurance.json:'PW3|t2'"])
_tok("ESSENTIALS_KWH_DAY", kind="cited_constant", value=17, fmt="num0",
     source="analysis/battery_backup_sims.py's essentials tier cap (t1 = "
            "min(load, 0.7 kW) -> 0.7 x 24h = 16.8 kWh/day, rounds to 17); "
            "data/backup_endurance.json stores the resulting endurance HOURS per "
            "config but not this input cap as its own field")


def _house_kwh_day(ctx):
    # A lighter-weight, fully committed-artifact derivation than re-running
    # battery_backup_sims.py's own SAM-8760 "nonev" heuristic (which needs the
    # private hourly archive and isn't packaged as an importable function):
    # whole-home load minus the year's total detected EV energy, per day.
    # This runs ~2 kWh/day (~4%) below the hand-authored report's own
    # SAM-8760-hourly-heuristic figure (44 vs 46) -- both are legitimate,
    # differently-scoped measurements of the same quantity; the gap is the
    # daily-average-vs-hourly-heuristic methodology difference, not an error.
    load = _annual_load_kwh(ctx)
    ev = _json("behavior_rebuild.json")["detection"]["ev_kwh_total"]
    days = _json("behavior_rebuild.json")["window"]["days"]
    return round((load - ev) / days)


_tok("HOUSE_KWH_DAY", kind="derived", get=_house_kwh_day,
     sources=["data/report_data.json", "data/enphase_daily_production.csv",
              "data/behavior_rebuild.json:detection.ev_kwh_total"], fmt="num0")

_tok("DISCOUNT_RATE", kind="data_json", file="tou_spread.json",
     path=("battery", "discount"), fmt="pct0")


def _pct0_from_fraction(v):
    return f"{round(v * 100)}%"


FORMATTERS["pct0_frac"] = _pct0_from_fraction
TOKENS["DISCOUNT_RATE"]["fmt"] = "pct0_frac"

_tok("ESCALATION_HISTORICAL", kind="cited_constant", value="8%",
     source="one of the four escalation rungs battery_dispatch_policies.json's "
            "escalation_greedy_pw3_post_behavior tests (3/5/8/12%); chosen as "
            "'recent SDG&E history' because it sits inside this household's own "
            "MEASURED delivery-rate escalation range in data/tou_spread.json "
            "(delivery_cell_escalation: summer on-peak 7.63%/yr, winter on-peak "
            "11.3%/yr, winter off-peak 12.06%/yr)")


def _escalation_rung(key):
    rungs = _json("battery_dispatch_policies.json")["escalation_greedy_pw3_post_behavior"]
    if key not in rungs:
        raise SystemExit(f"report_tokens: escalation rung {key!r} not present in "
                          "battery_dispatch_policies.json:escalation_greedy_pw3_post_behavior")
    return rungs[key]


_tok("PAYBACK_AT_HISTORICAL_ESCALATION", kind="derived",
     get=lambda ctx: _escalation_rung("8%")["payback"],
     sources=["data/battery_dispatch_policies.json:escalation_greedy_pw3_post_behavior['8%']"],
     fmt="yr1")
_tok("NPV_AT_HISTORICAL_ESCALATION", kind="derived",
     get=lambda ctx: f"+${_escalation_rung('8%')['npv10']:,}",
     sources=["data/battery_dispatch_policies.json:escalation_greedy_pw3_post_behavior['8%']"])


def _spread_observation_count(ctx):
    ts = _json("tou_spread.json")
    rows = _csv_rows("bill_tou_detail.csv")
    n_periods = len({r["period"] for r in rows})
    n_statements = len({r["statement_date"] for r in rows})
    return (f"{ts['inputs']['rows_priced']} priced cells across {n_periods} "
            f"billing periods on {n_statements} statements")


_tok("SPREAD_OBSERVATION_COUNT", kind="derived", get=_spread_observation_count,
     sources=["data/tou_spread.json:inputs", "data/bill_tou_detail.csv"])

_tok("CARBON_SAMPLE_DESCRIPTION", kind="derived",
     get=lambda ctx: (lambda cov: f"{cov['days_covered']} of the analysis year's "
                       f"{cov['days_covered'] + len(cov['missing_dates'])} days")(
         _json("carbon_fullyear_results.json")["coverage"]),
     sources=["data/carbon_fullyear_results.json:coverage"])


# ---- plans -------------------------------------------------------------
def _plan_row(provider):
    for r in _csv_rows("plan_results.csv"):
        if r["plan"] == hh1("household.plan") and r["provider"] == provider:
            return r
    raise SystemExit(f"report_tokens: no plan_results.csv row for "
                      f"{hh1('household.plan')!r}/{provider!r}")


_tok("BEST_PLAN_ANNUAL_CCA", kind="derived",
     get=lambda ctx: float(_plan_row("CEA")["total"]),
     sources=["data/plan_results.csv"], fmt="usd0")
_tok("BEST_PLAN_ANNUAL_BUNDLED", kind="derived",
     get=lambda ctx: float(_plan_row("SDGE")["total"]),
     sources=["data/plan_results.csv"], fmt="usd0")


def _bpm_plans():
    return _json("battery_plan_matrix.json")["plans"]


def _bpm_best():
    plans = _bpm_plans()
    plan = hh1("household.plan")
    if plan not in plans:
        raise SystemExit(f"report_tokens: household plan {plan!r} not present in "
                          "battery_plan_matrix.json:plans")
    return plan, plans[plan]


_tok("BEST_PLAN_NOBATT_MODELED", kind="derived",
     get=lambda ctx: _bpm_best()[1]["no_battery"],
     sources=["data/battery_plan_matrix.json:plans"], fmt="usd0")
_tok("BEST_PLAN_BATT_MODELED", kind="derived",
     get=lambda ctx: _bpm_best()[1]["with_battery"],
     sources=["data/battery_plan_matrix.json:plans"], fmt="usd0")
_tok("BATTERY_VALUE_BEST_PLAN", kind="derived",
     get=lambda ctx: _bpm_best()[1]["battery_value"],
     sources=["data/battery_plan_matrix.json:plans"], fmt="usd0")


def _runner_up():
    plan, best = _bpm_best()
    others = {k: v for k, v in _bpm_plans().items() if k != plan}
    if not others:
        raise SystemExit("report_tokens: battery_plan_matrix.json has no runner-up plan")
    return min(others.items(), key=lambda kv: kv[1]["no_battery"])


_tok("S4_VERDICT_SHORT", kind="derived",
     get=lambda ctx: (lambda plan, best, ru: (
         f"Yes — the battery widens {plan}'s lead over {ru[0]} from "
         f"${ru[1]['no_battery'] - best['no_battery']:,}/yr to "
         f"${ru[1]['with_battery'] - best['with_battery']:,}/yr"
         if (ru[1]["with_battery"] - best["with_battery"]) >
            (ru[1]["no_battery"] - best["no_battery"]) else
         f"No — the battery narrows {plan}'s lead over {ru[0]} from "
         f"${ru[1]['no_battery'] - best['no_battery']:,}/yr to "
         f"${ru[1]['with_battery'] - best['with_battery']:,}/yr"))(
         *_bpm_best(), _runner_up()),
     sources=["data/battery_plan_matrix.json:plans"])


def _wildcard_plan(ctx):
    best = hh1("household.plan")
    for key in _json("deep_results.json")["wildcard"]:
        if best not in key and "+" in key or "no battery" in key:
            name = re.split(r"\s*\+\s*PW3|\s+no battery", key)[0].strip()
            if name != best:
                return name
    raise SystemExit("report_tokens: could not identify a wildcard plan name in "
                      "deep_results.json:wildcard's keys")


_tok("WILDCARD_PLAN", kind="derived", get=_wildcard_plan,
     sources=["data/deep_results.json:wildcard"])
_tok("PLAN_MARGIN_VS_RUNNER_UP", kind="derived",
     get=lambda ctx: _runner_up()[1]["no_battery"] - _bpm_best()[1]["no_battery"],
     sources=["data/battery_plan_matrix.json:plans (no-battery column; ties out to "
              "data/plan_results.csv, asserted in-script)"], fmt="usd0")

_tok("NEM_GRANDFATHER_VALUE_RANGE", kind="derived",
     get=lambda ctx: (lambda r: f"${r['low']:,.2f}–{r['high']:,.2f}")(
         _json("nem3_grandfathering.json")["grandfathering_value_range_usd_per_yr"]),
     sources=["data/nem3_grandfathering.json:grandfathering_value_range_usd_per_yr"])


def _s8_verdict_short(ctx):
    nem = _json("nem3_grandfathering.json")["grandfathering_value_range_usd_per_yr"]
    exp_pct = round(_json("report_data.json")["totals"]["exp"] /
                     _annual_production_kwh(ctx) * 100)
    return (f"No, no, and not yet — the array already exports {exp_pct}% of "
            f"production at low value, and expansion risks the "
            f"${nem['low']:,.0f}–{nem['high']:,.0f}/yr NEM 2.0 grandfathering")


_tok("S8_VERDICT_SHORT", kind="derived", get=_s8_verdict_short,
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv",
              "data/nem3_grandfathering.json"])
_tok("EXPANSION_VERDICT_SHORT", kind="derived",
     get=lambda ctx: (lambda exp_pct: f"No — already exports {exp_pct}% of "
                       "production at the wrong time of day")(
         round(_json("report_data.json")["totals"]["exp"] / _annual_production_kwh(ctx) * 100)),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"])

_tok("RECOMMENDED_PACKAGE_SUMMARY", kind="derived",
     get=lambda ctx: f"MID — behavior fix plus {TOKENS['BATTERY_MODEL']['value']}",
     sources=["data/package_results.json (MID is the starred package)",
              "analysis/battery_dispatch_policies.py"])


# ---- lifetime payback --------------------------------------------------
_tok("FIRST_FULL_YEAR", kind="data_json", file="lifetime_payback.json",
     path=("years", 0, "year"), fmt="year")
_tok("FIRST_YEAR_PRODUCTION_KWH", kind="data_json", file="lifetime_payback.json",
     path=("years", 0, "production_kwh"), fmt="num0")
_tok("FIRST_YEAR_VALUE", kind="derived",
     get=lambda ctx: round(_json("lifetime_payback.json")["years"][0]["value_usd"] / 100) * 100,
     sources=["data/lifetime_payback.json:years[0].value_usd"], fmt="usd0_tilde")


def _crossover_season_year(which):
    """(season_label, year, month) for lifetime_payback.json's crossover[which].
    month is the numeric calendar month (1-12) _fraction_to_month() already
    computes on the way to the season label -- returned alongside it (not
    just the label) so callers needing to compare crossover timing against
    "today" can do it chronologically instead of by comparing season NAMES,
    which sort alphabetically, not by calendar order (see _paid_off)."""
    c = _json("lifetime_payback.json")["crossover"][which]
    month = _fraction_to_month(c["fraction_through_year"])
    return _season_for_month(month), c["year"], month


_tok("PAYBACK_CROSSOVER_DATE", kind="derived",
     get=lambda ctx: (lambda s, y, m: f"{s} {y}")(*_crossover_season_year("gross")),
     sources=["data/lifetime_payback.json:crossover.gross"])


def _paid_off(ctx):
    """Whether the crossover has already happened as of the system clock.
    Compares (year, MONTH) -- both numeric -- never (year, season-name):
    a Codex review caught the previous version comparing season NAMES as
    strings when years were equal ("fall" < "summer" is alphabetically TRUE,
    f < s, even though fall comes AFTER summer in the same calendar year),
    which could report a same-year crossover as already paid off before it
    had actually happened. Strict '<': a crossover landing in the CURRENT
    month is reported as NOT YET paid off -- this module's own resolution is
    monthly, not daily, so within the current month there is no way to know
    whether the crossover fell before or after "today", and CLAUDE.md
    section 0 records an event only after it has definitely happened, never
    on the strength of "sometime this month, probably.\""""
    season, year, month = _crossover_season_year("gross")
    today = dt.date.today()
    return (year, month) < (today.year, today.month)


_tok("PAYBACK_STATUS_SHORT", kind="derived",
     get=lambda ctx: "Paid off" if _paid_off(ctx) else
     f"Payback expected {_crossover_season_year('gross')[0]} "
     f"{_crossover_season_year('gross')[1]}",
     sources=["data/lifetime_payback.json:crossover.gross", "system clock"])
_tok("S11_VERDICT_SHORT", kind="derived",
     get=lambda ctx: "the solar array has already paid for itself" if _paid_off(ctx) else
     (lambda s, y, m: f"on pace to pay for itself by {s} {y}")(*_crossover_season_year("gross")),
     sources=["data/lifetime_payback.json:crossover.gross", "system clock"])
_tok("PAYBACK_HEADLINE", kind="derived",
     get=lambda ctx: (lambda s, y, m: (
         f"Paid off — cumulative value crossed the "
         f"${hh1('solar.install_invoice_usd'):,.0f} gross cost in {s} {y}."))(
         *_crossover_season_year("gross")) if _paid_off(ctx) else
     (lambda s, y, m: f"On pace to cross the ${hh1('solar.install_invoice_usd'):,.0f} "
      f"gross cost by {s} {y}.")(*_crossover_season_year("gross")),
     sources=["data/lifetime_payback.json:crossover.gross",
              "private/household.yaml:solar.install_invoice_usd"])


def _solar_annual_value(ctx):
    nosolar = _json("lifetime_payback.json")["nosolar_bill_usd"]
    baseline = _json("package_results.json")["model_baseline_current_rates"]
    return round((nosolar - baseline) / 100) * 100


_tok("SOLAR_ANNUAL_VALUE", kind="derived", get=_solar_annual_value,
     sources=["data/lifetime_payback.json:nosolar_bill_usd",
              "data/package_results.json:model_baseline_current_rates"], fmt="usd0_tilde")


# ---- cleaning / soiling -------------------------------------------------
def _cleaning_entry():
    # cleaning_history resolves (via privacy_tiers.resolve, no trailing "[]" in
    # its contract path) to [the whole list] -- one found node holding the
    # entire list, since the tier belongs to the container as a whole.
    lists = _hh_value("cleaning_history")
    if len(lists) != 1 or not isinstance(lists[0], list):
        raise SystemExit("report_tokens: private/household.yaml:cleaning_history did "
                          "not resolve to a single list")
    entries = lists[0]
    if not entries:
        raise SystemExit("report_tokens: private/household.yaml:cleaning_history is empty")
    return entries[0]


_tok("CLEANING_PRICE", kind="derived",
     get=lambda ctx: _usd0(_cleaning_entry()["cost_usd"]),
     sources=["private/household.yaml:cleaning_history"])
_tok("CLEANING_YEAR", kind="derived",
     get=lambda ctx: _as_date(_cleaning_entry()["date"]).year,
     sources=["private/household.yaml:cleaning_history"], fmt="year")
_tok("CLEANING_DATE", kind="derived",
     get=lambda ctx: (lambda d: f"{_MONTH_FULL[d.month]} {d.day}, {d.year}")(
         _as_date(_cleaning_entry()["date"])),
     sources=["private/household.yaml:cleaning_history"])
_tok("CLEANING_DATE_SHORT", kind="derived",
     get=lambda ctx: (lambda d: f"{_MONTH_ABBR[d.month]} {d.day}")(
         _as_date(_cleaning_entry()["date"])),
     sources=["private/household.yaml:cleaning_history"])


def _cleaning_window_medians(ctx):
    d0 = _as_date(_cleaning_entry()["date"])
    clean_date = dt.datetime(d0.year, d0.month, d0.day)
    rows = _csv_rows("cleaning_study_daily.csv")
    parsed = [(dt.datetime.strptime(r["date"], "%Y%m%d"), float(r["generated_kwh"]))
              for r in rows]
    pre = sorted(v for d, v in parsed
                 if clean_date - dt.timedelta(days=30) <= d < clean_date)
    post = sorted(v for d, v in parsed
                  if clean_date < d <= clean_date + dt.timedelta(days=30))
    if not pre or not post:
        raise SystemExit("report_tokens: data/cleaning_study_daily.csv does not cover "
                          "a full 30-day window on both sides of the cleaning date")

    def median(xs):
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    return median(pre), median(post)


_tok("CLEANED_PRE_MEDIAN", kind="derived",
     get=lambda ctx: _cleaning_window_medians(ctx)[0],
     sources=["data/cleaning_study_daily.csv", "private/household.yaml:cleaning_history"],
     fmt="num1")
_tok("CLEANED_POST_MEDIAN", kind="derived",
     get=lambda ctx: _cleaning_window_medians(ctx)[1],
     sources=["data/cleaning_study_daily.csv", "private/household.yaml:cleaning_history"],
     fmt="num1")
_tok("CLEANED_RATIO", kind="derived",
     get=lambda ctx: (lambda pre, post: round(post / pre, 3))(*_cleaning_window_medians(ctx)),
     sources=["data/cleaning_study_daily.csv"], fmt="num2")
_tok("CLEANING_EFFECT_PCT", kind="derived",
     get=lambda ctx: (lambda pre, post: f"{(post / pre - 1) * 100:.1f}%")(
         *_cleaning_window_medians(ctx)),
     sources=["data/cleaning_study_daily.csv"])
_tok("SOILING_RATE_RANGE", kind="derived",
     get=lambda ctx: (lambda ae: (
         f"{min(ae['scenario_A_this_years_evidence']['rate_pct_per_month'], ae['scenario_B_2024_cleaning_evidence']['rate_pct_per_month']):.1f}"
         f"–{max(ae['scenario_A_this_years_evidence']['rate_pct_per_month'], ae['scenario_B_2024_cleaning_evidence']['rate_pct_per_month']):.1f}%/month"))(
         _json("soiling_results.json")["annual_economics"]),
     sources=["data/soiling_results.json:annual_economics"])


# ---- Monday appendix ----------------------------------------------------
def _metric_now(ctx):
    return TOKENS["ONPEAK_IMPORT_SHARE_PCT"]["get"](ctx)


_tok("METRIC_NOW", kind="derived",
     get=lambda ctx: f"{_metric_now(ctx)}%",
     sources=["data/report_data.json:periods_chart (on-peak import share)"])
_tok("METRIC_TARGET", kind="derived",
     get=lambda ctx: (lambda dp, tot: f"~{round(dp['pw3']['onpeak_after_greedy'] / tot * 100)}%")(
         _json("battery_dispatch_policies.json"), _json("report_data.json")["totals"]["imp"]),
     sources=["data/battery_dispatch_policies.json:pw3.onpeak_after_greedy",
              "data/report_data.json:totals.imp"])


# ---- data / rate / env source inventories -------------------------------
def _monitoring_fields():
    sources = _hh_value("monitoring[].source")
    measures = _hh_value("monitoring[].measures")
    resolutions = _hh_value("monitoring[].resolution")
    return list(zip(sources, measures, resolutions))


def _data_sources_summary(ctx):
    parts = [f"utility {hh1('household.utility')} Green Button 15-minute interval data"]
    for source, measures, resolution in _monitoring_fields():
        parts.append(f"{source} ({measures}, {resolution})")
    return "; ".join(parts)


_tok("DATA_SOURCES_SUMMARY", kind="derived", get=_data_sources_summary,
     sources=["private/household.yaml:household.utility, monitoring[]"])


def _data_sources_detail(ctx):
    parts = [f"{hh1('household.utility')} Green Button 15-minute interval export "
             "and plan-comparison tool capture"]
    for source, measures, resolution in _monitoring_fields():
        parts.append(f"{source} — {measures}, {resolution}")
    return "; ".join(parts) + "."


_tok("DATA_SOURCES_DETAIL", kind="derived", get=_data_sources_detail,
     sources=["private/household.yaml:household.utility, monitoring[]"])
_tok("RATE_SOURCES_DETAIL", kind="derived",
     get=lambda ctx: (
         f"{hh1('household.utility')} Total Rates Tables and {hh1('household.cca')} "
         f"Adopted Residential Rates, both effective {_rates_effective_date().isoformat()}; "
         f"PCIA ${R.PCIA}; NBC ${R.NBC}; BSC ${R.BSC}/day (analysis/rates.py)."),
     sources=["private/household.yaml:household.utility/cca", "analysis/rates.py"])


def _env_sources_detail(ctx):
    precip = _json("soiling_results.json")["meta"]["precip_source"]
    carbon = _json("carbon_fullyear_results.json")["source"]
    return f"{precip}; {carbon['name']} (fetched {carbon['fetched']})."


_tok("ENV_SOURCES_DETAIL", kind="derived", get=_env_sources_detail,
     sources=["data/soiling_results.json:meta.precip_source",
              "data/carbon_fullyear_results.json:source"])


# ---- provenance (CLAUDE.md section 11, verbatim-required) ----------------
_tok("GENERATION_TOOL", kind="cited_constant", value="Claude Cowork (Fable 5)",
     source="CLAUDE.md section 11's required provenance sentence")
_tok("REVIEW_TOOL_1", kind="cited_constant", value="Claude Code (Fable 5)",
     source="CLAUDE.md section 11's required provenance sentence")
_tok("REVIEW_TOOL_2", kind="cited_constant", value="Codex (GPT-5.6 Sol)",
     source="CLAUDE.md section 11's required provenance sentence")


# ---- gas / electrification ------------------------------------------------
_tok("ACTUAL_ANNUAL_GAS_BILL", kind="derived",
     get=lambda ctx: sum(float(r["total_gas_service"]) for r in _csv_rows("gas_bill_summary.csv")),
     sources=["data/gas_bill_summary.csv"], fmt="usd0")
_tok("MODELED_ANNUAL_AT_CURRENT_RATES", kind="data_json", file="package_results.json",
     path=("model_baseline_current_rates",), fmt="usd0")
# ELECTRIFICATION_VERDICT_SHORT is declared in KNOWN_GAPS above (heat-pump
# space heating has a committed install-cost basis since issue #1; HPWH
# still does not, so a "which pencils" verdict needing both still isn't).


# ---------------------------------------------------------------------------
# 3. The resolver.
# ---------------------------------------------------------------------------
class _Ctx:
    """Passed to every 'derived' formula. Present mainly so formulas read as
    ctx.something() and stay easy to extend without changing every lambda's
    signature; today it carries no state of its own."""


CTX = _Ctx()


def resolve_token(name, spec=None):
    """The rendered string for one token. Raises SystemExit naming `name` and
    what failed if it cannot be produced."""
    if spec is None:
        if name not in TOKENS:
            raise SystemExit(f"report_tokens: unknown token {name!r} -- not in TOKENS")
        spec = TOKENS[name]
    kind = spec["kind"]
    try:
        if kind == "gap":
            raise SystemExit(f"report_tokens: token {name} has no committed source "
                              f"in this repo -- {spec['reason']}")
        elif kind == "data_json":
            raw = _dig(_json(spec["file"]), spec["path"])
        elif kind == "data_csv":
            raw = spec["get"](_csv_rows(spec["file"]))
        elif kind == "rates_module":
            raw = spec["get"](R)
        elif kind == "household_yaml":
            values = _hh_value(spec["path"])
            if spec.get("multi"):
                raw = values
            else:
                if len(values) != 1:
                    raise SystemExit(
                        f"household.yaml path {spec['path']!r} resolved to "
                        f"{len(values)} values, expected exactly 1")
                raw = values[0]
        elif kind == "cited_constant":
            raw = spec["value"]
        elif kind == "derived":
            raw = spec["get"](CTX)
        else:
            raise SystemExit(f"unknown token kind {kind!r}")
    except SystemExit as e:
        msg = str(e)
        if name in msg or kind == "gap":
            raise
        raise SystemExit(f"report_tokens: failed to resolve token {name} ({kind}): {msg}")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise SystemExit(f"report_tokens: failed to resolve token {name} ({kind}): "
                          f"{type(e).__name__}: {e}")

    fmt = spec.get("fmt")
    if fmt is not None and fmt not in FORMATTERS:
        raise SystemExit(f"report_tokens: token {name} has unknown format spec {fmt!r}")
    formatter = FORMATTERS.get(fmt, _raw)
    return formatter(raw)


def resolve_all(include_gaps=False):
    """{TOKEN: rendered_string} for every token in TOKENS.

    Gap tokens (kind='gap') are omitted by default -- they are a documented,
    named exception, not silently-produced empty strings. Pass
    include_gaps=True to get resolve_token's SystemExit behavior surfaced as
    part of a bulk run (used by the "everything really resolves" test to prove
    the gap set is exactly KNOWN_GAPS and nothing else silently fails).
    """
    out = {}
    failures = []
    for name, spec in TOKENS.items():
        if spec.get("kind") == "gap" and not include_gaps:
            continue
        try:
            out[name] = resolve_token(name, spec)
        except SystemExit as e:
            failures.append(str(e))
    if failures:
        raise SystemExit("report_tokens: " + str(len(failures)) +
                          " token(s) failed to resolve:\n" + "\n".join(failures))
    return out


def main():
    live, comment_only = template_tokens()
    total = live | comment_only
    missing = total - set(TOKENS)
    extra_gaps = {n for n, s in TOKENS.items() if s.get("kind") == "gap"}
    if missing:
        raise SystemExit(f"report_tokens: {len(missing)} template token(s) have no "
                          f"TOKENS entry at all: {sorted(missing)}")
    resolved = resolve_all(include_gaps=False)
    print(f"{len(TOKENS)} tokens declared ({len(live)} live, {len(comment_only)} "
          f"comment-only); {len(resolved)} resolved, {len(extra_gaps)} named gap(s):")
    for name in sorted(extra_gaps):
        print(f"  GAP  {name}")
    for name in sorted(resolved):
        val = resolved[name]
        shown = val if len(val) <= 80 else val[:77] + "..."
        print(f"  {name} = {shown}")


if __name__ == "__main__":
    main()
