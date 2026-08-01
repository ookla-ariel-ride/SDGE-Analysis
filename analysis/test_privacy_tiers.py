#!/usr/bin/env python3
"""The repo-wide intake-privacy gate.

CLAUDE.md §4 makes the intake tiers binding: no `private-only` or `secret`
answer may be written into any committed artifact. Until now nothing enforced
that at the artifact layer. The gitleaks hook screens for secret and PII
*patterns* -- an account number, an API key, a street address -- and a field
somebody tiered private-only last week matches no pattern at all. A private-only
figure duly reached three committed files, in eleven places, and six of those
were in index.html and TECHNICAL.md, which no generator writes. That is why the
scan here is driven from `git ls-files` rather than from data/: a gate scoped to
generator output would have found fewer than half of it.

What runs where:

  * WITHOUT private/household.yaml (CI): the resolver contract, the tier
    vocabulary, the synthetic positive controls -- which plant invented values
    in a temporary git tree and prove the scan is quiet on a clean tree, fires
    on a planted one, and that its scoping rule covers a declared literal's own
    source span and nothing more -- and BOTH halves of the substitute rule for
    the fields no literal scan can cover, which look for key names and dotted
    paths and so need no private data at all.
  * ONLY with private/household.yaml: the two cases that need real values --
    the repo-wide scan itself, and the reverse gate that every intake key is
    tiered. They raise SkipCase, and `main` prints what was skipped and why in
    a banner, because a green check that does not say what it did not check is
    worse than a red one. CI cannot ever run those two: the private file is
    gitignored and shipping it to a runner would be the leak this gate exists
    to prevent. `.githooks/pre-commit` is where they run on every commit,
    which is what CLAUDE.md §4 means by "the local hook is the real gate".

Nothing in this file contains a value from the household. Findings are reported
as a field id, a yaml path and a filename; the positive controls plant values
they invent themselves.

Run from the repo root:  ./.venv/bin/python analysis/test_privacy_tiers.py
"""
import collections
import json
import pathlib
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import privacy_tiers as PT


class SkipCase(Exception):
    """A case that cannot run here, raised rather than returned."""


# ---------------------------------------------------------------------------
# Intake keys that cannot be tiered, each with the reason. Empty, and meant to
# stay that way: the point of the reverse gate is that a new intake key with no
# cheatsheet field is invisible to the leak scan by construction, so its
# absence has to break this suite rather than quietly shrink the universe. An
# entry here is a decision someone wrote down, not a way to make the gate go
# quiet -- the case below rejects an entry with no reason attached.
# ---------------------------------------------------------------------------
UNTIERABLE_KEYS = {}


def case_the_tier_vocabulary_is_closed():
    """Every block declares the same six keys and one of three tiers.

    The universe of private values is read out of these blocks. A block that
    fails to parse, forgets its tier, or reuses an id would shrink that
    universe without changing anything a reader would notice.
    """
    fields = PT.cheatsheet_fields()
    counts = collections.Counter(f["privacy"] for f in fields)
    assert set(counts) <= set(PT.TIERS), sorted(counts)
    assert sum(counts.values()) == len(fields), counts
    assert counts["private-only"], "no private-only field left in the cheatsheet"
    for f in fields:
        assert set(f) >= PT.FIELD_KEYS, (f["id"], sorted(PT.FIELD_KEYS - set(f)))
        assert isinstance(f["required_if"], str) and f["required_if"], f["id"]
    ids = [f["id"] for f in fields]
    assert len(ids) == len(set(ids)), "duplicate field id"
    return (f"{len(fields)} field blocks, all six keys present, tiers closed "
            f"({counts['public-ok']} public-ok, {counts['private-only']} "
            f"private-only, {counts['secret']} secret)")


