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
  3. turns the private-only values into "needles" and scans every tracked file
     for them -- structurally where the file has a structure to walk (JSON and
     YAML: string leaves and keys, compared for equality), as text everywhere
     else, and both ways for YAML, which has comments outside its structure;
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
    tiers and from `Needle.text_searchable` -- the same floor the scanner
    applies, so the two cannot disagree about which fields the value scan
    reaches -- and `scan_artifact_keys` / `scan_script_reads` hold it to the
    greppable substitute the cheatsheet states: no committed artifact carries a
    key of that name, and no committed script reads that path. State plainly
    what that substitute reaches, because it is not the value: JSON, YAML and
    CSV keys, and python, shell and yaml scripts. The sub-floor word ITSELF
    goes on being unsearched in running prose and in a commit message, and that
    is the floor's whole purpose -- dropping it and searching this household's
    two sub-floor door legends in value position returns four matches on a tree
    that discloses nothing, in index.html and in the two
    service-headroom modules;
  * the same sub-floor word where the FIELD also holds searchable answers. The
    derivation is per field, and a field with one long answer stays with the
    value scan, so a short one beside it is covered by neither half. Pushing
    the derivation down to the leaf was measured and rejected: the leaf here is
    `panel.schedule[].label`, and banning the key `label` fires on nine
    committed artifacts whose labels are chart series and issue-form fields;
  * a value that a file declares as its own literal AND that the same file
    already declared in the committed baseline this change is measured against.
    There is no file class that goes unscanned: a test module and the example
    template are scanned like anything else, and the only thing that can excuse
    a hit in one is that the literal was demonstrably there before this change
    -- so whoever wrote it did not have these answers. Everything else in those
    files -- a comment, a docstring, prose, a reworded literal, a second copy
    of an old one -- fails.

