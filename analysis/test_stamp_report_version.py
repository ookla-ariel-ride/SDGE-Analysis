#!/usr/bin/env python3
"""Tests for stamp_report_version.py (issue #251): the Version row's build must
track the page's content, a restamp must be idempotent (on a later day too),
the date must be INSIDE the fingerprint so a hand-edited date or a malformed
one goes stale, the atomic write must keep the page's permission bits, a page
without the row must be refused, `--check` must never write, and the
COMMITTED index.html must pass `--check` -- that last case is what fails in
CI when someone edits the page without restamping.

Every case but the last works on temp copies of a small synthetic page; the
real index.html is read and never written. Needs no private/ and no git.

Run from the repo root:  ./.venv/bin/python analysis/test_stamp_report_version.py
"""
import contextlib
import datetime as dt
import io
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import suite_runner  # noqa: E402

import stamp_report_version as srv  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


VERSION_ROW = ('  <div class="meta-row"><span class="meta-k">Version</span>'
               '<span class="meta-v" id="report-version">{value}</span></div>\n')

PAGE = ('<h1 id="top">Synthetic report</h1>\n<div class="meta">\n'
        '  <div class="meta-row"><span class="meta-k">Window</span>'
        '<span class="meta-v"><b>Jan 1, 2025 → Dec 31, 2025</b> · rates effective '
        'June 1, 2026</span></div>\n'
        '{row}'
        '</div>\n<p>The battery saves $1,669/yr at constant current rates.</p>\n')

STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} · build [0-9a-f]{10}$")


def _page(value="2026-01-01 · build unstamped", row=True):
    return PAGE.format(row=VERSION_ROW.format(value=value) if row else "")


def _write(td, text, name="page.html"):
    p = pathlib.Path(td) / name
    p.write_text(text, encoding="utf-8")
    return p


def _run(argv):
    """main(argv) with stdout captured -> (exit code, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = srv.main(argv)
    return rc, buf.getvalue()


def _value(path):
    m = srv.find_row(pathlib.Path(path).read_bytes())
    return m.group(2).decode("utf-8")


@case
def case_stamp_fills_the_placeholder_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        rc, out = _run([str(p)])
        assert rc == 0, out
        assert "STAMPED" in out, out
        v1 = _value(p)
        assert STAMP_RE.match(v1), v1
        first = p.read_bytes()
        rc, out = _run([str(p)])
        assert rc == 0, out
        assert "STAMP CURRENT" in out, out
        assert p.read_bytes() == first, "a second stamp changed the bytes"
        rc, out = _run(["--check", str(p)])
        assert rc == 0 and out.startswith("STAMP OK " + v1), out
    return f"the placeholder stamps to {v1!r}; a second stamp is byte-identical; --check passes"


@case
def case_an_edit_outside_the_span_changes_the_fingerprint_and_fails_check():
    """Positive control for drift: the guard has to fire on a one-byte edit."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        _run([str(p)])
        stamped = _value(p)
        text = p.read_text(encoding="utf-8")
        assert text.count("$1,669/yr") == 1
        p.write_text(text.replace("$1,669/yr", "$1,939/yr"), encoding="utf-8")
        rc, out = _run(["--check", str(p)])
        assert rc == 1, out
        assert "STAMP STALE" in out and "expected build" in out, out
        expected = srv.fingerprint(p.read_bytes())
        assert expected != stamped.split("build ")[1], (expected, stamped)
        assert f"expected build {expected}" in out, out
        assert f"found {stamped.split('build ')[1]}" in out, out
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
        assert _value(p).endswith(expected)
        rc, _ = _run(["--check", str(p)])
        assert rc == 0
    return ("changing one figure outside the span changes the build, --check exits 1 naming "
            "expected vs found, and a restamp repairs it")