def case_every_field_id_resolves_or_is_declared_path_less():
    """The resolver contract, which is the case the old scan could not fail.

    Its completeness assertion keyed on `flags.get(required_if) is True`, and
    `always` is not a flag name, so every field gated on `always` was skipped
    and the assertion matched nothing. Here the claim is made against the
    committed schema instead: every id either resolves to a path
    household.example.yaml has room for, or is declared to store no value.
    """
    fields = PT.cheatsheet_fields()
    report = PT.resolution_report(fields=fields)
    assert not report["unresolvable"], (
        "intake id(s) that resolve to no household.yaml path: "
        + ", ".join(f"{fid} -> {path}" for fid, path in report["unresolvable"])
        + " -- give each a row in YAML_PATH_OVERRIDES, or declare it in "
          "PATHLESS_FIELDS if it stores no value")
    assert sum(len(v) for v in report.values()) == len(fields), report
    for fid, why in report["pathless"]:
        assert why, fid
    # the declared list may not quietly absorb a field that does hold a value:
    # step 1 wins over step 2, so an id in both would silently lose its path
    shape = PT.schema()
    both = sorted(set(PT.PATHLESS_FIELDS) & set(PT.YAML_PATH_OVERRIDES))
    assert not both, (
        f"{both} are declared path-less AND have an override row -- step 1 "
        f"wins, so the row is dead and one of the two is wrong")
    resolvable = len(report["pathless"]) + len(report["resolved"]) \
        + len(report["absent"])
    assert resolvable == len(fields), report
    assert len(shape) >= 8, "household.example.yaml has lost its blocks"
    return (f"all {len(fields)} ids resolve: {len(report['pathless'])} declared "
            f"path-less, {resolvable - len(report['pathless'])} to a path the "
            f"committed schema holds, 0 unresolvable")


def case_the_resolver_reaches_a_key_inside_a_list():
    """Section E4 tiers three keys that live inside `monitoring[]` entries.

    A resolver that walks dotted keys through dictionaries alone reaches none
    of them: `monitoring.url` is not a path, so all three were silently
    unenforced -- tiered, and never looked for. Synthetic, so it runs in CI.
    """
    shape = PT.schema()
    listy = [fid for fid in ("monitoring_url", "monitoring_api",
                             "monitoring_owned_by")
             if "[]" in (PT.yaml_path_for(fid, shape) or "")]
    assert len(listy) == 3, listy
    assert PT.yaml_path_for("monitoring_feeds", shape) == "monitoring[]", \
        "the list container's own tier no longer binds the list"

    house = {"monitoring": [{"url": "https://example.invalid/site-90001",
                             "source": "TESTFEED"},
                            {"owned_by": "Testfolk"},
                            {"source": "TESTFEED"}]}
    values, found = PT.resolve(house, "monitoring[].url")
    assert found and len(values) == 1, values
    # a key in no entry is absent, not a crash; a key in some entries binds
    # in those, which is the contract's "entries without the key hold nothing"
    assert PT.resolve(house, "monitoring[].api") == ([], False)

    fields = PT.cheatsheet_fields()
    got = {n.leaf_path for n in PT.needles(house, fields)}
    assert "monitoring[].url" in got and "monitoring[].owned_by" in got, got
    # monitoring[].source carries its own public-ok tier, so the container's
    # private-only tier does not make its value a needle
    assert "monitoring[].source" not in got, got

    # the walk this replaces, run on the same data: dotted keys through
    # dictionaries alone, which is why all three fields read as clean
    node = house
    for key in "monitoring.url".split("."):
        node = node.get(key) if isinstance(node, dict) else None
    assert node is None, "the old dict-only walk should reach nothing here"
    return ("keys inside monitoring[] resolve and produce needles; the "
            "public-ok keys inside the same list do not")


