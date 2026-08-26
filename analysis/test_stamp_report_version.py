#!/usr/bin/env python3
"""Tests for stamp_report_version.py (issue #251): the Version row's build must
track the page's content, a restamp must be idempotent, the date must be
outside the fingerprint, a page without the row must be refused, `--check`
must never write, and the COMMITTED index.html must pass `--check` -- that
last case is what fails in CI when someone edits the page without restamping.

Every case but the last works on temp copies of a small synthetic page; the
real index.html is read and never written. Needs no private/ and no git.

Run from the repo root:  ./.venv/bin/python analysis/test_stamp_report_version.py
"""
import contextlib
import io
import os
import pathlib
import re
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
def case_changing_only_the_date_keeps_the_fingerprint_and_check_passes():
    with tempfile.TemporaryDirectory() as td:
        p = _write(td, _page())
        _run([str(p)])
        date, build = _value(p).split(" · build ")
        other = "1999-12-31" if date != "1999-12-31" else "2000-01-01"
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace(f"{date} · build {build}", f"{other} · build {build}"),
                     encoding="utf-8")
        assert _value(p) == f"{other} · build {build}"
        assert srv.fingerprint(p.read_bytes()) == build
        rc, out = _run(["--check", str(p)])
        assert rc == 0, out
        before = p.read_bytes()
        rc, out = _run([str(p)])
        assert rc == 0 and "STAMP CURRENT" in out, out
        assert p.read_bytes() == before, "a stamp on a date-only change rewrote the file"
    return "the date is outside the hash: a date-only edit keeps the build and passes --check"


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
