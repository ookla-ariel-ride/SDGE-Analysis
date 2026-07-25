# Reusable prompt: whole-home energy optimization (rate plan + solar + battery + payback + carbon + gas)

**What this produces.** The report you found in this repo, but for YOUR home: one
self-contained interactive HTML report (GitHub-Pages-ready) answering whether you're on the
best rate plan, whether a battery pays, whether to expand solar, whether your solar has paid
for itself, whether panel cleaning is worth it, when your grid power is cleanest, and whether
electrifying gas pays — every number scripted, bill-validated, and privacy-audited.

**How to use it.**
- Paste everything below the line into a new Claude Cowork session.
- Have this repo's **DATA-SOURCES-CHEATSHEET.md** open or downloaded — Phase A walks it
  section by section.
- Written for an SDG&E customer with rooftop solar, an EV, a CCA, and NEM 2.0, but Claude
  adapts: any utility/CCA, no solar, no EV, no gas, NBT instead of NEM.
- Solar monitoring is vendor-agnostic — Enphase, SolarEdge, Tesla, SMA, Fronius, and PVOutput
  all expose the needed feeds (gross production, system size, metering config).

---

I want a complete, data-driven analysis of my home energy, ending in ONE unified interactive
HTML report I can publish to GitHub Pages. Ask me clarifying questions first (solar? EV? CCA?
gas service? battery interest?), keep a task list, use subagents for independent sub-analyses,
and verify every number programmatically before presenting.

**EVIDENCE-BASED ONLY — no guesses, no hallucination.** Every number, claim, and conclusion
must trace to a datum you loaded, a figure read off an official source or my bill, or a
calculation you ran and can show me. If you can't compute or cite it, say "not determined" and
tell me what data would settle it. Read facts (climate zone, rate plan, CCA product, credits,
baseline allowance) off my detailed BILL — never infer them from ZIP or assumptions. Label
MODELED vs ACTUAL everywhere; anchor absolute dollars to my real bills; prefer deltas between
two model runs to absolute levels. When two methods disagree, reconcile and quantify the gap
before concluding. No figure appears in the report unless a committed script produced it.

## PHASE A — SETUP & DATA INTAKE (gate: no modeling until this passes)

Walk me through **DATA-SOURCES-CHEATSHEET.md section by section (A–H)**: household basics,
interval data, rate PDFs, detailed bills, solar production + install invoice + cleaning
history, gas, weather/grid data, battery research. For each section confirm: what I already
have, what you will pull from my logged-in portals (I'll have Chrome open and logged in —
you drive via the browser extension, but **NEVER type my password**; single clicks only, and
confirm which window holds the session), and what to skip (no solar → E; no gas → F).

Verify every file actually loads: row counts, date coverage, gaps — tell me if a month is
missing. **Check bill coverage in DAYS, not number of files**: sum the billing-period days to
exactly 365. One statement can contain multiple billing periods (short stub cycles) — parse
periods, not files, and dedupe. A hidden shoulder-month gap silently skews every annual
figure. From the detailed bill, record my rate plan, NEM version + true-up date, generation
provider, and climate zone.

**Gate:** sections A + B in hand, every one of C–H explicitly confirmed in-use or skipped,
all files verified. Only then start Phase B.

## PHASE B — ANALYSIS

**1. Rates from official PDFs — don't trust memory (rates change Jan 1 and Jun 1).**
Download the utility's official Total Rates Table PDF for every eligible residential plan:
delivery/UDC, bundled generation (EECC), non-bypassable charges, PCIA by vintage, fixed/base
service charge, baseline credits. If I'm on a CCA, its current adopted schedule and any
per-kWh credits. Current TOU window definitions and my climate zone's baseline allowances.
Save `rates-reference.md` with every figure, source URL, and capture date.

**2. Model every eligible plan against my actual usage.** Price each 15-minute interval:
all-in import = delivery + non-bypassable + (CCA: PCIA + CCA generation | bundled: EECC);
credit exports per my NEM version; add the daily fixed charge and baseline credits; exclude
tiered non-TOU plans if I'm on NEM; model both CCA and bundled if applicable. Also emit a
**marginal price map** — import and export $/kWh for every season × TOU-period cell of my
current plan, from bill-validated rates — and, if I'm grandfathered on NEM 1.0/2.0, re-bill
the same year under NBT-style flat export credits to put a $/yr value on grandfathering.

