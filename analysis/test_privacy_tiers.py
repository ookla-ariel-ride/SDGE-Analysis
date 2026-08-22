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
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402
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


def case_a_test_module_and_the_template_are_scanned_like_any_other_file():
    """No file class goes unscanned, which it did until an adversarial pass.

    The module used to exempt every assigned string literal in a `test_*.py`
    and every value in `household.example.yaml`, on the reasoning that a
    synthetic fixture may legitimately coincide with a real answer. The
    reasoning is sound and the rule was not: it cannot tell an invented fixture
    from a real answer someone pasted in, and those two files are the likeliest
    in the repo to receive copied sample data. A monitoring URL planted in
    either returned clean while the same bytes failed in an ordinary module.

    So the file class buys nothing on its own. What excuses a hit is a baseline
    that already declared the same literal, and neither control here has one.
    """
    needles = PT.needles(PLANTED)
    url = [n for n in needles if n.leaf_path == "monitoring[].url"]
    assert url, "the planted household produced no monitoring url needle"

    # control 1: a household answer in a test-module ASSIGNMENT, the shape the
    # old rule masked outright
    tname = "analysis/test_demo.py"
    fixture = f'FEED = {{"url": "{url[0].value}"}}\n'
    hits = PT.scan_text(fixture, needles, relpath=tname)
    assert {h.leaf_path for h in hits} == {"monitoring[].url"}, (
        f"a household answer assigned in a test module was not caught: {hits}")

    # control 2: the same answer in the committed schema template
    yname = "household.example.yaml"
    tmpl = f"monitoring:\n  - url: {url[0].value}\n"
    hits = PT.scan_text(tmpl, needles, relpath=yname)
    assert {h.leaf_path for h in hits} == {"monitoring[].url"}, (
        f"a household answer written into the template was not caught: {hits}")

    # and the same bytes in an ordinary module, which always failed
    assert PT.scan_text(fixture, needles, relpath="analysis/thing.py"), \
        "the control value does not fail even in an ordinary file"
    assert PT.Baseline().text(tname) is None, \
        "the controls above are being handed a baseline, so they prove nothing"
    return ("a private answer assigned in a test module and one written into "
            "household.example.yaml both fail: neither file class is exempt")


def case_only_a_literal_the_baseline_already_held_is_excused():
    """The whole exemption, and it is a question about the repo's own history.

    A fixture literal is excused only where the BASELINE version of the same
    file already declared the same literal, and only as many times. Every
    weaker design this replaces has its own claim here: a brand-new file
    excuses nothing; a second copy of a baselined literal is a second span and
    fails; a reworded one fails; the same value in a comment, a docstring or
    prose fails even when the baseline is the identical file; another file's
    baseline excuses nothing; and outside the three declaring classes age
    excuses nothing at all, so a value already committed in an artifact or in
    prose stays a finding.

    Every value here is invented; the case runs in CI, with no repository and
    no private file.
    """
    assert not hasattr(PT, "DECLARED_FIXTURE_COLLISIONS"), (
        "the per-household collision table is back -- the counts in it are one "
        "household's and block every other clone's commits")
    needles = PT.needles(PLANTED)
    tname = "analysis/test_demo.py"
    fixture = 'PANEL = [{"label": "Zzyzx", "amps": 20}]\n'

    # only these three classes declare literals at all; everywhere else every
    # byte is scanned however old it is
    assert {PT._file_class(r) for r in ("analysis/test_x.py", "cheat.md",
                                        "household.example.yaml",
                                        "DATA-SOURCES-CHEATSHEET.md")} \
        == set(PT.DECLARED_LITERAL_SOURCES) | {None}, "the file classes moved"

    # a brand-new file has no baseline, so its fixture literals excuse nothing
    assert PT.scan_text(fixture, needles, relpath=tname), \
        "a fixture literal in a file with no baseline was excused"
    assert PT.Baseline().text(tname) is None, "an empty baseline holds a file"

    base = PT.Baseline(texts={tname: fixture})
    excused = collections.Counter()
    assert not PT.scan_text(fixture, needles, relpath=tname, baseline=base,
                            excused=excused), \
        "a literal the baseline already held was not excused"
    assert excused[(tname, "panel.schedule[].label")] == 1, dict(excused)

    # a SECOND copy of that literal is a second span, and the baseline holds
    # one. This is the case a count could not see: the count did not move.
    hits = PT.scan_text(fixture + 'SPARE = "Zzyzx"\n', needles, relpath=tname,
                        baseline=base)
    assert [h.leaf_path for h in hits] == ["panel.schedule[].label"], (
        f"a second copy of a baselined literal was excused by the first: {hits}")

    # a reworded literal carrying the same answer is a new literal. Shown with
    # a value-carrying needle, since a bare word is only ever looked for in
    # value position and a sentence around it is not value position anywhere.
    meter = 'METER = "CL999-TEST"\n'
    mbase = PT.Baseline(texts={tname: meter})
    assert not PT.scan_text(meter, needles, relpath=tname, baseline=mbase), \
        "a baselined literal was not excused"
    assert PT.scan_text('METER = "the CL999-TEST meter"\n', needles,
                        relpath=tname, baseline=mbase), \
        "a reworded literal holding a private answer was excused"
    # but re-quoting one, or moving it to another line, is not a rewording:
    # the comparison is on what the literal says, not on its source bytes
    assert not PT.scan_text("PANEL = [{'label':\n           'Zzyzx',\n"
                            "          'amps': 20}]\n", needles, relpath=tname,
                            baseline=base), \
        "re-quoting a baselined fixture counted as introducing it"

    # another file's baseline is not this file's
    assert PT.scan_text(fixture, needles, relpath=tname,
                        baseline=PT.Baseline(texts={"analysis/test_x.py":
                                                    fixture})), \
        "a literal was excused by a different file's baseline"

    # a second private value in the same file, which the baseline never held
    hits = PT.scan_text(fixture + 'METER = "CL999-TEST"\n', needles,
                        relpath=tname, baseline=base)
    assert [h.field_id for h in hits] == ["panel_meter_class"], (
        f"one excused literal excused an unrelated value beside it: {hits}")

    # prose is never a declared literal, so an identical baseline excuses none
    # of it -- a private answer in a comment or a docstring is the shape the
    # leak this gate was built for actually took
    for src, why in (
            (fixture + '# the legend on this panel reads Zzyzx\n', "a comment"),
            ('"""A demo module.\n\nThe legend reads Zzyzx\n"""\n' + fixture,
             "a module docstring"),
            (fixture + 'def f():\n    """Reads Zzyzx\n    """\n'
             '    return 1\n', "a function docstring")):
        hits = PT.scan_text(src, needles, relpath=tname,
                            baseline=PT.Baseline(texts={tname: src}))
        assert [h.leaf_path for h in hits] == ["panel.schedule[].label"], (
            f"a baselined file's own {why} was excused: {hits}")

    yname = "household.example.yaml"
    ydecl = "panel:\n  schedule:\n    - label: Zzyzx\n"
    assert PT.scan_text(ydecl, needles, relpath=yname), \
        "a template placeholder with no baseline was excused"
    assert not PT.scan_text(ydecl, needles, relpath=yname,
                            baseline=PT.Baseline(texts={yname: ydecl})), \
        "a baselined template placeholder was not excused"
    ycomment = ydecl + "# a real one reads Zzyzx\n"
    assert PT.scan_text(ycomment, needles, relpath=yname,
                        baseline=PT.Baseline(texts={yname: ycomment})), \
        "a value in a template comment was excused by the scalar above it"

    # the cheatsheet, whose declared literal is one `question:` scalar
    name = "DATA-SOURCES-CHEATSHEET.md"
    cheat_ok = ('```yaml\nid: panel_meter_class\n'
                'question: "What class is the meter (e.g. CL999-TEST)?"\n'
                'type: string\nrequired_if: always\nwhere: "on the meter"\n'
                'privacy: private-only\n```\n')
    cheat_base = PT.Baseline(texts={name: cheat_ok})
    assert not PT.scan_text(cheat_ok, needles, relpath=name,
                            baseline=cheat_base), \
        "the cheatsheet's own standing enumeration counted as a leak"
    assert PT.scan_text(cheat_ok + "\nThis meter is CL999-TEST.\n", needles,
                        relpath=name,
                        baseline=PT.Baseline(texts={name: cheat_ok})), \
        "prose outside the question text is not being scanned"
    assert PT.scan_text(cheat_ok, needles, relpath="docs/notes.md",
                        baseline=PT.Baseline(texts={"docs/notes.md":
                                                    cheat_ok})), \
        "the cheatsheet rule is being applied to files that are not it"

    # and the boundary that keeps an old leak a leak: outside the three
    # classes nothing is eligible, so age excuses nothing. A private value
    # already committed in an ordinary module, in data/ or in prose is still
    # a finding on every scan. Each file gets content its own suffix can carry,
    # because a `.json` that does not parse is now a finding of its own and
    # would prove this claim by the wrong route.
    for rel, src in (("analysis/thing.py", fixture),
                     ("data/results.json", json.dumps({"circuits": ["Zzyzx"]})),
                     ("data/results.yaml", "circuits:\n  - Zzyzx\n"),
                     ("TECHNICAL.md", fixture)):
        assert PT.scan_text(src, needles, relpath=rel,
                            baseline=PT.Baseline(texts={rel: src})), \
            f"{rel} had a private value excused merely for being old"

    # which ref a repository resolves to, in the three shapes a clone comes in
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")

        def git(*args):
            return subprocess.run(["git", *args], cwd=root, env=env,
                                  capture_output=True, text=True)
        git("init", "-q", "-b", "trunk")
        assert PT.Baseline.of(root).ref is None, \
            "a repository with no commits resolved to a baseline"
        (root / "a.txt").write_text("a\n")
        git("add", "-A")
        git("commit", "-m", "one")
        # a trunk named neither main nor master: HEAD is the fallback, and it
        # is a ref rather than a merge base
        assert PT.Baseline.of(root).ref == "HEAD", PT.Baseline.of(root).ref
        assert PT.Baseline.of(root).text("a.txt") == "a\n"
        assert PT.Baseline.of(root).text("nope.txt") is None
        git("branch", "main")
        git("checkout", "-q", "-b", "feature")
        (root / "a.txt").write_text("b\n")
        git("commit", "-qam", "two")
        # with a trunk, the baseline is the merge base with it, so this
        # branch's own commit cannot launder itself into its successor's excuse
        base_ref = PT.Baseline.of(root)
        assert base_ref.ref not in ("HEAD", None), base_ref.ref
        assert base_ref.text("a.txt") == "a\n", \
            "the baseline followed the branch instead of the merge base"
    return ("a fixture literal is excused only where the same file's baseline "
            "already declared it: a new file, a second copy, a rewording, a "
            "comment, a docstring, another file's baseline and every file "
            "outside the three declaring classes all still fail")


