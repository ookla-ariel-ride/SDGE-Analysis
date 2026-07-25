# Reusable prompt: whole-home energy optimization (rate plan + solar + battery + payback + carbon + gas)

Paste everything below the line into a new Claude Cowork session. Written for an SDG&E
customer with rooftop solar and an EV, but Claude will adapt to your situation (no solar,
no EV, a different California utility, bundled vs CCA generation, etc.). Pair this with the
**DATA-SOURCES-CHEATSHEET.md** in this repo, which lists every input and where to get it.

---

I want a complete, data-driven analysis of my home energy: whether I'm on the best electric
rate plan, whether a home battery makes sense (and which one), whether I should add or upgrade
solar, whether my solar has paid for itself yet, whether panel cleaning is worth paying for,
when my grid electricity is cleanest, and whether electrifying gas appliances pays off —
ending in ONE unified interactive HTML report I can publish to GitHub Pages. Ask me clarifying questions (solar? EV? CCA?
gas service? report format?) before starting, keep a task list, use subagents for independent
sub-analyses and for an adversarial math-verification pass, and verify every number
programmatically before presenting.

**EVIDENCE-BASED ONLY — no guesses, no hallucination.** Every number, claim, and conclusion
must trace to a datum you loaded, a figure read off an official source or my bill, or a
calculation you ran and can show me. If you can't compute or cite it, don't state it — say so
and tell me what data would settle it. Read facts (climate zone, rate plan, CCA product,
credits, baseline) off my detailed BILL rather than inferring them. Label MODELED vs ACTUAL
everywhere and anchor absolute dollars to my real bills. Validate your billing model against
my actual statements before quoting absolute costs. Report asset-alone paybacks, never crediting
free behavior savings to hardware. Keep every figure consistent across the whole document.
My solar happens to be Enphase, but treat production data generically — SolarEdge/Tesla/SMA/
Fronius/PVOutput all expose equivalent feeds; describe the DATA you need, not one vendor's menu.