@case
def case_changing_only_the_date_goes_stale_and_a_restamp_repairs_it():
    """The date is part of the hashed content: the row's build was computed for
    the date beside it, so a hand-edited date no longer matches."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        _run([str(p)])
        date, build = _value(p).split(" · build ")
        other = "1999-12-31" if date != "1999-12-31" else "2000-01-01"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace(f"{date} · build {build}", f"{other} · build {build}"),
                     encoding="utf-8")
        assert _value(p) == f"{other} · build {build}"
        expected = srv.fingerprint(p.read_bytes())          # for the row's (edited) date
        assert expected != build, "a date-only edit left the fingerprint unchanged"
        rc, out = _run(["--check", str(p)])
        assert rc == 1, out
        assert "STAMP STALE" in out and f"expected build {expected}" in out, out
        assert f"found {build}" in out, out
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
        new_date, new_build = _value(p).split(" · build ")
        assert new_date == dt.date.today().isoformat(), new_date
        assert new_build == srv.fingerprint(p.read_bytes()), "restamped build is not the fingerprint"
        assert new_build != expected, "the restamp kept the build computed for the edited date"
        # Nothing but the date was edited, and the restamp dates the page today
        # again, so the build the row had before the edit comes back.
        assert (new_date, new_build) == (date, build), ((new_date, new_build), (date, build))
        rc, out = _run(["--check", str(p)])
        assert rc == 0, out
    return ("the date is in the hash: a date-only edit fails --check naming expected vs found, "
            "and a restamp writes today's date with the build for today's date")


@case
def case_a_malformed_date_is_not_a_stamp_and_fails_check():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        _run([str(p)])
        date, build = _value(p).split(" · build ")
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace(f"{date} · build {build}", f"9999-99-99 · build {build}"),
                     encoding="utf-8")
        assert _value(p) == f"9999-99-99 · build {build}"
        assert srv.parse_value(_value(p).encode("utf-8")) == (None, None)
        rc, out = _run(["--check", str(p)])
        assert rc == 1, out
        assert "STAMP STALE" in out and "found no stamp" in out, out
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
        new_date, _ = _value(p).split(" · build ")
        assert new_date == dt.date.today().isoformat(), new_date
        rc, _ = _run(["--check", str(p)])
        assert rc == 0
    return "9999-99-99 is rejected as malformed: --check exits 1 (no stamp), a restamp repairs it"


@case
def case_a_consistent_row_dated_in_the_past_is_left_alone():
    """Idempotent across days: the date is the day the content last changed,
    not the day the script last ran."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        past = dt.date(2020, 2, 29)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            value = srv.stamp(p, today=past)
        assert value.startswith("2020-02-29 · build "), value
        assert _value(p) == value
        before = p.read_bytes()
        rc, out = _run(["--check", str(p)])
        assert rc == 0 and out.startswith("STAMP OK 2020-02-29"), out
        rc, out = _run([str(p)])                              # today != 2020-02-29
        assert rc == 0 and "STAMP CURRENT" in out, out
        assert p.read_bytes() == before, "a restamp on a later day rewrote a consistent row"
        assert _value(p).startswith("2020-02-29"), _value(p)
    return "a self-consistent row dated 2020-02-29 survives a restamp today byte-identically"


