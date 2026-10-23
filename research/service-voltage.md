# Service voltage: monitored samples within Range A

**Evidence date: 2026-10-22. Estimated associations; measured, rounded gateway samples.**
Confidence is moderate for the sampled readings and low for service-point compliance or causal production loss.
No sampled voltage excursion appears in this window. Formal within-specification closure remains unsupported.
Causal lost energy and cost remain **not determined**: `causal_loss_kwh` and `causal_cost_usd` are `null`, not zero.

The frozen study covers 2026-07-25 through 2026-10-21 in America/Los_Angeles.
There are 86 dates with paired-leg samples across an elapsed sample span of 87.770833 days.
This exceeds the issue's minimum observation window but does not establish continuous coverage.
All study figures below come from [data/service_voltage.json](../data/service_voltage.json), produced by [analysis/service_voltage.py](../analysis/service_voltage.py).
JSON paths identify each table or finding; retained decimal places describe the computation, not instrument accuracy.

## Voltage limits and what the comparison means

ANSI C84.1 service Range A uses inclusive limits of 114–126 V for nominal 120 V service.
Nominal 240 V service uses 228–252 V.
The source is PG&E's *Voltage Tolerance Boundary*, July 2018, pages 1–2, Figure 1 and Table 1.
It distinguishes service voltage from equipment utilization voltage and applies these limits to sustained levels, rather than momentary events.
Its explanatory text describes sustained voltage as usually longer than two minutes. [PG&E source](https://www.pge.com/assets/pge/docs/contact-us/report-an-issue/Voltage_Tolerance.pdf)

This is a utility interpretation, not the full ANSI standard.
The source does not identify the ANSI edition; this study did not obtain the full standard or establish a current-edition compliance test.
The [archived SDG&E interconnection handbook](https://webarchive.sdge.com/sites/default/files/documents/DistributionInterconnectionHandbook.pdf), revised 2015-10-21, provides historical context only.
It is not current tariff authority for this finding.

## Observed voltage and frequency

Each selected voltage channel has 24,366 samples.
The source preference applies separately to each channel: available local values win, and the mirror fills missing local values.
Complete channel masks govern coverage and models; they do not remove available individual-channel excursion evidence.

| Channel | Minimum V | p01 V | p05 V | Median V | p95 V | p99 V | Maximum V |
|---|---:|---:|---:|---:|---:|---:|---:|
| Leg 1 | 117.900 | 119.900 | 120.600 | 122.200 | 123.400 | 123.800 | 125.000 |
| Leg 2 | 118.100 | 120.165 | 120.700 | 122.200 | 123.200 | 123.500 | 124.800 |
| Line | 237.600 | 240.300 | 241.500 | 244.400 | 246.500 | 247.100 | 249.800 |

Source: `voltage.{leg1_v,leg2_v,line_v}`. Percentiles describe sampled values only.
All three channels have 0 outside-range samples, 0% outside-range sample share, and 0 estimated represented seconds outside Range A.
These duration estimates cap each sample at the scheduled cadence and stop before gaps.
They do not measure sustained event duration or count missing time as compliant time (`duration_method`).

Absolute leg difference has median 0.4 V, p95 1.0 V, and maximum 3.4 V.
Selected line voltage minus the leg sum ranges from -0.1 to 0.1 V.
This arithmetic check does not establish calibration or the physical measurement point.

Frequency spans 60.00–60.12 Hz across 3 reported values; its p99 is 60.00 Hz (`frequency`).
This is a measurement-chain sanity check only. It makes no grid-frequency quality or compliance claim.

## Coverage and missing evidence in time

Coverage uses complete required fields on each source's scheduled grid, across the whole declared date window.
Voltage coverage needs both legs, line voltage, and frequency; joined coverage needs both legs, solar, and inferred gross load.
Voltage, mirror, generation, and joined slots use five-minute cadence; EAGLE uses one minute; modeled weather uses one hour.
A maximum gap is the time between valid observations; it excludes missing time before the first or after the last observation.
Missing seconds include all unrepresented slots within the declared window.

| Series (`coverage` key) | Valid slots / possible | Coverage % | Dates with samples | Maximum gap s | Missing s |
|---|---:|---:|---:|---:|---:|
| `meter` | 22,716 / 25,632 | 88.623596 | 81 | 234,600 | 874,800 |
| `mirror` | 24,320 / 25,632 | 94.881398 | 86 | 234,600 | 393,600 |
| `combined_voltage` | 24,366 / 25,632 | 95.060861 | 86 | 234,600 | 379,800 |
| `eagle` | 105,685 / 128,160 | 82.463327 | 83 | 37,500 | 1,348,500 |
| `generation` | 7,977 / 25,632 | 31.121255 | 45 | 24,900 | 5,296,500 |
| `weather_poa` | 2,136 / 2,136 | 100.000000 | 89 | 3,600 | 0 |
| `joined_voltage_solar_grid` | 20,251 / 25,632 | 79.006710 | 81 | 234,600 | 1,614,300 |

Combined voltage has 27 gaps above one-and-a-half scheduled intervals.
Of these, 22 gaps exceed cadence through fifteen minutes, and 5 exceed one hour.
Its interval median, p95, and p99 are each 300 seconds.
The full daily coverage series and each source's gap distribution remain in `coverage`.

For the latest 60 local dates, combined voltage occupies 16,417 of 17,280 slots: 95.005787%.
Only 58 dates contain voltage samples; 2026-09-05 and 2026-09-06 contain none.
The longest gap is 234,600 seconds.
The joined voltage/solar/grid coverage is 84.664352% in that same window.
Sources: `latest_60_local_dates.{combined_voltage,joined_voltage_solar_grid}`.

The selected series contains 22,716 local gateway rows and 1,650 mirror rows.
They form one measurement chain. Across 22,670 overlap rows, both legs and frequency match exactly.
One line-voltage comparison differs by 2.7 V; the local value wins.
Sources: `source_selection`, `source_field_selection`, and `source_overlap`.

## Measurement-chain limits

The stored evidence identifies an Enphase IQ Gateway data path, not a checked gateway model or firmware version.
The collector selects the first meter with numeric voltage and its first two channels for the legs.
The selected meter's physical role, service-point relationship, calibration, and channel accuracy remain unknown (`measurement_chain`).
The [Enphase IQ Envoy data sheet](https://media-store.enphase.com/productattach/e/n/enphase_iq_envoy_datasheet.pdf), dated 2021-03-29, specifies production-meter and consumption-meter accuracy.
Those specifications do not establish an accuracy bound for this study's selected voltage channel or prove that this installation matches that model.
The [current IQ Gateway data-sheet URL](https://enphase.com/download/iq-gateway-datasheet) returned HTTP 403 during this check; its indexed revision is dated 2025-06-06.
No model-specific voltage accuracy claim follows from that inaccessible document.

Voltage rounds to 0.1 V; frequency rounds to 0.01 Hz.
Resolution is not accuracy.
The poller retains only 100 < V < 150 for legs and 180 < V < 300 for line voltage.
Its frequency acceptance mask is 55 < Hz < 65.
Values outside these strict acceptance masks become missing evidence, including possible extreme events.
Five-minute spot samples also miss shorter events, even if every scheduled sample exists.

On 2026-07-25, mirror fields mix old weather meanings with new voltage meanings.
The semantic mask excludes that whole date's mirror voltage and frequency while retaining the solar and consumption fields.
A non-null voltage test cannot resolve that ambiguity (`measurement_chain.ambiguous_mirror_deployment_date`).

## Load, solar, and time associations

Gross load equals signed EAGLE grid power plus simultaneous solar power, using the owner's statement that no battery is installed.
Import is positive and export is negative. Exact slot alignment does not prove acquisition simultaneity or device freshness.
The model excludes 505 negative inferred-load rows.
Reported mirror consumption does not serve as gross-load truth (`alignment.mirror_consumption_semantics_check`).

The regressions condition on same-day effects and local-clock harmonics.
Their 95% intervals use ordinary least squares with CR1 errors clustered by local date and Student-t critical values.
The following slopes describe conditional associations in V/kW (`voltage_conditioned_associations`).

| Response and predictor | Slope V/kW | 95% interval | Samples | Dates |
|---|---:|---:|---:|---:|
| Leg 1 / gross load | -0.158341 | -0.163767 to -0.152915 | 19,746 | 81 |
| Leg 2 / gross load | -0.118332 | -0.122585 to -0.114079 | 19,746 | 81 |
| Leg 1 / solar | 0.052278 | 0.016538 to 0.088018 | 19,746 | 81 |
| Leg 2 / solar | 0.042817 | 0.009713 to 0.075920 | 19,746 | 81 |
| Nighttime leg mean / net import | -0.138830 | -0.143293 to -0.134366 | 9,938 | 81 |

Higher load associates with lower voltage, while solar associates with higher voltage after these controls.
The nighttime check reduces shared-generation coupling. It does not establish the location of the voltage drop.
Neither regression separates utility variation from voltage drop or rise in premise conductors.
The artifact also characterizes local hour, month, solar, inferred load, and signed grid power (`characterization`).
No observed excursion group exists for an excursion-versus-normal comparison.

## Production association and unresolved source disagreement

The production model uses 452 daylight hours across 76 dates.
Each hour needs at least 11 common five-minute slots and modeled plane-of-array irradiance of at least 100 W/m².
Plane-of-array irradiance describes sunlight on the panel plane; it is modeled here, not measured on site.
The controls include irradiance, its square, air temperature, their interaction, local-clock harmonics, inferred gross load, and a quadratic date trend.

Open-Meteo defines hourly solar irradiance as the preceding-hour mean and air temperature as instantaneous at the hour label.
The generator aligns those meanings before it compares production. [Open-Meteo hourly definitions](https://open-meteo.com/en/docs/historical-weather-api#hourly-parameter-definition)
The base model includes 49 provisional weather hours and 0 hours with unknown weather provenance.

| Model | Voltage slope W/V | 95% interval W/V | Hours |
|---|---:|---:|---:|
| Base | 130.996971 | -112.409401 to 374.403343 | 452 |
| Without inferred load | 4.049368 | -133.678389 to 141.777125 | 452 |
| Final weather only | 74.253333 | -156.895587 to 305.402252 | 403 |
| Weather shifted −1 hour | 31.782372 | -250.622057 to 314.186801 | 452 |
| Weather shifted +1 hour | 187.856506 | -69.688315 to 445.401326 | 448 |

Sources: `production` and `production_sensitivities`. Intervals use the same daily-cluster method as the voltage regressions.
**The conditional models do not establish an inverse voltage-production association at the stated uncertainty level.**
This finding does not prove no relationship or zero production loss.
The base model's hourly mean leg voltage spans 119.483333–123.637500 V, with 0 hourly means outside Range A.

The expected-power reference leaves each local date out in turn; its prediction uses weather, clock, and date controls without voltage or load.
It scores 452 hours with 699.117391 W root mean square error and 1 negative prediction.
This empirical reference is not checked unconstrained production.
Hourly sampled power means are not energy-meter totals; integrating prediction errors would not establish lost kWh.

The generation poller has median source lag 1,200 seconds and 5,720 repeated source slots.
Even after exact source-time alignment, 7,977 finite pairs show a mean poller-minus-mirror difference of -2,513.517864 W.
The poller reports zero while the later mirror reports positive production in 4,068 rows.
The cause remains unresolved; timing/revision histories do not establish a parser error or a physical production loss.
The model uses the historical main mirror and excludes the poller copy as production truth (`alignment.generation_source_reconciliation`).
The mirror itself lacks independent production calibration.

Generation can change voltage as well as respond to it.
Weather error, changing shade or soiling, shared production/load data, omitted upstream conditions, and dependence across dates limit causal inference.
Persistent losses could also enter the empirical reference.
Neither coefficients nor their confidence intervals justify a loss mechanism, kWh total, or dollar cost.
A future priced counterfactual needs canonical rates and aligned interval re-billing (`pricing`).

## Disposition and remaining acceptance evidence

**Continue monitoring. Keep issue #19 open.**
This window documents no Range A excursion to support alleging a utility voltage violation.
The conditional models also detect no inverse voltage-production association at the stated uncertainty level.
These findings support a qualified decision now, while formal service compliance and causal loss remain unproved (`disposition`, `production`).
The existing observation span meets AC1; no new observation window is needed to report these results.

SDG&E accepts reports of suspected voltage problems through its Call Center at 1-800-411-SDGE.
Suspected symptoms or later events can justify an investigation request even when this dataset shows no excursion.
Its published process starts with an Electric Troubleshooter, who can measure service-entrance voltage with the customer's load operating.
The process can include a recording monitor and a Field Engineer's review.
SDG&E can supply service-point evidence during that process; customer calibration is not a prerequisite to request investigation.
Calibration, sensing location, and event duration matter before asserting a proved compliance breach.
[SDG&E voltage investigation process](https://www.sdge.com/businesses/savings-center/services/power-quality)

The table maps every acceptance criterion from [issue #19](https://github.com/ookla-ariel-ride/SDGE-Analysis/issues/19).
“Complete” describes the stated evidence scope, not formal compliance or causal attribution.

| AC | Requested evidence | Status and remaining evidence |
|---|---|---|
| 1 | At least 60 days before conclusions; coverage and gaps | Complete for observation window; all-source and latest-window coverage appear above. Gaps remain explicit. |
| 2 | Per-leg distribution and time outside cited Range A | Complete for sampled distributions and estimated represented duration. Measured sustained excursion duration remains unavailable. |
| 3 | Correlate excursions with time, load, solar to separate causes | Partial: conditioned associations and strata exist; no sampled excursion group exists for attribution. Two-location measurements could help answer the remaining location question. |
| 4 | Production impact in kWh and dollars, or a clear no-relationship finding | Partial: no inverse association is detected under the tested models at the stated uncertainty level. This is not proof of no relationship; causal kWh and dollars remain null. AC4 permits a no-relationship alternative rather than requiring causal-loss proof for every outcome. It does not require proof of zero loss. |
| 5 | Frequency as measurement-chain sanity | Complete within the stated rounding, filter, and calibration limits. |
| 6 | Utility complaint or close as within specification | Literal binary unresolved: no documented excursion supports alleging a violation, and formal within-specification closure remains unproved. A suspected problem can still justify an investigation request. Keep #19 open unless the owner explicitly changes the acceptance scope. |
| 7 | Explicit measurement accuracy and confidence | Complete disclosure: accuracy and physical sensing point are unknown. Confidence is moderate for samples and low for compliance or causal loss. |
| 8 | ANSI C84.1 Range A glossary entry | Complete in [GLOSSARY.md](../GLOSSARY.md#solar--hardware), with source and sampling/calibration limits. |

Further investigation can target a specific unresolved question; it is not a compulsory measurement campaign for every outcome (`missing_evidence`).
Optional checks of existing sensor records can establish which meter/channel the collector selects and whether records identify its location or accuracy.
Checks of source timestamps and revision histories can also test the unresolved production-source disagreement before anyone assigns it a loss mechanism.

| Unresolved question | Possible evidence and its purpose |
|---|---|
| Does service voltage breach the applicable limits? | Service-point measurements with known accuracy and event duration can support a compliance assessment. SDG&E can collect such evidence during an investigation. |
| Does voltage variation originate upstream or within the premises? | Synchronized service-point and inverter-side readings with known accuracy can help separate upstream variation from conductor drop or export rise. |
| Do suspected voltage events cause inverter trips or curtailment? | Inverter event records and faster synchronized voltage/production can test event response and duration missed by five-minute samples. |
| Is there causal production loss to quantify? | An independently checked production reference can support a counterfactual. On-site irradiance and module temperature are possible inputs. Causal loss still needs identification; dollars need aligned re-billing. |

These options do not prevent the current sampled finding or a request for utility investigation.
More data from the same measurement chain alone cannot establish the unknown sensing location, accuracy, or causal attribution.
Raw readings, exact event times, device identifiers, and installation-specific measurement records remain private under [the intake ledger](../DATA-SOURCES-CHEATSHEET.md#e5-service-voltage-telemetry-optional).
All external sources above were checked on 2026-10-22; their publication dates differ from this evidence date.
