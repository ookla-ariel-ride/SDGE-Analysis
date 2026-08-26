#!/usr/bin/env python3
"""Stamp the report's header ledger with a date and a content fingerprint (issue #251).

THE PROBLEM. index.html is hand-authored: generate_report.py writes
index.generated.html and never touches it. Its only date used to be the
"report generated <date>" clause in the Window meta-row, which came from a
token nobody re-resolved when the page was hand-edited, so the date sat
unchanged across dozens of commits that changed the figures beside it. A
reader had no way to tell one revision of the page from another.

THE ROW. The header ledger carries a Version meta-row, placed directly after
the Window row:

    <div class="meta-row"><span class="meta-k">Version</span>
      <span class="meta-v" id="report-version">YYYY-MM-DD · build 0123456789</span></div>

This script owns the text inside the id="report-version" span and nothing
else on the page. The date is the day the stamp was applied; the build is the
first 10 hex digits of the page's content fingerprint.

WHY A FINGERPRINT AND NOT A GIT SHA. The commit that writes the stamp changes
index.html, so a stamp can never equal the SHA of its own commit -- the SHA
does not exist until after the file is final. And CI checks out at depth 1,
so a guard comparing the stamp to history would have no history to compare
against. A fingerprint of the page's own bytes has neither problem: it is
computed from the file, checked from the file, and needs no repository.

WHAT IS HASHED. sha256 over the page's bytes with the WHOLE inner text of the
Version span (date and build alike) replaced by a fixed placeholder. The span
must be excluded, or the stamp would change the very bytes it is a digest
of. The DATE is excluded on purpose too: a restamp on a page whose content
did not change keeps the same build and leaves the file byte-identical,
which is what makes `stamp` idempotent and what lets `--check` say "current"
without knowing which day the stamp was applied. Every other byte is in the
hash, so an edit anywhere else on the page -- a figure, a pill, a comment --
changes the build.

THE GATE. `--check` recomputes the fingerprint and compares it to the row's
build. It never writes. test_stamp_report_version.py runs it against the
committed index.html, so a page edited without a restamp fails CI naming the
expected and found builds; the fix is to run this script and commit the
restamped page.

USAGE (from any CWD; the default path is <repo root>/index.html, the root
found by walking up from the CWD, then from this file, to the nearest
ancestor holding both analysis/ and data/ -- the same rule parse_bills.py
uses):

    stamp_report_version.py [PATH]           # restamp; byte-identical if current
    stamp_report_version.py --check [PATH]   # exit 0 current, exit 1 stale/missing

A page with no Version row is refused by both modes with exit 1: the row is
part of the template and is never inserted silently. Writes are atomic (temp
file beside the page + os.replace), like the repo's other writers. Stdlib
only.
"""
import argparse
import datetime as dt
import hashlib
import os
import pathlib
import re
import sys
import tempfile

# The exact markup of the managed span. Only its inner text (group 2) is ever
# rewritten; the attributes are part of the hashed content like any other byte.
ROW_RE = re.compile(
    rb'(<span class="meta-k">Version</span>'
    rb'<span class="meta-v" id="report-version">)([^<]*)(</span>)')

# What the span's text is replaced with for hashing. Never appears on the page.
PLACEHOLDER = b"@@REPORT_VERSION@@"

FINGERPRINT_HEX_LEN = 10

VALUE_RE = re.compile(
    rb"^(\d{4}-\d{2}-\d{2}) \xc2\xb7 build ([0-9a-f]{%d})$" % FINGERPRINT_HEX_LEN)

SEPARATOR = " · build "


class StampError(Exception):
    """The page has no usable Version row (missing, duplicated, or malformed)."""


def _repo_root():
    """Nearest ancestor holding both analysis/ and data/ (matches parse_bills.py)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains both analysis/ and data/")


def default_page():
    return _repo_root() / "index.html"


def find_row(page_bytes):
    """The single Version-row match, or raise StampError naming what is wrong."""
    matches = list(ROW_RE.finditer(page_bytes))
    if not matches:
        raise StampError(
            'no Version meta-row: expected exactly one <span class="meta-k">Version</span>'
            '<span class="meta-v" id="report-version">...</span> in the header ledger')
    if len(matches) > 1:
        raise StampError(f"{len(matches)} Version meta-rows found; expected exactly one")
    return matches[0]


def fingerprint(page_bytes):
    """sha256[:10] of the page with the Version span's inner text normalized."""
    m = find_row(page_bytes)
    normalized = page_bytes[:m.start(2)] + PLACEHOLDER + page_bytes[m.end(2):]
    return hashlib.sha256(normalized).hexdigest()[:FINGERPRINT_HEX_LEN]


def parse_value(value_bytes):
    """(date, build) from the span's text, or (None, None) if it is not a stamp."""
    m = VALUE_RE.match(value_bytes)
    if not m:
        return None, None
    return m.group(1).decode("ascii"), m.group(2).decode("ascii")


def inspect(path):
    """Read the page once and report (page_bytes, row match, expected build, found build).

    `found` is None when the span holds something that is not a stamp (the
    template's "build unstamped" placeholder, an empty span, or hand-typed text)."""
    page = pathlib.Path(path).read_bytes()
    m = find_row(page)
    expected = fingerprint(page)
    _date, found = parse_value(m.group(2))
    return page, m, expected, found


def _atomic_write(path, data):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def stamp(path, today=None):
    """Restamp the page at `path`. Returns the value now in the row.

    Leaves the file untouched (byte-identical, no write at all) when the row's
    build already equals the page's fingerprint."""
    page, m, expected, found = inspect(path)
    if found == expected:
        value = m.group(2).decode("utf-8")
        print(f"STAMP CURRENT {value} ({path})")
        return value
    today = today or dt.date.today()
    value = f"{today.isoformat()}{SEPARATOR}{expected}"
    old = m.group(2).decode("utf-8", errors="replace")
    new_page = page[:m.start(2)] + value.encode("utf-8") + page[m.end(2):]
    _atomic_write(path, new_page)
    print(f"STAMPED {path}: {old!r} -> {value!r}")
    return value


def check(path):
    """Exit status for --check: 0 when the row's build equals the fingerprint."""
    page, m, expected, found = inspect(path)
    if found == expected:
        print(f"STAMP OK {m.group(2).decode('utf-8')}")
        return 0
    shown = m.group(2).decode("utf-8", errors="replace")
    print(f"STAMP STALE {path}: expected build {expected}, found "
          f"{found if found is not None else 'no stamp'} (row reads {shown!r}); "
          f"run analysis/stamp_report_version.py and commit the restamped page")
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", nargs="?", help="page to stamp (default: <repo root>/index.html)")
    p.add_argument("--check", action="store_true",
                   help="compare the row's build to the page's fingerprint; never writes")
    args = p.parse_args(argv)
    path = pathlib.Path(args.path) if args.path else default_page()
    if not path.is_file():
        print(f"STAMP ERROR: no such file {path}")
        return 1
    try:
        if args.check:
            return check(path)
        stamp(path)
        return 0
    except StampError as e:
        print(f"STAMP ERROR {path}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