@case
def case_the_atomic_write_keeps_the_page_mode():
    """mkstemp creates the temp file 0600; the replace must not hand that mode
    to a tracked 0644 page (the finding: the worktree's index.html went 0600)."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        os.chmod(p, 0o644)
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
        assert stat.S_IMODE(p.stat().st_mode) == 0o644, oct(p.stat().st_mode)
        os.chmod(p, 0o640)
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("$1,669/yr", "$1,939/yr"), encoding="utf-8")   # stale again
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
        assert stat.S_IMODE(p.stat().st_mode) == 0o640, oct(p.stat().st_mode)
        fresh = pathlib.Path(td) / "new.html"
        srv._atomic_write(fresh, b"x", None)
        um = os.umask(0)
        os.umask(um)
        assert stat.S_IMODE(fresh.stat().st_mode) == (0o644 & ~um), oct(fresh.stat().st_mode)
        assert sorted(os.listdir(td)) == ["new.html", "page.html"], os.listdir(td)
    return "a 0644 page stays 0644, a 0640 page stays 0640, a new file gets 0644 & ~umask"


@case
def case_a_page_without_the_version_row_is_refused_by_both_modes():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page(row=False))
        before = p.read_bytes()
        rc, out = _run(["--check", str(p)])
        assert rc == 1, out
        assert "no Version meta-row" in out, out
        rc, out = _run([str(p)])
        assert rc == 1, out
        assert "no Version meta-row" in out, out
        assert p.read_bytes() == before, "stamping a rowless page changed the file"
        assert "report-version" not in p.read_text(encoding="utf-8"), "a row was inserted"
    return "no Version row: --check and stamp both exit 1 naming the missing row; nothing is inserted"


@case
def case_a_duplicated_version_row_is_refused():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page() + VERSION_ROW.format(value="x"))
        rc, out = _run(["--check", str(p)])
        assert rc == 1 and "2 Version meta-rows" in out, out
        rc, out = _run([str(p)])
        assert rc == 1 and "2 Version meta-rows" in out, out
    return "two Version rows: both modes exit 1 rather than guessing which one to manage"


def _refused_by_both_modes(td, text, needle):
    """The page at `text` (whose row would otherwise stamp) is refused by
    --check and by stamp with exit 1, the message names `needle`, and nothing
    is written: bytes and directory listing are unchanged."""
    p = _write(td, text)
    before = p.read_bytes()
    rc, out = _run(["--check", str(p)])
    assert rc == 1, out
    assert needle in out, (needle, out)
    rc, out = _run([str(p)])
    assert rc == 1, out
    assert needle in out, (needle, out)
    assert p.read_bytes() == before, "a refused page was written"
    assert sorted(os.listdir(td)) == ["page.html"], os.listdir(td)
    return out


@case
def case_a_second_version_label_in_another_form_is_refused():
    """ROW_RE matches one exact byte form; a second label with a space before
    its value span and no id is not a ROW_RE match, so only the loose label
    count sees it (finding: duplicate rows in a different format evaded find_row)."""
    extra = ('  <div class="meta-row"><span class="meta-k">Version</span> '
             '<span class="meta-v">v2</span></div>\n')
    with tempfile.TemporaryDirectory() as td:
        text = _page() + extra
        page = text.encode("utf-8")
        assert len(srv.ROW_RE.findall(page)) == 1, "the extra row must NOT be a ROW_RE match"
        assert srv.count_version_labels(page) == 2
        assert srv.count_row_ids(page) == 1
        out = _refused_by_both_modes(td, text, "2 Version labels")
        assert "1 Version meta-rows" not in out and "elements with id" not in out, out
    # A label whose class list holds meta-k among others, spaced and reordered.
    extra2 = ('<span  data-x="1"   class="x meta-k y" >\n  Version\n</span>')
    with tempfile.TemporaryDirectory() as td:
        page = (_page() + extra2).encode("utf-8")
        assert srv.count_version_labels(page) == 2
        _refused_by_both_modes(td, _page() + extra2, "2 Version labels")
    # A "Version" span WITHOUT meta-k is not a label and is left alone.
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page() + '<span class="meta-v">Version</span>')
        assert srv.count_version_labels(p.read_bytes()) == 1
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
    return ("a second <span class=meta-k>Version</span> in a form ROW_RE cannot match is "
            "refused by both modes naming the label count; nothing is written")


@case
def case_a_second_report_version_id_in_another_form_is_refused():
    """Single quotes, attributes reordered, extra whitespace, a different tag:
    none of it is a ROW_RE match, all of it is a second id="report-version"."""
    variants = [
        "<span id='report-version' class=\"meta-v\">x</span>",
        "<span   class='meta-v'  id = 'report-version'>x</span>",
        '<div id="report-version">x</div>',
        "<p class=\"a\" id=report-version>x</p>",
    ]
    for extra in variants:
        with tempfile.TemporaryDirectory() as td:
            page = (_page() + extra).encode("utf-8")
            assert len(srv.ROW_RE.findall(page)) == 1, extra
            assert srv.count_version_labels(page) == 1, extra
            assert srv.count_row_ids(page) == 2, extra
            out = _refused_by_both_modes(td, _page() + extra,
                                         '2 elements with id="report-version"')
            assert "Version labels" not in out, out
    # A near-miss id is not counted.
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page() + '<span id="report-version-2">x</span>')
        assert srv.count_row_ids(p.read_bytes()) == 1
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
    return (f"{len(variants)} forms of a second id=report-version element (quotes, order, "
            "spacing, tag) are refused by both modes naming the id count; nothing is written")


@case
def case_the_canonical_single_row_page_passes_all_three_counts():
    page = _page().encode("utf-8")
    assert len(srv.ROW_RE.findall(page)) == 1
    assert srv.count_version_labels(page) == 1
    assert srv.count_row_ids(page) == 1
    assert srv.find_row(page).group(2) == "2026-01-01 · build unstamped".encode("utf-8")
    real = (ANALYSIS.parent / "index.html").read_bytes()
    assert (len(srv.ROW_RE.findall(real)), srv.count_version_labels(real),
            srv.count_row_ids(real)) == (1, 1, 1)
    return "the synthetic page and index.html each count exactly 1 row, 1 label, 1 id"


@case
def case_a_page_edited_between_snapshot_and_replace_is_not_overwritten():
    """Lost update: stamp() snapshots the page in inspect(), then _atomic_write
    replaces it. An edit that lands in between must not be discarded. The
    pre-replace re-read is the guard: here pathlib.Path.read_bytes is wrapped
    so that, on the page's SECOND read (the one inside _atomic_write), an edit
    is written to the page first and the real read then sees it."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        snapshot = p.read_bytes()
        edited = snapshot.replace(b"$1,669/yr", b"$1,939/yr")
        assert edited != snapshot
        original = pathlib.Path.read_bytes
        reads = []

        def racing_read_bytes(self):
            if pathlib.Path(self) == p:
                reads.append(self)
                if len(reads) == 2:
                    with open(p, "wb") as f:      # the intervening edit
                        f.write(edited)
            return original(self)

        pathlib.Path.read_bytes = racing_read_bytes
        try:
            rc, out = _run([str(p)])
        finally:
            pathlib.Path.read_bytes = original
        assert len(reads) == 2, f"expected the snapshot read and one pre-replace read, got {len(reads)}"
        assert rc == 1, out
        assert out.strip() == f"STAMP ABORTED {p}: page changed during stamping; rerun", out
        assert "STAMPED" not in out, out
        assert p.read_bytes() == edited, "the stale snapshot overwrote the intervening edit"
        assert sorted(os.listdir(td)) == ["page.html"], f"temp file left behind: {os.listdir(td)}"
        # Direct form, no monkeypatch: a snapshot that does not match the file.
        try:
            srv._atomic_write(p, b"new", snapshot)
        except srv.StampError as e:
            assert isinstance(e, srv.LostUpdate), type(e)
            assert "page changed during stamping" in str(e), e
        else:
            raise AssertionError("_atomic_write replaced a page that differed from its snapshot")
        assert p.read_bytes() == edited
        assert sorted(os.listdir(td)) == ["page.html"], os.listdir(td)
        # With the race gone, the same page stamps and the edit survives in it.
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMPED" in out, out
        assert b"$1,939/yr" in p.read_bytes()
        rc, _ = _run(["--check", str(p)])
        assert rc == 0
    return ("an edit between the snapshot and the replace aborts the stamp (exit 1, STAMP "
            "ABORTED), keeps the edited page, leaves no temp file; a rerun stamps it")


