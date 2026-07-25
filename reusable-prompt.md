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

**9b. Carbon depth & honest labeling (addendum to 9).** Sample as many real ISO days as the
fetch channel practically allows — our run started at 4 seasonal days and was later expanded
to 28 (~2 per calendar month). Interpolate uncovered days with **month-hour means** of the
covered days in the same calendar month, commit the per-day hourly intensity table alongside
the results, and **label the output by coverage** ("estimated · N days sampled" — never
"measured" unless coverage is near-complete). Fuller sampling matters: it caught sunny spring
middays where CAISO's import-inclusive accounting drives intensity to ~0, cutting our
export-displacement estimate by 28% versus the 4-day version.

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

**12. Battery revenue programs — research CURRENT terms; count only what I can enroll in.**
Survey the demand-response/VPP landscape for my territory and battery brand as of today (for
California: the CEC **DSGS** options via the manufacturer's virtual power plant, utility
**ELRP** VPP pilots, CCA storage programs, and **SGIP** status — its general-market budget
closed 12/31/2025). Record closed or ineligible programs explicitly at $0 with the reason;
save program terms and source URLs in a research note and quote them in the report verbatim.
State the caveats: whether VPP export requires a **Rule 21** interconnection modification
(non-export agreements do), and that storage additions don't affect NEM grandfathering.
Stack only revenue the program explicitly permits alongside TOU arbitrage (DSGS does), and
show battery payback with and without it. In our run DSGS Option 3 via the Tesla app was the
one enrollable program (~$150–350/season; payback ~6.2 → ~5.4–5.6 yr).

**13. Resilience pricing — bound backup value instead of leaving it $0-or-infinite.** Pull
the utility's **Electric System Reliability Annual Report** (SAIDI/SAIFI, by district where
published) and the regulator's **PSPS post-event reports** for my district; convert to
expected outage-hours/yr for my circuit type, then multiply by a household outage-cost
bracket ($/h) to get a $/yr resilience-value bracket, labeled estimated. In our run: coastal
district ~57 SAIDI min/yr including major events and ~zero PSPS exposure → ~1–2.5
outage-hours/yr → ~$40–250/yr — real but small.

**14. Fixed-charge restructure — VERIFY it isn't already in the bills before modeling it as
a future.** Where a restructure like California's AB 205 income-graduated fixed charge
exists (CPUC D.24-05-028 plus the utility's implementation resolution), check the bill's
fixed-charge line and the rates module FIRST: in our run the $24.15/mo Base Services Charge
had billed since October 2025, REPLACED the EV plan's prior $16/mo fee, and was already in
`rates.py` — so the "scenario" was a verification. Then add the sensitivity for future
changes: every +$12/mo of fixed charge = +$144/yr on every scenario equally, so rankings and
all marginal behavior/battery figures are unchanged by construction.

**15. Gas HDD decomposition (extends 10).** Regress daily therms on heating degree-days
(base 65°F): the intercept is the weather-independent floor (water heating/cooking) and the
slope × annual HDD is space heating; cross-validate the split against the bills' seasonal
pattern. Then price the heat-pump ladder on that split — HPWH against the floor, heat-pump
space heating against the slope — valuing the added electricity at the **marginal
midday-surplus value**, not the blended rate.

**16. Electrification dividend (if I drive EVs).** Measured EV charging cost (detected
session kWh × the validated all-in rates, plus any DC fast-charging at an estimated price,
labeled as such) versus the gasoline counterfactual: odometer-derived annual miles ÷ a cited
fleet fuel economy (FHWA Highway Statistics VM-1) × the cited state gasoline price (EIA
monthly series). Label the result estimated (its external constants are cited, not
measured), and state it both at current charging times and post-schedule-fix.

**17. Cheap cross-checks that harden the story:** (a) **away-day baseload** — days with no
EV charging and imports well below the median give an unattended-house floor (a lower bound:
unattended load also eats solar midday); (b) **supercharging-vs-home delta** — DC-fast kWh ×
(estimated DCFC price − home super-off-peak all-in), an upper bound since road-trip energy
can't shift home; (c) **weekend super-off-peak window** — house kWh sitting outside the
weekend sop window and its half-shift value (quote the half-shift, not the fantasy full
shift); (d) **representative-year check** — same-months whole-home load across two
monitoring calendar years, so the report can say how typical the analysis year was.

**18. Post-grandfathering (NBT-era) battery value + sequencing.** Re-bill the battery year
under flat export credits (a 3/5/8¢ bracket — a flat-rate sensitivity, NOT a full Avoided
Cost Calculator run) to test whether the battery's marginal value falls when NEM
grandfathering expires. Ours ROSE ($2,325 → $2,504–2,539/yr) because stored surplus then
displaces 51–87¢ imports instead of earning 3–8¢ credits. State the expiry date (PTO +
20 yr), the hardware-warranty overlap (a battery bought today is out of warranty before
expiry), and that NEM 2.0 transfers with the property on sale.

**19. Tornado sensitivity on the battery payback.** Recompute the battery-alone payback
swinging one lever at a time — dispatch policy, install quote, rate escalation, program
revenue, behavior interaction — rank the levers by swing, and present the ranking with the
hardware verdict (ours: dispatch policy 2.3 yr > install quote 2.1 > escalation 0.9 > DSGS
0.8 > EV-fix interaction 0.3, around a 6.2-yr base).

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
8. **Fail-closed artifact discipline.** Derived-results scripts COMPUTE every figure from
   the engines and upstream artifacts — never hard-code a previously computed number.
   Assert cross-artifact consistency at run time (our extended-findings script re-runs the
   dispatch engine and aborts if its battery figures drift more than ~$1.50 from the
   committed dispatch artifact — a mismatch means a stale upstream artifact; regenerate it
   first). Validate every required output section before writing, and write artifacts
   atomically (tmp file + `os.replace`) so a partial or failed run changes nothing on disk.

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

**Design system ("Solar ledger" — keep on every regeneration; full spec in CLAUDE.md §10):**
light-first theme on warm-white paper with a dark variant via `[data-theme="dark"]` tokens
and a ◐ toggle (localStorage-persisted; honors prefers-color-scheme on first visit). A
SEMANTIC TOU palette — on-peak #BF3B2B, off-peak #C98A3D, super-off-peak #2E7D6B, solar
#E9B62F — applied identically wherever a period appears (day-band, chart series, tables,
price map); never introduce new accent colors for TOU-mapped data. Signature element: the
pure-CSS 24-hour DAY-BAND strip under the header (segments at 0-6-10-14-16-21, ticks and
prices). Typography: Space Grotesk (display), Source Serif 4 (body), IBM Plex Mono with
tabular-nums for every numeral; Google Fonts CDN with system fallbacks. Evidence pills are
mono uppercase stamps (measured=green / modeled=amber / estimated=red, same .pill.g/.y/.r
class names). Header household/window/sources facts render as labeled `.meta` ledger rows,
not a run-on paragraph. Charts take every color from the PAL object (which reads the CSS
tokens — never hardcode hex), use the semantic palette for TOU-mapped series, and add
`,plugins:[onpeakBand]` on any hourly time axis so 4–9pm is shaded. The Chart.js CDN tag is
pinned with an SRI integrity hash — keep integrity/crossorigin attributes, and recompute the
sha384 if the Chart.js version ever changes.

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