A found needle is reported as a field id, a yaml path and a file. The value
itself is never printed, never written into an assertion message, and never
appears in this file.
"""
import ast
import collections
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
#
# Which of those two applies is decided by the FORMAT, in STRUCTURED_FORMATS
# below, because "in value position" is a question a parser answers exactly and
# a regex only approximates.
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

# ---------------------------------------------------------------------------
# Which formats the scan reads structurally, and how far the structure goes.
#
# The regex above is a guess at where a value ends, and on a format that has a
# grammar it is the wrong tool: `label: Zzyzx # copied from the intake` and
# `{key: Zzyzx}` are both a scalar `Zzyzx` to a parser, and both fell outside a
# pattern that had to enumerate the closing characters itself. Widening the
# pattern only moves the boundary; parsing removes it. So a format this repo
# tracks AND can parse is walked the way JSON already was -- every string leaf
# and every key, compared for equality.
#
#   json  -- structure only. There is nothing outside it: every byte is a key,
#            a value or punctuation, so the walk IS the whole file.
#   yaml  -- structure AND text. YAML has comments, and a private answer quoted
#            in one is published just the same while being no part of the
#            structure. The two readings are a union: the walk catches what the
#            pattern's boundaries missed, the pattern catches what the parser
#            never sees.
#
# Everything else -- markdown, html, shell, plain text, a commit message -- has
# no structure to read and stays with the pattern alone.
# ---------------------------------------------------------------------------
STRUCTURED_FORMATS = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}
TEXT_READ_TOO = ("yaml",)


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

    @property
    def text_searchable(self):
        """Whether unstructured text can be searched for this value at all.

        The ONE place BARE_WORD_TEXT_MIN_CHARS is read, and it is a property
        rather than a check written twice because the two readers used to
        disagree. `_found_in` skips a sub-floor bare word in every file it
        cannot parse, so that needle covers nothing outside the structured
        formats; but
        `unsearchable_fields` counted the mere EXISTENCE of a needle as proof
        the field was covered by the value scan, and so left it out of the
        key/path substitute. A field whose answers are all sub-floor bare words
        fell through both. Both now ask the same question of the same constant,
        which is the discipline the NEC citation table in `service_headroom.py`
        uses: state the rule once, compute every use of it from that statement.
        """
        return (self.mode == "anywhere"
                or len(self.value) >= BARE_WORD_TEXT_MIN_CHARS)

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


class UnparseableArtifact(Exception):
    """A structured artifact the scan could not read, which is not a clean bill.

    A `.json` or `.yaml` file that will not parse is walked by nothing, so the
    equality half of the value scan and the key half of the substitute rule
    both return quiet for it -- quiet for the reason a gate must never be quiet
    for. Skipping it in silence is what this replaces; callers report it and
    refuse to pass the tree.

    It carries the path, the error CLASS and the line, and never the parser's
    own message: a yaml or json error quotes the offending source, and the
    offending source is exactly the content this module may not print.
    """

    def __init__(self, relpath, exc):
        self.relpath = relpath
        self.kind = type(exc).__name__
        mark = getattr(exc, "problem_mark", None)
        self.line = (mark.line + 1 if mark is not None
                     else getattr(exc, "lineno", None))
        where = "" if self.line is None else f", line {self.line}"
        super().__init__(f"{relpath}: {self.kind}{where}")


def structured_format(relpath):
    """The format whose structure `relpath` is read through, or None."""
    if relpath is None:
        return None
    dot = relpath.rfind(".")
    return STRUCTURED_FORMATS.get(relpath[dot:].lower()) if dot >= 0 else None


def parse_structured(relpath, text):
    """The parsed object behind a structured artifact, or None if it is not one.

    Multi-document YAML comes back as the LIST of its documents, so a value in
    the second one is walked like a value in the first. Anchors and aliases are
    resolved by the loader, which makes an alias the same object as its anchor
    and the walk below idempotent about it.

    Raises UnparseableArtifact rather than returning None for a file that
    claims a structured suffix and does not parse.
    """
    fmt = structured_format(relpath)
    if fmt == "json":
        try:
            return json.loads(text)
        except ValueError as e:
            raise UnparseableArtifact(relpath, e) from None
    if fmt == "yaml":
        try:
            return list(yaml.safe_load_all(text))
        except yaml.YAMLError as e:
            raise UnparseableArtifact(relpath, e) from None
    return None


def structured_strings(obj):
    """Every string a parsed artifact publishes, keys included.

    Keys are stringified rather than assumed to be strings: YAML types its
    scalars, so `on:` in a workflow is the key `True` and a date key is a
    `datetime.date`. The visited set is there for the same reason -- a yaml
    anchor can alias its own container, and the loader builds the cycle.
    """
    got, seen = set(), set()

    def walk(o):
        if isinstance(o, (dict, list)):
            if id(o) in seen:
                return
            seen.add(id(o))
        if isinstance(o, dict):
            for k, v in o.items():
                got.add(str(k).lower())
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            got.add(o.lower())
    walk(obj)
    return got


# ---------------------------------------------------------------------------
# The one thing that can excuse a hit, and it is the repository's own history.
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
# What replaces it is a question the repository can answer about itself, for any
# household: WAS THIS LITERAL ALREADY HERE? A literal that has been in the
# committed tree since before this change is by construction not something this
# user just pasted -- whoever wrote it did not have these answers. A literal
# this change INTRODUCES or REWORDS, and that matches a private answer, is
# exactly the paste the gate exists to catch. So a needle found inside a
# declared literal span is excused only where the BASELINE version of that same
# file already declares the same literal, and only as many times as the baseline
# declares it.
#
# Nothing in that rule is household-specific, which is the whole point. The
# version this replaces was a committed table of how many of one household's
# answers coincided with the fixtures in each file -- 1, 3 and 2 here. It holds
# for one house. Anyone who clones this repo, fills in their own
# private/household.yaml and enables the hook (the flow README.md documents and
# CLAUDE.md section 12 requires) has their own door legends and meter class
# coinciding with those same fixtures a DIFFERENT number of times, and every
# commit they made would have been blocked as a stale row until they edited
# shared, committed scanner code to match their own private answers. That is
# hostile, and it is a standing invitation to write a household-specific number
# into a committed file.
#
# The baseline rule is also strictly stronger than a count. A count cannot see a
# swap that keeps the total; and it excused every occurrence of a value once one
# was declared, so a fixture label pasted a SECOND time -- into a sentence that
# says what it is -- moved no count. Here the second occurrence is a second
# span, the baseline holds one, and it fails.
#
# What it costs, stated plainly: a private value that was already committed
# inside one of those three classes' literals before this rule existed goes on
# being excused, exactly as the count rule excused it. That is why the class
# list stays closed. For every other file -- every artifact in data/, every line
# of prose in index.html and TECHNICAL.md, where the leak that prompted all this
# actually landed -- no span is eligible and nothing is ever excused by age.
#
# The resolution for a blocked fixture is always available and needs no
# configuration: a fixture value is invented, so change it. That is what makes
# the rule safe to apply to everyone.
#
# Spans come from the AST node positions of a python module, the composer marks
# of a yaml document and the `question:` scalars of the cheatsheet, and never
# from blanking every occurrence of a string. That difference is why a value
# declared once as a fixture and then also quoted in a comment still fails on
# the comment -- the shape the leak this gate found actually took. For the same
# reason a python string that is a STATEMENT (a docstring, or any free-standing
# string expression) is prose, not a fixture literal, and its span is never
# eligible.
#
# The comparison is on the literal's VALUE rather than on its source bytes:
# re-quoting a fixture, or moving it to another line, does not make it new.
# ---------------------------------------------------------------------------
DECLARED_LITERAL_SOURCES = ("test-module", "example-template", "cheatsheet")

# The refs a change is measured against, in order. The merge base is preferred,
# so a branch is judged against the trunk it will land on rather than against
# its own last commit -- otherwise a value pasted in an earlier commit on the
# same branch launders itself into the baseline of the next one. The remote
# spellings are there for a clone that has no local trunk branch. HEAD is the
# fallback: a clone sitting on the default branch, a shallow clone with no merge
# base, a repo whose trunk is named something else. A repository with no commits
# at all -- the throwaway trees the controls build -- has no baseline, and
# nothing is excused there.
TRUNK_NAMES = ("main", "master", "origin/main", "origin/master")


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


def _python_literals(text):
    """The string literals a python module declares, with their source spans.

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
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant)
                and isinstance(node.value, str)) or id(node) in prose:
            continue
        if node.end_lineno is None:                        # pragma: no cover
            continue
        out.append((_char_offset(text, offs, node.lineno, node.col_offset),
                    _char_offset(text, offs, node.end_lineno,
                                 node.end_col_offset),
                    node.value))
    return out


