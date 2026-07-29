#!/usr/bin/env python3
"""Per-household configuration loader — the output side of the intake interview.

Reads ROOT/private/household.yaml (created by walking the interview spec in
DATA-SOURCES-CHEATSHEET.md; committed schema template: household.example.yaml
at the repo root) and exposes get("dotted.path", required=True).

Per-HOUSE constants (install invoice, PTO date, charger kW, cleaning history,
gas $/therm, odometer-derived miles) live in household.yaml, never hardcoded
in analysis scripts. Cited PUBLIC constants (EIA gasoline price, FHWA fleet
mpg, COPs, rate tables, hardware specs) stay in the scripts with their source
comments — they are not household data.

Fails CLOSED: a missing file or a missing required key raises SystemExit
pointing at the intake interview. NO defaults for required fields.
private/ is gitignored — the filled file never leaves the machine. Secrets
(API keys, monitoring tokens) never belong here: .env only.
"""
import pathlib

_MSG = ("missing %s — run the intake interview (DATA-SOURCES-CHEATSHEET.md) "
        "to create private/household.yaml")


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:  # pragma: no cover - reachable only outside a checkout
                break
            p = p.parent
    raise SystemExit(  # pragma: no cover - the module's own path always resolves in-repo
        "repo root not found: no ancestor of the CWD or of this "
        "script contains both analysis/ and data/")


ROOT = _repo_root()
PATH = ROOT / "private" / "household.yaml"
_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            import yaml
        except ImportError:  # pragma: no cover - deps are pinned in requirements.txt
            raise SystemExit("pyyaml not installed — pip install -r requirements.txt")
        if not PATH.is_file():
            raise SystemExit(_MSG % "private/household.yaml")
        with open(PATH) as f:
            _cache = yaml.safe_load(f) or {}
    return _cache


def get(path, required=True):
    """Fetch a value by dotted path, e.g. get("solar.install_invoice_usd").
    required=True (the default) fails closed on a missing key — no silent
    defaults for required fields; required=False returns None."""
    node = _load()
    for key in path.split("."):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            if required:
                raise SystemExit(_MSG % f"key '{path}' in private/household.yaml")
            return None
    return node
