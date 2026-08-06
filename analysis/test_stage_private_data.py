#!/usr/bin/env python3
"""stage-private-data.sh must copy every raw private input a committed
generator actually reads, so a fresh worktree staged only by the script can
run the full pipeline (issue #33).

The required-input set is DERIVED by scanning every generator's own source
for its private/1-raw-data path references, in the four shapes this repo's
generators actually use (a pathlib chain, os.path.join, a
ROOT/"private"/"1-raw-data" directory variable referenced later, or a .glob()
call directly on that bare root variable/expression with either a literal
pattern or a same-file string constant) -- not hand-typed here -- so a newly
added private input a future generator reads and stage-private-data.sh does
not stage fails this suite instead of silently breaking a fresh worktree the
way electric-bills/, gas-bills/ and electric_billing_history_2024-2026.csv
did before this issue.

KNOWN GAP, left honest rather than silently claimed solved (adversarial
review, issue #33, round 1): service_headroom.py's only_match(pattern, what) takes
its glob pattern as a FUNCTION PARAMETER, so `RAW_DIR.glob(pattern)` at
service_headroom.py:746 cannot be resolved by scanning that line alone --
doing so would require tracing every call site of an arbitrary function,
which this scanner does not attempt. Both of that function's actual call
sites (service_headroom.py:3941,4785) pass the literal
"Electric_15_Minute_*.csv", already staged and already caught directly at
its OTHER two call shapes elsewhere in the codebase (service_headroom.py's
own RAW_DIR.glob("enphase_sam8760_*.csv") calls, and
irreducible_bill.py's (ROOT/"private"/"1-raw-data").glob(RAW_INTERVAL_GLOB))
-- verified by hand, not by this scanner, and re-verify by hand if
only_match() ever gains a new call site with a new pattern.

Run from the repo root:  ./.venv/bin/python analysis/test_stage_private_data.py
"""
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "analysis"
SCRIPT = ROOT / "stage-private-data.sh"

# private/1-raw-data entries a generator's own NORMAL run needs but this
# script deliberately does not stage, each with the reason -- an exemption
# here is never silent. Both are read only under a non-default flag no
# automated pipeline run ever passes (dsgs_vpp_backtest.py --build-calendar,
# nem3_grandfathering.py --build-rates); caiso_raw is handled, conditionally,
# by the script itself, so it is exempt from the "must appear literally" check
# below without needing to be listed twice.
OPTIONAL_NOT_STAGED = {
    "dsgs_events": "dsgs_vpp_backtest.py reads this only under --build-calendar, "
                  "which no automated pipeline run passes",
    "sdge_nbt_export_rates": "nem3_grandfathering.py reads this only under "
                            "--build-rates, which no automated pipeline run passes",
    "caiso_raw": "staged conditionally by this same script when present; "
                "carbon_fullyear.py rebuilds exactly from the committed "
                "data/caiso_hourly_intensity.csv when it is not",
}

_ROOT_VAR = re.compile(r'^(\w+)\s*=\s*ROOT\s*/\s*"private"\s*/\s*"1-raw-data"\s*$', re.M)
_ANON_ROOT = r'\(\s*ROOT\s*/\s*"private"\s*/\s*"1-raw-data"\s*\)'
_DIRECT = re.compile(r'"private"\s*/\s*"1-raw-data"\s*/\s*"([^"/]+)"')
_OS_JOIN = re.compile(
    r'os\.path\.join\(\s*ROOT\s*,\s*"private"\s*,\s*"1-raw-data"\s*,\s*"([^"/]+)"')
_STR_CONST = re.compile(r'^(\w+)\s*=\s*"([^"]+)"\s*$', re.M)