def _yaml_value_literals(text, base=0):
    """The string SCALARS a yaml document holds as values, with their spans.

    Composed rather than loaded, because only the composer keeps the marks. A
    mapping's keys are not values and are not eligible.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:                                 # pragma: no cover
        return []
    out = []

    def walk(node):
        if isinstance(node, yaml.ScalarNode):
            if node.tag.endswith(":str"):
                out.append((base + node.start_mark.index,
                            base + node.end_mark.index, node.value))
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                walk(child)
        elif isinstance(node, yaml.MappingNode):
            for _key, value in node.value:
                walk(value)
    if root is not None:
        walk(root)
    return out


def _cheatsheet_question_literals(text):
    """Each field block's `question:` scalar, with its span, and nothing else.

    Lenient on purpose: this is a scoping rule, not the shape check, and it has
    to work on a synthetic cheatsheet in the positive control.
    """
    out = []
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
                out.append((m.start(1) + value.start_mark.index,
                            m.start(1) + value.end_mark.index, value.value))
    return out


def declared_literals(relpath, text):
    """(start, end, value) for each literal a file declares as its own.

    Empty for every file outside the three classes above, so the default is
    that every byte of a file is scanned. Spans rather than strings, because a
    value declared once as a fixture and quoted again in a comment must still
    fail on the comment; values as well as spans, because the baseline
    comparison is on what the literal SAYS, so re-quoting one or moving it to
    another line does not make it new.
    """
    kind = _file_class(relpath)
    if kind == "test-module":
        return _python_literals(text)
    if kind == "example-template":
        return _yaml_value_literals(text)
    if kind == "cheatsheet":
        return _cheatsheet_question_literals(text)
    return []


def _git_out(root, *args):
    """stdout of a git command, or None if it failed. Never raises."""
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True)
    except OSError:                                        # pragma: no cover
        return None
    return r.stdout if r.returncode == 0 else None


class Baseline:
    """The committed content a change is measured against.

    `text(relpath)` is that file as of the baseline ref, or None where the ref
    does not hold it -- a file this change adds, or a repository with no
    commits. Reads are lazy and memoized, and the only file ever fetched is one
    whose own declared literals hold a private answer, which on an ordinary
    commit is none of them: the baseline costs nothing until a collision exists.

    A default-constructed Baseline knows nothing, so nothing is excused. That is
    the right default for a caller holding one file's text and no repository --
    every positive control in `test_privacy_tiers.py` relies on it.
    """

    __slots__ = ("root", "ref", "_texts", "_cache")

    def __init__(self, root=None, ref=None, texts=None):
        self.root = pathlib.Path(root) if root is not None else None
        self.ref = ref
        self._texts = dict(texts) if texts else {}
        self._cache = {}

    @classmethod
    def of(cls, root=None):
        """The baseline of a working repository: merge base, else HEAD."""
        root = ROOT if root is None else pathlib.Path(root)
        if _git_out(root, "rev-parse", "--verify", "-q", "HEAD") is None:
            return cls()                       # no commits: nothing pre-exists
        for trunk in TRUNK_NAMES:
            out = _git_out(root, "merge-base", "HEAD", trunk)
            if out and out.strip():
                return cls(root, out.decode().strip())
        return cls(root, "HEAD")

    @property
    def label(self):
        return (self.ref or "no baseline")[:12]

    def text(self, relpath):
        if relpath in self._texts:
            return self._texts[relpath]
        if self.root is None or self.ref is None or relpath is None:
            return None
        if relpath not in self._cache:
            blob = _git_out(self.root, "show", f"{self.ref}:{relpath}")
            self._cache[relpath] = (None if blob is None
                                    else blob.decode("utf8", "replace"))
        return self._cache[relpath]

    def __repr__(self):                                    # pragma: no cover
        return f"<Baseline {self.label}>"


def _mask(text, spans):
    """Blank out the given spans, keeping every offset where it was."""
    if not spans:
        return text
    chars = list(text)
    for a, b in spans:
        for i in range(max(a, 0), min(b, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _found_in(text, needle_list, structured=None, as_text=True):
    """The needles that occur in one body of text. Returns needles, not Hits.

    `structured` is the parsed object when the file has a structure to walk;
    `as_text` says whether the pattern is run over the bytes as well. The two
    are independent because the formats differ: JSON is structure only, YAML is
    both, everything else is text only.
    """
    low = text.lower()
    strings = structured_strings(structured) if structured is not None else None
    out = []
    for n in needle_list:
        v = n.value.lower()
        if n.mode == "anywhere":
            found = v in low
        else:
            found = strings is not None and v in strings
            if not found and as_text and n.text_searchable:
                found = bool(re.search(_VALUE_POSITION % re.escape(v), low,
                                       re.M))
        if found:
            out.append(n)
    return out


def scan_text(text, needle_list, relpath=None, structured=None, baseline=None,
              excused=None):
    """Needles found in one file's text. Returns Hits, never values.

    A file whose suffix names a format in STRUCTURED_FORMATS is parsed here and
    walked: a bare word is compared for equality against the artifact's own
    string leaves and keys, which is the strict reading of "published as a
    value" and the only reading that gets the boundaries right. YAML is read as
    text as well, for what its comments carry. `structured` is for a caller
    that already holds the parsed object and no path to derive the format from;
    it is read as JSON, which is the only shape any caller passes.

    Raises UnparseableArtifact for a file that claims a structured suffix and
    does not parse -- an artifact the walk cannot read is not a clean one.

    The scan reads every byte of the file EXCEPT the literal spans this file
    declares and the baseline version of the same file already declared -- and
    those only as many times as the baseline declared them. So a comment, a
    docstring, prose, a reworded literal, a brand-new literal and a second copy
    of an old one are all read and all fail; only a literal that was already
    there is skipped. `baseline` is the Baseline to ask; the default knows
    nothing, and then nothing is excused.

    `excused` is an optional Counter, keyed (relpath, leaf path), recording the
    pre-existing literals actually skipped. Counts and paths only -- there is no
    value in any of it -- and it is what lets a caller report the size of the
    excuse instead of asserting a number nobody can see.
    """
    literals = declared_literals(relpath, text) if relpath is not None else []
    fmt = structured_format(relpath) or ("json" if structured is not None
                                         else None)
    as_text = fmt not in ("json",)
    reparse = structured is None and structured_format(relpath) is not None
    if reparse:
        structured = parse_structured(relpath, text)

    def look(t):
        """The needles in one rendering of this file, structure included.

        Masking a declared literal has to blank it out of the PARSE as well,
        or a value the baseline already excused would come back through the
        structural half. Blanking a scalar leaves a well-formed document -- the
        key keeps its colon and loses its value -- but if some shape does not
        survive it, the unmasked parse is used and the excuse simply does not
        apply, which errs toward a hit.
        """
        s = structured
        if reparse and t is not text:
            try:
                s = parse_structured(relpath, t)
            except UnparseableArtifact:                     # pragma: no cover
                s = structured
        return _found_in(t, needle_list, s, as_text)

    def hits_for(found):
        return [Hit(n.field_id, n.path, n.leaf_path, n.mode, relpath)
                for n in found]

    if not literals:
        return hits_for(look(text))
    # the fast path, and it is the usual one: if blanking EVERY declared literal
    # changes nothing, no needle sits inside one, so there is nothing to excuse
    # and the baseline is never read.
    everywhere = look(text)
    free = look(_mask(text, [(a, b) for a, b, _ in literals]))
    if len(free) == len(everywhere):
        return hits_for(everywhere)

    base = baseline.text(relpath) if baseline is not None else None
    already = collections.Counter(
        v for _a, _b, v in declared_literals(relpath, base)) if base else \
        collections.Counter()
    keep = []
    for a, b, v in literals:
        if already[v]:
            already[v] -= 1
            keep.append((a, b))
    if excused is not None:
        for a, b in keep:
            for n in _found_in(text[a:b], needle_list):
                excused[(relpath, n.leaf_path)] += 1
    return hits_for(look(_mask(text, keep)))


def leaks(needle_list, text, obj=None, relpath=None, baseline=None):
    """scan_text with the argument order the artifact scan already used."""
    return scan_text(text, needle_list, relpath=relpath, structured=obj,
                     baseline=baseline)


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
#   * the household's recorded answer is a scalar that yields no needle THE
#     SCANNER CAN USE -- `Needle.text_searchable`, read off the same floor: too
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
# skipped in silence. It stays subject to every value rule above, baseline
# included: a placeholder it did not already carry is a hit like any other.
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

    A field counts as covered by the value scan only where it has a needle the
    SCANNER can actually use -- `Needle.text_searchable`, off the same floor
    constant `_found_in` applies. Counting every needle instead was the drift:
    `needles` builds one for a bare string of any length, so a field answered
    with a short bare word looked searched here and was skipped there.
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    searched = ({n.field_id for n in needles(household, fields, shape)
                 if n.text_searchable}
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

    Both formats are parsed through `parse_structured`, which raises rather
    than skipping a file it cannot read: a key rule that returns quiet for an
    artifact nothing parsed is quiet for the wrong reason.
    """
    hits = []
    for rel, text in items:
        keys = set()
        if rel in KEY_RULE_EXEMPT:
            continue
        if structured_format(rel) is not None:
            _json_keys(parse_structured(rel, text), keys)
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