@case
def case_check_never_writes():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())          # placeholder value: a failing check
        before = p.read_bytes()
        os.utime(p, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        mtime = p.stat().st_mtime_ns
        rc, out = _run(["--check", str(p)])
        assert rc == 1 and "found no stamp" in out, out
        assert p.read_bytes() == before, "--check rewrote a stale page"
        assert p.stat().st_mtime_ns == mtime, "--check touched the file"
        assert sorted(os.listdir(td)) == ["page.html"], os.listdir(td)
    return "a failing --check leaves bytes, mtime and the directory untouched"


@case
def case_the_cli_exit_codes_match_main():
    """One subprocess pass: the wired CI step runs the file, not main()."""
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        script = str(ANALYSIS / "stamp_report_version.py")
        r = subprocess.run([sys.executable, script, "--check", str(p)],
                           capture_output=True, text=True, cwd=td)
        assert r.returncode == 1 and "STAMP STALE" in r.stdout, (r.returncode, r.stdout)
        r = subprocess.run([sys.executable, script, str(p)],
                           capture_output=True, text=True, cwd=td)
        assert r.returncode == 0 and "STAMPED" in r.stdout, (r.returncode, r.stdout)
        r = subprocess.run([sys.executable, script, "--check", str(p)],
                           capture_output=True, text=True, cwd=td)
        assert r.returncode == 0 and r.stdout.startswith("STAMP OK"), (r.returncode, r.stdout)
    return "as a subprocess from a foreign CWD: --check 1 on stale, stamp 0, --check 0 after"


@case
def case_the_committed_index_html_is_stamped_and_current():
    """READ-ONLY on the real page. This is the CI gate: an edit to index.html
    that was not followed by a restamp fails here, naming both builds."""
    page = ANALYSIS.parent / "index.html"
    assert page.is_file(), page
    before = page.read_bytes()
    rc, out = _run(["--check", str(page)])
    assert page.read_bytes() == before, "the read-only check changed index.html"
    assert rc == 0, (
        f"index.html's Version row is behind its content:\n{out}"
        "run ./.venv/bin/python analysis/stamp_report_version.py and commit the restamped page")
    value = _value(page)
    assert STAMP_RE.match(value), value
    assert "report generated" not in before.decode("utf-8"), (
        "index.html still carries the retired 'report generated <date>' clause")
    return f"index.html: {out.strip()}"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
    print(f"\n{ran}/{len(CASES)} passed")


if __name__ == "__main__":
    main()
