#!/usr/bin/env python3
"""The intake privacy tiers, resolved and enforceable.

CLAUDE.md section 4 makes the tiers binding: no `private-only` or `secret`
intake answer may be written into any committed artifact. Nothing enforced that
at the artifact layer -- the gitleaks hook screens for secret and PII
*patterns*, not for a field somebody tiered private-only last week -- and a
private-only figure duly reached three committed files before anyone noticed.

This module is the machinery that makes the rule checkable. It does three
things, and no more:

  1. parses the field blocks out of DATA-SOURCES-CHEATSHEET.md, which is where
     the tiers live;
  2. resolves a field `id` to its `private/household.yaml` path by the
     five-step contract stated in that file ("The `id` -> `private/household.yaml`
     path contract"), reporting each id as resolved, legitimately absent, or
     unresolvable -- the last being a broken rule, not a clean bill;
  3. turns the private-only values into "needles" and scans text for them.

It reads files and computes; it writes nothing and has no import-time side
effects. `test_privacy_tiers.py` drives it across every tracked file;
`test_service_headroom.py` drives it across one artifact.

WHAT THIS CANNOT SEE, stated so the gate is not read as more than it is
(TECHNICAL.md section 11.5 carries the same list):

  * a value that was reformatted -- a date rendered in English, a figure
    rounded, a number written with thousands separators -- is a different
    string and is not found;
  * anything DERIVED arithmetically from a private value: a ratio, a per-unit
    cost, a sum a private figure contributed to;
  * a boolean, and a one-word enum shorter than BARE_WORD_TEXT_MIN_CHARS. The
    cheatsheet says this in terms and holds those fields to a different,
    greppable rule instead: no committed script reads the key and no committed
    artifact carries a key of that name;
  * a value the scanned file itself declares as a literal, inside the two file
    classes listed in DECLARED_LITERAL_SOURCES -- a synthetic test fixture and
    the committed example template invent their own values, and those
    coincide with a real household's often enough that counting them would
    make the gate useless. Within those two classes a genuine leak written as
    a declared literal is not caught; a leak written into a comment, a
    docstring or prose still is, which is the case that has actually happened.

A found needle is reported as a field id, a yaml path and a file. The value
itself is never printed, never written into an assertion message, and never
appears in this file.
"""
import ast
import json
import pathlib
import re
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHEATSHEET = ROOT / "DATA-SOURCES-CHEATSHEET.md"
EXAMPLE_HOUSEHOLD = ROOT / "household.example.yaml"
REAL_HOUSEHOLD = ROOT / "private" / "household.yaml"

# The six keys every intake field block declares, and the closed tier vocabulary.
FIELD_KEYS = {"id", "question", "type", "required_if", "where", "privacy"}
TIERS = ("public-ok", "private-only", "secret")

# ---------------------------------------------------------------------------
# Step 1 of the contract: ids that store no value in household.yaml at all.
# Declared, with the reason, because "resolved by declaration" is a different
# outcome from "failed to resolve" -- and because the order matters:
# gas_bill_pdfs would otherwise derive to gas.bill_pdfs at step 3, a key that
# exists in no household's file.
# ---------------------------------------------------------------------------
PATHLESS_FIELDS = {
    # the answer is a document in gitignored private/1-raw-data/
    "electric_interval_csv": "document", "electric_bill_pdfs": "document",
    "gas_bill_pdfs": "document", "gas_interval_csv": "document",
    "plan_comparison_capture": "document", "cca_rate_schedule": "document",
    "rate_table_pdfs": "document", "solar_hourly_consumption_export": "document",
    "solar_daily_production_export": "document", "ev_charge_stats": "document",
    "wall_charger_export": "document",
    # the answer is a source or a research finding, recorded in the report
    "tou_windows_source": "source", "baseline_allowance_source": "source",
    "rate_history_source": "source", "weather_temps_source": "source",
    "precip_source": "source", "grid_co2_source": "source",
    "reliability_reports": "source", "fuel_constants_source": "source",
    "battery_price_quotes": "source", "incentive_status": "source",
    "vpp_programs": "source", "fixed_charge_status": "source",
    # applicability flags answered by the shape of the file
    "has_solar": "flag", "has_battery": "flag", "has_battery_interest": "flag",
    # folded into another field, or carried in prose
    "appliance_fuels": "folded", "metering_config": "folded",
    "gas_rate_schedule": "folded",
    # secret: lives only in a gitignored .env, never in household.yaml
    "pvoutput_api_key": "secret-env",
}

