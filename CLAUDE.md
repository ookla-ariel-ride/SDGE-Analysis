# CLAUDE.md — operating rules for the home-energy analysis

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository. It focuses the work and encodes the mistakes we already made so they aren't
repeated. Read it before touching this project. It applies to Claude Cowork, Claude Code,
and any agent.

## Commands

```bash
# One-time setup on any fresh clone — enable the secret/PII pre-commit gate:
git config core.hooksPath .githooks       # requires: brew install gitleaks

# Python environment (scripts need pandas/numpy/pyyaml; parse_bills.py also pdfplumber;
# perfect_foresight_dispatch.py also scipy, for its LP solver):
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# Data intake (Phase A) — create private/household.yaml BEFORE running analysis:
# walk the interview spec in DATA-SOURCES-CHEATSHEET.md (per-field yaml blocks:
# id/question/type/required_if/where/privacy) and fill the schema template
# household.example.yaml into gitignored private/household.yaml; log progress in
# private/intake-status.md. Analysis scripts fail closed (SystemExit via
# analysis/household.py) without it. Secrets (PVOutput API key, monitoring
# tokens) go ONLY to a gitignored .env — never household.yaml.
cp household.example.yaml private/household.yaml   # then replace every placeholder

# Running analysis scripts: each expects usage.csv (the raw Green Button 15-min
# export) in its working directory. The raw file lives in private/1-raw-data/ —
# NEVER copy it outside private/. Standard pattern (the private/verify/ sandbox):
mkdir -p private/verify && cd private/verify
cp ../../analysis/*.py .
cp ../1-raw-data/Electric_15_Minute_*.csv usage.csv
../../.venv/bin/python behavior_rebuild.py            # regenerates behavior_rebuild.json
../../.venv/bin/python battery_dispatch_policies.py   # regenerates battery_dispatch_policies.json
../../.venv/bin/python battery_plan_matrix.py         # regenerates data/battery_plan_matrix.json in place

# The §9 regeneration gate — run after ANY script or artifact change (still inside
# private/verify; extended_findings.py, package_results.py, carbon_fullyear.py and
# battery_plan_matrix.py find the repo root themselves by walking up from the CWD,
# so no path edits are needed):
cmp behavior_rebuild.json ../../data/behavior_rebuild.json
cmp battery_dispatch_policies.json ../../data/battery_dispatch_policies.json
git diff --exit-code ../../data/battery_plan_matrix.json
../../.venv/bin/python package_results.py && git diff --exit-code ../../data/package_results.json
../../.venv/bin/python extended_findings.py && git diff --exit-code ../../data/extended_results.json
# (must be byte-identical; a diff means a stale artifact or an unreproducible script.
#  extended_findings.py fails closed: it computes all battery figures from the dispatch
#  engine, asserts them against battery_dispatch_policies.json, validates every required
#  section, and writes the artifact atomically — a partial/failed run changes nothing.)

# Dry run — ask "what would this generator change?" without changing it. It copies the
# tracked tree into a temp sandbox outside the repo and COPIES private/ in (never a
# symlink — a link would be a writable path from the sandbox back into the raw archive,
# and a stray write there is unrecoverable), runs the generator's REAL write path
# (no --dry-run flag inside any script: a flag that branches around the write exercises a
# different path and can lie), then diffs the sandbox's data/ against the repo's — JSON by
# changed top-level key, CSV by changed rows. It writes nothing into the repo, and a crash,
# a no-op, a rootless sandbox or a sandbox that cannot be deleted afterwards is reported
# as a FAILURE (exit 2), never as "no changes". Run from anywhere:
./.venv/bin/python analysis/dry_run.py analysis/parse_bills.py            # report only
./.venv/bin/python analysis/dry_run.py analysis/parse_bills.py --check    # exit 1 if stale
./.venv/bin/python analysis/test_dry_run.py                               # its guard suite
# --check is the NON-MUTATING equivalent of the §9 gate above: use it to learn whether an
# artifact is stale without regenerating it. The gate stays the authority when you intend
# to commit the regeneration — it leaves the rebuilt artifact in the tree, which --check
# deliberately does not. Two cases where they can disagree, both real: (1) --check diffs
# against data/ as it is ON DISK, `git diff` against the index, so uncommitted edits under
# data/ split them (pass --baseline head to match the gate exactly); (2) --check gives each
# generator its own sandbox seeded from the COMMITTED artifacts, so a chain where one
# generator consumes another's freshly rewritten output is not reproduced — for a chain,
# run the gate.

# Bill artifacts (rerun after adding statements to private/1-raw-data/*-bills/):
# parse_bills.py finds the repo root itself, so run it from anywhere. It regenerates the
# two legacy summaries as its own reproduction gate — they must not change:
../../.venv/bin/python parse_bills.py && git diff --exit-code ../../data/electric_bill_summary.csv \
    ../../data/gas_bill_summary.csv ../../data/bill_periods_electric.csv \
    ../../data/bill_periods_gas.csv ../../data/bill_tou_detail.csv \
    ../../data/bill_gas_detail.csv ../../data/bill_corpus_boundary.json
# The seventh artifact, data/bill_corpus_boundary.json, states which statements the six
# above actually contain. The published corpus is DERIVED, not declared: a statement PDF
# with no row in the billing-history export is outside it and appears in no artifact,
# with the exclusion printed by the run and recorded (reason, remedy, day-coverage
# shortfall) in that file. Stage an export that covers the statement and the next run
# publishes it on its own — there is no date to delete. WHICH export depends on which end
# of the export's range the statement falls outside: a re-pull recovers a statement newer
# than the export, but not one older than it, because the export is a rolling window and a
# fresh pull starts no earlier. The artifact states the direction per statement in
# excluded_statements[].exclusion_ends_when. See parse_bills.py's "THE CORPUS BOUNDARY"
# and TECHNICAL.md.
# Its fail-closed behaviour has negative tests (missing statement, corpus gaps, TOU
# layout drift, mid-write failure, and every shape of export/corpus mismatch). Run them
# after touching the parser:
./.venv/bin/python analysis/test_parse_bills.py     # from the repo root

# Carbon artifacts (rerun when needed): carbon_fullyear.py uses the raw CAISO day-cache
# private/1-raw-data/caiso_raw/ when present, otherwise rebuilds exactly from the
# committed data/caiso_hourly_intensity.csv; it fails closed if coverage would shrink
# and writes both artifacts atomically (TECHNICAL.md §3.15):
../../.venv/bin/python carbon_fullyear.py && git diff --exit-code ../../data/carbon_fullyear_results.json ../../data/caiso_hourly_intensity.csv

# Coverage gate (local, needs the private archive like the §9 gate): every test
# suite plus every generator on the real inputs must keep the analysis package
# at >= 90% statement coverage (currently ~93%):
./analysis/check_coverage.sh                          # fails under 90%

# Full-history secret scan (CI runs the generic rules automatically on every push):
gitleaks git --config .gitleaks.toml .                # committed generic rules
gitleaks git --config private/pii-rules.toml .        # + personal PII rules (local-only)
```