def case_no_private_only_value_appears_in_any_tracked_file():
    """The gate. Every tracked file outside private/, against every
    private-only intake value this household actually holds.

    Reports what it scanned and how much it was looking for, because a clean
    bill is only worth the size of the universe behind it.
    """
    if not PT.REAL_HOUSEHOLD.is_file():
        raise SkipCase("the repo-wide leak scan needs private/household.yaml "
                       "(gitignored) -- it has no values to look for without it")
    household = yaml.safe_load(PT.REAL_HOUSEHOLD.read_text()) or {}
    fields = PT.cheatsheet_fields()
    needles = PT.needles(household, fields)
    assert needles, (
        "no private-only value resolved out of private/household.yaml -- a "
        "scan with nothing to look for is a broken scan, not a clean bill")
    report = PT.resolution_report(household, fields)
    assert not report["unresolvable"], report["unresolvable"]

    hits, files = PT.scan_tree(needles)
    assert not hits, (
        "private-only intake value(s) in tracked file(s): "
        + "; ".join(sorted(str(h) for h in hits)))

    where = collections.Counter(f.split("/")[0] if "/" in f else "(root)"
                                for f in files)
    modes = collections.Counter(n.mode for n in needles)
    return (f"{len(needles)} needles ({modes['anywhere']} searched anywhere, "
            f"{modes['as a value']} as a value) over {len(files)} tracked "
            f"files [{', '.join(f'{k}: {v}' for k, v in sorted(where.items()))}]"
            f"; {len(report['resolved'])} fields resolved, "
            f"{len(report['absent'])} legitimately absent, "
            f"{len(report['pathless'])} declared path-less")


def _tree(root, files):
    """Write files into a fresh git tree and index them, so git ls-files sees
    them. The scan's scope is the index, which is the thing that gets pushed."""
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


# A household of invented values, used by every control below. Nothing here
# comes from any real intake: the point of a positive control is that it runs
# in CI, where no private file exists.
PLANTED = {
    "panel": {"meter_class": "CL999-TEST",
              "enclosure_catalog": "TESTCO ZZ-0001",
              "schedule": [{"device": "TESTCO XY-1234", "poles": 2,
                            "amps": 60, "label": "Zzyzx"}]},
    "monitoring": [{"url": "https://example.invalid/site-90001",
                    "owned_by": "Testfolk", "source": "TESTFEED"}],
}

CLEAN_TREE = {
    "README.md": "# A repository\n\nIt describes a panel and a meter.\n",
    "data/results.json": json.dumps({"service_rating_a": 175,
                                     "note": "120/240 V residential service"},
                                    indent=1),
    "analysis/thing.py": "VALUE = 42  # a number\n",
}


def case_the_repo_scan_catches_a_planted_value():
    """The positive control. A scan that never fires is indistinguishable from
    a scan that cannot fire, and this one decides whether a privacy claim ships.

    Both modes get their own plant, because the two are looked for by different
    means: one inside running prose, one published as a JSON value.
    """
    needles = PT.needles(PLANTED)
    assert needles, "the planted household produced no needles"
    assert not any(n.value in ("60", "2") for n in needles), (
        "a bare ampere rating or pole count is being treated as identifying")

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        _tree(root, CLEAN_TREE)
        hits, files = PT.scan_tree(needles, root=root)
        assert not hits, f"the scan fired on a clean tree: {hits}"
        assert len(files) == len(CLEAN_TREE), files

    dirty = dict(CLEAN_TREE)
    # (a) inside markdown prose -- the shape the real incident took
    dirty["TECHNICAL.md"] = (
        "## Provenance\n\nThe service was read off the panel; the utility "
        "meter is class CL999-TEST, which bounds the current.\n")
    # (b) published as a JSON value, in a file no prose scan would question
    dirty["data/results.json"] = json.dumps(
        {"service_rating_a": 175, "circuits": ["Zzyzx"]}, indent=1)
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        _tree(root, dirty)
        hits, _ = PT.scan_tree(needles, root=root)
        by_file = {h.where: h for h in hits}
        assert "TECHNICAL.md" in by_file, \
            f"a private value quoted in prose was not caught: {hits}"
        assert by_file["TECHNICAL.md"].field_id == "panel_meter_class", by_file
        assert by_file["TECHNICAL.md"].mode == "anywhere", by_file
        assert "data/results.json" in by_file, \
            f"a private value published as a JSON value was not caught: {hits}"
        assert by_file["data/results.json"].leaf_path == \
            "panel.schedule[].label", by_file
    return ("the repo scan is quiet on a clean tree and fires on a value "
            "planted in markdown prose and on one published as a JSON value")