# Step 2: the override table, copied from the cheatsheet's own table. Every row
# is an id whose path steps 3-4 cannot derive. The table does not grow: a field
# added later gets an id of the form <block>_<key> so that step 3 resolves it.
YAML_PATH_OVERRIDES = {
    "climate_zone": "household.climate_zone",
    "utility": "household.utility",
    "cca": "household.cca",
    "nem_version": "household.nem_version",
    "pto_date": "household.pto_date",
    "has_ev": "household.has_ev",
    "has_gas": "household.has_gas",
    "has_new_load_interest": "household.has_new_load_interest",
    "rate_plan": "household.plan",
    "site_latitude": "location.lat",
    "module_count": "solar.module_count",
    "inverter_model": "solar.inverter_model",
    "install_invoice_usd": "solar.install_invoice_usd",
    "install_paid_date": "solar.install_paid_date",
    "itc_claimed": "solar.itc_claimed",
    "miles_per_year": "misc.miles_per_year",
    "supercharge_kwh_yr": "misc.supercharge_kwh_yr",
    "monitoring_feeds": "monitoring[]",
}

# ---------------------------------------------------------------------------
# How a private value is looked for depends on what kind of value it is, and
# the rule is the one the owner's tier rulings rest on: a bare number is not
# identifying, free text is.
#
#   * a value carrying a digit or a space -- a catalog number, a coordinate, a
#     date, a price, a multi-word label, a note -- is looked for ANYWHERE in
#     the file, prose and comments included. That is the mode that catches a
#     private answer quoted inside an explanatory sentence, which is how this
#     gate came to exist;
#   * a single bare word with no digits is ordinary English before it is a door
#     legend, and it turns up inside unrelated sentences by coincidence. Those
#     are compared for EQUALITY against a structured file's own string leaves
#     and keys, which still catches the value being PUBLISHED as a value; in
#     unstructured text they are looked for in value position only (quoted, in
#     a cell, after a colon), and only from BARE_WORD_TEXT_MIN_CHARS up --
#     below that the word is generic vocabulary that every file in the repo
#     contains for reasons of its own;
#   * a whole integer below INTEGER_NEEDLE_FLOOR is skipped: an ampere rating,
#     a pole count or a slot count comes from a short standard list that
#     millions of dwellings share, which is the owner's stated reason for
#     tiering the bare ratings public-ok, and matching on one flags NEC 240.6
#     arithmetic as a disclosure.
# ---------------------------------------------------------------------------
INTEGER_NEEDLE_FLOOR = 1000
BARE_WORD_TEXT_MIN_CHARS = 5

# A bare word counts as published only in value position: opening a line, or
# preceded by a quote, comma, colon, bracket or pipe, and closed the same way.
_VALUE_POSITION = r'(?:^|[">,|:=\[\(\s])\s*%s\s*(?:$|["<,|\]\)])'


class Needle:
    """One private-only value to look for, and how to look for it.

    `value` is a private answer. It stays inside the process: nothing that
    reports a finding may print it.
    """

    __slots__ = ("field_id", "path", "leaf_path", "value", "mode")

    def __init__(self, field_id, path, leaf_path, value, mode):
        self.field_id = field_id
        self.path = path
        self.leaf_path = leaf_path
        self.value = value
        self.mode = mode

    def __repr__(self):                                    # pragma: no cover
        # deliberately valueless: a needle must be safe to print
        return f"<Needle {self.field_id} {self.leaf_path} {self.mode}>"


class Hit:
    """A needle found in a file. Carries no value, by construction."""

    __slots__ = ("field_id", "path", "leaf_path", "mode", "where")

    def __init__(self, field_id, path, leaf_path, mode, where):
        self.field_id = field_id
        self.path = path
        self.leaf_path = leaf_path
        self.mode = mode
        self.where = where

    def __str__(self):
        at = f" in {self.where}" if self.where else ""
        return f"{self.field_id} ({self.leaf_path}, matched {self.mode}){at}"

    __repr__ = __str__