def read_paths(path):
    """Every accessor argument that constitutes a read of `path`.

    A read of a CONTAINER hands over everything underneath it, so an ancestor
    is a read of the leaf, which is why this is a set and not an equality. The
    set is derived from the path's own structure rather than by comparing
    string prefixes, because the contract's `[]` marker is notation and not
    part of any key name: `monitoring[].url` is handed over by `monitoring[]`
    and, since that is how the accessor spells it, by `monitoring` -- and a
    prefix test on the dotted string sees neither, which left the three tiered
    keys inside `monitoring[]` reachable by `HH.get("monitoring")` with nothing
    to say about it. Walking the segments gives every intermediate container
    for free, so `a[].b[].c` needs no further patch.
    """
    out, prefix = set(), ""
    for seg in path.split("."):
        prefix = f"{prefix}.{seg}" if prefix else seg
        out.add(prefix)
        if prefix.endswith("[]"):
            out.add(prefix[:-2])
    return out


def scan_script_reads(items, unsearchable):
    """Half two: a committed script reading the path.

    In python, two shapes, because one alone is not enough. The accessor call
    `hh.get("solar.itc_claimed")` is the direct read, and a read of an ANCESTOR
    (`HH.get("solar")`, `HH.get("monitoring")`) pulls the key out with it --
    `read_paths` above is the whole set, marker included. The bare path literal
    catches the indirection `P = "solar.itc_claimed"` ... `hh.get(P)`, and is
    matched by exact string equality so that a docstring or a comment MENTIONING
    the path -- which several files legitimately do -- is not a read.

    Ancestors are the accessor call's business alone. The other two shapes
    match a bare token, and a container's bare name is `panel`, `solar`,
    `monitoring`: banning those as tokens would fire on most of the tree and
    say nothing. A parsed accessor call naming a container is unambiguous; a
    word in a shell script is not.

    Python is not the only committed script here: the repo tracks shell (the
    hooks, `check_coverage.sh`, `stage-private-data.sh`) and yaml (the
    workflows), and a rule that skipped every file without a `.py` suffix left
    those returning clean. There is no AST for them, so the search is the
    conservative shape: the dotted path as a WHOLE TOKEN, outside comments. The
    prose files -- markdown, html -- are deliberately not searched: several of
    them have to name these paths in order to document the rule.
    """
    by_path = {path: field_id for field_id, path in unsearchable.items()}
    reads = {path: read_paths(path) for path in by_path}
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
                    if arg in reads[path]:
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


