#!/usr/bin/env python3
"""The intake privacy tiers, resolved and enforceable.

CLAUDE.md section 4 makes the tiers binding: no `private-only` or `secret`
intake answer may be written into any committed artifact. Nothing enforced that
at the artifact layer -- the gitleaks hook screens for secret and PII
*patterns*, not for a field somebody tiered private-only last week -- and a
private-only figure duly reached three committed files before anyone noticed.

This module is the machinery that makes the rule checkable. It does four
things, and no more:

  1. parses the field blocks out of DATA-SOURCES-CHEATSHEET.md, which is where
     the tiers live;
  2. resolves a field `id` to its `private/household.yaml` path by the
     five-step contract stated in that file ("The `id` -> `private/household.yaml`
     path contract"), reporting each id as resolved, legitimately absent, or
     unresolvable -- the last being a broken rule, not a clean bill;
  3. turns the private-only values into "needles" and scans text for them;
  4. for the fields no literal scan can cover -- a boolean, a short enum --
     enforces the greppable substitute the cheatsheet states instead: no
     committed artifact carries a key of that name, and no committed script
     reads that path.

It reads files and computes; it writes nothing and has no import-time side
effects. Run as a script (`--staged`, the default, or `--tree`) it is the
pre-commit gate: `.githooks/pre-commit` calls it on the staged content, which
is what CLAUDE.md section 4 means by "the local hook is the real gate" -- CI
has no private file and cannot be. `test_privacy_tiers.py` drives it across
every tracked file; `test_service_headroom.py` drives it across one artifact.

WHAT THIS CANNOT SEE, stated so the gate is not read as more than it is
(TECHNICAL.md section 11.5 carries the same list):

  * a value that was reformatted -- a date rendered in English, a figure
    rounded, a number written with thousands separators -- is a different
    string and is not found;
  * anything DERIVED arithmetically from a private value: a ratio, a per-unit
    cost, a sum a private figure contributed to;
  * a boolean, and a one-word enum shorter than BARE_WORD_TEXT_MIN_CHARS. That
    class is NOT left uncovered: `unsearchable_fields` derives it from the
    tiers, and `scan_artifact_keys` / `scan_script_reads` hold it to the
    greppable substitute the cheatsheet states -- no committed artifact carries
    a key of that name, and no committed script reads that path;
  * a value that a file declares as its own literal AND that has been declared
    a known collision in DECLARED_FIXTURE_COLLISIONS. There is no file class
    that goes unscanned: a test module and the example template are scanned
    like anything else, and the only thing that can excuse a hit in one is a
    row naming the file, the yaml path, the exact number of coinciding
    answers, and the reason. Everything else in those files -- a comment, a
    docstring, prose, and any value the row does not account for -- fails.

A found needle is reported as a field id, a yaml path and a file. The value
itself is never printed, never written into an assertion message, and never
appears in this file.
"""
import ast
import csv
import io
import json
import pathlib
import re
import subprocess
import sys

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
# The one thing that can excuse a hit, and it is a row somebody wrote down.
#
# Three file classes declare literals that a household's answer can legitimately
# coincide with: the cheatsheet's own `question:` text enumerates the standard
# values a field can take (the utility meter classes, for one) and the answer is
# one of them; a synthetic test fixture and the committed example template
# invent door legends, and an invented door legend collides with a real one
# about as often as two houses name a circuit the same way.
#
# A file-class exemption is the wrong answer to that, and this module carried
# one until an adversarial pass showed what it costs: every string literal in
# every test module, and every value in the example template, was unscanned, so
# a REAL answer pasted into either -- the two files most likely to receive
# copied sample data -- was invisible. The exemption could not tell a fixture
# from a copy, which is the only distinction it existed to make.
#
# What replaces it is the repo's own idiom: declare the exemption, with the
# reason, and make a stale declaration break the suite. A hit inside a declared
# literal span is excused only by a DECLARED_FIXTURE_COLLISIONS row naming the
# file, the yaml path, the EXACT number of private answers that coincide there,
# and why. Anything the row does not account for -- a further collision, a value
# in a comment, in a docstring or in prose -- fails, and `test_privacy_tiers.py`
# fails a row whose count no longer matches, in both directions.
#
# The row is keyed on file and path and NEVER on the value, deliberately. Here
# the fixture value and the private answer are the same bytes, so a value-keyed
# allowlist would collect this household's door legend into one committed table
# -- a sharper disclosure than the scattered fixture context it exempts, and the
# exact thing CLAUDE.md section 4 forbids. The count is what makes the row
# enforceable: a fourth real label pasted into a file budgeted for three fails.
#
# Spans are computed from the AST node positions of a python module, the
# composer marks of a yaml document and the `question:` scalars of the
# cheatsheet, and never by blanking every occurrence of a declared string. That
# difference is why a value declared once as a fixture and then also quoted in a
# comment still fails on the comment -- the shape the leak this gate found
# actually took. For the same reason a python string that is a STATEMENT (a
# docstring, or any free-standing string expression) is prose, not a fixture
# literal, and its span is never eligible.
# ---------------------------------------------------------------------------
DECLARED_LITERAL_SOURCES = ("test-module", "example-template", "cheatsheet")