# ---------------------------------------------------------------------------
# The cheatsheet
# ---------------------------------------------------------------------------
def _yaml_blocks(src):
    """The fenced yaml blocks in a markdown file, parsed, nothing asserted."""
    out = []
    for b in re.findall(r"```yaml\n(.*?)```", src, re.S):
        d = yaml.safe_load(b)
        if isinstance(d, dict):
            out.append(d)
    return out


def cheatsheet_fields(text=None):
    """Every intake field block in the cheatsheet, parsed and shape-checked.

    Shape-checked here rather than in one test because every caller's universe
    of private values comes out of these blocks: a block that fails to parse or
    forgets its tier would shrink that universe silently, and the scan would go
    quiet without going wrong in any way a reader could see.
    """
    src = CHEATSHEET.read_text() if text is None else text
    blocks = re.findall(r"```yaml\n(.*?)```", src, re.S)
    assert len(blocks) > 40, f"only {len(blocks)} field blocks parsed"
    out = []
    for b in blocks:
        d = yaml.safe_load(b)
        assert isinstance(d, dict), b[:80]
        missing = FIELD_KEYS - set(d)
        assert not missing, f"field block {d.get('id')!r} is missing {missing}"
        assert d["privacy"] in TIERS, (d["id"], d["privacy"])
        out.append(d)
    ids = [d["id"] for d in out]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate field ids in the cheatsheet: {dupes}"
    return out


def schema():
    """The committed household.example.yaml, which is the intake's shape.

    It is the authority for steps 3-5: which ids name a top-level block, which
    of those blocks are lists, and whether a derived path names a key that can
    exist at all. Using it rather than the private file is what lets the
    resolver contract be checked in CI, where no household.yaml exists.
    """
    return yaml.safe_load(EXAMPLE_HOUSEHOLD.read_text()) or {}


# ---------------------------------------------------------------------------
# The path contract (DATA-SOURCES-CHEATSHEET.md, steps 1-5)
# ---------------------------------------------------------------------------
def yaml_path_for(field_id, shape=None):
    """The household.yaml path for a field id, or None if declared path-less.

    Step 1 declared path-less -> None. Step 2 the override table. Step 3 split
    at the first underscore where the head names a top-level block; a block
    that is a LIST takes `[]` so the remainder is read inside every entry.
    Step 4 the id is itself a top-level key.
    """
    if field_id in PATHLESS_FIELDS:
        return None
    if field_id in YAML_PATH_OVERRIDES:
        return YAML_PATH_OVERRIDES[field_id]
    shape = schema() if shape is None else shape
    head, _, rest = field_id.partition("_")
    if rest and head in shape:
        marker = "[]" if isinstance(shape[head], list) else ""
        return f"{head}{marker}.{rest}"
    return field_id


def resolve(node, path):
    """(matched nodes, found) for a contract path in a parsed household file.

    A segment ending `[]` says the node there is a list and the rest of the
    path is read inside every entry: `monitoring[].url` is the url of each feed
    that has one. Walking dotted keys through dictionaries alone reaches none
    of those, which is the hole this replaces.
    """
    if path is None:
        return [], False
    nodes = [node]
    for seg in path.split("."):
        listy = seg.endswith("[]")
        key = seg[:-2] if listy else seg
        found = [n[key] for n in nodes if isinstance(n, dict) and key in n]
        if listy:
            expanded = []
            for n in found:
                if not isinstance(n, list):
                    return [], False
                expanded.extend(n)
            found = expanded
        if not found:
            return [], False
        nodes = found
    return nodes, True


def _leaf_items(node, prefix):
    """(leaf path, value) under a node, lists marked `[]` like the contract."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaf_items(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(node, list):
        for x in node:
            yield from _leaf_items(x, f"{prefix}[]")
    else:
        yield prefix, node


def leaf_paths(household):
    """Every leaf path in a parsed household file, lists collapsed to `[]`."""
    return sorted({p for p, _ in _leaf_items(household, "")})


def untiered_leaf_paths(household, fields=None, shape=None):
    """Keys in a household file that no cheatsheet field id covers.

    The reverse of the leak scan, and the gap that makes the leak scan
    incomplete by construction: an intake key nobody tiered contributes no
    needle, so publishing it is invisible. A key is covered when some field's
    resolved path is the key or a container of it.
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    paths = [p for p in (yaml_path_for(f["id"], shape) for f in fields) if p]
    out = []
    for leaf in leaf_paths(household):
        if not any(leaf == p or leaf.startswith(p + ".")
                   or leaf.startswith(p + "[]") for p in paths):
            out.append(leaf)
    return out