def _glob_matches(text, receiver_pattern):
    """.glob(...) calls on `receiver_pattern` (a bare 1-raw-data root, never a
    subdirectory var -- a glob on an already-staged subdirectory like
    ELEC_DIR/GAS_DIR/CAISO_DIR needs no separate entry, since cp -R already
    covers every file under it). The argument is either a literal string or
    a same-file string constant's name; a glob argument that is itself a
    function PARAMETER (service_headroom.py's only_match(pattern, ...)) is
    not resolved here -- see this module's own docstring."""
    consts = dict(_STR_CONST.findall(text))
    out = []
    for m in re.finditer(receiver_pattern + r'\.glob\(\s*(?:"([^"]+)"|(\w+))\s*\)', text):
        literal, name = m.group(1), m.group(2)
        if literal is not None:
            out.append(literal)
        elif name in consts:
            out.append(consts[name])
    return out


def _referenced_1raw_data_paths():
    """{leaf_name: {generator filenames that reference it}}, scanning every
    non-test .py file in analysis/. Test files build their own throwaway
    private trees (test_parse_bills.py's own ELEC/GAS fixtures) and are not
    part of what a real worktree's stage-private-data.sh run has to cover."""
    found = {}
    for f in sorted(ANALYSIS.glob("*.py")):
        if f.name.startswith("test_"):
            continue
        text = f.read_text()
        root_vars = set(_ROOT_VAR.findall(text))
        for pat in (_DIRECT, _OS_JOIN):
            for m in pat.finditer(text):
                found.setdefault(m.group(1), set()).add(f.name)
        for var in root_vars:
            for m in re.finditer(re.escape(var) + r'\s*/\s*"([^"/]+)"', text):
                found.setdefault(m.group(1), set()).add(f.name)
            for pattern in _glob_matches(text, re.escape(var)):
                found.setdefault(pattern, set()).add(f.name)
        for pattern in _glob_matches(text, _ANON_ROOT):
            found.setdefault(pattern, set()).add(f.name)
    return found


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


_CP_ARG = re.compile(r'^\s*cp\s+(?:-R\s+)?"?\$SRC"?/private/1-raw-data/([^\s"\\]+)', re.M)


def _staged_basenames(script_text):
    """Every source basename (or glob pattern) an actual cp/cp -R INVOCATION
    line copies out of private/1-raw-data/ -- e.g. "Electric_15_Minute_*.csv",
    "enphase_sam8760_2025.csv", "electric-bills". Anchored to a leading `cp`
    so the script's own unrelated caiso_raw existence-check `ls` line is
    never mistaken for a real copy."""
    return {pathlib.PurePosixPath(m.group(1)).name for m in _CP_ARG.finditer(script_text)}


def _is_staged(name, script_text):
    """A literal filename/dirname must be exactly one of the staged
    basenames. A glob PATTERN (contains '*') is satisfied only if it and a
    staged basename/pattern actually describe overlapping files -- checked
    with fnmatch in BOTH directions (either could be the more specific one),
    never a bare substring or prefix test, which a fixed-prefix-sharing but
    otherwise unrelated future pattern (e.g. "enphase_sam*.pdf" against the
    already-staged "enphase_sam8760_2025.csv") would pass by accident
    (adversarial review, issue #33, round 2)."""
    import fnmatch
    staged = _staged_basenames(script_text)
    if "*" not in name:
        return name in staged
    return any(name == s or fnmatch.fnmatch(s, name) or fnmatch.fnmatch(name, s)
              for s in staged)


@case
def case_every_referenced_private_input_is_staged_or_documented_optional():
    referenced = _referenced_1raw_data_paths()
    assert referenced, "the scanner found nothing -- it likely broke silently"
    script_text = SCRIPT.read_text()
    missing = {name: sorted(users) for name, users in referenced.items()
              if name not in OPTIONAL_NOT_STAGED and not _is_staged(name, script_text)}
    assert not missing, (
        f"stage-private-data.sh does not stage these private inputs a generator "
        f"reads, and they are not documented in OPTIONAL_NOT_STAGED: {missing}")
    return (f"{len(referenced)} referenced private inputs are all either "
           f"staged by the script or documented as intentionally optional")