def scan_items(needle_list, items, baseline=None, excused=None):
    """The value scan over (relpath, text) pairs, whatever produced them.

    `scan_text` decides from the path which files have a structure to walk and
    parses them itself, and raises UnparseableArtifact for one that claims a
    structured suffix and does not parse. That propagates: an artifact nothing
    could read is reported, never counted as scanned.
    """
    hits = []
    for rel, text in items:
        hits.extend(scan_text(text, needle_list, relpath=rel,
                              baseline=baseline, excused=excused))
    return hits


def tree_items(root=None, files=None):
    """(relpath, text) for every tracked file, read off the working tree."""
    root = ROOT if root is None else pathlib.Path(root)
    rels = tracked_files(root) if files is None else list(files)
    return [(rel, (root / rel).read_text(errors="ignore"))
            for rel in rels if (root / rel).is_file()], rels


def index_items(root, rels):
    """(relpath, text) read out of the INDEX for the given paths.

    The index, not the working tree: what a hook has to judge is the content
    about to be committed, which is not always what is on disk. A path the
    index does not hold is silently absent from the result -- the caller
    decides what that means.
    """
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
    return items


def staged_items(root=None):
    """(relpath, text) for every file this commit would ADD or CHANGE."""
    root = ROOT if root is None else pathlib.Path(root)
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=root, capture_output=True, text=True, check=True).stdout
    rels = sorted(p for p in names.split("\0")
                  if p and not p.startswith("private/"))
    return index_items(root, rels), rels