def path_tiers(fields=None, shape=None):
    """{resolved path: tier} for every field that resolves to one.

    A tier belongs to the FIELD, so a nested key with a tier of its own keeps
    it: `monitoring[]` is private-only as a container, and eight of the eleven
    keys inside it are separately public-ok. Publishing the whole list
    publishes the private ones with it; publishing `monitoring[].resolution`
    discloses nothing, and treating its value as private would flag every
    artifact in the repo that says "hourly".
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    out = {}
    for f in fields:
        p = yaml_path_for(f["id"], shape)
        if p is not None:
            out[p] = f["privacy"]
    return out


def resolution_report(household=None, fields=None, shape=None):
    """Where every cheatsheet id lands, counted three ways.

    The contract's own distinction, and the reason the previous version of this
    check passed while reaching nothing: it keyed on a flag name that was never
    a flag, so every field it meant to require was skipped.

      pathless      -- declared to store no value (resolved by declaration)
      resolved      -- a path that names a key this household holds: CHECKED
      absent        -- a path the schema allows but this household leaves out.
                       Not a failure and NOT a pass: three panel blocks say in
                       terms to leave the key out until someone has looked, and
                       a whole block is absent when its applicability flag is
                       false. Reported so a clean bill is never printed for a
                       field that had no value behind it
      unresolvable  -- no path derivable, or a path naming a key the committed
                       schema has no room for. A tier whose subject cannot be
                       located is a broken rule; callers fail on this
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    out = {"pathless": [], "resolved": [], "absent": [], "unresolvable": []}
    for f in fields:
        fid = f["id"]
        path = yaml_path_for(fid, shape)
        if path is None:
            out["pathless"].append((fid, PATHLESS_FIELDS[fid]))
            continue
        if not resolve(shape, path)[1]:
            out["unresolvable"].append((fid, path))
            continue
        if household is None:
            out["absent"].append((fid, path))
            continue
        out["resolved" if resolve(household, path)[1] else "absent"].append(
            (fid, path))
    return out


# ---------------------------------------------------------------------------
# Needles
# ---------------------------------------------------------------------------
def needles(household, fields=None, shape=None):
    """Every private-only (and secret) intake value, with how to look for it."""
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    tiers = path_tiers(fields, shape)
    out = []
    for f in fields:
        if f["privacy"] == "public-ok":
            continue
        path = yaml_path_for(f["id"], shape)
        if path is None:
            continue
        nodes, found = resolve(household, path)
        if not found:
            continue
        for node in nodes:
            for leaf_path, v in _leaf_items(node, path):
                if tiers.get(leaf_path) == "public-ok":
                    continue
                if v is None or isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)) and float(v).is_integer() \
                        and abs(float(v)) < INTEGER_NEEDLE_FLOOR:
                    continue
                s = str(v)
                mode = "anywhere" if re.search(r"[\d\s]", s) else "as a value"
                out.append(Needle(f["id"], path, leaf_path, s, mode))
    # two schedule rows can carry the same legend, and two feeds the same
    # owner; that is one value to look for, and one finding to report
    seen, unique = set(), []
    for n in out:
        key = (n.field_id, n.leaf_path, n.value, n.mode)
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique


def structured_strings(obj):
    """Every string a parsed artifact publishes, keys included."""
    got = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                got.add(k.lower())
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            got.add(o.lower())
    walk(obj)
    return got


# ---------------------------------------------------------------------------
# Two narrow scoping rules, each because the alternative is a gate nobody can
# keep green. Both are scoped to a REGION or to declared literals -- neither
# exempts a whole file, and neither is keyed on a private value (a table of
# those would be the leak it is meant to prevent).
#
#   1. DATA-SOURCES-CHEATSHEET.md's own `question:` text enumerates the
#      standard values a field can take -- the four utility meter classes, for
#      instance -- and a household's answer is one of them. That is an
#      enumeration, not an answer. Only the question strings are excluded; the
#      rest of the cheatsheet, `where:` text included, is scanned.
#   2. A synthetic test fixture and the committed example template invent their
#      own values, and an invented door legend collides with a real one about
#      as often as two houses name a circuit the same way -- which is to say
#      constantly. What a file DECLARES as a literal is excluded -- not the
#      file. A private value that reaches one of these files through a comment,
#      a docstring or running prose is outside every declared literal and still
#      fails, which is the shape the real incident took.
# ---------------------------------------------------------------------------
DECLARED_LITERAL_SOURCES = ("test-module", "example-template", "cheatsheet")


