# CLAUDE.md — operating rules for the home-energy analysis

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository. It focuses the work and encodes the mistakes we already made so they aren't
repeated. Read it before touching this project. It applies to Claude Cowork, Claude Code,
and any agent.

## Commands

```bash
# One-time setup on any fresh clone — enable the secret/PII pre-commit gate:
git config core.hooksPath .githooks       # requires: brew install gitleaks

# Python environment (scripts need pandas/numpy/pyyaml; parse_bills.py also pdfplumber):
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

# Bill artifacts (rerun after adding statements to private/1-raw-data/*-bills/):
# parse_bills.py finds the repo root itself, so run it from anywhere. It regenerates the
# two legacy summaries as its own reproduction gate — they must not change:
../../.venv/bin/python parse_bills.py && git diff --exit-code ../../data/electric_bill_summary.csv \
    ../../data/gas_bill_summary.csv ../../data/bill_periods_electric.csv \
    ../../data/bill_periods_gas.csv ../../data/bill_tou_detail.csv
# Its fail-closed behaviour has negative tests (missing statement, corpus gaps, TOU
# layout drift, mid-write failure). Run them after touching the parser:
./.venv/bin/python analysis/test_parse_bills.py     # from the repo root

# Carbon artifacts (rerun when needed): carbon_fullyear.py uses the raw CAISO day-cache
# private/1-raw-data/caiso_raw/ when present, otherwise rebuilds exactly from the
# committed data/caiso_hourly_intensity.csv; it fails closed if coverage would shrink
# and writes both artifacts atomically (TECHNICAL.md §3.15):
../../.venv/bin/python carbon_fullyear.py && git diff --exit-code ../../data/carbon_fullyear_results.json ../../data/caiso_hourly_intensity.csv

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

## 8. Scope discipline.
Ask clarifying questions before large work. Keep a task list. Use subagents for independent
sub-analyses and for an adversarial verification pass over the math before presenting. Verify
programmatically. Do not expand scope silently.

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
- Signature element: the DAY-BAND — pure-CSS 24-h TOU strip (segments at 0-6-10-14-16-21)
  with tick marks and prices, full-width under the header; keep it on every regeneration.
- Every time-axis chart shades the 4-9pm window via the `onpeakBand` Chart.js plugin.
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
  3. The tier's chart (#carbon, the only canvas inside it) lazy-inits on the first time
     the tier AND sec13 are both open, double-init guarded; the four §5 basic-tier
     charts keep initializing on load.
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
- Every h2 section opens with a one-line .verdict ("In one sentence: ...") — the teaser
  pattern extended to the non-collapsible sections.
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
committed requirements.txt (pandas, numpy, pyyaml, pdfplumber). These encode the working developer setup;
dropping them in a regeneration silently breaks the privacy gate and the reproduction path.