**0. Data intake gate — do this FIRST, before any analysis.** The companion checklist
[`DATA-SOURCES-CHEATSHEET.md`](https://github.com/ookla-ariel-ride/SDGE-Analysis/blob/main/DATA-SOURCES-CHEATSHEET.md)
lists every input (sections A–H: household basics, interval data, rate PDFs, detailed bills,
solar production + install invoice + cleaning history, gas, weather & grid data, battery
research). Walk me through it section by section and
ask me for each piece: confirm what I already have gathered, what you should pull from my
logged-in portals, and what to skip (no solar → skip E; no gas → skip F). Verify each file
actually loads (row counts, date coverage, no gaps — tell me if a month is missing) before
proceeding. Do NOT start modeling until section A + B are in hand and you've told me exactly
which of C–H we're using and which we're skipping. A missing month of bills silently skews
every annual figure — check coverage in DAYS, not number of files.

Follow this plan:

**1. Electric usage — 15-minute interval data.** I'll have my utility portal open and logged
in in Chrome (use the Claude-in-Chrome extension). Export the last 13 months of Green Button
15-minute interval data as CSV, then have me connect my Downloads folder. Note my current
rate plan, NEM version (1.0 / 2.0 / NBT) and true-up date, generation provider (utility or
CCA), and climate zone. NEVER type my password — I log in myself.

**2. Current rate schedules (don't trust memory — rates change Jan 1 and Jun 1).** Download
the utility's official "Total Rates Table" PDFs for every eligible residential plan (record
UDC/delivery, bundled generation/EECC, non-bypassable charges, PCIA by vintage, fixed/base
service charge, baseline credits). If I'm on a CCA, get its current adopted residential
generation schedule and any per-kWh credits. Get current TOU window definitions and the
baseline allowances for my climate zone. Save a `rates-reference.md` with every figure and
its source URL and capture date.

**3. Model every eligible plan against my actual usage.** Price each 15-minute interval:
all-in import rate = delivery + non-bypassable + (CCA: PCIA + CCA generation | bundled:
EECC); credit NEM exports per my NEM version; add the daily fixed charge; apply baseline
credits where they exist; exclude tiered non-TOU plans if I'm on NEM. Model both CCA and
bundled scenarios if I'm on a CCA. Also emit a **marginal price map** — the all-in import and
export $/kWh for every season × TOU-period cell of my current plan, from bill-validated rates —
and, if I'm grandfathered on NEM 1.0/2.0, re-bill the same year under NBT-style flat export
credits to put a $/yr value on my grandfathered status.

**4. Solar system + production (if I have solar).** The utility meter only sees exports, not
self-consumed solar, so get GROSS production from my solar monitoring:
- Enphase Enlighten: "SAM 8760" report (hourly whole-home consumption, emailed — request each
  calendar year and stitch into a rolling 365 days) and "Site Energy Production" (daily). Note
  system size, inverter model/count, metering config, and PTO date from the Devices/Details pages.
- If I publish to PVOutput: pull daily generation (and, if I'm a donor, per-year statistics
  2020→now for a degradation trend, and 5-minute intraday power to measure inverter clipping).
  Use my read-only API key only if I explicitly paste it, and remind me to revoke it after.
- Cross-validate production across sources (Enphase meter vs PVOutput vs derived
  load−imports+exports); confirm night-time residual ≈ 0 and totals agree within a few percent.
  Report specific yield (kWh/kW/yr), capacity factor, self-consumption vs export split, and any
  degradation or dead-panel signal.
- Verify system size against physics: registered module count × wattage (kW DC) and
  microinverter count × rating (kW AC) vs the measured multi-year peak-power distribution —
  they should agree within a few percent.
- If I've ever paid for panel cleaning (ask for dates + cost), MEASURE the effect with a
  diff-in-diff: pull the same multi-week daily windows around the cleaning date for several
  years from my monitoring history (PVOutput `getoutput` API, donor feature — or the public
  daily list) and compare the cleaned year's pre→post change against the identical calendar
  windows in the uncleaned control years (controls remove the seasonal decline).
- Quantify soiling independently from rain recovery: daily precipitation from the NOAA/RCC
  ACIS API (data.rcc-acis.org, free, no key, nearest airport gauge — also the fallback when
  Open-Meteo is unreachable), clear-sky-normalized production before/after rain events that
  follow long dry spells, plus a days-since-rain regression. If the regression and the
  cleaning evidence disagree, report the honest bracket — then model the optimal cleaning
  cadence (best single and two-cleaning dates), valuing recovered kWh at what a marginal
  midday kWh is actually worth on my tariff, not at the blended rate.

**5. Lifetime solar payback (needs my install invoice).** Ask me for the install
contract/invoice (total installed price, payment date, whether the federal ITC was claimed)
and the PTO date. Build the cumulative-value curve — each year's ACTUAL production × that
year's blended $/kWh value, scaled by the utility's rate history rather than pricing all of
history at today's rates — and find the gross and net-of-ITC crossover dates. Also state what
solar is worth per year TODAY by re-billing a no-solar counterfactual on the bill-validated
netting model. Flag the rate-history scaling as approximate (±10% on crossover timing).

**6. Behavior analysis (with $/yr each).** On-peak (4–9pm) import volume and cost; high-power
(>2.5 kW) loads by TOU period (EV-charging discipline; charging that spills past the 6am
super-off-peak boundary); an EV charging-session "report card" (sessions, kWh, cost vs
perfectly-timed cost, dollars lost to mistiming); a phantom/always-on baseload DECOMPOSITION
from EV-free quiet nights — median/p10/p90 kW, duty-cycling signature, seasonal profile, and
the lowest occupied-day import floor (present it cautiously — largely legitimate baseload, not
all recoverable); load-shifting scenarios (25%/50% of on-peak → super-off-peak); midday
self-consumption vs export economics.

**7. Weather normalization.** Pull daily temperatures for my area (Open-Meteo archive API,
free, no key) and regress non-EV daily load on cooling/heating degree-days to isolate A/C
load in kWh/°F, quantify the value of pre-cooling and setpoint changes, and enable
hot-vs-mild-summer bill projections.

**8. Battery study.** Simulate per-interval arbitrage (charge from would-be exports +
overnight super-off-peak top-up, discharge on-peak, ~90% round trip) for real current products
(e.g. Enphase IQ 5P/10C, Tesla Powerwall 3 ± expansion) at researched 2026 installed prices.
Simulate outage endurance hour-by-hour (outage at 6pm, solar recharge, median AND 10th
percentile) for backup tiers: essentials / whole-house-minus-EV / whole-home-incl-EV.
**Does a battery change the best-plan answer?** Model each candidate plan WITH and WITHOUT a
battery — battery value differs by plan because it arbitrages that plan's price spread. Check
current incentive status (federal ITC, SGIP) — DO NOT assume they still exist. Note NEM
implications of adding storage. Run a Monte Carlo on battery payback (vary rate escalation,
capacity fade, install price) AND an explicit rate-escalation sensitivity ladder (e.g.
3/5/8/12%/yr → payback and 10-yr NPV) so I can see how the answer depends on that one
assumption. Label paybacks honestly: PACKAGE payback (battery + free
behavior fixes) is NOT the same as BATTERY-ALONE payback — report both, don't credit free
behavior savings to hardware.

**9. Solar expansion / repowering / microinverter upgrade — answer with data.** Should I add
panels, install higher-capacity panels, or upgrade microinverters? Value a marginal midday
kWh at its ACTUAL current export credit (often ~10¢ under post-2024 TOU) vs what it costs;
factor NEM expansion/grandfathering limits (adding >~1 kW or 10% can drop NEM 2.0 → NBT).
If I mention clipping, MEASURE it from 5-minute power vs the AC nameplate ceiling (peak-hour
distribution, flat-top day detection) and value clipped energy at its time-of-day worth before
recommending any inverter swap — usually clipping is trivial and replace-on-failure is right.

**10. Grid-carbon timing (if my grid publishes it — CAISO does).** Pull REAL grid data from
CAISO's Today's Outlook history endpoints (`caiso.com/outlook/history/YYYYMMDD/co2.csv` and
`demand.csv` — free, no key), one representative mid-month day per season. Compute hourly
grid-average kg CO2/MWh (total CO2 ÷ demand) and apply it to my 15-minute imports/exports:
annual import footprint, CO2 avoided by exports, and the emissions delta of moving mistimed
EV charging overnight vs to solar midday. Cross the carbon answer with the tariff: if midday
and overnight price the same (post-2026 TOU windows often make weekday 10am–2pm
super-off-peak), say so — the cleaner choice may be free.

**11. Gas + electrification (if I have gas service).** Export gas Green Button (daily therms)
and note the gas rate schedule. Split usage into non-heating baseline (water heater + cooking)
vs space heating. Model a heat-pump water heater (gas therms saved vs electricity added on a
midday-solar timer) and note heat-pump space heating as a larger, bundle-with-HVAC move.
IMPORTANT: check the bills for whether gas has a real fixed monthly charge before claiming an
all-electric "drop the connection fee" windfall — many gas bills are nearly purely volumetric.

**12. Detailed bill audit (do this — it's where the truth is).** Have me download every
monthly detailed bill PDF (electric AND gas, ~12 months each) into my Downloads folder. Parse
each line-by-line (pdfplumber). This: (a) validates your modeled rates to the penny, (b)
resolves ambiguities your model can't — e.g. whether a CCA relief credit actually applies,
the exact product name, the real climate zone, (c) reconciles MODEL vs ACTUAL billed cost.
Expect the interval model to overstate absolute cost because utility NEM export crediting is
often more generous than a conservative interval model captures — quantify the gap, explain
it (NEM period-netting, generation credits), and tell me to anchor absolute-dollar figures to
my real bill while trusting the model for plan RANKINGS and PERCENTAGE savings. Also compare
against the utility's OWN plan-comparison tool (My Energy Center → Pricing Plans) and explain
any differences.

**13. Deliverables — one folder, GitHub-Pages-ready.**
- `index.html`: ONE unified, self-contained interactive report (dark theme, Chart.js from CDN)
  — a coherent document, not sections bolted on. Suggested structure: bottom-line integrated
  recommendation (plan + battery package + solar verdict, with actual vs projected monthly
  bill) → data & validation summary → your solar system profile → plan comparison (folding in
  the utility's tool) → does-a-battery-change-the-plan matrix → usage/behavior findings with
  charts → lifetime solar payback → battery hardware (arbitrage + outage endurance) → THREE costed packages (Low =
  behavior-only $0, Mid = +one battery, High = +expanded storage; each with annual cost,
  savings vs baseline, projected avg AND min–max monthly bill, payback/ROI, 10-yr value,
  backup capability; mark a recommendation and model the interaction so behavior and battery
  savings aren't double-counted) → solar expansion/clipping verdict → deep analyses
  (degradation, cleaning effect, soiling, weather-normalized cooling, EV report card,
  grid-carbon timing, plan wildcards, phantom decomposition, NEM-grandfathering value) →
  actual-bill reconciliation + gas + electrification → methodology/sources/caveats.
- `README.md` with a clickable live-report link, GitHub-Pages publish steps, and a privacy note.
- `analysis/*.py` scripts, de-identified aggregate data in `data/`, `rates-reference.md` and
  research notes in `research/`, and this prompt.
- `.gitignore` + a `private/` folder holding all raw PII (Green Button CSVs, bill PDFs,
  monitoring exports) — gitignored, never pushed.

**14. Privacy & safety review (MANDATORY before anything is shared or committed).** Audit
every deliverable (report, README, scripts, data, commit messages) and confirm none contains:
my name, street address, account/meter/RIN numbers, email/phone, exact coordinates, my
utility/solar/PVOutput account IDs, any API key, or a raw interval/bill file. Refer to
location only as climate zone. Keep all raw PII in `private/` behind `.gitignore` with
defensive filename patterns, and warn me that git history preserves anything once committed
(prefer deleting/recreating a repo over trying to scrub history). Report the audit results to
me explicitly, and grep the push-bound files to prove they're clean before every commit.