def _file_class(relpath):
    name = relpath.rsplit("/", 1)[-1]
    if name.startswith("test_") and name.endswith(".py"):
        return "test-module"
    if name == "household.example.yaml":
        return "example-template"
    if name == "DATA-SOURCES-CHEATSHEET.md":
        return "cheatsheet"
    return None


def declared_literals(relpath, text):
    """Strings a file declares itself, and which therefore are not answers.

    Empty for every file outside the three classes above, so the default is
    that every byte of a file is scanned.
    """
    kind = _file_class(relpath)
    out = set()
    if kind == "test-module":
        try:
            tree = ast.parse(text)
        except SyntaxError:                                # pragma: no cover
            return out
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    elif kind == "example-template":
        for _, v in _leaf_items(yaml.safe_load(text) or {}, ""):
            if isinstance(v, str):
                out.add(v)
    elif kind == "cheatsheet":
        # lenient on purpose: this is a scoping rule, not the shape check, and
        # it has to work on a synthetic cheatsheet in the positive control
        for f in _yaml_blocks(text):
            if isinstance(f.get("question"), str):
                out.add(f["question"])
    return {s for s in out if len(s) > 1}


def _mask(text, literals):
    """Blank out what the file declares, keeping every offset where it was."""
    low = text.lower()
    spans = []
    for lit in literals:
        needle = lit.lower()
        start = low.find(needle)
        while start != -1:
            spans.append((start, start + len(needle)))
            start = low.find(needle, start + 1)
    if not spans:
        return text
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def scan_text(text, needle_list, relpath=None, structured=None):
    """Needles found in one file's text. Returns Hits, never values.

    `structured` is a parsed object when the file is JSON: a bare word is then
    compared for equality against the artifact's own string leaves and keys,
    which is the strict reading of "published as a value".
    """
    if relpath is not None:
        text = _mask(text, declared_literals(relpath, text))
    low = text.lower()
    strings = structured_strings(structured) if structured is not None else None
    hits = []
    for n in needle_list:
        v = n.value.lower()
        if n.mode == "anywhere":
            found = v in low
        elif strings is not None:
            found = v in strings
        elif len(v) < BARE_WORD_TEXT_MIN_CHARS:
            continue
        else:
            found = bool(re.search(_VALUE_POSITION % re.escape(v), low, re.M))
        if found:
            hits.append(Hit(n.field_id, n.path, n.leaf_path, n.mode, relpath))
    return hits


def leaks(needle_list, text, obj=None, relpath=None):
    """scan_text with the argument order the artifact scan already used."""
    return scan_text(text, needle_list, relpath=relpath, structured=obj)


# ---------------------------------------------------------------------------
# The repo-wide scan
# ---------------------------------------------------------------------------
def tracked_files(root=None):
    """Every git-tracked file outside private/, relative to the repo root.

    Every tracked file, not data/: of the eleven places the incident's value
    appeared, six were in index.html and TECHNICAL.md, which no generator
    writes. A gate scoped to generator output would have found fewer than half
    of them.
    """
    root = ROOT if root is None else pathlib.Path(root)
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.split("\0")
                  if p and not p.startswith("private/"))


def scan_tree(needle_list, root=None, files=None):
    """Scan every tracked file. Returns (hits, files scanned)."""
    root = ROOT if root is None else pathlib.Path(root)
    rels = tracked_files(root) if files is None else list(files)
    hits = []
    for rel in rels:
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(errors="ignore")
        obj = None
        if p.suffix == ".json":
            try:
                obj = json.loads(text)
            except ValueError:                             # pragma: no cover
                obj = None
        hits.extend(scan_text(text, needle_list, relpath=rel, structured=obj))
    return hits, rels
