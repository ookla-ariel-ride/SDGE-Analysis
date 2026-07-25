# CLAUDE.md — operating rules for the home-energy analysis

This file focuses the work and encodes the mistakes we already made so they aren't repeated.
Read it before touching this project. It applies to Claude Cowork, Claude Code, and any agent.

## 0. Prime directive: EVIDENCE-BASED ONLY. No guesses, no hallucination.
Every number, claim, and conclusion in the report MUST be traceable to (a) a datum you
loaded, (b) a rate/figure read off an official source or bill, or (c) a calculation you ran
and can show. If you cannot compute or cite it, do not state it — say "not determined" and
list what data would settle it.
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
- Before EVERY commit, grep push-bound files for: name, street address, account/meter/RIN
  numbers, email, phone, exact coordinates, utility/solar/PVOutput account IDs, API keys.
  Prove clean output before committing. (We twice nearly leaked a name/meter number.)
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
- **Process narrative stays out of the report:** the report presents data → analysis →
  conclusions only. Corrections, superseded drafts, and "we fixed X" belong in commits/PRs,
  never in the published document.

## 10. Report navigation requirements (index.html — keep on every regeneration).
Single self-contained file, vanilla CSS/JS, Chart.js CDN only. Sticky TOC in three labeled
groups — Verdict (Bottom line, Plans, Battery×plan, Packages), Evidence (Data, System, Usage,
Battery HW, Array upgrades), Audit (Deep dives, Bills, Payback, Cleaning, Carbon/NEM,
Methodology) — with uppercase eyebrow labels and compact pills. Scroll-spy via ONE
IntersectionObserver on h2s (no scroll listeners); active pill in --acc. h2
scroll-margin-top ≥ nav height; smooth scrolling gated by prefers-reduced-motion. The three
heaviest audit sections (Deep dives, Cleaning, Carbon/NEM) are native <details>/<summary>,
closed by default, summary = h2 + one-line conclusion teaser; hashchange/load JS opens a
collapsed section before jumping; charts inside collapsed sections lazy-init on first open.
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
- Public: `index.html`, `README.md`, `CLAUDE.md`, `reusable-prompt.md`,
  `DATA-SOURCES-CHEATSHEET.md`, `data/` (de-identified), `analysis/` (scripts), `research/`.
- Private (gitignored): `private/` — raw Green Button, bill PDFs, monitoring exports, as-run
  scripts with personal headers.
