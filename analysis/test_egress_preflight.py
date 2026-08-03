#!/usr/bin/env python3
"""Tests for llm_providers.py's preflight() egress gate (issue #39, part 3):
the privacy safety net for the new network path a real LLM call would open.
Adapter/env/redaction tests live in the sibling test_llm_providers.py.

Follows this repo's established CASES/@case/main() convention (see e.g.
test_report_tokens.py). preflight() never opens a socket itself, so unlike
test_llm_providers.py this file does not need to poison urllib.request --
but it imports llm_providers, which already does so as a matter of process
hygiene for the whole test run.

Run from the repo root:  ./.venv/bin/python analysis/test_egress_preflight.py
"""
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import llm_providers as lp  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


def _require_household():
    if not lp.rt.hh.PATH.is_file():
        raise SkipCase(f"needs private/household.yaml ({lp.rt.hh.PATH}), which this "
                        "checkout does not have")


def _require_gitleaks():
    if shutil.which("gitleaks") is None:
        raise SkipCase("needs the gitleaks binary (brew install gitleaks), not installed here")


class _patched:
    """Temporarily replace an attribute on `obj`, restoring it on exit."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


# ---------------------------------------------------------------------------
# AC: preflight() refuses when any payload file is outside the allowlist --
# private/1-raw-data/ and private/household.yaml itself, specifically.
# ---------------------------------------------------------------------------
@case
def case_refuses_a_file_under_private_1_raw_data():
    poisoned = lp.ROOT / "private" / "1-raw-data" / "gas.csv"
    try:
        lp.preflight([("data_file", str(poisoned))], "clean body text")
        raise AssertionError("preflight() accepted a private/1-raw-data/ path")
    except lp.EgressRefused as e:
        assert "private" in str(e), e
    return "preflight() refuses a data_file item under private/1-raw-data/"


@case
def case_refuses_household_yaml_itself_even_labeled_as_a_data_file():
    poisoned = lp.ROOT / "private" / "household.yaml"
    try:
        lp.preflight([("data_file", str(poisoned))], "clean body text")
        raise AssertionError("preflight() accepted private/household.yaml directly")
    except lp.EgressRefused as e:
        assert "private" in str(e), e
    return "preflight() refuses private/household.yaml itself, whatever kind it is labeled"


@case
def case_refuses_a_private_path_even_when_mislabeled_template_file():
    """Defense in depth: the 'private' check runs before the kind-specific
    directory check, so mislabeling doesn't help an attacker sneak a private
    path past the allowlist."""
    poisoned = lp.ROOT / "private" / "household.yaml"
    try:
        lp.preflight([("template_file", str(poisoned))], "x")
        raise AssertionError("preflight() accepted a private path under template_file")
    except lp.EgressRefused as e:
        assert "private" in str(e), e
    return "the private/ check applies regardless of the claimed item kind"


# ---------------------------------------------------------------------------
# AC: accepts a data/*.json file, accepts a resolved public-ok household
# token STRING (never the household.yaml file).
# ---------------------------------------------------------------------------
@case
def case_accepts_a_real_committed_data_json_file():
    target = lp.ROOT / "data" / "package_results.json"
    assert target.is_file(), f"fixture assumption broken: {target} does not exist"
    result = lp.preflight([("data_file", str(target))], "clean body text")
    assert result["ok"], result
    return f"preflight() accepts the git-tracked {target.relative_to(lp.ROOT)}"

@case
def case_accepts_report_template_html_as_template_file():
    result = lp.preflight([("template_file", str(lp.rt.TEMPLATE))], "clean body text")
    assert result["ok"], result
    return "preflight() accepts report-template.html labeled template_file"


@case
def case_refuses_a_data_file_path_that_is_not_git_tracked_or_does_not_exist():
    ghost = lp.ROOT / "data" / "ZZZ_fabricated_nonexistent_for_this_test.json"
    assert not ghost.exists(), "fixture assumption broken: the ghost path actually exists"
    try:
        lp.preflight([("data_file", str(ghost))], "x")
        raise AssertionError("preflight() accepted a nonexistent data/ path")
    except lp.EgressRefused as e:
        assert "does not exist" in str(e) or "not git-tracked" in str(e), e
    return "preflight() refuses a data_file path that does not exist"


@case
def case_refuses_a_data_file_path_outside_the_data_directory():
    outside = lp.ROOT / "CLAUDE.md"
    try:
        lp.preflight([("data_file", str(outside))], "x")
        raise AssertionError("preflight() accepted a data_file item outside data/")
    except lp.EgressRefused as e:
        assert "not under data/" in str(e), e
    return "preflight() refuses a data_file item pointing outside data/"


@case
def case_accepts_a_resolved_public_ok_household_token_as_a_string():
    _require_household()
    value = lp.rt.resolve_token("PANEL_COUNT")
    result = lp.preflight([("household_token", ("PANEL_COUNT", value))], "clean body text")
    assert result["ok"], result
    return f"preflight() accepts household_token ('PANEL_COUNT', {value!r}) as a re-verified string"


@case
def case_refuses_a_tampered_household_token_value():
    _require_household()
    try:
        lp.preflight([("household_token", ("PANEL_COUNT", "not-the-real-value"))], "x")
        raise AssertionError("preflight() accepted a household token whose value doesn't match")
    except lp.EgressRefused as e:
        assert "PANEL_COUNT" in str(e), e
    return "preflight() refuses a household_token item whose value doesn't re-resolve"


@case
def case_refuses_a_non_string_claude_excerpt_item():
    try:
        lp.preflight([("claude_excerpt", 12345)], "x")
        raise AssertionError("preflight() accepted a non-string claude_excerpt value")
    except lp.EgressRefused as e:
        assert "claude_excerpt" in str(e), e
    return "preflight() refuses a claude_excerpt item that isn't a literal string"


@case
def case_refuses_a_household_token_item_that_is_not_a_two_tuple():
    try:
        lp.preflight([("household_token", "PANEL_COUNT")], "x")
        raise AssertionError("preflight() accepted a household_token value that isn't a 2-tuple")
    except lp.EgressRefused as e:
        assert "household_token" in str(e), e
    return "preflight() refuses a household_token item that is not a (name, value) tuple"


@case
def case_refuses_a_household_token_naming_an_unresolvable_token():
    try:
        lp.preflight([("household_token", ("NOT_A_REAL_TOKEN_NAME", "whatever"))], "x")
        raise AssertionError("preflight() accepted a household token name report_tokens.py "
                              "doesn't know")
    except lp.EgressRefused as e:
        assert "NOT_A_REAL_TOKEN_NAME" in str(e), e
    return "preflight() refuses a household_token whose name doesn't resolve through report_tokens.py"


@case
def case_refuses_a_data_file_that_exists_but_is_not_git_tracked():
    scratch = lp.ROOT / "data" / "ZZZ_test_scratch_untracked_for_egress_preflight_test.json"
    assert not scratch.exists(), f"fixture assumption broken: {scratch} already exists"
    scratch.write_text("{}")
    try:
        try:
            lp.preflight([("data_file", str(scratch))], "x")
            raise AssertionError("preflight() accepted an untracked file under data/")
        except lp.EgressRefused as e:
            assert "not git-tracked" in str(e), e
    finally:
        scratch.unlink(missing_ok=True)
    return "preflight() refuses a data/ file that exists on disk but isn't git-tracked"


@case
def case_refuses_an_unknown_item_kind():
    try:
        lp.preflight([("raw_household_yaml_file", "whatever")], "x")
        raise AssertionError("preflight() accepted an item kind outside the allowlist")
    except lp.EgressRefused as e:
        assert "raw_household_yaml_file" in str(e), e
    return "preflight() refuses any item kind not on ALLOWED_KINDS"


@case
def case_claude_excerpt_and_todo_text_kinds_accept_literal_strings():
    result = lp.preflight(
        [("claude_excerpt", "some CLAUDE.md section 10 excerpt text"),
         ("todo_text", "the TODO block's own comment text")],
        "clean body text")
    assert result["ok"], result
    return "claude_excerpt and todo_text items are accepted as caller-asserted literal strings"


# ---------------------------------------------------------------------------
# AC: a synthetic PII string matching a real committed .gitleaks.toml rule
# inside a fabricated data/-sourced payload is refused.
# ---------------------------------------------------------------------------
@case
def case_refuses_a_body_containing_a_fabricated_sdge_account_number():
    _require_gitleaks()
    # Shape-matches .gitleaks.toml's sdge-formatted-account-number rule
    # (0087 XXXX XXXX X) with an entirely fabricated number -- never a real one.
    poisoned_body = "some prose mentioning account number: 0087-1234-5678-9 in passing"  # gitleaks:allow
    try:
        lp.preflight([], poisoned_body)
        raise AssertionError("preflight() sent a body containing a fabricated account number")
    except lp.EgressRefused as e:
        assert "gitleaks" in str(e).lower() or "flagged" in str(e).lower(), e
    return "preflight() refuses a body matching the committed sdge-formatted-account-number rule"


@case
def case_accepts_a_clean_body_through_the_real_gitleaks_scan():
    _require_gitleaks()
    result = lp.preflight([], "a perfectly ordinary sentence about kilowatt-hours")
    assert result["ok"], result
    return "preflight() passes a clean body through the real gitleaks scan"


# ---------------------------------------------------------------------------
# AC: gitleaks itself being unavailable fails SAFE (refuses), it does not
# silently skip the scan. Independent of whether gitleaks happens to be
# installed on the machine running this suite.
# ---------------------------------------------------------------------------
@case
def case_gitleaks_scan_fails_safe_on_a_timeout():
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="gitleaks", timeout=60)

    with _patched(shutil, "which", lambda name: "/usr/bin/gitleaks"), \
         _patched(subprocess, "run", fake_run):
        try:
            lp.preflight([], "clean text")
            raise AssertionError("preflight() sent a body after a gitleaks scan timeout")
        except lp.EgressRefused as e:
            assert "timed out" in str(e), e
    return "preflight() refuses (fails safe) when the gitleaks scan itself times out"


@case
def case_gitleaks_scan_fails_safe_when_the_binary_cannot_be_run():
    def fake_run(*a, **kw):
        raise OSError("exec format error")

    with _patched(shutil, "which", lambda name: "/usr/bin/gitleaks"), \
         _patched(subprocess, "run", fake_run):
        try:
            lp.preflight([], "clean text")
            raise AssertionError("preflight() sent a body when gitleaks could not be run")
        except lp.EgressRefused as e:
            assert "gitleaks scan failed to run" in str(e), e
    return "preflight() refuses (fails safe) when the gitleaks binary can't actually be executed"


@case
def case_gitleaks_scan_fails_safe_on_an_unexpected_exit_code():
    class _FakeResult:
        returncode = 2
        stdout = ""
        stderr = "some unexpected gitleaks internal error"

    with _patched(shutil, "which", lambda name: "/usr/bin/gitleaks"), \
         _patched(subprocess, "run", lambda *a, **kw: _FakeResult()):
        try:
            lp.preflight([], "clean text")
            raise AssertionError("preflight() sent a body on a non-0/1 gitleaks exit code")
        except lp.EgressRefused as e:
            assert "exited 2" in str(e), e
    return "preflight() refuses on a gitleaks exit code that is neither 'clean' (0) nor 'leaks found' (1)"


@case
def case_missing_gitleaks_binary_fails_safe_not_open():
    with _patched(shutil, "which", lambda name: None):
        try:
            lp.preflight([], "a perfectly clean sentence with nothing sensitive in it")
            raise AssertionError("preflight() sent a body when gitleaks was unavailable")
        except lp.EgressRefused as e:
            assert "gitleaks" in str(e).lower(), e
            assert "not installed" in str(e).lower(), e
    return "preflight() refuses to send (fails safe) when the gitleaks binary is unavailable"


# ---------------------------------------------------------------------------
# AC: --dry-run writes the request body under private/ and opens no socket.
# ---------------------------------------------------------------------------
@case
def case_dry_run_writes_the_body_under_private_and_never_calls_post_json():
    _require_gitleaks()

    def _boom(*a, **kw):  # pragma: no cover - must never be called
        raise AssertionError("preflight(dry_run=True) called _post_json")

    body_text = "this exact text should land on disk under private/llm_dry_run/"
    with _patched(lp, "_post_json", _boom):
        result = lp.preflight([], body_text, dry_run=True)
    assert result["dry_run"] is True, result
    written = pathlib.Path(result["path"])
    try:
        assert written.is_file(), written
        assert "private" in written.parts, written
        assert written.read_text() == body_text
    finally:
        written.unlink(missing_ok=True)
    return f"dry_run=True wrote the exact body under {written.parent.relative_to(lp.ROOT)}/ " \
           "and never touched _post_json"


@case
def case_dry_run_still_enforces_the_allowlist_and_the_gitleaks_scan():
    """dry_run must not become a bypass of either gate -- it only changes
    what happens on SUCCESS."""
    poisoned = lp.ROOT / "private" / "household.yaml"
    try:
        lp.preflight([("data_file", str(poisoned))], "x", dry_run=True)
        raise AssertionError("dry_run=True bypassed the allowlist check")
    except lp.EgressRefused as e:
        assert "private" in str(e), e
    _require_gitleaks()
    try:
        lp.preflight([], "account number: 0087-1234-5678-9", dry_run=True)  # gitleaks:allow
        raise AssertionError("dry_run=True bypassed the gitleaks scan")
    except lp.EgressRefused:
        pass
    return "dry_run=True still enforces both the allowlist and the gitleaks scan"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran, skipped = 0, 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP {fn.__name__}\n     {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