**README.md structure (required; keep on every regeneration):** (a) a "Companion documents" block immediately after the provenance blockquote, linking TECHNICAL.md, GLOSSARY.md, DATA-SOURCES-CHEATSHEET.md, and reusable-prompt.md with one-line descriptions; (b) a "Reproduce this for your own home - start here" section (blank-slate clone commands, cheatsheet data-gathering, personal private/pii-rules.toml setup, AI route vs manual route, the CLAUDE.md pre-publication gates, then publish); (c) a privacy note describing the MECHANICAL enforcement (pre-commit hook via core.hooksPath .githooks, CI gitleaks workflow, local-only private/pii-rules.toml) - never manual grepping alone; (d) a "Refreshing this analysis" flow reflecting the current pipeline (rates.py as single source of truth -> pipeline scripts -> regeneration diff-check -> report-template.html). Likewise preserve CLAUDE.md's "Commands" section, its mechanical-enforcement privacy text, and the committed requirements.txt in any regeneration of those files.
**EV telemetry cross-validation (whenever the EVs expose their own charging data):** pull each car's charging summary (e.g. Tesla app Charge Stats: trailing-12-month energy by location and TOU bucket, battery-side kWh) and any wall-charger daily export (wall-side kWh), plus odometer + in-service date per car (cheatsheet E2). Cross-validate the session detector on energy (battery/wall ratio should imply an 8-12% charging loss), session counts, and TOU shares; reconcile odometer-implied wall energy against measured charging and attribute residuals to measured real-world consumption before suspecting the detector. Frame the result honestly: the detector is meter-derived and *cross-checked* by vehicle and charger telemetry — the battery/wall gap is an implied loss (windows rarely align exactly), wall-charger agreement is at the totals level over its clean window, and odometers are a scale sanity-check, not a third energy measurement. In our run: 99.6% aggregate agreement over a 20-day clean window.

**Also deliver TECHNICAL.md and GLOSSARY.md** (the README links to both): TECHNICAL.md is the methods-section documentation — every script, data schema, algorithm, chart pipeline, and validation chain, written so the analysis can be audited or rebuilt; GLOSSARY.md defines every term of art in plain homeowner English with links to authoritative sources.

**Plan wildcards:** where the utility offers an event-based plan (e.g. SDG&E TOU-DR-P with Reduce Your Use event-day surcharges), simulate it explicitly — price the event-day exposure and test whether a battery that dodges events changes the answer — rather than excluding it silently.

**"What to do Monday" implementation appendix (required):** close the report with a short
content-only appendix — no new analysis; every item cites the section that justifies it —
containing: (1) the concrete schedule changes (charger windows, load staggering to respect
the service's coincident-draw limit, the midday-plug-in habit); (2) the pre-battery
checklist (panel/service headroom check, Rule 21 export-capability check, VPP/DSGS
enrollment step); (3) **pre-registered success metrics** for the behavior fix — current
value → target → which bill/cycle to check them on — logged BEFORE the change so the
evaluation can't be gamed; and (4) a **re-run triggers table**: utility and CCA rate-change
dates (SDG&E Jan 1/Jun 1, CEA Feb/Jun), regulator changes to the fixed charge,
fleet/appliance changes, and before-quotes/after-install for a battery — plus a standing
quarterly re-run if the tooling supports scheduled tasks.

**Privacy tooling (create it, don't just describe it):** set up the mechanical enforcement the README documents — a gitleaks pre-commit hook under .githooks/ (enabled via git config core.hooksPath .githooks), a committed .gitleaks.toml with generic account-format rules, a CI workflow that rescans full history on push, a local-only private/pii-rules.toml carrying my person-specific patterns (never committed), and a committed requirements.txt for the Python environment.