def case_the_scoping_rules_scope_to_a_region_not_to_a_file():
    """The two documented exemptions, controlled the way the scan itself is.

    Each exists because the alternative is a gate nobody can keep green, and
    each is narrow: a region of a file, or the literals a file declares. The
    control proves the rest of those same files is still scanned -- an
    exemption that swallowed the file would be the leak it is meant to
    prevent.
    """
    needles = PT.needles(PLANTED)

    # 1. the cheatsheet's own question text enumerates standard values (the
    #    four meter classes, for one), and a household's answer is one of them.
    #    An enumeration is not an answer -- but only the question is excluded.
    cheat_ok = ('```yaml\nid: panel_meter_class\n'
                'question: "What class is the meter (e.g. CL999-TEST)?"\n'
                'type: string\nrequired_if: always\nwhere: "on the meter"\n'
                'privacy: private-only\n```\n')
    cheat_bad = cheat_ok + "\nThis household's meter is CL999-TEST.\n"
    name = "DATA-SOURCES-CHEATSHEET.md"
    assert not PT.scan_text(cheat_ok, needles, relpath=name), \
        "the cheatsheet's own enumeration of standard values counted as a leak"
    assert PT.scan_text(cheat_bad, needles, relpath=name), \
        "prose outside the question text is not being scanned"

    # 2. a synthetic fixture declares its own invented values, and invented
    #    door legends collide with real ones. What the module DECLARES is
    #    excluded; a value that arrives through a comment or a docstring is not.
    fixture = 'PANEL = [{"label": "Zzyzx", "amps": 20}]\n'
    commented = fixture + '# the meter here is class CL999-TEST\n'
    tname = "analysis/test_demo.py"
    assert not PT.scan_text(fixture, needles, relpath=tname), \
        "a value a test fixture declares itself counted as a leak"
    hits = PT.scan_text(commented, needles, relpath=tname)
    assert [h.field_id for h in hits] == ["panel_meter_class"], hits

    # and neither rule applies anywhere else: the same bytes in an ordinary
    # file are a leak
    assert PT.scan_text(cheat_ok, needles, relpath="docs/notes.md"), \
        "the cheatsheet rule is being applied to files that are not it"
    assert PT.scan_text(fixture, needles, relpath="analysis/thing.py"), \
        "the fixture rule is being applied outside test modules"
    return ("both scoping rules exclude a region or a declared literal, and "
            "the rest of the same file still fails")


def case_a_declared_literal_is_exempt_only_in_its_own_span():
    """The exemption covers the literal's SOURCE SPAN, not the whole file.

    The distinction is the whole gate. A module that legitimately declares a
    value as a fixture and ALSO quotes it in a comment must fail on the
    comment: the leak this gate found was a meter class in an explanatory
    comment, and an exemption keyed on the string rather than on its position
    blanks every later occurrence of it in the same file. A docstring is a
    string constant in the AST and would land in the exemption set for the
    same reason, so prose in a docstring is excluded from it by name.

    Every value here is invented; the case runs in CI.
    """
    needles = PT.needles(PLANTED)
    tname = "analysis/test_demo.py"
    fixture = 'METER = "CL999-TEST"\nPANEL = [{"label": "Zzyzx"}]\n'
    assert not PT.scan_text(fixture, needles, relpath=tname), \
        "a value declared as a fixture literal counted as a leak"

    # the SAME value, declared as a fixture and repeated in a comment
    commented = fixture + "# the meter on this synthetic panel is CL999-TEST\n"
    hits = PT.scan_text(commented, needles, relpath=tname)
    assert [h.field_id for h in hits] == ["panel_meter_class"], (
        "declaring a value as a fixture hid the same value in a comment "
        f"elsewhere in the file: {hits}")

    # and repeated in a docstring, which is a string constant like any other
    documented = ('"""A demo module.\n\nThe meter is class CL999-TEST.\n"""\n'
                  + fixture)
    hits = PT.scan_text(documented, needles, relpath=tname)
    assert [h.field_id for h in hits] == ["panel_meter_class"], (
        f"a private value disclosed in a docstring was masked: {hits}")

    # a function docstring, and a free-standing string expression, are prose too
    inner = (fixture + 'def f():\n    """Reads the CL999-TEST meter."""\n'
             '    return 1\n')
    assert [h.field_id for h in PT.scan_text(inner, needles, relpath=tname)] \
        == ["panel_meter_class"], "a function docstring was treated as a fixture"

    # the same rule in the example template: a declared value is exempt, the
    # same value in a yaml comment is not
    yname = "household.example.yaml"
    ydecl = "panel:\n  meter_class: CL999-TEST\n"
    assert not PT.scan_text(ydecl, needles, relpath=yname), \
        "a placeholder the template declares counted as a leak"
    assert PT.scan_text(ydecl + "# a real one reads CL999-TEST\n", needles,
                        relpath=yname), \
        "a value in a template comment was masked by the declaration above it"
    return ("the literal exemption masks the declared span only: the same "
            "value in a comment, a module docstring, a function docstring or "
            "a yaml comment still fails")