**3. Solar validation (if I have solar).** The utility meter sees only exports, so get GROSS
production from my monitoring platform (hourly/daily production, whole-home consumption if
metered, system size, inverter model/count, PTO date; PVOutput history if I publish there —
read-only API key only if I paste it, remind me to revoke it). Cross-validate production
across three sources (monitoring meter vs published records vs derived load − imports +
exports); confirm nighttime residual ≈ 0 and totals agree within a few percent. Report
specific yield, capacity factor, self-consumption vs export split, degradation trend. Verify
system size against physics: module count × wattage (kW DC) and inverter count × rating
(kW AC) vs the measured multi-year peak-power distribution. If I've paid for panel cleaning,
MEASURE the effect with a diff-in-diff: same multi-week daily windows around the cleaning
date across several years — uncleaned years are the control for seasonal decline. Quantify
soiling independently from rain recovery (daily precipitation from the free NOAA/RCC ACIS
API, clear-sky-normalized production around rain-after-dry-spell events, plus a
days-since-rain regression); if the two lines of evidence disagree, report the honest
bracket. Then model optimal cleaning cadence, valuing recovered kWh at what a **marginal
midday kWh** is worth on my tariff — not the blended rate.

**4. Lifetime solar payback (needs my install invoice: price, date, ITC claimed).** Build
the cumulative-value curve: each year's ACTUAL production × that year's blended $/kWh under
the TOU structure in force at the time, scaled by the utility's average-rate history (a rate
index — never today's rates back-cast over history). Find gross and net-of-ITC crossover
dates; flag them ±10%. Also state solar's value per year TODAY by re-billing a no-solar
counterfactual on the bill-validated netting model.