# {(tracked path, yaml leaf path): (how many answers coincide, why)}
DECLARED_FIXTURE_COLLISIONS = {
    ("DATA-SOURCES-CHEATSHEET.md", "panel.meter_class"): (
        1, "the field block's own `question:` enumerates the standard utility "
           "meter classes, and every household's answer is one of them"),
    ("analysis/test_service_headroom.py", "panel.schedule[].label"): (
        3, "synthetic panel fixtures name circuits after the appliances they "
           "serve, which is what a real door legend does too"),
    ("household.example.yaml", "panel.schedule[].label"): (
        2, "the schema template's placeholder schedule names circuits after "
           "the appliances they serve, for the same reason"),
}


def _file_class(relpath):
    name = relpath.rsplit("/", 1)[-1]
    if name.startswith("test_") and name.endswith(".py"):
        return "test-module"
    if name == "household.example.yaml":
        return "example-template"
    if name == "DATA-SOURCES-CHEATSHEET.md":
        return "cheatsheet"
    return None


def _offset_table(text):
    """Character offset of the start of each line, 0-indexed by line number."""
    offs, pos = [0], 0
    for line in text.splitlines(keepends=True):
        pos += len(line)
        offs.append(pos)
    return offs


def _char_offset(text, offs, lineno, col):
    """(lineno, col_offset) from the ast, as a character index into `text`.

    `col_offset` is a UTF-8 BYTE offset into the line, so a source line with a
    non-ASCII character above the node would shift every span after it. The
    round trip through the encoded line is what keeps them aligned.
    """
    start = offs[lineno - 1]
    end = offs[lineno] if lineno < len(offs) else len(text)
    return start + len(text[start:end].encode("utf8")[:col].decode("utf8",
                                                                  "ignore"))


def _python_literal_spans(text):
    """Source spans of the string literals a python module declares.

    Excludes every string that stands alone as a statement: a module, class or
    function docstring, and any free-standing string expression. Those carry
    prose, and prose that quotes a private answer is the leak.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:                                    # pragma: no cover
        return []
    prose = {id(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
             and isinstance(n.value.value, str)}
    offs = _offset_table(text)
    spans = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant)
                and isinstance(node.value, str)) or id(node) in prose:
            continue
        if node.end_lineno is None:                        # pragma: no cover
            continue
        spans.append((_char_offset(text, offs, node.lineno, node.col_offset),
                      _char_offset(text, offs, node.end_lineno,
                                   node.end_col_offset)))
    return spans


def _yaml_value_spans(text, base=0):
    """Source spans of the string SCALARS a yaml document holds as values.

    Composed rather than loaded, because only the composer keeps the marks. A
    mapping's keys are not values and are not exempt.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:                                 # pragma: no cover
        return []
    spans = []

    def walk(node):
        if isinstance(node, yaml.ScalarNode):
            if node.tag.endswith(":str"):
                spans.append((base + node.start_mark.index,
                              base + node.end_mark.index))
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                walk(child)
        elif isinstance(node, yaml.MappingNode):
            for _key, value in node.value:
                walk(value)
    if root is not None:
        walk(root)
    return spans


def _cheatsheet_question_spans(text):
    """Source span of each field block's `question:` scalar, and nothing else.

    Lenient on purpose: this is a scoping rule, not the shape check, and it has
    to work on a synthetic cheatsheet in the positive control.
    """
    spans = []
    for m in re.finditer(r"```yaml\n(.*?)```", text, re.S):
        try:
            node = yaml.compose(m.group(1))
        except yaml.YAMLError:                             # pragma: no cover
            continue
        if not isinstance(node, yaml.MappingNode):         # pragma: no cover
            continue
        for key, value in node.value:
            if (isinstance(key, yaml.ScalarNode) and key.value == "question"
                    and isinstance(value, yaml.ScalarNode)):
                spans.append((m.start(1) + value.start_mark.index,
                              m.start(1) + value.end_mark.index))
    return spans


