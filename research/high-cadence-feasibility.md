# High-cadence consumption: evidence and optional feasibility protocol

**Status: household detection remains undetermined.** Stored EAGLE history and scripted
collection diagnostics exist. Continuity qualification, device freshness, and controlled-load
performance remain unresolved.
This document prepares [issue #18](https://github.com/ookla-ariel-ride/SDGE-Analysis/issues/18).
It contains no completed detection study, performance result, or main-report section.

## Availability and evidence limits

The private telemetry availability audit dated 2026-10-22 checked the sibling observatory's
stored records and collector semantics. EAGLE supplies signed net-grid power history with
import positive and export negative. The audit found multi-hour gaps. Collector row times
do not establish fresh device measurements. These limits prevent a detection-readiness claim.
The private audit retains its queries, source references, and gap results.

Earlier issue comments treated the absent sustained Sense stream as absent high-cadence
history. EAGLE is an existing candidate; a new Sense campaign is not mandatory.
The earlier Sense daily-only history claim was incorrect. The recorded local check, corrected
on 2026-07-31 and reviewed on 2026-10-22, found hourly breakdown arrays in `scale=DAY` responses.
This historical check does not establish current API behavior, a stored high-cadence stream,
or controlled on/off ground truth. No controlled-test record appeared in the audit's checked
sources. That search cannot establish whether an undocumented test occurred elsewhere.

[`analysis/service_voltage.py`](../analysis/service_voltage.py) already produces collection
diagnostics from a frozen private export and manifest. The existing de-identified artifact,
[`data/service_voltage.json`](../data/service_voltage.json), contains these evidence paths:

| Existing diagnostic | Artifact path and limit |
|---|---|
| Retained interval distribution | `coverage.eagle.interval_seconds` and `latest_60_local_dates.eagle.interval_seconds`; intervals between retained collector timestamps, not device update times. |
| Scheduled-slot missingness | `occupied_slots`, `possible_slots`, and `coverage_pct` under both EAGLE objects. Missing-slot fraction is `(possible_slots - occupied_slots) / possible_slots` on the script's stated schedule. It is not a device failure rate. |
| Gaps and daily coverage | `max_gap_seconds`, `gap_counts`, and `daily` under both EAGLE objects. The maximum covers adjacent retained timestamps; missing boundary time needs separate treatment. |

The overall coverage denominator uses the study's common source window, including time before
EAGLE's retained history starts. The latest-window object covers 60 local dates; local dates
and 60 elapsed days can differ across daylight-saving changes. Its `rows` counts the input
frame; `valid_rows` counts observations within the window. These diagnostics partly address
the original collection and resolution criteria. They do not establish freshness or a
reliably detectable watt step. This correction adds no household sampling statistics.

## What the study can test

Non-intrusive load monitoring (NILM) separates a shared electricity measurement into estimates
of appliance use. Hart describes step signatures and the added difficulty of continuously
variable loads. [Hart's primary technical overview](https://www.georgehart.com/research/nalm.html)
supports that distinction; its historical equipment results do not predict this home's limit.

NILMTK separates data diagnostics from algorithm evaluation. It describes gaps, expected
sample counts, different sampling rates, and comparisons against appliance-level reference
data. [Batra et al., sections 3.2–3.7](https://arxiv.org/html/1404.3878) provide the primary
methodological reference. Our inference: sparse real-power records can test sustained steps,
but cannot establish that short transients or overlapping appliances remain separable.
No universal sampling rate guarantees useful NILM.

Fifteen-minute data can support some appliance-specific disaggregation.
[Zufferey, Valverde, and Hug](https://arxiv.org/abs/2104.03120) demonstrate water-heater
disaggregation from low-resolution smart-meter data. This corrects the issue's categorical
impossibility claim; it does not establish performance for this household's loads.
Coarse intervals still limit short-event timing and overlapping-load separation.

## Original requirements and the stronger option

The original issue requirements remain unchanged. The numerical rules in sections 2–4 form
an **unadopted, optional stronger protocol**, not mandatory issue blockers or an external
standard. The issue uses 300 W as an example criterion; it does not require that threshold.

| Original requirement | Additional choices in the optional protocol |
|---|---|
| At least 60 continuous days before analysis, with gap statistics | 99% overall and 95% daily fresh-slot coverage, maximum gap `5A`, and the specified slot/freshness model. |
| Actual intervals, missing-sample rate, and smallest reliably detectable step | Fixed probe bands, timing limits, and the reference-error bound `B`. |
| Controlled timestamped tests of at least three known loads, with measured accuracy | A target at or below 300 W, 10 calibration plus 60 held-out cycles per state, three contexts, and their background exposure. |
| Recommendation against a pre-stated criterion | The particular accuracy, uncertainty, background-error, step-error, and 300 W rules below. |

The original word “continuous” needs an explicit operational definition before performance
scoring. Retain the 60-day minimum and report all gaps. Justify the definition against the
measurement task; do not choose thresholds to make the known archive pass. Failure of the
optional bounded-gap rule alone does not require a new campaign under the original wording.
It also does not establish that the original continuity requirement is satisfied.

The optional target covers correct load and on/off-event detection for at least three known
loads with sustained plateaus, including small steps. A supported result stays limited to
the tested loads, contexts, and criterion. It does not establish arbitrary appliance
recognition, annual appliance energy, or detection below the smallest tested passing step.
No model or smaller numerical protocol is introduced here.

## 1. Keep intake private and preserve the source

The [cheatsheet's study fields](../DATA-SOURCES-CHEATSHEET.md#e6-high-cadence-consumption-study--optional-study-prerequisites-only)
set all three document answers to `private-only`: `high_cadence_consumption_export`,
`high_cadence_measurement_metadata`, and `high_cadence_controlled_test_log`.
Keep raw exports, manifests, reference-meter records, load labels, and event times under
gitignored `private/1-raw-data/high-cadence/`. Keep credentials only in gitignored `.env`.
These are document prerequisites; they add no mandatory `household.yaml` answers.

Use the existing frozen export where its permitted scope and fields fit the study.
For any additional export, use a fixed cutoff and retain missing values. Record its query or
export method, source code/schema revisions, units, snapshot bounds, row counts, and file
hashes in the private manifest. Keep device/account identifiers and physical locations
private. Never replace a gap with an interpolated value described as measured.

Public output needs a committed script and de-identified aggregates. Suitable candidates
include pooled interval histograms, gap summaries, test counts, confusion counts, and
uncertainty ranges. Exclude raw timestamps, circuit/device identifiers, anonymous load
activity traces, event sequences, and occupancy notes. Check each proposed output for
activity inference before publication. The path-less registry does not scan raw documents;
its declaration is not proof that their contents cannot escape.

## 2. Optional protocol: qualify the measurement chain and continuous window

Sections 2–4 apply only if this stronger option is adopted. Source checks and existing
collection diagnostics can continue before adoption. Before performance scoring, record the
chosen protocol, private manifest, acquisition schedule, quality rules, target and probe
lists, watt-bound calculation, and scoring rules. Explain the decision value of the chosen
thresholds. No held-out performance data exists in this preparation, so this proposal can
change transparently before adoption. Changes after performance labels are examined need
a new protocol version and fresh held-out evidence; do not fit criteria to known outcomes.

1. Establish the physical sensing point, units, sign, and net-versus-gross meaning from source documentation and independent checks.
   The owner states that no battery is installed. With that configuration, gross load equals
   signed grid import plus contemporaneous gross solar production. Check the solar source's
   physical timestamp and measurement meaning before that calculation. Do not treat energy
   registers as instantaneous power or five-minute solar as minute-by-minute ground truth.
2. Check timezone-aware device, collector, solar, and reference-meter clocks against a common clock before and after tests.
   Preserve UTC offsets and daylight-saving transitions. Record timestamp quantization,
   clock drift, collection latency, and source age separately. Distinguish measurement time
   from poll time; repeated power alone does not prove either freshness or staleness.
3. Distinguish device updates from planned acquisition before window selection.
   Record the device update period `D` and its documented maximum update jitter `J_D`.
   Separately freeze the planned acquisition period `A`, UTC slot anchor, and retention
   method. `A` controls expected retained observations; it need not equal `D`.
   Do not infer either period from a convenient subset of the study data.
4. Select one campaign of at least 60 consecutive elapsed days before performance analysis.
   Use UTC elapsed time for duration and the bounded-gap rules below for qualification.
   Keep all expected acquisition slots, including missing, invalid, and stale slots.
   Do not concatenate separate periods, trim failures out of the denominator, or increase
   `A` retrospectively to hide missing observations. Failure disqualifies that campaign for
   this optional protocol; it does not impose an unconditional restart under original AC1.
5. Use the existing scripted diagnostics before selecting controlled-test windows, then add missing measurement-chain diagnostics.
   Report device, retained measurement, and collector interval distributions separately
   where available. Include counts, duplicates, out-of-order and stale records, expected
   acquisition slots, missing/invalid fraction, and longest fresh-sample gap. Show overall
   and local-day coverage. Report boundaries, partial local days, and missingness by context.
   Nominal `A` and `D` define the plan; they do not supply the measured distributions.

| Proposed quality criterion | Rule and rationale |
|---|---|
| Bounded continuity | At least 99% of expected acquisition slots contain valid fresh observations overall, and at least 95% do on each complete local day. Every local day intersected by the campaign has observations. The longest fresh-sample gap is at most `5A`, including uncovered campaign boundaries. This defines one continuous collection campaign with bounded losses. |
| Denominator and slot assignment | Anchor the acquisition schedule in UTC before window selection. Count every scheduled center in the campaign: `N`. Assign at most one observation to each center within `j = A/4`, using its measurement time after the frozen clock correction. Count valid fresh occupied slots as `V`; coverage is `V/N`. All missing, invalid, and stale slots remain in `N`. Use the same schedule for complete local-day denominators, including daylight-saving changes. Keep duplicates and rejected samples in diagnostics. |
| Cadence and timing for this target | `A` is at most 120 seconds. On and off plateaus each last at least `5A`. Combined clock/timestamp uncertainty `u` is at most `A/4`. These planned-acquisition bounds give multiple retained observations per plateau and limit event-time ambiguity; short events remain outside scope. |
| Independent freshness | Each occupied valid slot needs a new source timestamp/counter or an independent update comparison. A repeat source timestamp is stale despite a new collector row. Freeze an age cap `F = D + J_D + u_source`, where `u_source` bounds the device-to-collector clock error. Check that actual source age is between zero and `F`, separately from acquisition coverage. Unknown update bounds, source age, or clock error leave freshness undetermined. Neither a new collector time nor a larger `A` proves freshness. |
| Test-window completeness | Every planned source and independent reference observation, plus aligned solar where needed, remains valid throughout each plateau and matching window. No missing, invalid, or stale acquisition slots are permitted in controlled windows. Apply a frozen quality mask, record exclusions, and obtain replacement cycles. Missing ground truth is never a detector miss or a success. |

The 99% overall, 95% complete-local-day, and `5A` limits are optional prospective project choices.
They permit occasional collection losses without accepting long unobserved periods.
The tighter overall limit constrains total loss; the daily and maximum-gap limits constrain
its concentration. These limits are not externally mandated or completed performance results.

Losses can still bias the represented solar and load contexts. Report every failed slot,
gap, and context's coverage. The optional policy keeps controlled windows complete.
The audited multi-hour gaps exceed its maximum-gap rule. That finding disqualifies the
archive for this optional policy; it establishes neither NILM failure nor a household pass.
Freshness and controlled ground truth remain unresolved. Check available source metadata
and collector behavior before treating either gap as a request for new owner measurements.

## 3. Optional protocol: obtain independent ground truth across the target range

Use ordinary appliance controls and metering rated for the appliance and connection.
Do not open panels, change wiring, attach sensors inside electrical equipment, or use
unrated adapters. Use an existing suitable reference or qualified professional support when
ordinary plug-in metering does not apply. Respect manufacturer cycling and operating limits.

1. Freeze two lists: required targets and separate minimum-step probes.
   The target list contains at least three independently known loads with measurable
   on/off changes, including at least one at or below 300 W. Every target must pass.
   The probe list supplies steps in proposed 50–100 W and 100–200 W bands, a step near
   but no greater than 300 W, and a larger positive control. A target can also serve as
   a probe if both roles are declared before held-out tests. More loads or safe ordinary operating states
   may be needed to cover these bands. Nominal bands guide selection; measured reference
   power and its error bound determine the actual step. Three large obvious loads alone
   cannot test this optional small-step scope. Freeze list membership before held-out tests;
   never relabel a failed target as a probe. A failed lower probe records a load-specific
   failure without independently vetoing passing targets. Missing probe coverage leaves
   those step/load combinations untested.
2. Record independent, timezone-aware on/off timestamps and reference watts for every cycle.
   Record anonymous load/state labels, instrument specifications, reference error bounds,
   clock uncertainty, plateaus, concurrent loads, solar context, and cycle exclusions.
   A nameplate rating alone does not measure the actual step. A software-inferred appliance
   label, including a Sense detection, is not independent ground truth.
3. Collect at least 10 calibration cycles and 60 held-out cycles for each target and declared probe state.
   Use at least three dates. Reserve 20 held-out cycles each for low-solar/night conditions,
   changing daytime solar, and concurrent-load/noisy conditions. Avoid rapid artificial
   cycling. If safe ordinary operation cannot supply the repetitions, record the shortfall.
4. Freeze calibration-derived signatures, thresholds, timing correction, and predictions before opening held-out event labels.
   An independent record keeper retains the reference log. Keep complete cycles together;
   no sample from a held-out cycle enters calibration. Randomize the safe test order and
   vary switch times within sampling periods. Score same-size or overlapping-load ambiguity
   rather than resolving it from the reference labels.
5. Collect at least 24 hours of independent reference/background coverage in each of the three contexts.
   Include periods without target transitions to measure false positives. Independent
   reference data must show whether a target actually changes state. Missing solar alignment
   permits a limited nighttime result only; it cannot support the full target recommendation.

Before held-out tests, freeze the step ladder, both role lists, and this conservative watt
bound. For each state, let `w_i` be the independent reference step magnitude in cycle `i`.
Let `e_i` be its absolute instrument error bound, including accuracy, resolution, and
calibration limits. Define `B = max_i(w_i + e_i)` across every valid held-out cycle, for both
on and off transitions and all required contexts. The maximum includes cycle variation;
do not replace it after tests with a favorable percentile or median. Unknown reference
error leaves `B` undetermined. This bounds the amplitudes of the tested cycles only, if the
reference-error bounds are valid. It does not bound the amplitude of future cycles.

Use `B` to order the tested steps and compare the smallest passing tested step with 300 W.
Report both the reference distribution and `B`, plus each load/state's outcome.
Different appliance signatures can make detection non-monotonic in watts: a larger step
need not be easier to detect. Failed lower probes and untested intervals describe only
the tested ladder. They do not establish a universal minimum appliance threshold.
Do not estimate a watt limit solely from aggregate noise or an algorithm threshold.

## 4. Optional protocol: score a fixed feasibility gate

Use the frozen acquisition period `A`, slot jitter allowance `j = A/4`, and combined
clock/timestamp uncertainty `u` to set the matching tolerance `A + 2j + u` before predictions.
Two slot deviations can separate adjacent retained observations by `A + 2j`; `u` allows
bounded clock and timestamp error between source and reference observations. This expression
does not bound arbitrary detector delay or source age. Before adoption, define the prediction
timestamp convention and check that the tolerance fits it. Only complete controlled windows
use this tolerance. Observed gaps cannot inflate it. Report measured intervals and timing errors separately;
do not change `A`, `j`, `u`, or the tolerance after missed events.

Match predictions one-to-one to ground-truth events within that tolerance. A true positive
needs the correct load/state and on/off direction. An unmatched prediction is a false positive;
an unmatched reference event is a false negative. A wrong label or direction produces both
a false positive and a false negative. Keep on and off scores separate, then show pooled
scores. Count all extra predictions, including those during negative background periods.

Use `precision = TP / (TP + FP)` and `recall = TP / (TP + FN)`. Report zero-denominator
scores as undetermined. Report false positives per observed background hour and per 24 hours,
with the exposure hours. Show results per load/state, measured step band, and context;
a pooled success cannot conceal a failing target load. Score probes in separate rows,
using the same accuracy rules to classify each tested step as passing or failing.

| Proposed performance criterion | Pass rule and reason |
|---|---|
| Detection and separation | Precision and recall each reach 0.90 for every target load/state, separately for on and off events. Each context also reaches 0.90. Correct labels test separation rather than event counting alone. |
| Repetition and uncertainty | Complete the stated repetitions for targets and declared probes. Show two-sided 95% Wilson intervals for precision and recall; both lower bounds must reach 0.80 for each direction. Before adoption, specify whether that bound pools contexts within each direction or applies separately to each context; that choice remains unresolved. Required targets must pass; failed probes retain their load-specific outcomes. Report cycle dependence and day-level variability; add a day-block uncertainty estimate before any stronger confidence claim. These are limited-sample criteria, not guaranteed confidence. |
| Background errors | No more than one false positive per 24 observed background hours in each context. This rule uses the observed rate, not its confidence bound or a guaranteed future rate. Report a Poisson rate interval and the aggregate count/exposure. A short quiet test alone cannot establish a low future error rate. |
| Step estimate | The proposed 20% limit is not scoreable until its statistic is defined. Before adoption, specify whether it means median per-event relative error or median absolute watt error divided by a stated reference. Define which events enter it and how reference-meter uncertainty and cycle variation enter the result. Freeze this definition before scoring. |
| Smallest passing tested step | Find the smallest tested target or probe step that passes the accuracy, uncertainty, background, and defined step-error rules, with the larger tested steps also passing. Its pre-defined bound `B` is at most 300 W. This is an optional consistency rule for the tested ladder, not a universal monotonic detection threshold. Report each load/state's outcome, untested intervals, and context limits. Passing one small event is insufficient. |

A **go under this optional protocol** needs its qualifying 60-day window, complete independent
ground truth, and every required-target criterion, plus a smallest passing tested step with
`B <= 300 W`. Lower probes need adequate data, but they need not pass. Their failures remain
specific to the tested loads and contexts.

A **no-go for this target scope** needs the same adequate quality and ground truth, plus
demonstrated failure of the frozen criteria.
For example, successful larger controls with failure of adequately repeated steps at or below
300 W can support a no-go for this small-load scope. A failed
required target also fails the frozen target scope; a failed lower probe alone does not.
Do not generalize that result to all NILM methods or every appliance.

Insufficient continuity, freshness, small-step coverage, repetitions, blinding, or reference
quality means **undetermined for this optional scope**, not no-go. Report failures and
uncertainty without changing the adopted gate to fit results. A different prospective scope
can support a limited recommendation if it meets the original criteria and states its limits.
It cannot silently waive the original 60-day or three-load requirements.
Any eventual public result needs a committed script and the repository's publication checks.
No main-report section appears unless the adopted feasibility gate passes.

## Acceptance criteria and exact missing inputs

| Issue #18 criterion | Current disposition | Evidence still needed |
|---|---|---|
| 1. At least 60 continuous days, with gap statistics | Partly evidenced. A frozen private export, manifest, and scripted gap statistics exist. The archive fails the optional bounded-gap rule; original continuity qualification remains open. | Justify and state the operational continuity rule without changing the 60-day minimum, then assess the archive. Check measurement freshness separately. Existing gaps alone do not impose a new Sense campaign. |
| 2. Effective resolution and smallest reliable watt step | Partly evidenced. The artifact contains retained collector intervals, scheduled-slot missingness, and gap diagnostics. No reliable watt-step result exists. | Device timing/freshness evidence and independently measured step outcomes with uncertainty. Collector intervals do not establish physical updates. |
| 3. Three known loads and measured detection accuracy | Open. No independent controlled-test record appeared in checked sources. | Independent timestamped on/off logs and suitable power references for at least three known loads, followed by measured accuracy. Choose and justify scope, repetition, and context rules before scoring; the stronger option is not mandatory. |
| 4. Recommendation against a pre-stated criterion | Partly prepared, still open. A candidate criterion exists; it is unadopted and needs the stated scoring definitions. | Adopt and state a criterion before scoring, then produce a supported recommendation. A candidate 300 W target is a project choice, not an original requirement. |
| 5. Privacy tier and standard intake fields | Addressed by the cheatsheet and matching path-less registry. | Keep actual documents private; check every future aggregate before publication. |
| 6. NILM glossary entry | Addressed in [GLOSSARY.md](../GLOSSARY.md#methods-data--statistics), with primary sources. | No additional field evidence for the definition. |
| 7. No report section unless the gate passes | Preserved. No main-report section is part of this work. | Keep that restriction until a supported go result. |

## Next work and external inputs

1. Check the existing export's reuse scope and source metadata for timing, retention, and freshness evidence.
2. Justify and adopt the continuity definition and feasibility criterion before performance scoring.
   Preserve the original 60-day and three-load requirements. Resolve the optional scoring
   ambiguities only if that stronger protocol is chosen. Do not tune rules to admit known gaps.
3. Obtain independent timestamped controlled tests for at least three actual loads if no suitable record exists.
   Physical switching and suitable reference measurements need outside participation.
   Daytime claims also need aligned solar evidence; a narrower context needs an explicit limit.
4. Score the chosen scope with a committed script and report uncertainty against the pre-stated criterion.
   Keep #18 open until every original acceptance criterion has supporting evidence.