def case_the_unsearchable_fields_are_derived_from_the_tiers():
    """The class a literal scan cannot cover, and how it is found.

    `needles` drops a boolean and skips a short bare word, correctly. The
    cheatsheet has always named a substitute for that class -- no artifact
    carries a key of that name, no script reads the path -- and the set it
    applies to has to be DERIVED, or a private-only boolean added next year is
    covered only if somebody remembers to add it to a list.

    Runs in CI: field ids, types and yaml paths are public, so the whole
    substitute rule is enforceable without any private data.
    """
    fields = PT.cheatsheet_fields()
    shape = PT.schema()
    derived = PT.unsearchable_fields(fields=fields, shape=shape)
    assert derived, "no field resolved into the unsearchable class"

    tiers = {f["id"]: f["privacy"] for f in fields}
    for fid in derived:
        assert tiers[fid] != "public-ok", f"{fid} is public-ok and banned"
    # the claim that makes it derived rather than listed
    should = {f["id"] for f in fields
              if f["privacy"] != "public-ok"
              and f["type"] in PT.UNSEARCHABLE_TYPES
              and PT.yaml_path_for(f["id"], shape) is not None}
    assert should <= set(derived), sorted(should - set(derived))
    # and a field whose answer IS searchable must not be swept in with them:
    # the two halves are alternatives, not belt and braces
    searchable = [f["id"] for f in fields
                  if f["privacy"] == "private-only" and f["type"] == "string"
                  and f["id"] in derived]
    assert not searchable, (
        f"{searchable} are searchable by value and should not also be held to "
        f"the key/path rule")
    keys = PT.banned_keys(derived)
    assert len(keys) == len(derived), f"two fields share a key name: {keys}"
    return (f"{len(derived)} field(s) derived into the unsearchable class from "
            f"the tiers and the declared types, banning {len(keys)} key "
            f"name(s): {', '.join(sorted(keys))}")