**5. Behavior — session-based, energy moved physically, never as lump sums.** Detect EV
charging sessions explicitly (contiguous runs of excess over a rolling baseline, with a
realistic peak-power signature), then shift by PHYSICALLY placing moved kWh into destination
intervals (respect the charger's kW cap, verify energy conservation) and re-bill the modified
year on the validated netting model — "shifted kWh × average rate" misprices the shift.
Report a compliance ladder (EV-only at 100% and 80%; flexible house load as a labeled
stretch) and state how much on-peak load is actually EV vs house (house load may not move).
Add an EV charging report card (sessions, kWh, dollars lost to mistiming and where), a
phantom/always-on baseload decomposition from EV-free quiet nights (median/p10/p90 kW,
duty-cycling signature — present cautiously, mostly legitimate baseload), and midday
self-consumption vs export economics.

**6. Weather normalization.** Daily temperatures (Open-Meteo archive API, free; ACIS as
fallback); regress non-EV daily load on cooling/heating degree-days to isolate A/C kWh/°F,
value pre-cooling and setpoint changes, and enable hot-vs-mild-summer projections.

**7. Battery.** Per-interval arbitrage simulation (charge from would-be exports + overnight
top-up, discharge on-peak, ~90% round trip) for real current products at researched installed
prices; hour-by-hour outage endurance by backup tier (median AND 10th percentile; note that
full-charge-at-outage-start is optimistic). Model every candidate plan WITH and WITHOUT a
battery — battery value depends on the plan's price spread. Check current incentive status
(ITC, SGIP) — do not assume they exist. Run a Monte Carlo on payback AND an explicit
rate-escalation ladder (e.g. 3/5/8/12%/yr → payback and 10-yr NPV). Compute the battery's
marginal saving on the POST-behavior-fix year and state the behavior/battery overlap in
dollars. Label paybacks honestly: PACKAGE payback ≠ BATTERY-ALONE payback — report both,
never credit free behavior savings to hardware.
**Dispatch policy is the biggest modeling lever — simulate at least three:** evening-only (discharge on-peak only), two-window (+morning house load), and **price-aware** (discharge against every import priced above the battery's stored-energy cost — typically all non-super-off-peak hours; a stored kWh costs only the forgone export credit ÷ RTE, or the super-off-peak rate ÷ RTE). Exclude EV-spillover intervals from battery service (the free schedule fix moves that load; don't double-pay for it), always charge from solar surplus before grid, and report kWh served and cycles/day per policy. In our run the price-aware policy was worth ~35% more per year than evening-only — publish it as the basis and show the others as the conservative bracket.

**8. Solar expansion / repowering / inverter upgrade.** Value a marginal midday kWh at its
ACTUAL export credit (often ~10¢ post-2024) vs cost; factor NEM expansion limits (adding
>~1 kW or 10% can forfeit grandfathering). If clipping is suspected, MEASURE it from 5-minute
power vs the AC nameplate before recommending any inverter swap — usually it's trivial and
replace-on-failure is right.

**9. Grid-carbon timing (if my ISO publishes it — CAISO does).** Pull real history CSVs
(CAISO Today's Outlook `co2.csv` + `demand.csv`, free), one mid-month day per season; compute
hourly kg CO2/MWh and apply to my 15-minute imports/exports: annual footprint, CO2 avoided by
exports, and the emissions delta of moving mistimed EV charging overnight vs to solar midday.
Cross carbon with tariff — if midday and overnight price the same, the cleaner choice is free.

**10. Gas + electrification (if I have gas).** Gas Green Button daily therms + rate
schedule; split non-heating baseline from space heating; model a heat-pump water heater on a
midday-solar timer; note heat-pump space heating as a bundle-with-HVAC move. Check the bills
for whether gas has a real fixed monthly charge before claiming an all-electric
"drop the connection fee" windfall — many gas bills are nearly purely volumetric.

**11. Detailed bill audit — this is where the truth is.** Parse every monthly detailed PDF
(electric AND gas) line-by-line. This validates modeled rates to the penny, resolves what the
model can't (whether a CCA credit actually applies, exact product, real climate zone), and
reconciles MODEL vs ACTUAL. If they disagree, find the REAL driver before writing a story —
test candidates separately: netting methodology, **rate vintage** (a model priced entirely at
current rates reads high against a year billed on older, cheaper tariffs — in our run this
was nearly the whole gap; the netting methods agreed to well under 1%), and coverage gaps.
Anchor absolute dollars to the bills; trust the model for RANKINGS and DELTAS. Cross-check
the bills' NEM year-to-date ledger arithmetic, and compare against the utility's own
plan-comparison tool.

## PHASE C — PRE-PUBLICATION GATES (run ALL, mechanically — not from memory)

Also enforce: **one canonical rates module** (every script imports constants + billing engine
from a single `rates.py`; independently declared rates drift), **every committed artifact must
be regenerable by its committed script** (run it, diff the output), and **one pipeline per
package figure** (behavior + hardware = one integrated simulation re-billed end-to-end, never
spliced across models).

1. **Artifact–prose diff.** Every figure in the report must match the committed data
   artifacts (`data/*.json`, `*.csv`). When prose is re-based, REGENERATE the artifacts — a
   stale results JSON will keep publishing a retired number long after the prose is fixed.
2. **Code implements its docs.** Verify each model's docstring against its code. In
   particular: under NEM, non-bypassable charges are levied on **GROSS imported kWh**, not
   netted — verify against an actual bill line (e.g. a wildfire-fund charge on more kWh than
   the period's net) before trusting the netting model.
3. **One rate vintage per projection.** Never subtract current-rate model deltas from
   prior-year actual bills. Project on one basis, label it "at constant current rates," and
   note that historical actuals were billed on older tariffs.
4. **A committed script per headline number.** If a headline figure has no script in
   `analysis/` that reproduces it, write one before shipping.
5. **Confidence labels.** Tag sections visibly as **measured** (meters/bills/multi-source),
   **modeled** (validated model at current rates), or **estimated** (scaled history, single
   events, sampled days). One cleaning event or four sampled grid days must not read with the
   same authority as a year of 15-minute data.
6. **No process narrative.** The report presents data → analysis → conclusions only.
   Corrections and superseded drafts live in commits, never in the published document.
7. **Adversarial verification.** Spawn a subagent to attack the math, rates, and claims
   end-to-end; resolve every finding before anything ships.

## PHASE D — DELIVERABLES

**The report must open with a Purpose block** (above the Bottom line): one paragraph stating
what the document is — a decision document computed from measured data by committed scripts —
followed by an enumerated list of the questions it answers, each with a section pointer, and a
closing line explaining the measured/modeled/estimated evidence labels. Adapt the question
list to the analyses actually run (the template carries the reference list).

**Start `index.html` from `report-template.html` (in this repo) — do not build the shell from scratch.** The template already contains the finished dark-theme CSS, the grouped Verdict/Evidence/Audit sticky TOC with scroll-spy, the collapsible audit sections with lazy-chart init, the five chart scaffolds, confidence-pill examples, hanging-indent bottom line, and the provenance slot. Your job is to replace every `{{TOKEN}}` with a script-produced value and fill the TODO blocks with your findings — never invent a number to fill a token, and never strip the navigation/provenance machinery.

**One GitHub-Pages-ready folder:** `index.html`, `report-template.html`, `README.md`,
`analysis/*.py`, de-identified aggregates in `data/`, `rates-reference.md` + notes in
`research/`, this prompt and the cheatsheet, and a `.gitignore`d `private/` folder holding
ALL raw PII (Green Button CSVs, bill PDFs, monitoring exports) — never pushed.

**index.html** — one self-contained interactive report (vanilla CSS/JS, Chart.js CDN only),
ordered: bottom line → data & validation → solar system profile → plan comparison → does-a-
battery-change-the-plan matrix → usage/behavior findings → battery hardware (arbitrage +
outage endurance) → three costed packages (Low = behavior-only $0 / Mid = +battery / High =
+expanded storage; each with annual cost, savings, projected bills at constant current rates,
honest asset-alone payback, backup capability) → expansion/clipping verdict → deep analyses →
bill reconciliation + gas + electrification → lifetime payback → cleaning & soiling →
carbon/NEM-value/escalation/price-map → methodology & caveats.

**Navigation (build it in, keep it on every regeneration):** sticky TOC in three labeled
groups — **Verdict / Evidence / Audit** — as compact pills under uppercase eyebrow labels;
scroll-spy via ONE IntersectionObserver on the h2s (no scroll listeners), active pill
highlighted; h2 `scroll-margin-top` ≥ nav height; smooth scrolling gated by
`prefers-reduced-motion`. The heaviest audit sections are native `<details>/<summary>`,
open by default (collapsible; do not hide content behind closed sections), each summary = the h2 plus a one-line conclusion teaser;
hashchange/load JS opens a collapsed section before jumping to it; charts inside collapsed
sections lazy-init on first open. Quiet back-to-top button (after §1, aria-labeled). Mobile
≤800px: grouped TOC collapses to ≤ ~2 rows with horizontal scroll. Keyboard `:focus-visible`
on pills and summaries. The page must degrade cleanly with JS disabled.

**Provenance note (required; must survive every regeneration).** The methodology section's
closing small-print in `index.html` ends with a "How this report was produced" sentence, and
`README.md` carries the equivalent blockquote immediately before the report description.
Keep the structure: *generated with [tool], independently reviewed with [second tool],
adversarially reviewed with [third tool], then re-worked to incorporate the findings of both
reviews* — substituting whatever tools/models were actually used. Never drop or reword it
away when regenerating either file.

**README.md** — clickable live-report link, GitHub-Pages publish steps, a **"What the report
covers"** table (section → the question it answers), and a privacy note.

**Privacy & safety review (MANDATORY before anything is shared or committed).** Audit every
deliverable — report, README, scripts, data, commit messages — for: name, street address,
account/meter/RIN numbers, email/phone, coordinates, utility/solar/PVOutput account or
system IDs, API keys, raw interval/bill files. Refer to location ONLY as a climate zone.
Grep the push-bound files and report the results to me before EVERY commit. Git history is
permanent — if PII ever lands in a commit, recommend delete-and-recreate over scrubbing.
After each commit, verify it actually landed on the remote before moving on.