def scan_tree(needle_list, root=None, files=None, baseline=None, excused=None):
    """Scan every tracked file. Returns (hits, files scanned).

    Unlike `scan_text`, this one knows which repository it is reading, so it
    resolves that repository's baseline unless the caller passes one. A tree
    with no commits resolves to a Baseline that knows nothing, which is what the
    synthetic controls want and get without asking.
    """
    items, rels = tree_items(root, files)
    baseline = Baseline.of(root) if baseline is None else baseline
    return scan_items(needle_list, items, baseline=baseline,
                      excused=excused), rels


class UnresolvableField(Exception):
    """A tiered field whose subject cannot be located, which is a broken rule.

    Raised by `gate` BEFORE it scans anything. A field id that resolves to no
    household.yaml path -- mistyped, renamed, or given a shape steps 3-5 cannot
    derive -- yields no needle and bans no key, so the rule someone wrote in the
    cheatsheet is enforced against nothing and every scan of it comes back
    clean. That is the one failure this gate must never report as a pass, and
    until now only the suite caught it: the hook, which CLAUDE.md section 4
    calls the real gate, scanned on and said nothing.

    It is a property of the cheatsheet and the committed schema template alone,
    so it is checkable everywhere -- in CI, and in a clone with no
    private/household.yaml -- and it is checked there.
    """

    def __init__(self, fields):
        self.fields = list(fields)
        super().__init__("; ".join(f"{fid} -> {path}"
                                   for fid, path in self.fields))