def case_a_banned_key_or_path_read_fails():
    """The positive control for both halves of the substitute rule.

    Half one: the key name published in a structured artifact -- and the key
    name IS the disclosure, since `"itc_claimed": false` publishes the answer
    whichever way the boolean falls. Half two: a committed script reading the
    path. Each is planted, proved to fail, and the clean shape proved not to.
    """
    derived = PT.unsearchable_fields()
    path = sorted(derived.values())[0]
    key = path.split(".")[-1]
    banned = PT.banned_keys(derived)

    # half one, both artifact shapes, and a near miss that must stay quiet
    dirty_json = [("data/x.json", json.dumps({"panel": {key: True}}))]
    dirty_csv = [("data/x.csv", f"month,{key}\n2026-01,true\n")]
    clean = [("data/x.json", json.dumps({"panel": {"spaces": 20}})),
             ("data/x.csv", "month,kwh\n2026-01,5\n"),
             ("data/x.json.md", f'a paragraph naming {key} in prose\n')]
    assert PT.scan_artifact_keys(dirty_json, banned), \
        f"a banned key published as a JSON key was not caught ({key})"
    assert PT.scan_artifact_keys(dirty_csv, banned), \
        f"a banned key published as a CSV column was not caught ({key})"
    assert not PT.scan_artifact_keys(clean, banned), \
        "the key scan fired on an artifact that carries no such key"

    # half two: the accessor call, an ancestor read that pulls the key out
    # with it, and the indirection through a name
    parent = path.rsplit(".", 1)[0]
    for src, why in (
            (f'v = hh.get("{path}")\n', "a direct accessor read"),
            (f'v = HH.get("{path}", required=False)\n', "a keyword-arg read"),
            (f'v = HH.get("{parent}")\n', "a read of the containing block"),
            (f'P = "{path}"\nv = hh.get(P)\n', "the path via a name")):
        hits = PT.scan_script_reads([("analysis/thing.py", src)], derived)
        assert hits, f"{why} of a banned path was not caught"
        assert all(h.field_id in derived for h in hits), hits

    # precision: naming the path in prose is not reading it, and several
    # committed docstrings legitimately do
    quiet = [("analysis/thing.py",
              f'"""A note: a null answer for {path} is fine."""\n'
              f'# {path} is not read here\n'
              f'v = hh.get("misc.{key}")\n'
              f'w = hh.get("{path}x")\n')]
    hits = PT.scan_script_reads(quiet, derived)
    assert not hits, f"the read scan fired on prose or on a near-miss path: {hits}"
    return (f"a banned key in JSON and in CSV, and four shapes of script read "
            f"of a banned path, all fail; prose naming the path and a "
            f"near-miss path do not")


def case_the_committed_tree_carries_no_banned_key_or_read():
    """The substitute rule, run over the real tracked tree, in CI.

    The value scan needs the private file and skips without it. This half does
    not: it looks for key names and dotted paths, both of which are public.
    """
    items, rels = PT.tree_items()
    derived = PT.unsearchable_fields()
    hits = PT.scan_artifact_keys(items, PT.banned_keys(derived))
    hits += PT.scan_script_reads(items, derived)
    assert not hits, (
        "a committed file carries a key or a read of an unsearchable "
        "private-only field: " + "; ".join(sorted(str(h) for h in hits)))
    structured = sum(1 for r, _ in items if r.endswith((".json", ".csv")))
    scripts = sum(1 for r, _ in items if r.endswith(".py"))
    return (f"{structured} structured artifact(s) carry none of the "
            f"{len(derived)} banned key name(s) and {scripts} script(s) read "
            f"none of the paths, over {len(rels)} tracked files")