## 0. Prime directive: EVIDENCE-BASED ONLY. No guesses, no hallucination.
Every number, claim, and conclusion in the report MUST be traceable to (a) a datum you
loaded, (b) a rate/figure read off an official source or bill, or (c) a calculation you ran
and can show. If you cannot compute or cite it, do not state it — say "not determined" and
list what data would settle it.
- The report records events only AFTER they have happened. Never include future or scheduled
  events (an upcoming panel cleaning, a bill or evaluation date that hasn't arrived yet) — a
  data point enters the report when it exists, not when it is planned or anticipated.
- Never infer a value you can read directly. Climate zone, rate plan, CCA product, credits,
  baseline allowance, meter/account facts → read them off the **detailed bill**, don't guess
  from ZIP or assumptions. (We wrongly assumed "Inland" — the bill said Coastal. We assumed a
  CEA relief credit applied — the bill showed it doesn't.)
- Distinguish MODELED from ACTUAL everywhere. Label modeled figures as modeled; anchor absolute
  dollars to actual bills. (Our interval model overstated the annual bill ~40%.)
- When two methods disagree, reconcile them explicitly and quantify the gap before drawing a
  conclusion. Do not paper over a discrepancy.
- Prefer differences to levels: savings/paybacks computed as deltas between two model runs are
  far more robust than absolute levels. Say which is which.
- No figure appears in the report unless a script produced it. Keep the script in `analysis/`.
- Every conclusion must name its evidence in-line or in the methodology. If challenged with
  "how do you know that?", the answer must be a file, a bill line, or a computation — never a
  prior or a plausible-sounding estimate.

## 1. Validate against reality before quoting absolute dollars.
Build the billing model to reproduce the customer's ACTUAL bills (monthly, per-TOU-period NEM
netting), and validate month-by-month against the detailed statements. Only after it
reproduces the bills (state the residual error and where it concentrates) may you quote
absolute costs. If it doesn't fully reconcile, find the REAL driver before writing a story:
ours was rate vintage (model at current rates vs bills on older, cheaper tariffs), NOT the
NEM-export-crediting explanation we first wrote — the netting methods agreed to ~0.3%.
Anchor absolute dollars to the bills; use the model only for deltas.
- Check bill coverage in DAYS, not number of files. Our "12 months" of bills covered 338 days
  with a hidden 27-day October gap, silently understating the annual baseline ~9%.
- One PDF can contain multiple billing periods (short stub cycles) — parse periods, not files,
  and dedupe before summing.

## 1b. Move energy physically, not as lump sums.
When modeling load shifting or a battery, place shifted kWh into actual destination intervals
(respecting charger/inverter power caps, verifying energy conservation) and re-bill the
modified year. A year-end "shifted × average rate" credit misprices the shift and breaks the
monthly min/max bills. Attribute shiftable load honestly: identify EV sessions explicitly —
in our data only ~22% of on-peak import was EV; the rest was house load that may not move.
Model behavior and hardware together so savings aren't double-counted (state the overlap).

## 2. Payback honesty.
PACKAGE payback (hardware ÷ total savings incl. free behavior) ≠ ASSET payback (hardware ÷ the
hardware's OWN marginal savings). Always report the asset-alone payback for any purchase; never
credit free behavior savings to hardware. (We mislabeled a battery as "5.4-yr" when it was
~10 yr alone.)

## 3. Keep figures consistent across the whole document.
When a rigorous simulation supersedes an early estimate, replace EVERY instance — headline,
cards, findings, packages, methodology. (We left an early "$1,939/yr battery" in one finding
after the sim said $1,669.) After any figure change, grep the report for the old number.

## 4. Privacy is non-negotiable and verified, not assumed.
- All raw PII (Green Button CSVs, bill PDFs, solar-monitoring exports) lives in `private/`,
  gitignored, never pushed. Public repo gets only de-identified aggregates.
- **Mechanical enforcement exists — enable it before your first commit:**
  `git config core.hooksPath .githooks` turns on a gitleaks pre-commit gate that blocks
  commits containing secrets or account data (it refuses to commit if gitleaks isn't
  installed). Rules chain: gitleaks defaults → committed `.gitleaks.toml` (generic SDGE
  account-format patterns) → `private/pii-rules.toml` (person-specific patterns: name,
  address, actual account/meter numbers — local-only, gitignored, NEVER commit it; it
  contains the very values it guards). CI re-scans full history on every push with the
  committed rules only — it cannot check person-specific patterns, so the local hook is
  the real gate.
- The hook screens staged changes; for anything it can't see (filenames, images, PDFs),
  still eyeball push-bound files for: name, street address, account/meter/RIN numbers,
  email, phone, exact coordinates, utility/solar/PVOutput account IDs, API keys.
  (We twice nearly leaked a name/meter number.)
- **Intake privacy tiers are binding:** every DATA-SOURCES-CHEATSHEET.md field is tagged
  public-ok / private-only / secret — no private-only or secret answer may ever be written
  into any committed artifact (report, data/, scripts, README, commit messages); private-only
  values live in gitignored private/household.yaml, secrets ONLY in gitignored .env (never
  household.yaml).
- Never type the user's password or enter credentials. The user logs in; you drive.
- git history is permanent — if PII is ever committed, recommend delete+recreate over scrubbing.
- **Never delete `.env`, in this checkout or any worktree, for any reason — not as
  "cleanup," not because a copy already exists elsewhere.**
  Copy-in is the only sanctioned operation; there is no sanctioned `rm`. On
  2026-08-02, two independent subagents each invented their own unrequested `rm -f`
  "cleanup" step while following the copy instructions above and deleted the live
  credential files from the main checkout — one was recoverable from a worktree's
  stale copy, one (an untracked `.env.backup`) was not. If a stray copy genuinely
  needs removing, stop and ask; do not act on that judgment call unsupervised.

## 5. Commits actually land — verify, don't assume.
The GitHub web UI can silently fail if you navigate away before the commit POST completes.
After every commit, re-read the repo/commit list and confirm it's there before moving on.
(We had four commits silently fail this way.)

## 6. Browser hygiene.
- Single clicks; double-clicks caused a duplicate-request error on the bill PDF endpoint.
- Multiple Chrome windows: confirm which one holds the logged-in session before acting.
- Bulk file downloads that trigger native OS "Save As" dialogs can't be automated — have the
  user download, or read the content in-browser instead.

## 7. Solar is not Enphase-specific.
This project pulled Enphase Enlighten data, but the method is monitoring-agnostic: SolarEdge,
Tesla, SMA, Fronius, PVOutput, etc. all expose production feeds. Describe the DATA needed
(gross hourly/daily production, system size, metering config), not one vendor's menu.

## 8. Scope discipline, and use subagents wherever possible.
Ask clarifying questions before large work. Keep a task list. Verify programmatically. Do not
expand scope silently.

**Default to delegating.** Any unit of work that can be stated as a self-contained brief —
an analysis, a script, a fix, an audit, a doc pass — should go to a subagent rather than be
done inline. Reserve inline work for deciding *what* to delegate, verifying what comes back,
and talking to the user. This is not about speed: a subagent that reads the files itself and
reports a conclusion keeps the main thread's attention on judgment, which is where the
mistakes in this project have actually happened.

Rules that make delegation work here, each learned by it failing:

- **Disjoint file ownership.** Parallel agents must own non-overlapping files, named
  explicitly in the brief ("you own EXACTLY these two files"). Two agents editing one file
  produce a mid-edit tree where a third agent's test run fails for reasons unrelated to its
  own work.
- **Give every agent a scope box** — the exact files and acceptance criteria in bounds — and
  tell it to stop and report rather than touch anything outside it. Agents are otherwise
  helpful in exactly the way that causes drift.
- **Never let a subagent commit.** They leave changes in the working tree; the parent reads
  the diff, verifies, and commits. A commit is a claim about verified state, and the agent
  cannot verify its own claim.
- **Verify independently, do not accept the report.** Re-run the assertions yourself with
  your own commands. Subagent reports here have been accurate about what was done and
  occasionally wrong about what it means — a summary saying "26/26 pass, artifact
  byte-identical" is a starting point for checking, not a substitute.
- **Make byte-identity a stated constraint.** Any agent touching a generator gets: "the
  committed artifact must regenerate byte-identically; prove it with cmp." That single line
  catches most accidental behavior changes.
- **Have agents sweep, not patch.** When a defect is found at one site, instruct the agent to
  find every other instance of the same shape rather than fixing the named one. Three review
  rounds were spent on the same defect appearing at three different exits.
- **Adversarial passes stay adversarial.** Use subagents for an independent verification pass
  over the math before presenting, and for the review loops. The parent classifies each
  finding as in or out of scope and reports that classification to the user *before* any fix
  is applied — never fix silently.
- **Isolate issue work in a git worktree** (never `worktrees/` inside the repo), with its own
  venv and private data staged per the clean-room procedure, so a branch cannot disturb the
  main checkout.

## 9. Pre-publication gates — run ALL of these before any report ships.
Round-three review found every one of these violated. Check them mechanically, not by memory:
- **Artifact–prose diff:** every figure in the report must match the committed data artifacts
  (data/*.json, *.csv). When prose is re-based, REGENERATE the artifacts — a stale
  package_results.json kept publishing a retired 5.4-yr payback long after the prose fixed it.
- **Code-implements-its-docs:** verify each model's docstring against its code. Our netting
  model described NBC on gross imports but netted it (−$208/yr). Non-bypassable charges are
  charged on GROSS imported kWh under NEM — verify against a bill line, then implement it.
- **One rate vintage per projection:** never subtract current-rate model deltas from
  prior-year actual bills to project a future bill — that mixes tariff vintages. Project on
  one basis (the current-rate model), label it "at constant current rates," and note that the
  historical actual was billed on older tariffs.
- **A script per headline number:** every headline figure needs a committed script that
  reproduces it (our lifetime-payback date initially had none → analysis/lifetime_payback.py).
- **Confidence labels:** precision must match evidence density. Tag thin-evidence sections
  visibly: measured (meters/bills/multi-source), modeled (validated model, current rates),
  estimated (scaled history, single events, sampled days). One cleaning event, four CAISO
  days, and a rate-index extrapolation must not read with the same authority as a year of
  15-minute data.
- **One canonical rates module:** all analysis scripts import rate constants and the
  billing engine from a single module (analysis/rates.py, bill-derived). Two scripts with
  independently declared rates WILL drift (ours disagreed by $209/yr on the same house —
  rate-table vs bill-derived values). Legacy cross-plan ranking may keep table rates, labeled.
- **Every committed artifact regenerable by its committed script:** run the script, diff its
  output against the committed JSON/CSV. A truncated script that can't reproduce its own
  artifact (ours imported json and never wrote it) fails this gate.
- **One pipeline per package figure:** composite results (behavior + hardware) come from a
  single integrated simulation re-billed end-to-end, never by adding numbers from different
  models and subtracting an overlap estimated in a third.
- **Process narrative stays out of the report:** the report presents data → analysis →
  conclusions only. Corrections, superseded drafts, and "we fixed X" belong in commits/PRs,
  never in the published document.
  **The report is a snapshot of the current dataset, not a changelog of the analysis.**
  It states what the data shows now. It never compares a figure to an earlier version of
  itself, explains why a number moved between revisions, or refers to superseded work.
  Ban these constructions in report prose: "X% below the earlier N-day estimate", "carried
  from the retired ... workpaper", "supersedes the previous ...", "originally we ...",
  "this replaces ...", "the legacy ... is kept for reference". If a figure changed because
  the method improved, publish the new figure and the reason it is right — a reader who
  never saw the old one must not be able to tell there was an old one. Method lineage,
  retired scripts, and correction history live in TECHNICAL.md, commit messages, and
  CLAUDE.md. Two things that are NOT process narrative and must stay: evidence labels
  about the CURRENT data ("estimated · 28 days sampled", "not artifact-backed"), and
  reconciliations between two live methods run on the same data ("the netting methods
  agree to 0.3%"), which §0 requires.

## 10. Report design & navigation requirements (index.html — keep on every regeneration).
Start from `report-template.html` — it implements everything below plus the chart scaffolds,
confidence pills, and provenance slot; replace {{TOKENS}} with script-produced values only.
Single self-contained file, vanilla CSS/JS; CDN allowed only for Chart.js (pinned with SRI
integrity hash) and Google Fonts (Space Grotesk display / Source Serif 4 body / IBM Plex
Mono data — with system fallbacks).

**Design system ("Solar ledger") — do not revert to the old dark-slate/emerald look:**
- Light default on warm-white paper (#FBFAF7/#1A2332 ink); dark variant via
  `[data-theme="dark"]` tokens, user-toggleable (◐ button, localStorage-persisted, honors
  prefers-color-scheme on first visit; charts read colors from CSS vars so toggle reloads).
- SEMANTIC TOU palette, used consistently everywhere (CSS tokens → chart palette via JS):
  on-peak #BF3B2B · off-peak #C98A3D · super-off-peak #2E7D6B · solar #E9B62F. A period's
  color is the same in the day-band, every chart series, tables, and the price map.
- Signature element: the DAY-BAND — pure-CSS 24-h TOU strip (segments at the tariff's TOU
  boundaries — 0-6-10-14-16-21 for this household's EV-TOU-5) with tick marks and prices,
  full-width under the header; keep it on every regeneration.
- Every time-axis chart shades the on-peak window (4-9pm on this tariff) via the
  `onpeakBand` Chart.js plugin.
- Evidence pills are mono uppercase stamps, color-coded measured=sop-green /
  modeled=off-peak-amber / estimated=on-peak-red.
- All numerals in IBM Plex Mono with tabular-nums (cards, tables, pills).
- Header meta: the household / window / sources facts under the h1 render as the `.meta`
  ledger rows (mono uppercase `.meta-k` labels + `.meta-v` values, hairline rules,
  single-column on mobile) — never as a run-on `.sub` paragraph.

**Basic/advanced tier structure (keep on every regeneration):**
- BASIC tier (always visible) = header + .meta ledger + day-band + Bottom line (§0) +
  §1–§7 (including the four §5 charts: #hourly #battery #monthly #periods) + the
  "What to do Monday" appendix (id="s15"), which sits directly AFTER §7's section.
  The basic tier carries the decision narrative and the majority of the charts.
- ADVANCED tier = §8–§14 only — the audit trail (array upgrades, deep dives sec9, bills,
  lifetime payback, cleaning sec12, carbon sec13, methodology) — wrapped in ONE native
  `<details id="advanced" class="advanced">`, CLOSED by default (no persistence, no
  position:sticky). Its <summary> is a loud full-width section divider: mono uppercase
  eyebrow "ADVANCED ANALYSIS" + display-font headline "The full evidence" + inventory
  line "7 sections · the full audit trail · every figure traceable to a script and a
  data artifact" + a ▸ affordance that rotates 90° when open (details[open]); hairline
  top/bottom rules like .meta rows; :focus-visible outline; cursor:pointer.
- REQUIRED mitigations (all four, wired in JS; verify on every regeneration):
  1. Every deep link into the tier auto-opens it — on load-with-hash AND hashchange —
     before scrolling (openHashTarget opens #advanced first, then any closed inner
     details, then re-scrolls).
  2. Nav pills whose targets sit inside the tier (the Audit-group pills s9–s14 and the
     Array-upgrades pill s8 in Evidence) dim to ~55% opacity while it is closed
     (nav.tier-closed class, driven by the details' native toggle event) but still
     navigate — hash navigation goes through openHashTarget, which opens the tier.
     Verdict-group pills never dim.
  3. EVERY canvas inside the tier lazy-inits on first reveal — a Chart.js canvas built
     while an ancestor <details> is closed measures 0x0 and never recovers. This is a
     generic registry (`lazyChart(id, build)` + `runLazyCharts()`), never a special case
     for one canvas: it walks each canvas's own <details> ancestors, is re-run on every
     details toggle, on deep-link opens (openHashTarget) and before printing
     (openForPrint), and guards double init. Adding a chart to a collapsed section must
     need no new wiring beyond one lazyChart() call. The four §5 basic-tier charts sit
     outside any <details> and keep initializing on load.
  4. Printing force-opens the tier and all inner details via a beforeprint listener
     (plus a matchMedia('print') change fallback) so print always emits the full
     report — CSS alone cannot open a details element.

**Formatting & navigation mechanics (implemented in the template — keep ALL of these):**
- Skip-link ("Skip to the bottom line") as the first element in <body>.
- "⌂ Top" home pill leads the Verdict nav group (href="#top", id="top" on the h1) — readers
  can always return to the full report from any section.
- Reading-progress hairline inside the sticky nav (passive rAF scroll handler — the
  no-scroll-listener rule applies to the scroll-SPY, which stays IntersectionObserver).
- Nav compacts after the reader passes §1 (only eyebrows + active pill + home pill remain;
  hover/focus-within expands) — reuses the existing #s2 observer.
- "▾ collapse audit" control in the Audit nav group toggles the three <details> sections.
- Hover-revealed # copy-links on every h2/h3 with an id — give h3s ids when adding them.
- Tables become their own horizontal scroll containers at ≤800px — never page-level
  horizontal scroll.
- .rec/.note boxes carry mono eyebrow labels (Verdict/Caveat defaults; override with
  data-label="..."); print adds break-inside:avoid on .chartbox/table/.rec/.note/.pkg.

**Content formatting rules (apply during every prose regeneration):**
- Every prose pass over the report (and README) ends with a de-AI-writing edit — in
  Claude Code, invoke the humanizer skill (https://github.com/blader/humanizer); elsewhere
  apply its source checklist manually
  (Wikipedia's "Signs of AI writing": https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing):
  no inflated symbolism, no promotional language, no rule-of-three padding, no negative
  parallelisms ("not just X, but Y"), no filler transitions, no em-dash overuse. The
  report reads like a careful homeowner's engineering notebook, not marketing copy.
  Calibration: the report's structural em dashes (day-band labels, table cells, meta
  rows, heading verdicts) are the design language and stay; the rule targets running
  prose. See TECHNICAL.md §8 for the full LLM configuration.
- No paragraph over ~800 characters. Findings use the .finding pattern: one bold claim
  sentence → a compact table.evidence (source | value | agreement) → a .small caveat line.
- **BASIC-tier density cap (issue #68):** every finding's FIRST claim sentence — a
  `.finding .claim`, a `.verdict` line or `<summary>` `.teaser`, or, absent a separate claim
  ahead of it, a `.small`/`.note` methodology block's own opening sentence — must state the
  plain-language conclusion in **35 words or fewer up to its first sentence-ending period**
  (a period followed by a space or the tag's end; ignore periods glued to digits, as in
  `$264.10` or `91.5%` — a human reader skips those too), with **at most one parenthetical
  or em-dash aside** before that break. Formula names, derivative/elasticity mechanics,
  confound explanations, and multi-term decomposition arithmetic move AFTER the lead: into
  the ONE `.small`/`.note` block immediately following it (that single block is exempt from
  the cap and may run as dense as the evidence requires — not an open-ended chain of further
  blocks), a trailing caveat sentence in the same block, or a "see TECHNICAL.md §N" pointer.
  Checkable by inspection: count words to the first real sentence break and count asides
  before it; if either cap is blown on a LEAD sentence, split it and move the excess after
  the break. Governs ONLY the basic tier (§0–§7, "What to do Monday"); the ADVANCED tier
  (§8–§14) and TECHNICAL.md are exempt, since that audience reads for the full derivation on
  purpose. No figure, evidence pill, or artifact citation may be deleted to hit the cap —
  move it after the break, never drop it.
- Every h2 section opens with a one-line conclusion, and carries it exactly one of three
  ways: (a) the verdict written INTO the heading itself ({{S4_VERDICT_SHORT}},
  {{S8_VERDICT_SHORT}}, {{S11_VERDICT_SHORT}}); (b) for the three collapsible sections
  (§9, §12, §13), the `<span class="teaser">` inside their `<summary>`; (c) everywhere
  else, a `<p class="verdict">` line directly after the h2, opening "In one sentence: ".
  Every (c) line is token-owned ({{S0_VERDICT}} … {{S15_VERDICT}}) — the token supplies
  the whole sentence including every sigil and unit, and index.html must carry it verbatim
  (as rendered — token values are HTML-escaped on the way in). Never give one section two of
  these; a section with none is a bug.
- Every "§N" reference in prose is a real <a href="#sN"> link.
- Chart.js titles stay terse; the narrative conclusion goes in a .small caption below the
  .chartbox — never crammed into the chart title.

- Print stylesheet hides nav/toggle buttons. Sticky TOC in three labeled
groups — Verdict (Bottom line, Plans, Battery×plan, Packages, Do Monday), Evidence (Data,
System, Usage, Battery HW, Array upgrades), Audit (Deep dives, Bills, Payback, Cleaning,
Carbon/NEM, Methodology) — with uppercase eyebrow labels and compact pills. Scroll-spy via ONE
IntersectionObserver on h2s (no scroll listeners); active pill in --acc. h2
scroll-margin-top ≥ nav height; smooth scrolling gated by prefers-reduced-motion. The three
heaviest audit sections (Deep dives, Cleaning, Carbon/NEM) are native <details>/<summary>
and remain OPEN by default WITHIN the advanced tier (closed-by-default inner sections made
readers think the content was missing); the advanced tier itself is the single intentional
closed-by-default boundary, made discoverable by the loud divider summary — which is why the
closed-by-default lesson does not apply to it. Summary = h2 + one-line conclusion teaser;
hashchange/load JS opens a collapsed section (and the tier around it) before jumping; charts
inside collapsed sections lazy-init on first open.
Quiet back-to-top button (appears after §1, aria-label). Mobile ≤800px: grouped TOC ≤ ~2 rows
(horizontal scroll). Keyboard :focus-visible on pills/summaries. Page must degrade cleanly
with JS disabled.

## 11. Production-provenance note (REQUIRED in every regeneration).
index.html §Methodology's closing small-print paragraph must end with:
"How this report was produced: generated with Claude Cowork (Fable 5); the data, methodology,
and conclusions were then independently reviewed with Claude Code (Fable 5) and adversarially
reviewed with Codex (GPT-5.6 Sol); the analysis was subsequently re-worked in Claude Cowork to
incorporate the findings of both reviews."
README.md carries the equivalent blockquote immediately before the report-description
paragraph. Never drop or reword these when regenerating either file.

## Repo map (what's public vs private)
- Public: `index.html`, `report-template.html`, `README.md`, `TECHNICAL.md`, `CLAUDE.md`,
  `reusable-prompt.md`, `DATA-SOURCES-CHEATSHEET.md`, `household.example.yaml` (placeholders
  only — the filled copy lives at `private/household.yaml`), `requirements.txt`,
  `data/` (de-identified), `analysis/` (scripts), `research/`.
- Private (gitignored): `private/` — raw Green Button, bill PDFs, monitoring exports, as-run
  scripts with personal headers. Exception: `private/README.md` is committed as a placeholder
  documenting what's withheld.


## 12. README structure requirements (keep on every regeneration).
README.md must retain: (a) a "Companion documents" block immediately after the provenance
blockquote, linking TECHNICAL.md, GLOSSARY.md, DATA-SOURCES-CHEATSHEET.md, and
reusable-prompt.md with one-line descriptions; (b) a "Reproduce this for your own home -
start here" section (blank-slate clone commands, cheatsheet data-gathering, personal
private/pii-rules.toml setup, AI route vs manual route, the S9 validation gates, then
publish); (c) a privacy note describing the MECHANICAL enforcement (pre-commit hook via
core.hooksPath .githooks, CI gitleaks workflow, local-only private/pii-rules.toml) - never
manual grepping alone; (d) a "Refreshing this analysis" flow reflecting the current
pipeline (rates.py as single source of truth -> pipeline scripts -> regeneration
diff-check -> report-template.html), never the retired analyze.py/D-block flow.

## 13. Self-preservation (this file).
Any future edit of CLAUDE.md must keep: the "Commands" section at the top (hook setup,
venv/requirements.txt, the private/verify sandbox pattern, the regeneration gate, gitleaks
scan invocations), the mechanical-enforcement text in section 4, and the reference to the
committed requirements.txt (pandas, numpy, pyyaml, pdfplumber, openpyxl, scipy — the last
added for issue #13's perfect-foresight LP solver, `scipy.optimize.linprog`/HiGHS). These
encode the working developer setup;
dropping them in a regeneration silently breaks the privacy gate and the reproduction path.