def case_the_tree_is_clean_and_the_baseline_is_what_excuses_it():
    """The excuse has to be load-bearing, and it has to be the baseline.

    A gate that reports a clean tree because it excused everything is
    indistinguishable from one that found nothing, so this case makes the
    difference visible: the tree scans clean against its real baseline, the
    SAME tree with no baseline does not, and every file the difference lands in
    is one of the three classes that declare literals. Nothing is asserted
    about how many answers this household happens to hold -- that number is the
    thing the module no longer commits anywhere.

    Needs the private file, since the collisions are collisions with real
    answers.
    """
    if not PT.REAL_HOUSEHOLD.is_file():
        raise SkipCase("what the baseline excuses is a coincidence with real "
                       "answers, so this needs private/household.yaml "
                       "(gitignored)")
    household = yaml.safe_load(PT.REAL_HOUSEHOLD.read_text()) or {}
    needles = PT.needles(household)
    excused = collections.Counter()
    hits, _rels = PT.scan_tree(needles, excused=excused)
    assert not hits, "; ".join(sorted(str(h) for h in hits))
    assert excused, (
        "nothing was excused, so this case proves nothing about the rule that "
        "excuses -- if the fixtures no longer collide, say so and delete it")
    for rel, _leaf in excused:
        assert PT._file_class(rel) in PT.DECLARED_LITERAL_SOURCES, (
            f"{rel} declares no literals, so nothing in it may be excused")

    bare, _rels = PT.scan_tree(needles, baseline=PT.Baseline())
    assert bare, (
        "the same tree scans clean with no baseline at all, so the baseline is "
        "not what is excusing anything and this rule is dead weight")
    assert {h.where for h in bare} == {rel for rel, _ in excused}, (
        f"the files the baseline excuses and the files that fail without one "
        f"differ: {sorted({h.where for h in bare})} vs "
        f"{sorted({rel for rel, _ in excused})}")
    base = PT.Baseline.of()
    assert base.ref, "the repository resolved to no baseline ref"

    # and the entry point the hooks call, in process, over the same tree: it
    # reports the baseline it used and how much it excused rather than
    # asserting a number no reader can check
    for scope in ("--tree", "--staged"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = PT.main([scope])
        said = err.getvalue()
        assert code == 0, f"{scope} did not come back clean: {said}"
        if scope == "--tree":
            assert base.label in said and "pre-existing fixture literal(s)" \
                in said, said
    return (f"the tracked tree is clean against {base.label}; the same tree "
            f"with no baseline fails in {len({h.where for h in bare})} file(s), "
            f"all of them declaring classes, on "
            f"{sum(excused.values())} pre-existing literal(s) -- so the excuse "
            f"is the baseline and nothing else")


def case_a_yaml_artifact_is_read_structurally_and_not_by_pattern():
    """A bare word in a YAML file is found by parsing it, not by a regex.

    The "as a value" pattern has to enumerate the characters that can close a
    value, and YAML has more of them than any pattern kept up with: a trailing
    comment and a flow mapping both put a private answer in plain value
    position and both fell outside it. Widening the pattern moves the boundary;
    parsing removes it, so a tracked format the repo can parse is now walked --
    every string leaf and every key, compared for equality, exactly as JSON
    already was.

    YAML keeps the pattern as well, because comments are not part of the
    structure and a private answer quoted in one is published just the same.

    Every value here is invented, and the case runs in CI.
    """
    needles = PT.needles(PLANTED)
    legend = [n for n in needles if n.leaf_path == "panel.schedule[].label"]
    assert legend and legend[0].mode == "as a value", legend
    v = legend[0].value
    rel = "data/x.yaml"

    # the two shapes the pattern missed, plus the plain block scalar it caught,
    # plus a second document and an alias -- each is a value position to a
    # parser and each must be a hit
    for text, why in (
            (f"panel:\n  label: {v} # copied from the intake\n",
             "a scalar with a trailing comment"),
            (f"panel: {{key: {v}}}\n", "a flow mapping"),
            (f"panel:\n  label: {v}\n", "a plain block scalar"),
            (f"panel:\n  label: other\n---\npanel:\n  label: {v}\n",
             "the second document of a multi-document file"),
            (f"defaults: &d\n  label: {v}\npanel:\n  <<: *d\n",
             "a value reached through an alias"),
            (f"panel:\n  labels: [a, {v}, b]\n", "a flow sequence member"),
            (f"panel:\n  label: \"{v}\"\n", "a quoted scalar"),
            (f"{v}:\n  label: a\n", "a mapping KEY")):
        hits = PT.scan_text(text, needles, relpath=rel)
        assert [h.leaf_path for h in hits] == ["panel.schedule[].label"], (
            f"a private answer as {why} in a YAML artifact was not caught: "
            f"{hits}")
        assert all(v not in str(h) for h in hits), "a finding printed a value"

    # the reading this replaces, run on the same bytes: the pattern alone, with
    # no structure to fall back on, which is every YAML file's whole scan
    # before this change
    for text, why in (
            (f"panel:\n  label: {v} # copied from the intake\n",
             "a trailing comment"),
            (f"panel: {{key: {v}}}\n", "a flow mapping"),
            (f"{v}:\n  label: a\n", "a mapping key")):
        assert not PT._found_in(text, needles), (
            f"the pattern alone already caught {why} -- this control proves "
            f"nothing")

    # and the pattern is still run, because a comment is outside the structure
    comment = f"panel:\n  label: other  # the real one reads {v}\n"
    assert PT.parse_structured(rel, comment) == [{"panel": {"label": "other"}}]
    assert [h.leaf_path for h in PT.scan_text(comment, needles, relpath=rel)] \
        == ["panel.schedule[].label"], \
        "a private answer in a YAML comment is outside the structure and was " \
        "not read as text either"

    # a YAML artifact that will not parse is REPORTED, not skipped: nothing
    # walked its keys or its values, so a quiet return would be quiet for the
    # one reason a gate may never be quiet
    broken = f"panel:\n  label: {v}\n   bad: [unclosed\n"
    try:
        PT.scan_text(broken, needles, relpath=rel)
        raise AssertionError("an unparseable YAML artifact scanned clean")
    except PT.UnparseableArtifact as e:
        assert e.relpath == rel and v not in str(e), \
            f"the unparseable report leaked the source it could not parse: {e}"
    try:
        PT.scan_artifact_keys([(rel, broken)], {})
        raise AssertionError("the key rule skipped an unparseable artifact")
    except PT.UnparseableArtifact:
        pass
    # the same file with a suffix that claims no format is text, not an error
    assert PT.scan_text(broken, needles, relpath="notes.txt"), \
        "the same bytes under a suffix claiming no format should just be text"

    # the two tracked YAML shapes behave as documented. The workflows parse and
    # disclose nothing; the schema template's placeholders stay excused by its
    # baseline, which means the mask has to reach the PARSE and not only the
    # bytes -- or the structural half would hand back what the baseline excused.
    items = dict(PT.tree_items()[0])
    flows = [r for r in items if r.startswith(".github/") and
             r.endswith((".yml", ".yaml"))]
    assert len(flows) >= 5, flows
    for r in flows:
        assert PT.parse_structured(r, items[r]) is not None, r
        assert not PT.scan_text(items[r], needles, relpath=r), r
    yname = "household.example.yaml"
    ydecl = f"panel:\n  schedule:\n    - label: {v}\n"
    assert PT.scan_text(ydecl, needles, relpath=yname), \
        "a template placeholder with no baseline was excused"
    assert not PT.scan_text(ydecl, needles, relpath=yname,
                            baseline=PT.Baseline(texts={yname: ydecl})), \
        "the mask did not reach the structural half: a baselined template " \
        "placeholder came back through the parse"
    return ("a YAML artifact is walked structurally -- trailing comment, flow "
            "mapping, second document, alias, sequence member and mapping key "
            "all fail, three of which the pattern alone missed -- and is read "
            "as text as well for its comments; an unparseable one is reported "
            f"without quoting itself; {len(flows)} tracked workflows and the "
            f"schema template behave as documented")


def case_a_container_read_hands_over_the_keys_inside_it():
    """An ancestor of a list-member path is an ancestor, marker and all.

    The ban on reading an unsearchable field's path has always covered a read
    of a CONTAINER, since the container hands the key over. It compared dotted
    string prefixes to find one, and `monitoring[].url` does not start with
    `monitoring.`, so the list marker hid every container of every list-member
    path: `HH.get("monitoring")` returns the url, the api and the owner of
    every feed, and the scan had nothing to say about it. `read_paths` derives
    the set from the path's segments instead, so an intermediate container and
    a doubly-nested list are covered without another patch.

    Synthetic paths, so the case runs in CI.
    """
    # the plain dotted case is unchanged, which is what keeps the real tree
    # where it was. Spelled with an invented path: naming a genuinely banned
    # one here would be this module reading it, and the rule below would fire
    # on this very file.
    assert PT.read_paths("block.leaf") == {"block", "block.leaf"}, \
        "the dotted case changed shape"
    assert PT.read_paths("monitoring[].url") == {"monitoring", "monitoring[]",
                                                 "monitoring[].url"}
    assert PT.read_paths("a[].b[].c") == {"a", "a[]", "a[].b", "a[].b[]",
                                          "a[].b[].c"}

    derived = {"monitoring_url": "monitoring[].url", "deep_c": "a[].b[].c"}
    for src, why in (
            ('v = HH.get("monitoring")\n', "the container by its bare name"),
            ('v = HH.get("monitoring[]")\n', "the container with its marker"),
            ('v = hh.get("monitoring[].url")\n', "the path itself"),
            ('v = hh.get("a")\n', "the outer container of a nested list"),
            ('v = hh.get("a[].b")\n', "an intermediate container"),
            ('v = hh.get("a[].b[]")\n', "an intermediate list")):
        hits = PT.scan_script_reads([("analysis/thing.py", src)], derived)
        assert hits, f"a read of {why} was not caught"
        assert all(h.field_id in derived for h in hits), hits

    # the reading this replaces, on the same arguments: a dotted prefix test,
    # which is why a container read of a list-member path returned clean
    for arg, path in (("monitoring", "monitoring[].url"),
                      ("a", "a[].b[].c"), ("a[].b", "a[].b[].c")):
        assert not (arg == path or path.startswith(arg + ".")), (
            f"the prefix test already matched {arg!r} -> {path!r} -- this "
            f"control proves nothing")

    # precision: a neighbouring key, a sibling list and a prefix of a name are
    # not reads of it
    quiet = [("analysis/thing.py",
              'a = hh.get("monitoringx")\n'
              'b = hh.get("mon")\n'
              'c = hh.get("monitoring[].source")\n'
              'd = hh.get("a[].bb")\n')]
    hits = PT.scan_script_reads(quiet, derived)
    assert not hits, f"the read scan fired on a near-miss container: {hits}"
    return ("every container of a list-member path is a read of it -- the bare "
            "name, the `[]` spelling, and each intermediate container of "
            "`a[].b[].c` -- where the dotted prefix test matched none of them; "
            "a neighbouring key and a name prefix stay quiet")


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


def case_a_sub_floor_bare_answer_is_covered_by_the_substitute_rule():
    """No value may be skipped by BOTH mechanisms, which one class was.

    `needles` builds a needle for a bare string of any length, so
    `unsearchable_fields` read the mere existence of one as proof the value
    scan covered the field and left it out of the key/path substitute.
    `_found_in` disagreed: it skips a bare word below BARE_WORD_TEXT_MIN_CHARS
    in every file it cannot parse. A field answered with a short bare word was
    therefore searched by nothing -- not by the value scan, which skipped it,
    and not by the substitute, which thought itself unnecessary. Both halves
    now read `Needle.text_searchable`, computed from the one floor constant.

    Every value here is invented, and the case runs in CI.
    """
    fields = PT.cheatsheet_fields()
    shape = PT.schema()
    fid, path = "panel_enclosure_type", "panel.enclosure_type"
    key = path.split(".")[-1]
    assert PT.yaml_path_for(fid, shape) == path, PT.yaml_path_for(fid, shape)
    assert [f["privacy"] for f in fields if f["id"] == fid] == ["private-only"]
    floor = PT.BARE_WORD_TEXT_MIN_CHARS

    # the predicate itself, read off the same constant the scanner applies
    def needle(value, mode="as a value"):
        return PT.Needle(fid, path, path, value, mode)
    assert needle("z" * floor).text_searchable, "the floor moved"
    assert not needle("z" * (floor - 1)).text_searchable, "the floor moved"
    assert needle("z 1", "anywhere").text_searchable, \
        "a value carrying a digit or a space is searchable at any length"

    short = {"panel": {"enclosure_type": "z" * (floor - 1)}}
    long = {"panel": {"enclosure_type": "z" * floor}}
    mine = [n for n in PT.needles(short, fields, shape) if n.field_id == fid]
    assert len(mine) == 1 and mine[0].mode == "as a value", mine
    assert not mine[0].text_searchable, \
        "a sub-floor bare answer still counts as searchable"

    # the two mechanisms partition: the short answer moves into the key/path
    # class, the long one stays with the value scan and is not swept in as well
    assert fid in PT.unsearchable_fields(short, fields, shape), (
        "a sub-floor bare answer is covered by neither mechanism -- the value "
        "scan skips it and the substitute rule does not claim it")
    assert fid not in PT.unsearchable_fields(long, fields, shape), (
        "a searchable answer was swept into the key/path rule as well")

    derived = PT.unsearchable_fields(short, fields, shape)
    banned = PT.banned_keys(derived)
    v = short["panel"]["enclosure_type"]
    ns = PT.needles(short, fields, shape)
    # the substitute rule is what covers it, in each structured format the repo
    # tracks and in a script; the value scan is silent on all of them, which is
    # the whole reason the substitute has to bind
    for rel, text, why in (
            ("data/x.csv", f"month,{key}\n2026-01,{v}\n", "a CSV column"),
            ("data/x.yaml", f"panel:\n  {key}: {v}\n", "a YAML key"),
            ("data/x.json", json.dumps({"panel": {key: v}}), "a JSON key")):
        assert PT.scan_artifact_keys([(rel, text)], banned), \
            f"{why} of a sub-floor bare field was not caught"
    read = [("analysis/thing.py", f'v = hh.get("{path}")\n')]
    assert PT.scan_script_reads(read, derived), \
        "a script read of a sub-floor bare field's path was not caught"

    # and the boundary, pinned so it cannot move in silence: below the floor a
    # bare word is ordinary vocabulary, so it goes on being unsearched in
    # running prose and in a commit message. Dropping the floor instead was
    # measured on this tree and returns matches in files that disclose nothing.
    prose = [("TECHNICAL.md", f"The enclosure is {v}.\n"),
             (PT.MESSAGE_LABEL, f"panel: the enclosure is {v}\n")]
    assert not PT.scan_items(ns, prose), \
        "the sub-floor floor has moved; re-measure before relying on this"
    # the same field one character longer is caught by the value scan in both
    long_ns = PT.needles(long, fields, shape)
    w = long["panel"]["enclosure_type"]
    caught = PT.scan_items(long_ns, [("TECHNICAL.md", f"| enclosure | {w} |\n"),
                                     (PT.MESSAGE_LABEL, f"panel: type: {w}\n")])
    assert {h.where for h in caught} == {"TECHNICAL.md", PT.MESSAGE_LABEL}, \
        f"a floor-length bare answer was not caught by the value scan: {caught}"
    return (f"a bare answer below the {floor}-character floor is held to the "
            f"key/path substitute -- caught as a CSV column, a YAML key, a JSON "
            f"key and a script read -- and one at the floor stays with the "
            f"value scan, caught in markdown and in a commit message; neither "
            f"is claimed by both")


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

    # half one, every artifact shape, and a near miss that must stay quiet.
    # YAML is in the list because the repo tracks it -- the workflows and the
    # schema template -- and a rule that stopped at JSON and CSV left a whole
    # tracked format returning clean.
    dirty_json = [("data/x.json", json.dumps({"panel": {key: True}}))]
    dirty_csv = [("data/x.csv", f"month,{key}\n2026-01,true\n")]
    dirty_yaml = [("data/x.yaml", f"panel:\n  nested:\n    {key}: true\n")]
    dirty_yml = [(".github/workflows/x.yml", f"jobs:\n  {key}:\n    runs: no\n")]
    clean = [("data/x.json", json.dumps({"panel": {"spaces": 20}})),
             ("data/x.csv", "month,kwh\n2026-01,5\n"),
             ("data/x.yaml", "panel:\n  spaces: 20\n"),
             ("data/x.json.md", f'a paragraph naming {key} in prose\n')]
    assert PT.scan_artifact_keys(dirty_json, banned), \
        f"a banned key published as a JSON key was not caught ({key})"
    assert PT.scan_artifact_keys(dirty_csv, banned), \
        f"a banned key published as a CSV column was not caught ({key})"
    assert PT.scan_artifact_keys(dirty_yaml, banned), \
        f"a banned key nested in a YAML artifact was not caught ({key})"
    assert PT.scan_artifact_keys(dirty_yml, banned), \
        f"a banned key in a .yml file was not caught ({key})"
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

    # half two beyond python: the repo tracks shell and yaml, and a scan that
    # skipped every file without a `.py` suffix let both read the path clean
    for rel, src, why in (
            (".githooks/demo", f'#!/bin/sh\nyq ".{path}" private/x.yaml\n',
             "a shell hook, recognised by its shebang"),
            ("tool.sh", f'#!/bin/bash\nP={path}\n', "a .sh script"),
            (".github/workflows/x.yml",
             f'jobs:\n  a:\n    run: read {path} < f\n', "a workflow")):
        hits = PT.scan_script_reads([(rel, src)], derived)
        assert hits, f"{why} naming a banned path was not caught"
        assert all(h.field_id in derived for h in hits), hits

    # precision: naming the path in prose is not reading it, and several
    # committed files legitimately do -- in a python docstring, in a shell or
    # yaml comment, and throughout the markdown that documents this very rule
    quiet = [("analysis/thing.py",
              f'"""A note: a null answer for {path} is fine."""\n'
              f'# {path} is not read here\n'
              f'v = hh.get("misc.{key}")\n'
              f'w = hh.get("{path}x")\n'),
             ("tool.sh", f'#!/bin/sh\n# {path} is deliberately not read\n'
                         f'echo ok  # nor {path} here\n'),
             (".github/workflows/x.yml", f'# {path} is documented, not read\n'
                                         f'jobs:\n  a:\n    run: echo hi\n'),
             ("TECHNICAL.md", f'`{path}` stayed private-only.\n'),
             ("index.html", f'<p>{path} is a field id</p>\n')]
    hits = PT.scan_script_reads(quiet, derived)
    assert not hits, f"the read scan fired on prose or on a near-miss path: {hits}"
    return (f"a banned key in JSON, CSV and YAML, and seven shapes of script "
            f"read of a banned path across python, shell and yaml, all fail; "
            f"comments, prose and a near-miss path do not")


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


def case_the_template_is_excused_from_the_key_rule_in_writing():
    """The schema template carries every intake key, banned ones included.

    That is what makes it the schema, so the KEY half of the substitute rule
    cannot apply to it. The point of the case is that the exemption is stated
    and load-bearing rather than a silent skip: the template really does carry
    a banned key, so dropping the row would turn the suite red, and the row
    buys nothing beyond the key rule -- the template is scanned for values like
    any other file.
    """
    for rel, why in PT.KEY_RULE_EXEMPT.items():
        assert isinstance(why, str) and len(why) > 40, (
            f"{rel} is excused from the key rule with no written reason")
    text = PT.EXAMPLE_HOUSEHOLD.read_text()
    derived = PT.unsearchable_fields()
    banned = PT.banned_keys(derived)
    rel = "household.example.yaml"
    assert rel in PT.KEY_RULE_EXEMPT, rel
    assert not PT.scan_artifact_keys([(rel, text)], banned), \
        "the declared exemption is not being applied"
    assert PT.scan_artifact_keys([("data/copy.yaml", text)], banned), (
        "the template carries no banned key, so its exemption is dead weight "
        "and should be removed")
    return (f"{rel} is excused from the key rule in writing, the exemption is "
            f"load-bearing ({len(banned)} banned key name(s) checked), and it "
            f"reaches the key rule only")


def case_the_commit_message_is_inside_the_boundary_and_scanned():
    """CLAUDE.md section 4 lists commit messages, and nothing checked them.

    A pre-commit hook structurally cannot: it runs before the message exists.
    The `--message` scope is where the same needles are run over the proposed
    message, and it gets no literal-span exemption of any kind, because a
    commit message declares no fixtures.
    """
    needles = PT.needles(PLANTED)
    assert PT._file_class(PT.MESSAGE_LABEL) is None, \
        "the commit message is being treated as a file class with exemptions"
    subject = "panel: record the CL999-TEST meter class\n"
    body = ("panel: record the meter class\n\nThe meter turned out to be "
            "class CL999-TEST, which bounds the current.\n")
    for text, why in ((subject, "the subject"), (body, "the body")):
        hits = PT.scan_items(needles, [(PT.MESSAGE_LABEL, text)])
        assert [h.field_id for h in hits] == ["panel_meter_class"], (
            f"a private answer in {why} of a commit message was not caught: "
            f"{hits}")
        assert all(PLANTED["panel"]["meter_class"] not in str(h) for h in hits), \
            "a finding printed a value"
    clean = "panel: tier the schedule keys by what they reveal\n"
    assert not PT.scan_items(needles, [(PT.MESSAGE_LABEL, clean)]), \
        "the message scan fired on a message that discloses nothing"
    return ("a private answer in a commit subject and in a commit body are "
            "both caught, with no span exemption; a clean message is not")


def case_the_commit_msg_hook_blocks_and_leaves_the_history_alone():
    """The hook itself, end to end, on a throwaway repository.

    `core.hooksPath` is already `.githooks`, so a file added there is dispatched
    by name with no further setup -- the control proves that rather than
    assuming it. The household here is the invented one, written into the
    throwaway repo's own `private/household.yaml`, so the case runs in CI: no
    real answer is involved at any point.
    """
    src = PT.ROOT
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        for rel in ("analysis/privacy_tiers.py", "DATA-SOURCES-CHEATSHEET.md",
                    "household.example.yaml", ".githooks/commit-msg"):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((src / rel).read_bytes())
            dest.chmod(0o755)
        (root / "private").mkdir()
        (root / "private" / "household.yaml").write_text(yaml.safe_dump(PLANTED))
        # the hook's first interpreter candidate, so the search is exercised.
        # A wrapper rather than a symlink: a venv interpreter linked out of its
        # own tree loses the venv and with it pyyaml.
        (root / ".venv" / "bin").mkdir(parents=True)
        shim = root / ".venv" / "bin" / "python"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        shim.chmod(0o755)
        (root / "README.md").write_text("# throwaway\n")

        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")

        def git(*args, **kw):
            return subprocess.run(["git", *args], cwd=root, env=env,
                                  capture_output=True, text=True, **kw)

        git("init", "-q", check=True)
        git("config", "core.hooksPath", ".githooks", check=True)
        assert git("config", "core.hooksPath").stdout.strip() == ".githooks", \
            "the throwaway repo does not reproduce the real hooksPath setting"
        git("add", "-A", check=True)

        def count():
            out = git("rev-list", "--count", "HEAD")
            return int(out.stdout.strip()) if out.returncode == 0 else 0

        for msg, why in (
                ("panel: record the CL999-TEST meter class",
                 "a private answer in the subject"),
                ("panel: record the meter class\n\nIt reads CL999-TEST.\n",
                 "a private answer in the body")):
            r = git("commit", "-m", msg)
            assert r.returncode != 0, f"{why} was committed: {r.stderr}"
            assert "privacy tiers: BLOCKED" in r.stderr, r.stderr
            assert "panel_meter_class" in r.stderr, r.stderr
            assert PLANTED["panel"]["meter_class"] not in r.stderr, \
                "the hook printed the value"
            assert count() == 0, f"{why} changed the history: {r.stderr}"

        r = git("commit", "-m", "add a throwaway readme")
        assert r.returncode == 0, f"a clean message was blocked: {r.stderr}"
        assert count() == 1, "the clean commit did not land"

        # and the gate fails CLOSED: an unreadable message file is not a clean
        # one, and a scanner that cannot run refuses rather than waving through
        assert PT.main(["--message", str(root / "nope.txt")]) == 3, \
            "an unreadable commit message did not fail closed"
    return ("the commit-msg hook is dispatched by core.hooksPath alone, blocks "
            "a private answer in the subject and in the body naming the field "
            "id only, leaves the commit count at 0, passes a clean message, "
            "and exits 3 rather than 0 when it cannot read the message")


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


# A SECOND invented household, and the fixture module a repository carries. The
# labels below coincide with that fixture five times, which is a different
# number from the owner's coincidences with the fixtures in THIS tree -- which
# is the whole point of `case_the_precommit_hook_is_portable_to_another_house`.
# `spare` is a sixth answer of the same kind that no committed file holds, so
# pasting it in is a fresh disclosure. Nothing here comes from any real intake.
OTHER_HOUSEHOLD = {
    "panel": {"meter_class": "CL777-OTHER",
              "enclosure_catalog": "OTHERCO QQ-0002",
              "schedule": [{"label": "Quorra", "amps": 20},
                           {"label": "Xylanth", "amps": 30},
                           {"label": "Vibrino", "amps": 40},
                           {"label": "Meridax", "amps": 50},
                           {"label": "Thalos", "amps": 15},
                           {"label": "Ombrix", "amps": 25}]},
}
OTHER_PLANTED = 5                     # how many of those the fixture declares
OTHER_SPARE = "Ombrix"                # the one it does not
OTHER_FIXTURE = '''"""A synthetic panel fixture module."""
PANEL = [
    {"label": "Quorra", "amps": 20},
    {"label": "Xylanth", "amps": 30},
    {"label": "Vibrino", "amps": 40},
    {"label": "Meridax", "amps": 50},
    {"label": "Thalos", "amps": 15},
]
'''


def case_the_precommit_hook_is_portable_to_another_household():
    """The gate has to work for a household that is not this repo's owner.

    README.md documents cloning this repo, filling in your own
    `private/household.yaml` and turning the hook on, and CLAUDE.md section 12
    makes that flow a requirement. The version of this gate that this case
    replaces committed a table of how many of THIS household's answers coincide
    with each fixture file -- 1, 3 and 2. A second household's door legends
    coincide with the same fixtures a different number of times, so every
    ordinary commit it made was blocked as a stale row until it edited shared
    scanner code to match its own private answers.

    So the control is a clone with somebody else's intake in it. The private
    file is written AFTER the seed commit, which is the real order of events: a
    fork's fixtures arrive already committed, and the household file comes
    later. Six claims, all through the real `.githooks/pre-commit`: the seed
    commit passes with no private file at all; an ordinary commit passes; a
    commit that re-stages the fixture module passes, with its five coinciding
    literals excused as already committed; a sixth answer pasted into that
    module is blocked; a SECOND copy of an already-committed literal is blocked
    (a count could not see that one); and an ordinary fixture edit that holds no
    private answer passes.

    Every value here is invented, so the case needs no private data and runs in
    CI wherever gitleaks is installed.
    """
    if not shutil.which("gitleaks"):                       # pragma: no cover
        raise SkipCase("the real pre-commit hook refuses to run without "
                       "gitleaks, so the control cannot drive it")
    src = PT.ROOT
    fixture_rel = "analysis/test_panel_fixtures.py"
    leaf = "panel.schedule[].label"

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        # a clone of the parts of this repo the gate reads, plus the largest
        # fixture module in it, so the other household is judged against the
        # same committed fixtures the owner is
        for rel in ("analysis/privacy_tiers.py", ".githooks/pre-commit",
                    "DATA-SOURCES-CHEATSHEET.md", "household.example.yaml",
                    "analysis/test_service_headroom.py"):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((src / rel).read_bytes())
            dest.chmod(0o755)
        (root / fixture_rel).write_text(OTHER_FIXTURE)
        (root / ".gitignore").write_text("private/\n.venv/\n")
        # the hook's first interpreter candidate, as a wrapper rather than a
        # symlink: a venv interpreter linked out of its tree loses the venv
        (root / ".venv" / "bin").mkdir(parents=True)
        shim = root / ".venv" / "bin" / "python"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        shim.chmod(0o755)

        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")

        def git(*args, **kw):
            return subprocess.run(["git", *args], cwd=root, env=env,
                                  capture_output=True, text=True, **kw)

        def count():
            out = git("rev-list", "--count", "HEAD")
            return int(out.stdout.strip()) if out.returncode == 0 else 0

        def commit(message, body=None):
            if body is not None:
                (root / fixture_rel).write_text(body)
            git("add", "-A", check=True)
            return git("commit", "-m", message)

        git("init", "-q", check=True)
        git("config", "core.hooksPath", ".githooks", check=True)

        # 1. the clone commits before it has any private file, which is how
        #    every fork starts and the state CI is permanently in
        r = commit("seed the clone")
        assert r.returncode == 0, f"the seeded clone was blocked: {r.stderr}"
        assert "NOT CHECKED" in r.stderr, \
            f"a scan with no private file did not say what it skipped: {r.stderr}"
        assert count() == 1, r.stderr

        # now the second household fills in its own intake, gitignored
        (root / "private").mkdir()
        (root / "private" / "household.yaml").write_text(
            yaml.safe_dump(OTHER_HOUSEHOLD))
        assert PT._found_in((root / fixture_rel).read_text(),
                            [n for n in PT.needles(OTHER_HOUSEHOLD)
                             if n.value == OTHER_SPARE]) == [], \
            "the spare answer is already in the fixture; the control is void"

        # 2. an ordinary commit, touching none of it
        (root / "notes.md").write_text("# a note that discloses nothing\n")
        r = commit("add an unrelated note")
        assert r.returncode == 0, (
            f"an ordinary commit by a second household was blocked: {r.stderr}")
        assert count() == 2, r.stderr

        # 3. a commit that re-stages the fixture module itself: its five
        #    coinciding literals are excused because the clone already carried
        #    them, and the hook says how many rather than asserting a number
        r = commit("annotate the panel fixtures",
                   OTHER_FIXTURE + "\n# an ordinary comment\n")
        assert r.returncode == 0, f"re-staging the fixtures was blocked: {r.stderr}"
        assert f"{OTHER_PLANTED} pre-existing fixture literal(s)" in r.stderr, \
            f"the pre-existing literals were not excused: {r.stderr}"
        assert count() == 3, r.stderr

        # 4. a sixth answer, which no committed file holds: a fresh paste
        r = commit("add one more panel fixture",
                   OTHER_FIXTURE + f'\nEXTRA = [{{"label": "{OTHER_SPARE}"}}]\n')
        assert r.returncode != 0, "a freshly pasted private answer was committed"
        assert "privacy tiers: BLOCKED" in r.stderr, r.stderr
        assert leaf in r.stderr and fixture_rel in r.stderr, r.stderr
        assert OTHER_SPARE.lower() not in r.stderr.lower(), \
            "the hook printed a value"
        assert count() == 3, f"the blocked commit changed the history: {r.stderr}"

        # 5. a SECOND copy of a literal the baseline already holds. The count
        #    this rule replaces did not move for this one: the value was
        #    already declared once, so every further occurrence rode on it.
        r = commit("quote a fixture label again",
                   OTHER_FIXTURE + '\nSPARE = "Quorra"\n')
        assert r.returncode != 0, "a second copy of a fixture label was committed"
        assert "privacy tiers: BLOCKED" in r.stderr, r.stderr
        assert leaf in r.stderr, r.stderr
        assert count() == 3, f"the blocked commit changed the history: {r.stderr}"

        # 6. and the ordinary case this must not disturb: editing a fixture
        #    file without introducing a private answer
        r = commit("add an invented fixture",
                   OTHER_FIXTURE + '\nOTHERS = [{"label": "Nonesuch"}]\n')
        assert r.returncode == 0, f"an invented fixture was blocked: {r.stderr}"
        assert count() == 4, r.stderr

        # 7. the resolver check the hook now makes before it scans anything:
        #    a field id the schema cannot locate is a rule pointed at nothing,
        #    and it blocks -- with the private file, and without it
        cheat = root / "DATA-SOURCES-CHEATSHEET.md"
        whole = cheat.read_text()
        assert "id: panel_meter_class" in whole, "the control's field id moved"
        cheat.write_text(whole.replace("id: panel_meter_class",
                                       "id: panel_no_such_key", 1))
        for why in ("with the private file", "without it"):
            r = commit(f"retype a field id ({why})")
            assert r.returncode != 0, f"an unresolvable field committed {why}"
            assert "privacy tiers: BLOCKED" in r.stderr, r.stderr
            assert "panel_no_such_key -> panel.no_such_key" in r.stderr, \
                f"the blocked commit did not name the field: {r.stderr}"
            assert count() == 4, r.stderr
            (root / "private" / "household.yaml").unlink(missing_ok=True)
    return (f"a second household's clone seeds, commits, re-stages its fixture "
            f"module with all {OTHER_PLANTED} coinciding literals excused, and "
            f"edits a fixture freely -- with no committed count and nothing to "
            f"configure; a sixth answer pasted in and a second copy of an "
            f"existing literal are both blocked, as is a field id that resolves "
            f"to nothing, with or without a private file")


def case_the_gate_refuses_a_tier_whose_subject_it_cannot_locate():
    """A rule pointed at nothing must not report every tree as clean.

    `yaml_path_for` returns the id itself when steps 1-4 derive nothing, and a
    path the schema has no room for resolves to no value, produces no needle
    and bans no key. So a mistyped or renamed field id silently removes itself
    from the universe and every scan of it comes back clean -- the one failure
    mode a gate must never dress up as a pass. `resolution_report` has always
    named those, `test_privacy_tiers` has always failed on them, and the hook
    scanned straight past. `gate` now refuses before it scans.

    Public data only -- an id, a type and a yaml path -- so it runs in CI and
    in a clone with no private file, which is the point: resolution is a
    property of the cheatsheet and the committed template, not of an intake.
    """
    fields = PT.cheatsheet_fields()
    shape = PT.schema()
    broken = list(fields) + [{"id": "panel_no_such_key", "question": "?",
                              "type": "string", "required_if": "always",
                              "where": "-", "privacy": "private-only"}]
    assert not PT.resolution_report(fields=fields, shape=shape)["unresolvable"]
    assert [fid for fid, _ in
            PT.resolution_report(fields=broken, shape=shape)["unresolvable"]] \
        == ["panel_no_such_key"], "the control field resolves after all"

    # with a household and without one: the check reads neither
    for household, why in ((None, "no private file"), (PLANTED, "one")):
        try:
            PT.gate([("data/x.json", "{}")], household, fields=broken,
                    shape=shape)
            assert False, f"the gate scanned past an unresolvable field ({why})"
        except PT.UnresolvableField as e:
            assert [fid for fid, _ in e.fields] == ["panel_no_such_key"], e.fields
            assert "panel.no_such_key" in str(e), str(e)
    # and it does not fire on the committed cheatsheet
    hits, _ = PT.gate([("data/x.json", "{}")], None, fields=fields, shape=shape)
    assert not hits, hits
    return ("the gate refuses to scan when a tiered field resolves to no "
            "household.yaml path, naming the id and the path, with or without "
            "a private file present")


def case_the_gate_refuses_an_untiered_private_key():
    """A key present in household.yaml with NO field id at all -- worse than
    an id that fails to resolve, because nothing declared it in the first
    place.

    `UnresolvableField` (the case above) catches a field id the schema cannot
    locate. This is the reverse gap: a key that exists in the household and
    that no cheatsheet field id resolves to, in either direction, builds no
    needle and bans no key -- invisible to every mechanism in this module by
    construction, and a tree holding it reported clean until now.
    `untiered_leaf_paths` has always computed this set; `gate` did not call it.

    Synthetic, so the planted key runs in CI: nothing here is a real answer.
    """
    fields = PT.cheatsheet_fields()
    shape = PT.schema()
    planted = dict(PLANTED, an_untiered_field="an answer nobody gave a tier")
    untiered = PT.untiered_leaf_paths(planted, fields, shape)
    assert untiered == ["an_untiered_field"], untiered

    try:
        PT.gate([("data/x.json", "{}")], planted, fields=fields, shape=shape)
        assert False, "the gate scanned past an untiered private key"
    except PT.UntieredKey as e:
        assert e.paths == ["an_untiered_field"], e.paths
        assert "an_untiered_field" in str(e), str(e)

    # without a private file there is nothing to find an untiered key IN, so
    # the check is skipped -- not passed -- and the gate proceeds
    hits, _ = PT.gate([("data/x.json", "{}")], None, fields=fields, shape=shape)
    assert isinstance(hits, list)

    # the ordinary case this must not disturb: PLANTED itself is fully tiered,
    # which every other control in this file already relies on
    PT.gate([("data/x.json", "{}")], PLANTED, fields=fields, shape=shape)

    if not PT.REAL_HOUSEHOLD.is_file():
        return ("the gate raises UntieredKey, naming the leaf path, only "
                "when a household is given and it holds an untiered key; the "
                "real-household leg was skipped -- no private/household.yaml "
                "here")
    household = yaml.safe_load(PT.REAL_HOUSEHOLD.read_text()) or {}
    # must not raise: the real intake is confirmed fully tiered through the
    # same gate a commit is judged by, not only through untiered_leaf_paths
    # directly (case_every_intake_key_is_tiered, below)
    PT.gate([("data/x.json", "{}")], household, fields=fields, shape=shape)
    return ("the gate raises UntieredKey, naming the leaf path, when a "
            "private household key has no cheatsheet field id at all -- with "
            "no private file the check is skipped rather than passed -- and "
            "the real household.yaml passes the same gate untiered-key-free")


BROKEN_PY_SYNTAX = "def broken(:\n    pass\n"


def case_a_staged_python_syntax_error_blocks_the_scan():
    """A staged `.py` file that fails to parse must not go unscanned.

    `scan_script_reads` used to `continue` past a `SyntaxError`, so a read of
    a banned path hidden inside a file that fails to parse went unchecked --
    inconsistent with the fail-closed treatment an unparseable JSON/YAML
    artifact already gets (`UnparseableArtifact`, which blocks). It now raises
    the same exception, carrying the path, the error class and the line, and
    never the source.
    """
    derived = PT.unsearchable_fields()
    try:
        PT.scan_script_reads(
            [("analysis/broken_module.py", BROKEN_PY_SYNTAX)], derived)
        assert False, ("scan_script_reads silently skipped a staged .py file "
                       "with a syntax error")
    except PT.UnparseableArtifact as e:
        assert e.relpath == "analysis/broken_module.py", e.relpath
        assert e.kind == "SyntaxError", e.kind
        assert e.line is not None, "no line number carried for the syntax error"

    # and through the gate a commit is actually judged by, on a throwaway
    # staged tree -- not just the scanning function in isolation
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        tree = dict(CLEAN_TREE)
        tree["analysis/broken_module.py"] = BROKEN_PY_SYNTAX
        _tree(root, tree)
        items, _ = PT.staged_items(root)
        try:
            PT.gate(items, PLANTED)
            assert False, "the gate scanned past an unparseable staged .py file"
        except PT.UnparseableArtifact as e:
            assert e.relpath == "analysis/broken_module.py", e.relpath
    return ("a staged .py file with a genuine syntax error raises "
            "UnparseableArtifact from scan_script_reads and from gate, "
            "naming the file and the error class rather than being silently "
            "skipped")


def case_the_precommit_hook_blocks_an_unparseable_staged_script():
    """The real hook, not just the scanning function, on a broken `.py` file.

    Same throwaway-repository control the other hook-level cases use: the
    real `.githooks/pre-commit`, dispatched by `core.hooksPath`, against a
    commit that stages a `.py` file which does not parse. No private file is
    present -- this is the state CI and a fresh clone are permanently in --
    so this exercises the half of the rule that has to hold even then.
    """
    if not shutil.which("gitleaks"):                       # pragma: no cover
        raise SkipCase("the real pre-commit hook refuses to run without "
                       "gitleaks, so the control cannot drive it")
    src = PT.ROOT
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        for rel in ("analysis/privacy_tiers.py", ".githooks/pre-commit",
                    "DATA-SOURCES-CHEATSHEET.md", "household.example.yaml"):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((src / rel).read_bytes())
            dest.chmod(0o755)
        (root / ".gitignore").write_text("private/\n.venv/\n")
        (root / ".venv" / "bin").mkdir(parents=True)
        shim = root / ".venv" / "bin" / "python"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        shim.chmod(0o755)
        (root / "README.md").write_text("# throwaway\n")

        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")

        def git(*args, **kw):
            return subprocess.run(["git", *args], cwd=root, env=env,
                                  capture_output=True, text=True, **kw)

        def count():
            out = git("rev-list", "--count", "HEAD")
            return int(out.stdout.strip()) if out.returncode == 0 else 0

        git("init", "-q", check=True)
        git("config", "core.hooksPath", ".githooks", check=True)
        git("add", "-A", check=True)
        r = git("commit", "-m", "seed the clone")
        assert r.returncode == 0, f"the seed commit was blocked: {r.stderr}"
        assert count() == 1, r.stderr

        (root / "analysis" / "broken_module.py").write_text(BROKEN_PY_SYNTAX)
        git("add", "-A", check=True)
        r = git("commit", "-m", "add a module with a syntax error")
        assert r.returncode != 0, "a staged .py file with a syntax error was committed"
        assert "privacy tiers" in r.stderr and "BLOCKED" in r.stderr, r.stderr
        assert "broken_module.py" in r.stderr, r.stderr
        assert count() == 1, f"the blocked commit changed the history: {r.stderr}"
    return ("the real pre-commit hook blocks a commit that stages a .py file "
            "with a genuine syntax error, naming the file, and leaves the "
            "commit count unchanged")


def case_the_hooks_refuse_to_run_when_the_gate_script_is_missing():
    """A missing `analysis/privacy_tiers.py` must not read as "ran clean."

    CPython exits 2 when it cannot open the target script at all (`can't open
    file ...: [Errno 2] No such file or directory`) -- the SAME code this gate
    returns from inside `main()` to mean "ran fine, household.yaml is just
    absent, so only the value scan was skipped." Before this fix, both hooks
    read that collision as the legitimate case and printed "Commit allowed;
    the key/path rules above did run" -- when in fact nothing had run at all,
    not even the key/path half. Reproduced directly (not through this suite)
    in throwaway repos before the fix: deleting the script let a staged
    `itc_claimed` key, and a commit message carrying a private literal, both
    through.

    Two throwaway repos, one hook each -- the sibling hook file is simply
    absent from `.githooks/`, so `core.hooksPath` dispatch has nothing else to
    run, isolating each hook the way `case_the_commit_msg_hook_blocks_and_
    leaves_the_history_alone` already does. `analysis/privacy_tiers.py` is
    never written into either repo, so every commit attempt is the vulnerable
    case from the first one.
    """
    if not shutil.which("gitleaks"):                       # pragma: no cover
        raise SkipCase("the real pre-commit hook refuses to run without "
                       "gitleaks, so the control cannot drive it")
    src = PT.ROOT
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")

    def seed_hook_only(root, hook_rel):
        dest = root / hook_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((src / hook_rel).read_bytes())
        dest.chmod(0o755)
        (root / ".gitignore").write_text("private/\n.venv/\n")
        # the hook's first interpreter candidate, as a wrapper rather than a
        # symlink: a venv interpreter linked out of its tree loses the venv
        (root / ".venv" / "bin").mkdir(parents=True)
        shim = root / ".venv" / "bin" / "python"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        shim.chmod(0o755)

    def git(root, *args, **kw):
        return subprocess.run(["git", *args], cwd=root, env=env,
                              capture_output=True, text=True, **kw)

    def count(root):
        out = git(root, "rev-list", "--count", "HEAD")
        return int(out.stdout.strip()) if out.returncode == 0 else 0

    # 1. .githooks/pre-commit alone, gate script never present: a staged file
    #    carrying a banned key (itc_claimed, private-only per the cheatsheet)
    #    must not slip through on a false "ran clean."
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        seed_hook_only(root, ".githooks/pre-commit")
        (root / "data").mkdir()
        (root / "data" / "leak.json").write_text('{"itc_claimed": true}\n')
        git(root, "init", "-q", check=True)
        git(root, "config", "core.hooksPath", ".githooks", check=True)
        git(root, "add", "-A", check=True)
        assert not (root / "analysis" / "privacy_tiers.py").exists(), \
            "the control committed the gate script; it must stay absent"
        r = git(root, "commit", "-m",
                 "stage a banned key with the gate script missing")
        assert r.returncode != 0, (
            f"a banned key was committed while the gate script was missing: "
            f"{r.stderr}")
        assert "analysis/privacy_tiers.py" in r.stderr, r.stderr
        assert ("missing" in r.stderr or "unreadable" in r.stderr), r.stderr
        assert "Commit allowed" not in r.stderr, (
            f"the old false-pass message survived the fix: {r.stderr}")
        assert count(root) == 0, f"the blocked commit changed the history: {r.stderr}"

    # 2. .githooks/commit-msg alone, gate script never present: a commit
    #    message carrying a private-only literal must not slip through either.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        seed_hook_only(root, ".githooks/commit-msg")
        (root / "README.md").write_text("# throwaway\n")
        git(root, "init", "-q", check=True)
        git(root, "config", "core.hooksPath", ".githooks", check=True)
        git(root, "add", "-A", check=True)
        assert not (root / "analysis" / "privacy_tiers.py").exists(), \
            "the control committed the gate script; it must stay absent"
        r = git(root, "commit", "-m",
                 "panel: record the CL999-TEST meter class")
        assert r.returncode != 0, (
            f"a commit was allowed while the gate script was missing: "
            f"{r.stderr}")
        assert "analysis/privacy_tiers.py" in r.stderr, r.stderr
        assert ("missing" in r.stderr or "unreadable" in r.stderr), r.stderr
        assert "WARNING" not in r.stderr, (
            f"the old false-pass warning survived the fix: {r.stderr}")
        assert count(root) == 0, f"the blocked commit changed the history: {r.stderr}"
    return ("both hooks refuse to run, naming analysis/privacy_tiers.py, "
            "when the gate script itself is missing -- rather than reading "
            "CPython's exit code 2 for 'can't open file' as the unrelated, "
            "legitimate exit code 2 the script returns for an absent "
            "household.yaml -- and neither blocked commit changes the "
            "history")


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
    case_a_test_module_and_the_template_are_scanned_like_any_other_file,
    case_only_a_literal_the_baseline_already_held_is_excused,
    case_a_yaml_artifact_is_read_structurally_and_not_by_pattern,
    case_a_container_read_hands_over_the_keys_inside_it,
    case_the_unsearchable_fields_are_derived_from_the_tiers,
    case_a_sub_floor_bare_answer_is_covered_by_the_substitute_rule,
    case_a_banned_key_or_path_read_fails,
    case_the_template_is_excused_from_the_key_rule_in_writing,
    case_the_committed_tree_carries_no_banned_key_or_read,
    case_the_commit_message_is_inside_the_boundary_and_scanned,
    case_the_gate_refuses_a_tier_whose_subject_it_cannot_locate,
    case_the_gate_refuses_an_untiered_private_key,
    case_a_staged_python_syntax_error_blocks_the_scan,
    case_the_precommit_hook_blocks_an_unparseable_staged_script,
    case_the_hooks_refuse_to_run_when_the_gate_script_is_missing,
    case_the_commit_msg_hook_blocks_and_leaves_the_history_alone,
    case_the_precommit_gate_blocks_a_staged_leak,
    case_the_precommit_hook_is_portable_to_another_household,
    case_the_example_template_is_tiered_too,
    case_no_private_only_value_appears_in_any_tracked_file,
    case_the_tree_is_clean_and_the_baseline_is_what_excuses_it,
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
        except suite_runner.CASE_FAILURES as e:                     # noqa: BLE001
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