def declared_literal_spans(relpath, text):
    """(start, end) character spans a file declares as its own literals.

    Empty for every file outside the three classes above, so the default is
    that every byte of a file is scanned. Spans, not strings: see the block
    comment above for why the difference decides whether this gate works.
    """
    kind = _file_class(relpath)
    if kind == "test-module":
        return _python_literal_spans(text)
    if kind == "example-template":
        return _yaml_value_spans(text)
    if kind == "cheatsheet":
        return _cheatsheet_question_spans(text)
    return []


def _mask(text, spans):
    """Blank out the given spans, keeping every offset where it was."""
    if not spans:
        return text
    chars = list(text)
    for a, b in spans:
        for i in range(max(a, 0), min(b, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _found_in(text, needle_list, structured=None):
    """The needles that occur in one body of text. Returns needles, not Hits."""
    low = text.lower()
    strings = structured_strings(structured) if structured is not None else None
    out = []
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
            out.append(n)
    return out


def scan_text(text, needle_list, relpath=None, structured=None, excused=None):
    """Needles found in one file's text. Returns Hits, never values.

    `structured` is a parsed object when the file is JSON: a bare word is then
    compared for equality against the artifact's own string leaves and keys,
    which is the strict reading of "published as a value".

    Run twice where the file declares literal spans. The first pass reads the
    file with those spans blanked, so anything it finds sits in a comment, a
    docstring or prose and is a leak whatever else the file declares. The second
    pass reads every byte; what only the second pass finds is a value the file
    declares as its own, and that is a hit too UNLESS a DECLARED_FIXTURE_
    COLLISIONS row accounts for it. `excused` is an optional Counter the caller
    passes to learn which rows were used and how often, which is how a stale row
    is made to fail.
    """
    spans = declared_literal_spans(relpath, text) if relpath is not None else []
    outside = _found_in(_mask(text, spans), needle_list, structured)
    hits = [Hit(n.field_id, n.path, n.leaf_path, n.mode, relpath)
            for n in outside]
    if not spans:
        return hits
    seen = {id(n) for n in outside}
    for n in _found_in(text, needle_list, structured):
        if id(n) in seen:
            continue
        key = (relpath, n.leaf_path)
        if key in DECLARED_FIXTURE_COLLISIONS:
            if excused is not None:
                excused[key] += 1
            continue
        hits.append(Hit(n.field_id, n.path, n.leaf_path, n.mode, relpath))
    return hits


def leaks(needle_list, text, obj=None, relpath=None):
    """scan_text with the argument order the artifact scan already used."""
    return scan_text(text, needle_list, relpath=relpath, structured=obj)


# ---------------------------------------------------------------------------
# The substitute rule, for the fields a literal scan cannot cover
#
# `needles` drops a boolean, and `scan_text` skips a bare word shorter than
# BARE_WORD_TEXT_MIN_CHARS in unstructured text. That is right: a scan for
# `true`, or for a four-letter enum, flags every file in the repo and is
# worthless in both directions. But dropping a field from the value scan is not
# the same as exempting it, and the cheatsheet has always stated the substitute
# those fields are held to instead:
#
#     no committed artifact carries a key of that name, and no committed script
#     reads that path.
#
# The set is DERIVED, never listed. Two ways in, and both follow the tiers, so
# a private-only boolean added next year is covered without anyone remembering:
#
#   * its declared `type` in the cheatsheet is one a literal scan can never
#     search -- a boolean. True of the FIELD, so it holds in CI, where no
#     household file exists and the artifact/script halves of this rule still
#     run in full;
#   * the household's recorded answer is a scalar that yields no needle: too
#     short, or a whole integer below INTEGER_NEEDLE_FLOOR. That reading needs
#     the private file, so it widens the set locally and cannot run in CI.
#     Restricted to scalars on purpose -- a container that produced no needle
#     would ban its BLOCK name (`panel`, `monitoring`), which is far wider than
#     the rule says and would fire on artifacts that disclose nothing.
# ---------------------------------------------------------------------------
UNSEARCHABLE_TYPES = ("bool",)

# The accessor this repo reads household values through (analysis/household.py,
# imported as `hh` or `HH`). A read is `<accessor>.get("<dotted.path>")`.
ACCESSOR_NAMES = ("hh", "HH", "household", "HOUSEHOLD")

# The one file whose string literals are not read as a script naming a path:
# the tier machinery has to name a path in order to ban reading it, and
# YAML_PATH_OVERRIDES above is that naming. The accessor-call half of the rule
# still applies to it, so a genuine read here would still fail.
PATH_LITERAL_EXEMPT = {
    "analysis/privacy_tiers.py":
        "the enforcing module: its override table names paths so that the ban "
        "on reading them can be computed at all",
}

# The one file the KEY half of the rule cannot apply to, declared rather than
# skipped in silence. It stays subject to every value rule, including the
# declared-collision accounting above.
KEY_RULE_EXEMPT = {
    "household.example.yaml":
        "the intake schema itself: carrying every intake key, banned ones "
        "included, is what makes it the schema new households copy",
}

# The formats the path-read half reaches beyond python. A committed shell hook
# or workflow reading `solar.itc_claimed` discloses the same thing a python
# script does, and this repo tracks both. There is no parser for them, so the
# search is an exact one for the dotted path as a whole token, outside comments.
SCRIPT_LIKE_SUFFIXES = (".sh", ".bash", ".zsh", ".yml", ".yaml")


def unsearchable_fields(household=None, fields=None, shape=None):
    """{field id: dotted path} for private-only fields no literal scan covers.

    `household` is optional: without it the set is the type-derived half alone,
    which is what CI can compute. Pass the parsed private file to widen it with
    the answers that turned out to be unsearchable in fact.
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    searched = ({n.field_id for n in needles(household, fields, shape)}
                if household is not None else None)
    out = {}
    for f in fields:
        if f["privacy"] == "public-ok":
            continue
        path = yaml_path_for(f["id"], shape)
        if path is None:
            continue
        if f.get("type") in UNSEARCHABLE_TYPES:
            out[f["id"]] = path
            continue
        if searched is None or f["id"] in searched:
            continue
        nodes, found = resolve(household, path)
        if found and not any(isinstance(n, (dict, list)) for n in nodes):
            out[f["id"]] = path
    return out


def banned_keys(unsearchable):
    """{key name: (field id, path)} -- the key names no artifact may carry."""
    out = {}
    for field_id, path in unsearchable.items():
        key = path.split(".")[-1].replace("[]", "")
        out[key.lower()] = (field_id, path)
    return out


def _json_keys(obj, got=None):
    """Every dict key in a parsed artifact, lowercased."""
    got = set() if got is None else got
    if isinstance(obj, dict):
        for k, v in obj.items():
            got.add(str(k).lower())
            _json_keys(v, got)
    elif isinstance(obj, list):
        for v in obj:
            _json_keys(v, got)
    return got


def scan_artifact_keys(items, banned):
    """Half one of the substitute rule: the key name in a structured artifact.

    JSON and YAML keys at any depth, and the header row of a CSV. YAML because
    this repo tracks it -- the workflows and the schema template -- and a rule
    that stopped at JSON left a whole tracked format returning clean. The key
    NAME is the disclosure here -- `itc_claimed: false` publishes the answer
    whichever way the boolean falls -- so this looks for the key, never a value.
    """
    hits = []
    for rel, text in items:
        keys = set()
        if rel in KEY_RULE_EXEMPT:
            continue
        if rel.endswith(".json"):
            try:
                keys = _json_keys(json.loads(text))
            except ValueError:                             # pragma: no cover
                continue
        elif rel.endswith((".yaml", ".yml")):
            try:
                for doc in yaml.safe_load_all(text):
                    _json_keys(doc, keys)
            except yaml.YAMLError:                         # pragma: no cover
                continue
        elif rel.endswith(".csv"):
            row = next(csv.reader(io.StringIO(text)), [])
            keys = {c.strip().lower() for c in row}
        else:
            continue
        for key, (field_id, path) in sorted(banned.items()):
            if key in keys:
                hits.append(Hit(field_id, path, key, "a key of that name", rel))
    return hits


def _uncommented(text):
    """A shell/yaml file with its comment tails removed.

    Both languages start a comment at a `#` that opens a line or follows
    whitespace. Removing those is what keeps the search below off prose: a hook
    or a workflow that MENTIONS a banned path in a comment is not reading it,
    the same ruling the python half already makes for a docstring.
    """
    return "\n".join(re.sub(r"(?:(?<=\s)|^)#.*$", "", line)
                     for line in text.splitlines())


def scan_script_reads(items, unsearchable):
    """Half two: a committed script reading the path.

    In python, two shapes, because one alone is not enough. The accessor call
    `hh.get("solar.itc_claimed")` is the direct read, and a read of an ANCESTOR
    (`HH.get("solar")`) pulls the key out with it. The bare path literal catches
    the indirection `P = "solar.itc_claimed"` ... `hh.get(P)`, and is matched by
    exact string equality so that a docstring or a comment MENTIONING the path
    -- which several files legitimately do -- is not a read.

    Python is not the only committed script here: the repo tracks shell (the
    hooks, `check_coverage.sh`, `stage-private-data.sh`) and yaml (the
    workflows), and a rule that skipped every file without a `.py` suffix left
    those returning clean. There is no AST for them, so the search is the
    conservative shape: the dotted path as a WHOLE TOKEN, outside comments. The
    prose files -- markdown, html -- are deliberately not searched: several of
    them have to name these paths in order to document the rule.
    """
    by_path = {path: field_id for field_id, path in unsearchable.items()}
    seen, hits = set(), []

    def record(field_id, path, mode, rel):
        key = (field_id, mode, rel)
        if key not in seen:
            seen.add(key)
            hits.append(Hit(field_id, path, path, mode, rel))

    for rel, text in items:
        if not rel.endswith(".py"):
            if rel.endswith(SCRIPT_LIKE_SUFFIXES) or text.startswith("#!"):
                body = _uncommented(text)
                for path, field_id in by_path.items():
                    # word boundaries only: `.solar.itc_claimed` is how yq and
                    # jq spell a read, so a dot to the left must not disqualify
                    # it. Erring toward a hit is the right direction here
                    if re.search(r"(?<!\w)%s(?!\w)" % re.escape(path), body):
                        record(field_id, path, "the path named in a script",
                               rel)
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:                                # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ACCESSOR_NAMES
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                arg = node.args[0].value
                for path, field_id in by_path.items():
                    if arg == path or path.startswith(arg + "."):
                        record(field_id, path, "an accessor read", rel)
            elif (rel not in PATH_LITERAL_EXEMPT
                    and isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in by_path):
                record(by_path[node.value], node.value,
                       "the path named as a string literal", rel)
    return hits


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


def scan_items(needle_list, items, excused=None):
    """The value scan over (relpath, text) pairs, whatever produced them."""
    hits = []
    for rel, text in items:
        obj = None
        if rel.endswith(".json"):
            try:
                obj = json.loads(text)
            except ValueError:                             # pragma: no cover
                obj = None
        hits.extend(scan_text(text, needle_list, relpath=rel, structured=obj,
                              excused=excused))
    return hits


def tree_items(root=None, files=None):
    """(relpath, text) for every tracked file, read off the working tree."""
    root = ROOT if root is None else pathlib.Path(root)
    rels = tracked_files(root) if files is None else list(files)
    return [(rel, (root / rel).read_text(errors="ignore"))
            for rel in rels if (root / rel).is_file()], rels


def staged_items(root=None):
    """(relpath, text) for every file this commit would ADD or CHANGE.

    Read out of the INDEX, not the working tree: what the hook has to judge is
    the content about to be committed, which is not always what is on disk.
    """
    root = ROOT if root is None else pathlib.Path(root)
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=root, capture_output=True, text=True, check=True).stdout
    rels = sorted(p for p in names.split("\0")
                  if p and not p.startswith("private/"))
    # one `cat-file --batch` rather than one `git show` per file: this runs on
    # every commit, and 107 process spawns were half its wall time. Paths are
    # newline-delimited on the way in, so a path containing a newline (legal,
    # and absent here) is read singly instead.
    batch = [r for r in rels if "\n" not in r]
    items = []
    if batch:
        out = subprocess.run(
            ["git", "cat-file", "--batch"], cwd=root, check=True,
            input="".join(f":{r}\n" for r in batch).encode(),
            capture_output=True).stdout
        pos = 0
        for rel in batch:
            nl = out.index(b"\n", pos)
            header, pos = out[pos:nl].split(), nl + 1
            if len(header) != 3:            # missing, ambiguous: nothing follows
                continue
            size = int(header[2])
            if header[1] == b"blob":
                items.append((rel, out[pos:pos + size].decode("utf8",
                                                              "replace")))
            pos += size + 1                 # git writes a newline after the data
    for rel in (r for r in rels if "\n" in r):             # pragma: no cover
        blob = subprocess.run(["git", "show", f":{rel}"], cwd=root,
                              capture_output=True, check=False)
        if blob.returncode == 0:
            items.append((rel, blob.stdout.decode("utf8", "replace")))
    return items, rels


def scan_tree(needle_list, root=None, files=None, excused=None):
    """Scan every tracked file. Returns (hits, files scanned)."""
    items, rels = tree_items(root, files)
    return scan_items(needle_list, items, excused=excused), rels


def gate(items, household=None, fields=None, shape=None, excused=None):
    """Every enforceable half of the tier rule, over one set of items.

    Returns (hits, unsearchable), where `hits` carries no value by
    construction. The two halves of the substitute rule run whether or not the
    private file is present; the value scan needs it and is skipped without it,
    which the caller has to report rather than count as clean.
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    unsearchable = unsearchable_fields(household, fields, shape)
    hits = scan_artifact_keys(items, banned_keys(unsearchable))
    hits += scan_script_reads(items, unsearchable)
    if household is not None:
        hits += scan_items(needles(household, fields, shape), items,
                           excused=excused)
    return hits, unsearchable


# ---------------------------------------------------------------------------
# The hook entry point (CLAUDE.md section 4: the local hook is the real gate for
# anything person-specific -- CI has no private file and cannot be).
#
# Three scopes. `--staged` and `--tree` judge file content. `--message <file>`
# judges a proposed commit message, which CLAUDE.md section 4 lists inside the
# boundary in as many words -- "not the report, not data/, not scripts, not
# commit messages" -- and which no content scan can ever see: a pre-commit hook
# runs before the message exists. `.githooks/commit-msg` is where it runs. The
# message is scanned with no literal-span exemption of any kind, because a
# commit message declares no fixtures.
#
# Exit codes, which both hooks read:
#   0  clean
#   1  a tier rule is broken; the commit is blocked
#   2  private/household.yaml is absent, so the value half could not run. Not a
#      failure -- somebody else's clone legitimately has no private file -- but
#      never silent either
#   3  the gate could not run at all; refuse to commit unscanned
# ---------------------------------------------------------------------------
MESSAGE_LABEL = "the commit message"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    scope = "--staged"
    for flag in ("--tree", "--message"):
        if flag in argv:
            scope = flag
    try:
        if scope == "--message":
            where = argv[argv.index("--message") + 1]
            items = [(MESSAGE_LABEL,
                      pathlib.Path(where).read_text(errors="ignore"))]
            rels = [MESSAGE_LABEL]
        elif scope == "--staged":
            items, rels = staged_items()
        else:
            items, rels = tree_items()
        household = None
        if REAL_HOUSEHOLD.is_file():
            household = yaml.safe_load(REAL_HOUSEHOLD.read_text()) or {}
        hits, unsearchable = gate(items, household)
    except Exception as e:                                 # noqa: BLE001
        print(f"privacy tiers: the gate could not run ({type(e).__name__}: "
              f"{e}) -- refusing to pass unscanned.", file=sys.stderr)
        return 3
    subject = (MESSAGE_LABEL if scope == "--message"
               else f"{scope.lstrip('-')} content")
    if hits:
        print(f"privacy tiers: BLOCKED. {len(hits)} tier violation(s) in "
              f"{subject}:", file=sys.stderr)
        for h in sorted({str(x) for x in hits}):
            print(f"  - {h}", file=sys.stderr)
        print("Field ids and paths only -- no value is printed. See "
              "DATA-SOURCES-CHEATSHEET.md for the tier of each field.",
              file=sys.stderr)
        return 1
    scanned = (subject if scope == "--message"
               else f"{len(rels)} {scope.lstrip('-')} file(s)")
    if household is None:
        print(f"privacy tiers: {scanned} clean of the {len(unsearchable)} "
              f"key/path rule(s) that need no private data. NOT CHECKED: the "
              f"real-value scan -- private/household.yaml is absent here, so "
              f"there were no answers to look for.", file=sys.stderr)
        return 2
    print(f"privacy tiers: {scanned} clean ({len(unsearchable)} unsearchable "
          f"field(s) held to the key/path rule).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