def gate(items, household=None, fields=None, shape=None, baseline=None,
         excused=None):
    """Every enforceable half of the tier rule, over one set of items.

    Returns (hits, unsearchable), where `hits` carries no value by
    construction. The two halves of the substitute rule run whether or not the
    private file is present; the value scan needs it and is skipped without it,
    which the caller has to report rather than count as clean.

    Raises UnresolvableField before scanning if any tiered field has no
    locatable subject -- a scan that cannot find what a rule is about must not
    return a clean bill for it, and UnparseableArtifact if a file claiming a
    structured suffix will not parse, for the same reason one step down.
    """
    fields = cheatsheet_fields() if fields is None else fields
    shape = schema() if shape is None else shape
    unresolvable = resolution_report(fields=fields, shape=shape)["unresolvable"]
    if unresolvable:
        raise UnresolvableField(unresolvable)
    unsearchable = unsearchable_fields(household, fields, shape)
    hits = scan_artifact_keys(items, banned_keys(unsearchable))
    hits += scan_script_reads(items, unsearchable)
    if household is not None:
        hits += scan_items(needles(household, fields, shape), items,
                           baseline=baseline, excused=excused)
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
# The two file scopes measure the content against the repository's own
# BASELINE: a needle inside a declared literal span is excused only where the
# baseline version of that file already declared the same literal. That check
# belongs at the gate that blocks commits and not only in a suite nobody is
# obliged to run -- CI, which holds no private values, can never make it. It
# needs no configuration and no committed count, so it holds for any household
# that clones this repo; `Baseline` and the block comment above it say why the
# count it replaces did not. The `--message` scope has no literal spans at all,
# so no baseline is read for it.
#
# Before any of that, the gate checks that every tiered field still resolves to
# a locatable subject, and refuses to scan if one does not: a rule pointed at
# nothing reports every tree as clean, which is the one failure a gate must
# never dress up as a pass. That check reads only the cheatsheet and the
# committed schema, so it runs in a clone with no private file too.
#
# Exit codes, which both hooks read:
#   0  clean
#   1  a tier rule is broken, or a tiered field resolves to nothing; the commit
#      is blocked
#   2  private/household.yaml is absent, so the value half could not run. Not a
#      failure -- somebody else's clone legitimately has no private file -- but
#      never silent either
#   3  part of the content could not be scanned -- the gate failed outright, or
#      a file claiming a structured suffix would not parse, so nothing walked
#      it; refuse to commit unscanned
# ---------------------------------------------------------------------------
MESSAGE_LABEL = "the commit message"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    scope = "--staged"
    for flag in ("--tree", "--message"):
        if flag in argv:
            scope = flag
    try:
        baseline = Baseline()
        if scope == "--message":
            where = argv[argv.index("--message") + 1]
            items = [(MESSAGE_LABEL,
                      pathlib.Path(where).read_text(errors="ignore"))]
            rels = [MESSAGE_LABEL]
        elif scope == "--staged":
            items, rels = staged_items()
            baseline = Baseline.of()
        else:
            items, rels = tree_items()
            baseline = Baseline.of()
        household = None
        if REAL_HOUSEHOLD.is_file():
            household = yaml.safe_load(REAL_HOUSEHOLD.read_text()) or {}
        excused = collections.Counter()
        hits, unsearchable = gate(items, household, baseline=baseline,
                                  excused=excused)
    except UnresolvableField as e:
        print(f"privacy tiers: BLOCKED. {len(e.fields)} cheatsheet field(s) "
              f"resolve to no household.yaml path, so their tier is enforced "
              f"against nothing and every scan of them reports clean:",
              file=sys.stderr)
        for fid, path in e.fields:
            print(f"  - {fid} -> {path}", file=sys.stderr)
        print("Give each a row in YAML_PATH_OVERRIDES, declare it in "
              "PATHLESS_FIELDS if it stores no value, or fix the id. See the "
              "path contract in DATA-SOURCES-CHEATSHEET.md.", file=sys.stderr)
        return 1
    except UnparseableArtifact as e:
        at = "" if e.line is None else f" at line {e.line}"
        print(f"privacy tiers: BLOCKED. {e.relpath} claims a structured format "
              f"and does not parse ({e.kind}{at}), so nothing walked its keys "
              f"or its values and the scan of it would report clean for the "
              f"wrong reason. Fix the file, or give it a suffix that does not "
              f"claim a format. (The parser's own message is withheld: it "
              f"quotes the offending source.)", file=sys.stderr)
        return 3
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
              "DATA-SOURCES-CHEATSHEET.md for the tier of each field. A hit in "
              "a test fixture or in household.example.yaml means the literal is "
              "NEW since the baseline: if it is an invented placeholder, invent "
              "a different one.", file=sys.stderr)
        return 1
    scanned = (subject if scope == "--message"
               else f"{len(rels)} {scope.lstrip('-')} file(s)")
    if household is None:
        print(f"privacy tiers: {scanned} clean of the {len(unsearchable)} "
              f"key/path rule(s) that need no private data. NOT CHECKED: the "
              f"real-value scan -- private/household.yaml is absent here, so "
              f"there were no answers to look for.", file=sys.stderr)
        return 2
    total = sum(excused.values())
    accounted = ("" if not total else
                 f", {total} pre-existing fixture literal(s) in "
                 f"{len(excused)} file/path pair(s) excused as already "
                 f"committed at {baseline.label}")
    print(f"privacy tiers: {scanned} clean ({len(unsearchable)} unsearchable "
          f"field(s) held to the key/path rule{accounted}).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