@case
def case_is_staged_rejects_an_unrelated_pattern_sharing_a_prefix():
    """Adversarial review, issue #33, round 2: an earlier version of
    _is_staged checked only whether a glob pattern's fixed prefix appeared
    anywhere in the script's text, which would have wrongly certified a
    brand-new, genuinely different glob pattern as covered merely because it
    shares a prefix with an already-staged, unrelated filename -- e.g.
    "enphase_sam*.pdf" (a hypothetical future export) against the real,
    already-staged "enphase_sam8760_2025.csv" (a .csv, not a .pdf)."""
    script_text = SCRIPT.read_text()
    assert not _is_staged("enphase_sam*.pdf", script_text), (
        "a pattern for a different file type must not be certified staged "
        "just because it shares a text prefix with an unrelated staged file")
    assert not _is_staged("gas*.xlsx", script_text)
    assert not _is_staged("electric_billing*.pdf", script_text)
    # the real, genuinely-covered pattern must still pass
    assert _is_staged("enphase_sam8760_*.csv", script_text)
    return "an unrelated pattern sharing only a text prefix with a staged file is correctly rejected"


@case
def case_the_scanner_catches_a_planted_missing_input():
    """The derivation above is only useful if it actually fails when a real
    generator starts reading something new -- proven by planting one."""
    with tempfile.TemporaryDirectory() as td:
        planted = pathlib.Path(td) / "_planted_generator.py"
        planted.write_text(
            'NEW_INPUT = ROOT / "private" / "1-raw-data" / "brand_new_export.csv"\n')
        try:
            shutil.copy2(planted, ANALYSIS / "_planted_generator.py")
            referenced = _referenced_1raw_data_paths()
            assert "brand_new_export.csv" in referenced, referenced
            assert "brand_new_export.csv" not in SCRIPT.read_text()
        finally:
            (ANALYSIS / "_planted_generator.py").unlink(missing_ok=True)
    return "a planted new private-input reference is detected as unstaged"


@case
def case_real_archive_stage_script_produces_every_required_path():
    """End-to-end proof of AC-1: run the actual script against this
    machine's real private archive into a scratch directory, and check every
    non-optional referenced input, plus the private/verify sandbox copies,
    actually exist afterward -- not just that the script's own source text
    mentions them."""
    src = ROOT
    if not (src / "private" / "household.yaml").is_file():
        raise SkipCase("needs this machine's real private/ archive, which "
                       "this checkout does not have")
    referenced = _referenced_1raw_data_paths()
    with tempfile.TemporaryDirectory() as td:
        dst = pathlib.Path(td) / "dst"
        import subprocess
        result = subprocess.run(
            ["bash", str(SCRIPT), str(src), str(dst)],
            capture_output=True, text=True)
        assert result.returncode == 0, (
            f"stage-private-data.sh exited {result.returncode}: {result.stderr}")
        missing = []
        for name in referenced:
            if name in OPTIONAL_NOT_STAGED:
                continue
            if not list((dst / "private" / "1-raw-data").glob(name)) \
                    and not (dst / "private" / "1-raw-data" / name).exists():
                missing.append(name)
        assert not missing, f"staged directory is missing: {missing}"
        for verify_file in ("usage.csv", "samA.csv", "samB.csv"):
            assert (dst / "private" / "verify" / verify_file).is_file(), verify_file
    return (f"a real run of stage-private-data.sh produced all "
           f"{len(referenced) - len(OPTIONAL_NOT_STAGED)} required private "
           f"inputs plus the three private/verify sandbox copies")


def run():
    passed = failed = skipped = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS  {fn.__name__}: {msg}")
            passed += 1
        except SkipCase as e:
            print(f"SKIP  {fn.__name__}: {e}")
            skipped += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(CASES)} passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