def case_the_precommit_gate_blocks_a_staged_leak():
    """The hook's entry point, on a throwaway repository.

    CLAUDE.md section 4 makes the local hook the real gate for anything
    person-specific, so the thing that has to be controlled is the exit code
    the hook branches on -- and that it reads the INDEX, since what gets
    committed is not always what is on disk.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        derived = PT.unsearchable_fields()
        path = sorted(derived.values())[0]
        key = path.split(".")[-1]
        _tree(root, dict(CLEAN_TREE))
        items, _ = PT.staged_items(root)
        assert {r for r, _ in items} == set(CLEAN_TREE), items
        hits, _ = PT.gate(items, PLANTED)
        assert not hits, f"the gate fired on a clean staged tree: {hits}"

        # the index is what is judged: change the file on disk WITHOUT staging
        # it and the gate must still see the staged bytes
        (root / "data" / "results.json").write_text(
            json.dumps({key: True}))
        items, _ = PT.staged_items(root)
        hits, _ = PT.gate(items, PLANTED)
        assert not hits, "the gate read the working tree instead of the index"

        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        items, _ = PT.staged_items(root)
        hits, _ = PT.gate(items, PLANTED)
        assert [h.leaf_path for h in hits] == [key], (
            f"a banned key staged for commit was not blocked: {hits}")
        assert all(PLANTED["panel"]["meter_class"] not in str(h) for h in hits), \
            "a finding printed a value"
    return ("the staged-content gate is quiet on a clean index, ignores an "
            "unstaged change, and blocks a banned key once it is staged")


def case_every_intake_key_is_tiered():
    """The reverse gap, and the reason it has to be a test.

    A key in household.yaml that no cheatsheet field covers contributes no
    needle, so publishing it is invisible to the scan -- not caught and not
    reported, which is the worst of the three outcomes. The absence of a tier
    therefore has to break this suite.
    """
    if not PT.REAL_HOUSEHOLD.is_file():
        raise SkipCase("the reverse gate needs private/household.yaml "
                       "(gitignored) to have keys to check")
    household = yaml.safe_load(PT.REAL_HOUSEHOLD.read_text()) or {}
    for key, why in UNTIERABLE_KEYS.items():
        assert isinstance(why, str) and len(why) > 40, (
            f"{key} is exempted from tiering with no written reason")
    untiered = [p for p in PT.untiered_leaf_paths(household)
                if p not in UNTIERABLE_KEYS]
    assert not untiered, (
        f"household.yaml key(s) that no cheatsheet field id covers: "
        f"{untiered} -- give each a field block with a privacy tier, or an "
        f"entry in UNTIERABLE_KEYS with the reason it cannot have one")
    total = len(PT.leaf_paths(household))
    return (f"all {total} keys in the intake map to a tiered field id "
            f"({len(UNTIERABLE_KEYS)} declared untierable)")


def case_the_example_template_is_tiered_too():
    """The schema the resolver reads, held to the same rule, in CI.

    household.example.yaml is what every new household copies. A key that
    exists there and in no field block would be untiered from the day someone
    filled it in, and the case above cannot see it without private data.
    """
    untiered = [p for p in PT.untiered_leaf_paths(PT.schema())
                if p not in UNTIERABLE_KEYS]
    assert not untiered, (
        f"household.example.yaml key(s) with no cheatsheet field id: "
        f"{untiered}")
    return (f"all {len(PT.leaf_paths(PT.schema()))} keys in the committed "
            f"template map to a tiered field id")


CASES = [
    case_the_tier_vocabulary_is_closed,
    case_every_field_id_resolves_or_is_declared_path_less,
    case_the_resolver_reaches_a_key_inside_a_list,
    case_the_repo_scan_catches_a_planted_value,
    case_the_scoping_rules_scope_to_a_region_not_to_a_file,
    case_a_declared_literal_is_exempt_only_in_its_own_span,
    case_the_unsearchable_fields_are_derived_from_the_tiers,
    case_a_banned_key_or_path_read_fails,
    case_the_committed_tree_carries_no_banned_key_or_read,
    case_the_precommit_gate_blocks_a_staged_leak,
    case_the_example_template_is_tiered_too,
    case_no_private_only_value_appears_in_any_tracked_file,
    case_every_intake_key_is_tiered,
]


def _cases_defined_here():
    return {name for name, obj in globals().items()
            if name.startswith("case_") and callable(obj)}


def main():
    listed = [c.__name__ for c in CASES]
    assert len(listed) == len(set(listed)), "CASES lists a case twice"
    unlisted = sorted(_cases_defined_here() - set(listed))
    assert not unlisted, (
        f"case function(s) defined but not in CASES, so they never run: "
        f"{unlisted}")
    ran, failures, skipped = 0, 0, []
    for case in CASES:
        try:
            print(f"PASS  {case.__name__}: {case()}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped.append((case.__name__, str(e)))
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
        except Exception as e:                     # noqa: BLE001
            print(f"FAIL  {case.__name__}: {type(e).__name__}: {e}")
            failures += 1
    tail = f", {len(skipped)} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    # A green check that does not say what it did not check is the failure
    # this banner exists to prevent: on a runner there is no household file,
    # so nothing here has looked at a real answer, and the name of the step
    # must not imply otherwise.
    if skipped:
        print(f"\n{'=' * 72}\nNOT CHECKED HERE — private/household.yaml is "
              f"gitignored and absent, so no case below ran against a real "
              f"intake answer:")
        for name, why in skipped:
            print(f"  · {name}\n      {why}")
        print("These run only where the private archive lives; "
              "`.githooks/pre-commit` is the gate that runs them on every "
              f"commit there (CLAUDE.md §4).\n{'=' * 72}")
    else:
        print("\nThe real-value scan RAN: private/household.yaml is present "
              "and every case above was checked against it.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
