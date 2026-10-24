# SB 868: qualifying plug-in PV needs no utility approval; equipment evidence remains open

**Decision as of 2026-10-23: WAIT for a grid-parallel purchase.** Exact system certification, household connection, supported performance, and full installed costs remain unresolved. SB 868 bars prior SDG&E approval for qualifying devices once its exemption takes effect on January 1, 2027. NEM/CEA compensation affects the economics; it is a separate question from statutory eligibility.

A selected-appliance pilot can answer useful questions with a borrowed or returnable supported battery if the owner later chooses that pilot. The household owns no battery. Selected-load measurements can improve the desk evaluation without a battery purchase or field pilot. Conditional modeled price ceilings do not establish a purchase budget.

This study separates electricity savings from backup. It does not authorize purchases, utility applications, or electrical work. A product-specific purchase recommendation remains deferred. The report publishes conditional model results. The existing report and glossary include the supported tariff qualifications. Issue #304 remains incomplete where the acceptance map names missing evidence.

## 1. The household already has solar and owns no battery

The owner confirmed **no installed battery** on 2026-10-22. Every battery here is prospective. The Powerwall is a hypothetical benchmark.

| Existing equipment or service | Evidence used |
|---|---|
| Rooftop PV | 30 Panasonic 335 W modules; 10.05 kW DC |
| Rooftop inverters | 30 Enphase IQ7X-96-2-US; 9.45 kW continuous AC |
| Electricity service | NEM 2.0; CEA Clean Impact Plus; EV-TOU-5 |
| EV charging | Two EVs; documented 11.5 kW charger; one documented 60 A charger circuit |
| Service constraints | 175 A main; 200 A bus; 50 A PV breaker; constrained circuit space |

Source: the documented project inventory, with public equipment values also recorded in [`installed_baseline` and `provenance`](../data/portable_solar_storage.json). Circuit labels have partial confidence. Equipment ratings do not establish spare service capacity or a permitted connection.

The inventory records a December 2019 permission-to-operate date. The original approval's capacity field and interconnection agreement are absent. Current panel DC capacity, inverter AC capacity, and utility-approved capacity are different quantities. Original capacity helps determine conventional expansion headroom under §4. It is not an SB 868 eligibility condition.

## 2. Five approaches answer different questions

| Approach | Useful comparison | Decision now |
|---|---|---|
| A. Plug-in PV without storage | Daytime import offsets versus the value of incremental exports | WAIT for exact certification, supported code-compliant connection, site yield, and costs; compare settlement scenarios separately |
| B. Plug-in PV with modular storage | Later import offsets versus the same battery without new panels | WAIT for exact combination certification, connection, controls, and costs; shared PV-plus-discharge cap applies to the modeled system |
| C. Storage using existing solar or cheap grid hours | Avoid new-panel cost; price charging and forgone existing export credit | Study a lawful storage connection independently; no blanket SB 868 battery exemption |
| D. Portable battery with directly connected appliances | Only measured selected-appliance energy and backup needs | Conditional measurement pilot; no household receptacle backfeed |
| E. Professionally connected home battery | Larger supported loads and transfer/backup capability versus a hypothetical Powerwall | WAIT for a design, exact controls, quote, and NEM treatment |

The model prices hardware **after the free EV schedule change**. That free change saves a modeled **$1,222.26/year**. Hardware never receives credit for that saving.

## 3. Law: qualifying devices need no prior utility approval from January 1, 2027

SB 868 is Chapter 985, signed/filed September 30, 2026. The Governor lists the signature. The official text endpoint returned HTTP 403; the chaptered text came from the disclosed [LegiScan mirror](https://legiscan.com/CA/text/SB868/2025). The ordinary **January 1, 2027** effective date follows from the [Secretary of State's rule](https://www.sos.ca.gov/administration/bill-chapters). The exemption is not effective on this study date. [Governor's signed-bill list](https://www.gov.ca.gov/2026/09/30/governor-newsom-issues-legislative-update-9-30-2026/)

Section 8530(c) defines qualifying photovoltaic equipment or combinations through six conditions. They cover receptacle connection, onsite-consumption intent, current electrical-code compliance, NRTL plug-in-PV-system certification, and certified outage isolation. The limit is **1,200 W aggregate AC per dwelling**, across qualifying devices. NRTL means nationally recognized testing laboratory. [Chaptered text, §8530(c)](https://legiscan.com/CA/text/SB868/2025)

Section 8531(a) exempts qualifying devices from all interconnection requirements imposed by state law, the CPUC, and utility rules, including interconnection agreements. Section 8531(b) bars prior utility approval, device-related fees, and extra utility-required controls or equipment. Utilities may require simple online notification of address, make, model, and size. **Notification cannot require approval.** [Chaptered text, §8531(a)–(d)](https://legiscan.com/CA/text/SB868/2025)

Existing rooftop PV does not disqualify a household under these conditions. The hardwired rooftop array is distinct from the receptacle-connected devices that the definition covers. Original rooftop capacity and an individualized utility letter are not additional statutory eligibility conditions. Exact export compensation and battery-only qualification remain **not determined**. These questions do not remove the approval exemption. [Chaptered text, §§8530–8531](https://legiscan.com/CA/text/SB868/2025)

Section 8531 repeals January 1, 2030. Section 8532 then restricts sales of receptacle-connected PV devices that fail its specified code, certification, and isolation conditions. Neither automatic shutdown nor permanent exemption follows. Applicable future equipment and account treatment, and any compliance cost, remain unknown. Local code, fire, siting, mounting, and circuit checks remain separate. Recheck the [official chapter](https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=202520260SB868) before deployment.

No SB 868 notification form or implementation notice appeared in the checked SDG&E pages and official-domain research. That limited search does not prove absence or add an approval requirement. A vendor article is not authority for broader permit relief.

## 4. NEM protection needs the exact use and connection

The current compiled [NEM-ST tariff](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE) was retrieved on 2026-10-22. Sheet dates vary. SC 9(b), sheet 27, effective February 14, 2024, governs conventional expansion. Its limit is the greater of **10% of original interconnected nameplate capacity or 1 kW**, subject to onsite-load limits. Larger additions have separate-tariff or other authorized pathways.

The 1 kW floor allows a small-addition screen without the original capacity figure. Prior expansion can consume that allowance, so 1 kW of remaining headroom is not established. Original capacity, the applicable capacity method, expansion history, and onsite load determine the exact remaining allowance. This conventional rule is separate from SB 868's 1,200 W portable-device limit and approval exemption. [NEM-ST, SC 9(b)](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

SC 9(d), sheet 28, effective September 3, 2024, preserves the remaining legacy period for storage-only additions. Its **SGIP exception matters**. SGIP means Self-Generation Incentive Program. Under this exception, specified reservations accepted on/after June 4, 2024 require NBT transition, except the stated equity categories. An incentive cannot enter the purchase case without its tariff consequences. [NEM-ST, SC 9](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

SC 10 measures separate-inverter storage by AC nameplate power. Solar-paired storage at 10 kW or less uses small-storage rules, including an optional monthly export-credit cap. Larger storage needs the applicable metering/control route. Grid-charged exports do not receive unrestricted NEM credit by assumption. The printed 150%-of-PV limit suspension ends August 16, 2025; a later extension was not established. [NEM-ST, SC 10](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

[Rule 21](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=96&utilId=SDGE) covers NEM and non-export facilities. Parallel operation exceeds 60 cycles/one second. Storage charging remains subject to load rules. **Non-export alone does not remove interconnection requirements.** Transfer-isolated wiring needs its own topology check. Use the [SDG&E installation process](https://www.sdge.com/solar/solar-and-battery-installation-center) outside an established exemption.

[CEA Personal Impact](https://thecleanenergyalliance.org/personal-impact/) accumulates generation charges and credits through the annual relevant period. Its published net-surplus rate is $0.06/kWh; direct payment starts at $100. Those terms do not establish SB 868 compensation. This study keeps avoided CEA imports, forgone existing-PV credit, and hypothetical new-export credit separate. San Diego Community Power programs do not establish this CEA household's eligibility.

The tariff prices NBC from consumption net of exports within each metered interval, without cross-interval credit offsets. The canonical model uses [`rates.nbc_kwh`](../analysis/rates.py) to sum `max(physical imports − physical exports, 0)` within each fifteen-minute interval. The [NBC reconciliation](../data/nbc_bill_reconciliation.json) supports this numerical basis across 16 covered bill periods. Exact utility interval mapping, source precision, grouped rounding, historical rate segments, and midnight cutoffs remain unresolved. Uncredited physical exports still enter modeled NBC netting; their compensation and interconnection eligibility remain separate questions. [NEM-ST, SC 1(f) and 3(d)](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

## 5. Exact system certification remains unproved

UL's January 8, 2026 [plug-in solar framework](https://www.ul.com/news/ul-solutions-debuts-testing-and-certification-framework-safer-plug-solar-across-united-states) uses UL 3700. An inverter's UL 1741 claim, battery's UL 1973 claim, or storage claim under UL 9540 does not establish plug-in-PV-system certification.

The [UL Product iQ search](https://productiq.ulprospector.com/en/search?term=EcoFlow) was inaccessible. The [CSA directory](https://www.csagroup.org/testing-certification/product-listing/) yielded no established exact-model result. The [Intertek directory](https://ramuk.intertekconnect.com/WebClients/ITS/DLP/products.nsf/$$Search?OpenForm) search returned EcoFlow records, but no checked scope established these candidate systems under UL 3700. This is a limited search, not proof that no certificate exists. A [connector listing](https://ramuk.intertekconnect.com/WebClients/ITS/DLP/products.nsf/4c8700f3b75987a08525777700583333/f495aa2862b2cfdd86258a8600343a37?OpenDocument) cannot establish the complete system's scope.

The certificate needs the NRTL's identity, file number, current directory record, and exact U.S. models. Check the component combination, connections, output/isolation conditions, firmware, and multi-unit use against the certificate's scope and installation instructions. The statute permits UL or an equivalent NRTL; it does not name UL 3700 as the exclusive route. **No candidate receives an unconditional eligible label.**

### Prospective SB 868 classification for STREAM uses

These classifications concern use after the exemption's effective date. They do not authorize installation today.

| Exact use | Classification | Missing evidence or reason |
|---|---|---|
| STREAM Ultra EF-EA-HD-U2K-1K2U plus PV through the supported receptacle connection | **Not determined** | Exact system certificate and circuit/code check; billing treatment belongs in the separate economic assessment |
| Same STREAM, battery-only, charged from existing rooftop/grid AC, with receptacle feed-in | **Not determined** | Photovoltaic statutory scope and independent storage/interconnection treatment |
| STREAM Ultra plus Microinverter, or multiple Ultra units within the dwelling cap | **Not determined** | Combination certificate and aggregate control; no separate allowance per unit |
| U.S. STREAM Microinverter plus panels, without storage | **Not determined** | Exact model/certificate and installation scope; the bundle store SKU is not a certificate |
| STREAM hardwired, or above the aggregate statutory cap | **Ineligible for this exemption** | Receptacle/output condition fails; ordinary connection pathway needs a design |

### DELTA uses need the appropriate appliance or transfer pathway

| Exact use | SB 868 classification | Connection assessment |
|---|---|---|
| DELTA Pro 3 EFD521 U.S.; supported appliance outputs | **Ineligible/outside scope** | Direct-appliance operation; only connected loads count |
| DELTA Pro Ultra EFYJ751-PCS-US + EFYJ751-BP; supported appliance outputs | **Ineligible/outside scope** | Direct-appliance operation; only connected loads count |
| DELTA Pro Ultra X EF-DU-001 + EFYJ751-BP; supported appliance outputs | **Ineligible/outside scope** | Direct-appliance operation; only connected loads count |
| Any DELTA output connected to an ordinary household receptacle for backfeed | **Ineligible as evaluated** | No supported feed-in connection or qualifying system certificate established; do not use |
| These DELTA models with supported inlet/transfer equipment or smart panel | **Ineligible for the receptacle-PV exemption** | Check the actual topology for electrical, permit, and isolation conditions; utility interconnection rules depend on whether the system operates in parallel |

“Ineligible/outside scope” does not mean ordinary supported appliance use is illegal. Transfer equipment does not become exempt plug-in PV merely because it uses a cord.

## 6. U.S. equipment evidence: ratings are not measured usable energy

Product observations below date to **2026-10-22**. STREAM, DP3, and DPU price/stock variants were rechecked on **2026-10-23**, with unchanged observations. Manual dates identify the checked document, not current firmware. Regional availability and accessories need separate checks.

| Candidate | Exact U.S. identity and checked manufacturer manual | Nominal capacity; continuous output |
|---|---|---|
| STREAM Ultra | EF-EA-HD-U2K-1K2U; store EFSTREAMULTRA800W-US; [36-page manual, 2025 copyright](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/manual/1753783795065/EcoFlow%20STREAM%20Ultra_User%20Manual.pdf); [NA safety V1.0](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/upload/1747017077422/Online-EcoFlow%20STREAM%20Ultra_Safety%20Instructions_en%2Bes_V1.0_NA.pdf) | 1.92 kWh; 120 V/60 Hz; 360 W default feed-in, conditional 800 W single-unit |
| DELTA Pro 3 | EFD521 U.S. entries; store EFDELTAPRO3-US; [45-page manual, export 2025-09-25](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/manual/1758794589811/EcoFlow%20DELTA%20Pro%203%20Portable%20Power%20Station_User%20Manual.pdf) | 4.096 kWh; 4 kW; 120/240 V |
| DELTA Pro Ultra | EFYJ751-PCS-US inverter + EFYJ751-BP battery; store DPU-US; [51-page manual, metadata 2025-07-25](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/manual/1753439627172/EcoFlow%20DELTA%20Pro%20Ultra%20User%20Manual.pdf) | 6.144 kWh per battery; 7.2 kW; 120/240 V |
| DELTA Pro Ultra X | EF-DU-001 inverter + EFYJ751-BP batteries; store DPUX-US; [34-page manual, export 2025-10-15](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/manual/1760495654551/EcoFlow%20DELTA%20Pro%20Ultra%20X_User%20Manual.pdf) | Two-battery bundle 12.288 kWh; 12 kW; 120/240 V |

The STREAM manual explicitly sets **360 W default feed-in**. Higher feed-in needs the documented dedicated-circuit or breaker/wire conditions plus app configuration. It describes **800 W single-unit** and **1,200 W combined** limits. These are distinct from battery capacity. STREAM battery charge is 1,050 W; grid AC input is 1,200 W; PV input is 2,000 W. Its U.S. voltage/model evidence does not establish a qualifying certificate. The NA safety instructions exclude connection to a load center with multiwire branch circuits. [NA Safety V1.0, page 1, instruction 7](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/upload/1747017077422/Online-EcoFlow%20STREAM%20Ultra_Safety%20Instructions_en%2Bes_V1.0_NA.pdf#page=1) Whether this household meets that condition remains unknown. [STREAM manual, grid connection and technical specifications](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/manual/1753783795065/EcoFlow%20STREAM%20Ultra_User%20Manual.pdf)

### Controls that can change savings or backup

| Equipment | Supported evidence | Still not determined |
|---|---|---|
| STREAM | Scheduled/fixed output; sensor-based control; local Bluetooth has restrictions; full system control needs internet | Offline schedule persistence, phase/CT coverage, exact output conditions, usable AC capacity, measured losses/standby |
| DELTA Pro 3 | Reserve/timers/output memory; local Bluetooth; supported transfer/inlet or SHP2 path | Exact schedule persistence; supported circuit coverage; U.S. high/low-voltage outputs cannot operate simultaneously |
| DELTA Pro Ultra | TOU; charge ceiling 50–100%; discharge floor 0–30%; reserve and Storm Guard can override schedules | Offline TOU persistence and interaction with the selected backup mode |
| Ultra X / SHP3 / Gateway | Operating modes and scheduled tasks; compatible inlet/cables needed; forecast feature uses Energy Master | Subscription price, offline behavior, exact installation controls |
| All candidates | Manufacturer nominal ratings and cycle claims | Load-dependent AC efficiency, usable AC kWh, minimum useful load, measured standby, firmware response, warranted throughput allowance |

Control sources are the linked manuals and [Gateway FAQ](https://us.ecoflow.com/products/smart-gateway). SHP3/Gateway supports DPU/Ultra X with specified hardware; **it does not support DP3**. DP3's supported home path includes SHP2 or an appropriate inlet/transfer arrangement. DPU supports the documented 30 A transfer and 50 A Hub arrangements. These options need a complete connection design.

A whole-meter model does not prove both split-phase legs are accessible. The STREAM [U.S. page](https://us.ecoflow.com/products/stream-ultra) places its stated Shelly arrangement on the monitored phase. The model does not resolve CT feedback, phase imbalance, surges, or competing control transients. STREAM premium AI features also need an app subscription; its price remains unknown.

### Advertised equipment prices and stock

The live manufacturer store JSON distinguishes available variants from hidden “Sold out” page images. An `available` flag does not promise delivery or California installation eligibility.

| Equipment/configuration | Advertised price | Store status on 2026-10-22; primary source |
|---|---:|---|
| STREAM Ultra; Ultra + Microinverter | $1,399; $1,599 | Unavailable; page states March 2027 restock. [Page](https://us.ecoflow.com/products/stream-ultra), [JSON](https://us.ecoflow.com/products/stream-ultra.js) |
| DP3 base; DP3 + extra battery | $2,799; $4,459 | Cited variants available. [Page](https://us.ecoflow.com/products/delta-pro-3-portable-power-station), [JSON](https://us.ecoflow.com/products/delta-pro-3-portable-power-station.js) |
| DPU base; DPU + SHP2 | $4,199; $5,499 | Cited variants available. [Page](https://us.ecoflow.com/products/delta-pro-ultra), [JSON](https://us.ecoflow.com/products/delta-pro-ultra.js) |
| Ultra X base; + Gateway; + SHP3 | $7,999; $9,899; $10,999 | Cited variants available. [Page](https://us.ecoflow.com/products/delta-pro-ultra-x), [JSON](https://us.ecoflow.com/products/delta-pro-ultra-x.js) |
| Gateway alone, store EFATS-US | $1,999 | Cited variant available. [Page](https://us.ecoflow.com/products/smart-gateway), [JSON](https://us.ecoflow.com/products/smart-gateway.js) |

These are equipment observations, not installed quotes. The STREAM page's regional notices do not establish California permission. No address-specific checkout or installer quote supports this table.

The [U.S. warranty policy](https://us.ecoflow.com/pages/warranty-policy), updated October 20, 2025, lists ten years for STREAM/Microinverter and SHP2/SHP3/Gateway. It lists five years for DP3 and DPU/Ultra X inverter/battery. Normal wear and removal/reinstallation/integration costs are excluded. Stated cycle life is not a separately established throughput/capacity-retention warranty. Service, shipping, and labor exposure still need a written quote.

The advertised $0 [Live Installation Support](https://us.ecoflow.com/pages/live-installation-terms-conditions) is remote video guidance. It excludes onsite work, tools/materials, and PV wiring guidance. It does not supply a local design, permit, or compliance approval.

## 7. Model basis: comparable deltas, not actual annual bills

All numerical model results come from [`data/portable_solar_storage.json`](../data/portable_solar_storage.json), generated by [`analysis/portable_solar_storage.py`](../analysis/portable_solar_storage.py). Scenario IDs below select rows in `scenarios`; economics are each row's `economics`. The complete JSON holds every monthly bill, component, flow, and scenario.

The comparison uses **2025-07-24 through 2026-07-23**, **35,040 meter intervals**, and frozen **June 2026 EV-TOU-5/CEA Clean Impact Plus rates**. It does not forecast 2027 prices. The unchanged-behavior modeled bill is $4,901.38; after the free fix it is $3,679.11. These are model levels, not actual billed costs.

Every bill and savings figure uses positive physical interval net for NBC, as §4 explains. Actual bills support that basis; they do not establish exact utility billing replication. These calculations remain conditional modeled estimates.

The [bill corpus boundary](../data/bill_corpus_boundary.json) covers service dates from 2024-05-25 through 2026-09-25, including all 365 study days. The separate NBC comparison covers 2025-06-27 through 2026-09-25. Those broader records do not extend this model's fixed comparison year. Calendar-month model buckets differ from statement periods, and frozen rates differ from historical rates. The documented historical residual remains unexplained; see [TECHNICAL.md, historical bill reconciliation](../TECHNICAL.md). Hardware deltas compare complete modeled bills; they do not repair that residual.

### Reproduction evidence

| Input/evidence | SHA256 recorded in `provenance` |
|---|---|
| Staged meter series | `c86bec4088e8739198402d9db5989156c2f7561aa2e6a4eaab73b8e591a450e8` |
| SAM A | `13a4f04d28852871adaeb0de97fcefe29ef3f11e90d74bb067e273f7642cd637` |
| SAM B | `98dd23beb5f50f2996c1c5a01ff59243bf3a333fb748bad01031f19a0353b094` |
| Canonical rates | `5db04d27ff6a960a7c0319539de1eb526cca3b3ae615a6c3922028bcaf4a3b2d` |
| Public equipment parameters | `3a2965954f9127925edf79f7855b0e1342ad9d07fb8b25bceae80905779583b8` |

The JSON also hashes both production evidence artifacts and the canonical sizing reference. Raw meter/household records stay private.

The PV proxy follows existing rooftop production. It is not a measured portable south-facing system. A two-hour delay is a hypothetical west/delayed sensitivity, not an irradiance model. Shade and winter reductions are assumptions. Both DST transition dates have zero added PV; retained hourly production spreads across meter rows. Every meter row represents 0.25 elapsed hours. Site-specific orientation, mounting, shade, and DC-coupled clipping recovery remain unavailable.

The new base battery assumptions are **20% reserve, 85% round-trip efficiency, and 10 W continuous AC standby per modeled controller**. Those are assumptions, not product measurements. The simple schedule charges allowed surplus solar and super-off-peak grid energy; it discharges off/on-peak. It does not claim optimality or proven firmware support.

The physical model keeps imports, existing exports, added exports, direct PV use, charging sources, losses, curtailment, and SOC separate. SOC means stored DC energy. Each controller uses remaining load and solar. PV plus battery discharge shares the stated new-system AC ceiling. Batteries do not intentionally export. Annual SOC converges before billing; no initial energy subsidy enters savings.

### The preliminary optimistic screen answers a different question

The canonical price-aware screen assumes **90% efficiency, no reserve, no standby, full nominal capacity, and household load access**. Its 1.92 kWh/800 W case saves $523.16/year. A matching simple-schedule case saves $436.98/year before the new reserve/loss/standby assumptions. The STREAM-shaped revised 800 W case saves $294.94/year; the default 360 W case saves $255.33/year.

These results differ by control policy and assumptions. The canonical screen is not a product promise or a proven upper bound. The first preliminary capacity increment also changes power. The revised fixed-power capacity sweep below avoids that particular confounding change.

| Canonical optimistic capacity | Discharge / charge kW | Annual modeled saving |
|---|---|---|
| 1.92 kWh | 0.8 / 1.05 | $523.16 |
| 3.84 kWh | 1.2 / 1.2 | $958.84 |
| 5.76 kWh | 1.2 / 1.2 | $1,260.08 |
| 7.68 kWh | 1.2 / 1.2 | $1,460.02 |
| 11.52 kWh | 1.2 / 1.2 | $1,558.08 |
| 4.096 kWh | 4 / 1.8 | $1,065.00 |
| 6.144 kWh | 7.2 / 1.8 | $1,455.05 |
| 12.288 kWh | 7.2 / 1.8 | $2,282.28 |
| 13.5 kWh | 11.5 / 5 | $2,378.36 |

The 13.5 kWh canonical row reproduces the committed benchmark within $0.002. It remains hypothetical ownership. Hardware standalone payback cannot include the free fix.

## 8. Revised storage results: a conditional ceiling for each configuration

Every result below assumes access to the modeled household load. A directly connected refrigerator or office cannot claim these savings. Output/charge ratings in this table describe model inputs, not approved product installations.

The annual savings and price ceilings use the positive physical interval-net NBC basis and retain the billing-replication limits stated in §§4 and 7.

Five- and ten-year columns are **discounted break-even installed-price ceilings before unknown ownership costs**. They use 5% real discount, 1% annual savings decline, and zero real tariff escalation. Installed price, installed-price NPV, and actual asset payback remain unknown.

| Scenario | Capacity; discharge/charge caps | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|---|
| STREAM-shaped default, `stream_single_default_360w` | 1.92 kWh; 0.36/1.05 kW | $255.33 | $1,084.60 | $1,892.77 |
| STREAM-shaped conditional, `stream_single_conditional_800w` | 1.92 kWh; 0.8/1.05 kW | $294.94 | $1,252.89 | $2,186.46 |

The conditional output change adds only **$39.62/year**, with ceilings of **$168.29 over five years** and **$293.69 over ten years**. Those ceilings apply to the extra circuit/control cost, subject to all eligibility gates.

### Capacity at fixed generic 1.2 kW discharge/charge power

| Scenario | Capacity; discharge/charge caps | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|---|
| `battery_1.92kwh` | 1.92 kWh; 1.2/1.2 kW | $288.11 | $1,223.85 | $2,135.78 |
| `battery_3.84kwh` | 3.84 kWh; 1.2/1.2 kW | $666.01 | $2,829.13 | $4,937.18 |
| `battery_5.76kwh` | 5.76 kWh; 1.2/1.2 kW | $938.90 | $3,988.37 | $6,960.20 |
| `battery_7.68kwh` | 7.68 kWh; 1.2/1.2 kW | $1,146.08 | $4,868.43 | $8,496.01 |
| `battery_11.52kwh` | 11.52 kWh; 1.2/1.2 kW | $1,377.55 | $5,851.72 | $10,211.98 |

The 1.92 kWh/1.2 kW row is generic. It exceeds one STREAM Ultra's stated single-unit feed-in power. A single controller's 10 W standby across this capacity sweep does not prove actual multi-unit standby. Measure all units before product sizing.

### Larger, properly connected hypothetical storage

| Scenario | Capacity; discharge/charge caps | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|---|
| `large_4.096kwh_4kw` | 4.096 kWh; 4/1.8 kW | $708.54 | $3,009.82 | $5,252.51 |
| `large_6.144kwh_7.2kw` | 6.144 kWh; 7.2/1.8 kW | $1,070.43 | $4,547.11 | $7,935.27 |
| `large_12.288kwh_7.2kw` | 12.288 kWh; 7.2/1.8 kW | $1,873.89 | $7,960.09 | $13,891.36 |
| `large_12.288kwh_12kw` | 12.288 kWh; 12/1.8 kW | $1,874.00 | $7,960.59 | $13,892.24 |
| `large_13.5kwh_11.5kw` | 13.5 kWh; 11.5/5 kW | $1,964.18 | $8,343.65 | $14,560.72 |
| `large_27kwh_11.5kw` | 27 kWh; 11.5/8 kW | $2,490.47 | $10,579.30 | $18,462.21 |

The 4.096/6.144/12.288 kWh cases assume **1.8 kW charging**. This is not every connection's charging maximum. The 12.288 kWh/7.2 kW row is a DPU-shaped expansion comparison, not an Ultra X claim. The separate 12 kW row tests Ultra X-shaped output. The Powerwall-shaped 13.5 kWh case charges at 5 kW; the 27 kWh case assumes 8 kW charging.

### Extra batteries must earn their own cost

The following are integrated rebill differences, not sums of independent savings. Extra battery cost must fit its own ceiling.

| Increment | Extra annual saving | 5-year extra-cost ceiling | 10-year extra-cost ceiling |
|---|---|---|---|
| 1.92 → 3.84 kWh; fixed 1.2 kW | $377.90 | $1,605.27 | $2,801.41 |
| 3.84 → 5.76 kWh; fixed 1.2 kW | $272.90 | $1,159.24 | $2,023.02 |
| 5.76 → 7.68 kWh; fixed 1.2 kW | $207.17 | $880.06 | $1,535.81 |
| 7.68 → 11.52 kWh; fixed 1.2 kW | $231.48 | $983.29 | $1,715.97 |
| 6.144 → 12.288 kWh; fixed 7.2/1.8 kW | $803.45 | $3,412.99 | $5,956.09 |
| 13.5 → 27 kWh; charge power also rises 5 → 8 kW | $526.29 | $2,235.65 | $3,901.49 |

At the advertised $1,399 per unit, the last generic 3.84 kWh increment costs $2,798 in equipment alone. Its modeled equipment-only simple marginal payback is **12.08 years**. Tax, connection, subscriptions, and replacement can extend that period. It differs from the preliminary optimistic screen; neither is an installed quote.

### A small battery after a hypothetical home battery has lower value

| Controller priority | Total annual hardware saving | Small-system increment versus home battery | 5-year increment ceiling | 10-year increment ceiling |
|---|---|---|---|---|
| Home battery first | $2,196.11 | $231.93 | $985.21 | $1,719.31 |
| Small battery first | $2,248.79 | $284.61 | $1,208.98 | $2,109.82 |

These are alternative orders for one shared household. They are not additive. Sequential priority represents competition for energy/load, not proof that real CT controllers coordinate or remain stable. No home battery exists today, so this is not an installed-baseline purchase case.

## 9. New PV value depends strongly on incremental credit

The DC assumption is 1.25 times the AC cap. It is a scenario sizing choice. The AC-coupled proxy does not recover clipped DC energy directly into a battery.

“Credited” grants the existing NEM treatment to incremental meter exports as a hypothetical case. “Zero credit” removes credit from **all incremental meter exports**, including displaced rooftop exports. Baseline rooftop exports keep credit unless storage consumes them. This counterfactual allocation does not prove legal settlement or identify electrons.

“Zero export” curtails only new residual PV. It allows existing rooftop exports. It assumes perfect incremental control; it is not whole-site zero-export or proven product behavior. Zero-credit and zero-export happen to give equal bills in these cases, but physical exports and curtailment differ.

### Solar without storage

| AC cap / DC size | Credited annual saving | Zero-credit annual saving | Incremental zero-export annual saving |
|---|---|---|---|
| 0.4 kW / 0.5 kW | $237.19 | $55.93 | $55.93 |
| 0.8 kW / 1 kW | $473.76 | $102.92 | $102.92 |
| 1.2 kW / 1.5 kW | $710.22 | $145.37 | $145.37 |

Source IDs: `pv_{AC}kw_{treatment}`. The 0.8 kW AC/1 kW DC credited ceiling is **$2,012.49 over five years**, or **$3,512.05 over ten years**. Without incremental export credit, it falls to **$437.21** or **$762.98**. The same low ceilings apply to the perfect incremental-zero-export case. Preserving existing NEM matters more than assuming new exports receive its credits.

### Equal bills can hide different physical exports

For the 0.8 kW AC/1 kW DC solar-only case, the meter imports remain 23,124.32 kWh in each treatment. Credit and curtailment differ:

| Treatment | New PV AC delivered | New PV curtailed | Physical meter exports | Credited meter exports |
|---|---|---|---|---|
| `credited` | 1,630.13 kWh | 7.60 kWh | 11,341.53 kWh | 11,341.53 kWh |
| `zero_credit` | 1,630.13 kWh | 7.60 kWh | 11,341.53 kWh | 9,963.53 kWh |
| `zero_export` | 252.13 kWh | 1,385.60 kWh | 9,963.53 kWh | 9,963.53 kWh |

The credited and zero-credit cases deliver the same PV energy. Zero-export delivers less because it curtails the new surplus. Existing rooftop exports remain intact in this solar-only comparison.

### PV plus the same generic 3.84 kWh battery

The shared cap includes new PV and discharge together. The battery's individual discharge/charge settings remain 1.2 kW.

| Shared AC cap / DC size | Credited annual saving | Zero-credit annual saving | Incremental zero-export annual saving |
|---|---|---|---|
| 0.4 kW / 0.5 kW | $653.76 | $495.72 | $495.72 |
| 0.8 kW / 1 kW | $1,057.02 | $741.69 | $741.69 |
| 1.2 kW / 1.5 kW | $1,356.58 | $886.22 | $886.22 |

Source IDs: `combined_{AC}kw_{treatment}`. A larger cap changes both usable PV and discharge opportunities. Do not treat differences across these rows as panel-only value.

### Panel-only increments with a fixed 1.2 kW shared cap

These credited cases keep the same 3.84 kWh battery and output ceiling. They price new panels against storage using the existing array.

| Added panels | Extra annual saving | 5-year extra-cost ceiling | 10-year extra-cost ceiling |
|---|---|---|---|
| No new panels → 0.5 kW DC | $231.80 | $984.68 | $1,718.38 |
| 0.5 → 1 kW DC | $230.48 | $979.06 | $1,708.58 |
| 1 → 1.5 kW DC | $228.29 | $969.75 | $1,692.33 |

These panels require their own installed quotes and lawful export treatment. The JSON also reports PV-only increments under each export treatment. Direct daytime EV/appliance flexibility remains unavailable: vehicle presence and selected-load timing were not measured. Adoption after NEM ends remains unavailable; the present-2026 sensitivity below does not establish a future tariff or adoption value.

### Joint home-storage, balcony PV, and portable-storage comparison

The report publishes these conditional model results in its basic [balcony section](../index.html#balcony-solar). They do not establish a product-specific purchase recommendation. All cases retain the existing rooftop array and the post-free-EV-change baseline. No battery is installed.

Schema 3 contains 117 scenarios, 138 matched comparisons, and nine canonical references. The joint extension adds 42 scenarios: 12 home-plus-PV, 24 home-plus-PV-plus-portable, and six matched no-PV controls. Its 120 new marginal comparisons cover balcony after home, home after balcony, portable after home-plus-PV, and panels after matched home-plus-portable. The existing scenarios and references retain their original results.

The primary case adds 1 kW DC PV behind a 0.8 kW shared AC cap and 3.84 kWh generic portable storage. The higher-cap sensitivity uses 1.5 kW DC and 1.2 kW shared AC. Each battery assumes 20% reserve, 85% round-trip efficiency, 10 W continuous AC standby, and whole-meter load access. Home storage has 13.5 or 27 kWh and retains its own discharge limit. The balcony cap covers added PV and portable discharge, including PV that charges the home battery. It does not cap home-battery discharge.

Generic home storage alone saves $1,964.18/year at 13.5 kWh or $2,490.47/year at 27 kWh. These schedule/reserve/loss assumptions differ from the canonical price-aware purchase model. Do not add the balcony increment below to the canonical $2,378/year home-battery result. A standing-reserve version of that canonical purchase case remains uncalculated.

Extra saving equals the home-storage-only bill minus the bill after the balcony system is added. Total saving equals the existing-rooftop, post-behavior bill with no added hardware minus the joint bill. Ranges cover home-first and portable-first controller priority. They are directional sensitivities, not uncertainty intervals or proof of real controller coordination.

| Home storage | Extra-export treatment | Balcony + portable extra saving/year | All added hardware total saving/year |
| --- | --- | --- | --- |
| None | Hypothetical credit | $1,057 | $1,057 |
| None | No extra credit | $742 | $742 |
| 13.5 kWh | Hypothetical credit | $613 to $691 | $2,577 to $2,655 |
| 13.5 kWh | No extra credit | $307 to $390 | $2,271 to $2,354 |
| 27 kWh | Hypothetical credit | $428 to $460 | $2,919 to $2,950 |
| 27 kWh | No extra credit | $121 to $158 | $2,611 to $2,649 |

Higher-cap sensitivity:

| Home storage | Extra-export treatment | Balcony + portable extra saving/year | All added hardware total saving/year |
| --- | --- | --- | --- |
| None | Hypothetical credit | $1,357 | $1,357 |
| None | No extra credit | $886 | $886 |
| 13.5 kWh | Hypothetical credit | $863 to $924 | $2,827 to $2,889 |
| 13.5 kWh | No extra credit | $389 to $469 | $2,353 to $2,433 |
| 27 kWh | Hypothetical credit | $642 to $681 | $3,133 to $3,172 |
| 27 kWh | No extra credit | $166 to $226 | $2,657 to $2,716 |

Source IDs: `combined_{cap}kw_{treatment}` without home storage; `home_{size}kwh_combined_{cap}kw_{treatment}_{order}` with it. Matched home baselines are `large_13.5kwh_11.5kw` and `large_27kwh_11.5kw`. Orders are `home_first` and `small_first`; treatments here are `credited` and `zero_credit`. New `zero_export` cases have equal bills to zero-credit cases while curtailment and physical exports differ.

#### Add panels and storage in either order

The first column below adds home storage after the balcony-plus-portable system. The second adds portable storage after home-plus-PV. The third adds panels after matched home-plus-portable. Every figure subtracts complete bills from the same behavior baseline and holds the retained hardware's parameters fixed. The corresponding `marginal_comparisons` entries state each before/after ID.

| Home storage | Shared AC cap | Extra-export treatment | Home after balcony + portable: extra/year | Portable after home + PV: extra/year | Panels after home + portable: extra/year |
| --- | --- | --- | --- | --- | --- |
| 13.5 kWh | 0.8 kW | Hypothetical credit | $1,520 to $1,598 | $177 to $255 | $419 to $423 |
| 13.5 kWh | 0.8 kW | No extra credit | $1,530 to $1,612 | $184 to $267 | $113 to $121 |
| 13.5 kWh | 1.2 kW | Hypothetical credit | $1,470 to $1,532 | $208 to $270 | $631 to $640 |
| 13.5 kWh | 1.2 kW | No extra credit | $1,467 to $1,547 | $216 to $296 | $157 to $184 |
| 27 kWh | 0.8 kW | Hypothetical credit | $1,862 to $1,893 | $13 to $44 | $399 to $402 |
| 27 kWh | 0.8 kW | No extra credit | $1,869 to $1,907 | $20 to $58 | $92 to $100 |
| 27 kWh | 1.2 kW | Hypothetical credit | $1,776 to $1,815 | $20 to $58 | $600 to $607 |
| 27 kWh | 1.2 kW | No extra credit | $1,771 to $1,830 | $27 to $87 | $124 to $152 |

The matched 0.8 kW no-PV controls use `home_{size}kwh_small_0.8kw_{order}`. The 27 kWh/1.2 kW controls use the same pattern. The 13.5 kWh/1.2 kW controls are `powerwall_then_small` and `small_then_powerwall`. Those controls keep the portable output limit equal to the combined case. Changing both the portable limit and the panels would not isolate panel value.

After larger home storage, the portable battery's own increment becomes small. Storage and PV use the same remaining load and energy; independent standalone savings cannot be added. The fixed-power capacity table in §8 also shows why each extra storage increment needs its own cost ceiling.

#### Joint incremental components

These are the primary system's incremental bill offsets after home storage, under home-first priority. Negative means added cost. Values use positive physical interval-net NBC and retain the billing-replication limits in §4. Display rounding can leave a cent difference between component cells and the total.

| Home storage | Extra-export treatment | Delivery | CEA generation | PCIA | NBC | Fixed daily charge | Total extra/year |
| --- | --- | --- | --- | --- | --- | --- | --- |
| None | Hypothetical credit | $550.49 | $477.04 | $22.21 | $7.28 | $0.00 | $1,057.02 |
| None | No extra credit | $392.89 | $345.61 | -$4.09 | $7.28 | $0.00 | $741.69 |
| 13.5 kWh | Hypothetical credit | $339.41 | $257.40 | $13.70 | $2.29 | $0.00 | $612.81 |
| 13.5 kWh | No extra credit | $184.81 | $127.92 | -$7.82 | $2.29 | $0.00 | $307.21 |
| 27 kWh | Hypothetical credit | $215.81 | $182.68 | $25.68 | $4.05 | $0.00 | $428.22 |
| 27 kWh | No extra credit | $60.63 | $52.14 | $3.87 | $4.05 | $0.00 | $120.69 |

The fixed daily electric charge remains unchanged. Gas remains unchanged and excluded from this electric model.

#### Complete incremental cost ceilings and assumed paybacks

`financial_sensitivities.incremental_complete_cost_sensitivities` contains 12 packages: PV-only and PV-plus-portable, no home/13.5 kWh/27 kWh home, and credited/zero-credit exports. Each row subtracts the matching home baseline and excludes home-battery cost. Combined home cases use home-first priority. Ceilings use 5% real discount, 1% annual savings decline, and zero real tariff escalation, before unknown ongoing ownership costs.

The four costs below are assumed complete incremental costs, not equipment advertisements or quotes. Long simple-payback ratios do not establish equipment life. Actual complete quotes, supported connections, equipment performance, and ownership costs remain unknown.

| Home; added system | Extra-export treatment | Five-year ceiling | Ten-year ceiling | $1,000 assumed: payback | $2,500 assumed: payback | $5,000 assumed: payback | $7,500 assumed: payback |
| --- | --- | --- | --- | --- | --- | --- | --- |
| None; PV only | Hypothetical credit | $2,012 | $3,512 | 2.1 yr | 5.3 yr | 10.6 yr | 15.8 yr |
| None; PV + portable | Hypothetical credit | $4,490 | $7,836 | 0.9 yr | 2.4 yr | 4.7 yr | 7.1 yr |
| None; PV only | No extra credit | $437 | $763 | 9.7 yr | 24.3 yr | 48.6 yr | 72.9 yr |
| None; PV + portable | No extra credit | $3,151 | $5,498 | 1.3 yr | 3.4 yr | 6.7 yr | 10.1 yr |
| 13.5 kWh; PV only | Hypothetical credit | $1,852 | $3,232 | 2.3 yr | 5.7 yr | 11.5 yr | 17.2 yr |
| 13.5 kWh; PV + portable | Hypothetical credit | $2,603 | $4,543 | 1.6 yr | 4.1 yr | 8.2 yr | 12.2 yr |
| 13.5 kWh; PV only | No extra credit | $522 | $911 | 8.1 yr | 20.3 yr | 40.7 yr | 61.0 yr |
| 13.5 kWh; PV + portable | No extra credit | $1,305 | $2,277 | 3.3 yr | 8.1 yr | 16.3 yr | 24.4 yr |
| 27 kWh; PV only | Hypothetical credit | $1,764 | $3,079 | 2.4 yr | 6.0 yr | 12.0 yr | 18.1 yr |
| 27 kWh; PV + portable | Hypothetical credit | $1,819 | $3,174 | 2.3 yr | 5.8 yr | 11.7 yr | 17.5 yr |
| 27 kWh; PV only | No extra credit | $428 | $747 | 9.9 yr | 24.8 yr | 49.7 yr | 74.5 yr |
| 27 kWh; PV + portable | No extra credit | $513 | $895 | 8.3 yr | 20.7 yr | 41.4 yr | 62.1 yr |

Each package also stores five- and ten-year NPV for every assumed cost. Its actual installed cost, actual simple payback, and actual-cost NPV remain null. Future subscription, maintenance, replacement, downtime, and compliance costs reduce the amount available for initial equipment and installation. Backup value and VPP revenue stay separate.

### Present-2026 NBT sensitivity: fixed flows, signed costs before settlement limits

**The three storage-only cases below have larger modeled hardware deltas under these NBT assumptions.** Credited added PV has a smaller delta. These are signed cost-ledger differences before settlement limits. They do not establish payable-bill savings or support a tariff-switch recommendation. The NEM tables and conditional price ceilings above retain their separate NEM basis.

The sensitivity uses the same 35,040 intervals from 2025-07-24 through 2026-07-23. It keeps physical imports, exports, charging sources, SOC, and controller order unchanged. It prices gross imports at frozen June 2026 EV-TOU-5/CEA rates, including gross-import NBC. NEM uses positive physical interval-net NBC instead. All credited exports receive the selected NBT prices, including exports from the existing rooftop array. This is a whole-account counterfactual; it establishes no SB 868 compensation rule.

The four cases combine `NBT00` and `NBT26` with two export-credit assumptions: delivery plus generation plus the CEA $0.01/kWh bonus, or delivery plus that bonus alone. Both vintages give equal results in the priced 2026 first year. Their equality says nothing about later years. The two credit assumptions reflect unresolved CEA generation-credit interpretation. Their extrema are not rigorous bounds on settlement savings.

SDG&E's methodology assigns generation export pricing to bundled customers. CEA states that Solar Impact uses the same SDG&E pricing plus its bonus. These statements leave the component interpretation unresolved. The sources were checked on 2026-10-23. [SDG&E export pricing](https://www.sdge.com/solar/solar-billing-plan/export-pricing), [CEA Solar Impact](https://thecleanenergyalliance.org/solar-impact/)

Each case stores `unchanged_behavior` and `post_free_fix` baselines. Each hardware row uses its own matched behavior baseline. The following rows all use `post_free_fix`, excluding free EV scheduling. The NBT free-behavior delta is $1,238.66 under either credit assumption; the separate NEM figure remains $1,222.26. These different pricing bases cannot be added together.

| Fixed physical scenario | NEM modeled annual saving | NBT delivery + generation + bonus annual signed delta | NBT delivery + bonus annual signed delta |
|---|---:|---:|---:|
| `stream_single_default_360w`; 1.92 kWh, 0.36/1.05 kW discharge/charge | $255.33 | $356.74 | $370.93 |
| `stream_single_conditional_800w`; 1.92 kWh, 0.8/1.05 kW | $294.94 | $428.96 | $450.09 |
| `battery_3.84kwh`; 3.84 kWh, 1.2/1.2 kW | $666.01 | $829.82 | $863.08 |
| `pv_0.8kw_credited`; 1 kW DC, 0.8 kW AC | $473.76 | $171.51 | $121.71 |
| `pv_0.8kw_zero_credit`; same PV | $102.92 | $104.88 | $104.88 |
| `combined_0.8kw_credited`; same PV plus 3.84 kWh storage | $1,057.02 | $910.17 | $886.50 |
| `combined_0.8kw_zero_credit`; same combined system | $741.69 | $854.97 | $871.95 |

Storage assumes 20% reserve, 85% round-trip efficiency, and 10 W continuous standby, with total-meter load access. The combined system shares a 0.8 kW cap across new PV and battery discharge. Its battery settings remain 1.2/1.2 kW. These assumptions do not establish product performance or accessible circuits. `zero_credit` removes credit from all incremental meter exports, including displaced rooftop exports. `zero_export` retains the same billing channel but curtails new residual PV; its physical exports differ.

Source: [`data/portable_solar_storage.json`](../data/portable_solar_storage.json), `scenarios[id=ID].annual_savings_usd` and `present_2026_nbt_sensitivity.cases.CASE.scenarios.ID.annual_hardware_signed_ledger_delta_usd`. Here `CASE` is either `NBT00_delivery_generation_cea_bonus` or `NBT00_delivery_cea_bonus_only`; corresponding `NBT26` rows are equal. The block covers all 117 scenarios, nine canonical references, and 138 marginal pairs. It reprices saved dispatch without NBT optimization.

**Credit-limit diagnostics do not settle bills.** Every baseline, scenario, and reference reports annual/monthly delivery and generation charge/credit pools, component excesses, negative months, and net-export flags. The CEA bonus joins generation only for diagnostics; PCIA remains outside both pools. These modeled results show no negative month, pool excess, or net-export flag. That result does not prove credit availability across actual billing or relevant periods.

Current NBT permits credits against corresponding volumetric charges, excluding NBC and fixed charges. SDG&E carries eligible credits forward during NBT service. CEA terms describe annual remaining-credit forfeiture and separate net-surplus settlement. The model applies neither provider's credit banks, relevant periods, floors, nor net-surplus compensation. Calendar months are not established billing cycles. [NBT, SC 3](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=1064&utilId=SDGE), [CEA terms](https://thecleanenergyalliance.org/wp-content/uploads/2024/03/CEA-Solar-Impact-Plan-Terms-and-Conditions-Adopted-2024_01.pdf)

The block keeps `payable_bill_established`, `actual_tariff_accuracy_established`, and `settlement_credit_limits_applied` false. Its `economics` and diagnostic `nsc_usd` remain null. No NBT price ceiling, NPV, payback, or future purchase date follows. `unavailable_scenarios.post_nem_adoption.annual_savings_usd` remains null because future tariffs, equipment prices, and supported connections remain unknown.

## 10. Physical flows and monthly bills are visible

The generic `battery_3.84kwh` case illustrates source accounting. Its annual modeled charges are **1,282.57 kWh from the grid** and **693.93 kWh from existing solar**. It serves **1,680.03 kWh**, with **296.48 kWh conversion loss** plus **87.60 kWh continuous AC standby**. These are modeled flows, not measured product performance.

Its modeled equivalent full cycles are **1.30/day**. Its peak import rises from **20.16 to 20.17 kW**. The 13.5 kWh scheduled case reaches **22.83 kW**. Cheap-hour charging does not prove service headroom. An electrician needs the actual simultaneous loads and charging topology.

| Bill component, generic 3.84 kWh case | Modeled annual saving; negative means added cost |
|---|---|
| `delivery_usd` | $365.03 |
| `generation_usd` | $311.55 |
| `pcia_usd` | $-16.26 |
| `nbc_usd` | $5.68 |
| `fixed_usd` | $0.00 |

PCIA is the power charge indifference adjustment. NBC means non-bypassable charges. The NBC row uses positive physical interval net, including physical exports that receive no credit. Exact billing-replication limits remain as stated in §4. The unchanged daily electric charge and gas costs do not disappear through storage.

### Monthly modeled electric bills: comparison, not statement totals

The endpoints are partial calendar months. All scenarios' complete bill components, physical/credited exports, charging sources, cycles, curtailment, and peak imports appear in the JSON monthly records.

| Calendar month | After free fix, before hardware | Generic 3.84 kWh battery |
|---|---|---|
| 2025-07 | $88.41 | $70.07 |
| 2025-08 | $502.34 | $432.71 |
| 2025-09 | $541.98 | $472.07 |
| 2025-10 | $433.40 | $356.67 |
| 2025-11 | $287.26 | $245.77 |
| 2025-12 | $344.48 | $295.94 |
| 2026-01 | $313.92 | $263.33 |
| 2026-02 | $230.37 | $188.20 |
| 2026-03 | $215.99 | $164.12 |
| 2026-04 | $141.77 | $94.25 |
| 2026-05 | $130.59 | $87.81 |
| 2026-06 | $263.59 | $200.99 |
| 2026-07 | $185.01 | $141.17 |

Synthetic tests cover conservation, reserve/power/shared caps, empty/full batteries, absent load/generation, simultaneous meter channels, annual SOC, DST, missing inputs, and controller overlap. The generator fails on absent required evidence or failed production checks. Reproduction commands and pipeline gates appear in [TECHNICAL.md](../TECHNICAL.md). A successful model run does not establish product eligibility or measured circuit performance.

## 11. Financial sensitivity: ceilings precede the missing quote

For annual hardware saving `S`, the pre-cost ceiling for `N` years is `sum(S × 0.99^(y−1) / 1.05^y, y=1..N)`. NPV subtracts the complete initial cost and discounted future ownership costs. Simple asset payback divides complete hardware cost by hardware-only annual saving. Marginal payback uses only the increment's cost and saving. These are distinct from package savings that include free behavior.

The [IRS](https://www.irs.gov/credits-deductions/residential-clean-energy-credit) states that property placed in service after December 31, 2025 does not qualify for homeowner Section 25D. This study uses **zero federal homeowner credit**. Other incentives, VPP eligibility/value, and monetary backup value remain unknown. No credit fills the quote gap.

### Assumed complete-cost ladder for `battery_3.84kwh`

These are assumed costs, not quotes. Higher rungs also show cost-overrun exposure.

| Assumed complete installed cost | Simple asset payback | 5-year NPV | 10-year NPV |
|---|---|---|---|
| $1,000.00 | 1.50 years | $1,829.13 | $3,937.18 |
| $2,500.00 | 3.75 years | $329.13 | $2,437.18 |
| $5,000.00 | 7.50 years | $-2,170.87 | $-62.82 |
| $7,500.00 | 11.25 years | $-4,670.87 | $-2,562.82 |

### Hypothetical tariff and availability stresses

The tariff stresses rebill both baseline and hardware flows. They scale non-super-off-peak delivery/generation spreads around the same-season super-off-peak rate. They keep physical schedule flows, NBC, PCIA, and fixed charges unchanged. They are not future rate forecasts.

| Spread relative to frozen case | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|
| 0.5× | $312.93 | $1,329.29 | $2,319.79 |
| 0.75× | $489.47 | $2,079.21 | $3,628.48 |
| 1.25× | $842.54 | $3,579.04 | $6,245.88 |

An 80% cashflow availability assumption lowers the generic 3.84 kWh ceilings to **$2,263.30/$3,949.75** for five/ten years. A separate physical scenario disconnects the battery for each month's first three days. It saves **$599.01/year**; this is not the same operation as a cashflow multiplier.

At fixed 3.84 kWh, reserve assumptions of 10/20/30% give **$742.43/$666.01/$581.47 per year**. Efficiency assumptions of 80/85/90% give **$630.64/$666.01/$699.56**. Standby assumptions of 0/10/30 W give **$696.48/$666.01/$603.02**. None substitutes for measured hardware losses.

Solar-only, grid-only, and mixed charging give **$434.85/$764.41/$666.01 per year** for that case. A simple mixed controller can perform worse than grid-only because it diverts credited exports and follows different charging priorities. This is a controller result, not a claim that solar charging is intrinsically inferior.

### The 2030 transition needs a cost case, not an assumed shutdown

Assume hypothetical 2027 commissioning. Continued lawful operation without extra cost keeps the generic 3.84 kWh ceilings at **$2,829.13/$4,937.18**. An assumed $500 compliance cost in year four lowers them to **$2,417.78/$4,525.83**. An assumed $1,500 cost lowers them to **$1,595.07/$3,703.13**.

These cases continue operation. Neither cost is a quote or legal determination. Restricted operation, delayed adoption, future tariffs, and possible later obligations remain scenario questions. The sunset creates uncertainty; it does not establish a present extra approval requirement or known future compliance cost. A purchaser can weigh that uncertainty without a guaranteed post-2029 outcome.

### Complete quote template: all rows remain open

| Cost group | Quote lines needed | Status |
|---|---|---|
| Equipment and PV | Exact inverter/batteries, panels, mounts, ballast/roof work, weather protection, approved connectors/cables | Equipment advertisements only; complete configuration unknown |
| Electrical connection | Receptacle/circuit work, transfer/inlet/panel/Gateway, CTs, monitoring, grounding, service/code repairs | Design and electrician quote absent |
| Delivery and commissioning | Freight, tax, labor, design, permits, inspection, setup, functional checks | Unknown |
| Ongoing ownership | Subscription fee, financing/interest, maintenance, downtime, insurance implications, battery replacement | Unknown |
| Service and transition | Warranty shipping/labor, removal/reinstallation, integration repair, 2030 compliance/retirement | Unknown |

The tables give conditional break-even installed-price ceilings before unknown ownership costs. A quote is not needed to calculate these ceilings. A supported product-specific ceiling still needs the applicable load access, performance, billing, and ownership-cost assumptions. Actual installed cost, asset payback, and NPV need a complete quote. An equipment advertisement alone does not establish positive installed NPV. Backup willingness-to-pay belongs in a separate owner decision.

## 12. Backup: assume no existing rooftop recharge

The [IQ7X U.S. datasheet](https://enphase.com/sites/default/files/2021-05/IQ7X-DS-EN-US.pdf) describes grid-interactive equipment. No checked source establishes this array's approved island, compatible grid profile, restart/curtailment control, or permitted PV-to-battery ratio with a candidate EcoFlow system.

The [Gateway FAQ](https://us.ecoflow.com/products/smart-gateway) describes indirect existing-PV use and conditional PV integration. It does not prove this exact IQ7X system recharges a battery during an outage. The base backup case therefore has **zero existing-rooftop production during outages**. Dedicated new DC panels are a separate cost/yield case.

STREAM's branch-circuit connection does not power ordinary home outlets during an outage. Its directly connected appliance transfer can take three seconds; it is not a UPS. [STREAM manual, appliance supply](https://ecoflow-service-us-prod.oss-us-west-1.aliyuncs.com/cms/manual/1753783795065/EcoFlow%20STREAM%20Ultra_User%20Manual.pdf)

The generic 3.84 kWh model lasts **9.14 hours at a constant 300 W**, from full charge down to its 20% reserve. The preserved reserve alone lasts **2.28 hours** if outage operation overrides the reserve floor. Both include assumed 10 W standby and conversion loss, without solar recharge. These are constant-load calculations, not refrigerator measurements or household outage promises.

Which household circuits work, their surge needs, overnight/cloudy/multi-day runtime, and the owner's desired duration remain **not determined**. They need measured selected circuits, a supported transfer design, outage-weather assumptions, and a stated reserve policy. A portable battery with appliance cords does not create a rooftop microgrid.

## 13. Conditional selected-appliance pilot: measure before purchase

**First measure one selected appliance's input energy and startup demand if an appliance-specific comparison is wanted.** Choose a supported direct-output connection only. Do not backfeed an ordinary receptacle. A household-wiring pilot first needs an electrician-reviewed connection concept and applicable electrical, permit, isolation, circuit, and service checks. Utility interconnection requirements depend on the topology and any statutory exemption.

The following is a proposed measurement protocol for a later justified pilot. The desk evaluation can recommend waiting without a battery purchase or completed pilot. The issue's matched-baseline field comparison remains conditional and unexecuted:

1. Record the chosen load, owner-required backup duration, spending limit, meter accuracy, surge demand, and normal duty cycle. Keep raw appliance traces private. Use a borrowed or returnable supported unit only after compatibility checks and an owner decision to proceed.
2. Meter at least 14 representative baseline days with the free EV fix already applied. Record appliance energy by tariff period, occupancy/use, and weather where relevant. Keep the appliance's service unchanged.
3. Meter at least 14 matched battery days. Record AC charging input, delivered appliance output, idle consumption, SOC endpoints, reserve, outages, and failed service. Record firmware/app versions, exact ports, charge power, schedule, and subscription. Use interval energy plus a suitable surge measurement.
4. Check the schedule with internet available and absent. Check reserve enforcement, output memory, recovery, and supported outage transfer. Do not accept a control result that stops the selected appliance's needed service. Restore equal SOC for the energy comparison or account for its change.
5. Rebill matched selected-load energy with measured losses and existing-PV opportunity cost. Accept a purchase case only if lawful connection, service/backup targets, schedule behavior, complete quote, and positive cost-adjusted NPV all pass. Stop the case if any remains unproved.

The 14-day periods are protocol minimums, not proof of annual representativeness. Extend measurement if seasons, occupancy, or workload differ. Select matched days by these factors and tariff period; do not multiply one favorable day into an annual claim. Apply the result only to measured load coverage. Whole-house savings remain unavailable for an appliance-only pilot.

## 14. Dated source register and quality limits

Entries were checked on 2026-10-22. The law/effective-date, STREAM manual, STREAM/DP3/DPU price/stock, IRS, and NBT/CEA sources were rechecked on 2026-10-23. The precise NEM-ST storage/SGIP statement retains its 2026-10-22 check. Statute text, current tariffs, manuals, advertisements, and measured household performance have different authority.

### Regulatory and settlement sources

| Source | Version/date evidence | Supports; limitation |
|---|---|---|
| [Official SB 868 endpoint](https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=202520260SB868), [chaptered mirror](https://legiscan.com/CA/text/SB868/2025), [Governor](https://www.gov.ca.gov/2026/09/30/governor-newsom-issues-legislative-update-9-30-2026/) | Chapter 985; September 30, 2026 | Final text fallback disclosed; official endpoint inaccessible |
| [Secretary of State](https://www.sos.ca.gov/administration/bill-chapters) | General effective-date rule | January 2027 inference; no household interpretation |
| [NEM-ST](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE), [Rule 21](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=96&utilId=SDGE) | Current compiled PDFs; relevant sheet dates stated above | Existing tariff pathways; no established exact SB 868 household treatment |
| [CEA Personal Impact](https://thecleanenergyalliance.org/personal-impact/) | Current program page | Existing settlement terms; no new-device compensation determination |
| [Solar Billing Plan](https://www.sdge.com/solar/solar-billing-plan), [export pricing](https://www.sdge.com/solar/solar-billing-plan/export-pricing), [NBT tariff](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=1064&utilId=SDGE) | Current pages and compiled NBT, SC 3; checked 2026-10-23 | EV-TOU-5 assignment, hourly components, separate credit pools and SDG&E carryforward; signed proxy does not implement settlement |
| [CEA Solar Impact](https://thecleanenergyalliance.org/solar-impact/), [terms](https://thecleanenergyalliance.org/wp-content/uploads/2024/03/CEA-Solar-Impact-Plan-Terms-and-Conditions-Adopted-2024_01.pdf) | Current page; PDF header: adopted October 2023; filename: 2024_01; checked 2026-10-23 | One-cent bonus, annual remaining-credit forfeiture and separate surplus settlement; generation-component interpretation remains unresolved |
| [IRS credit guidance](https://www.irs.gov/credits-deductions/residential-clean-energy-credit) | Current guidance | Zero homeowner 25D base case; no other incentive approval |

The tariff viewer provides the public access point. Its public API supplied the compiled PDFs when the viewer did not expose text. Use the current compilation rather than a stale search-result tariff.

### Product and installation sources

| Source | Version/date evidence | Supports; limitation |
|---|---|---|
| [UL framework](https://www.ul.com/news/ul-solutions-debuts-testing-and-certification-framework-safer-plug-solar-across-united-states), linked NRTL directories | UL news January 8, 2026; directory checks dated above | Certification scope matters; no exact candidate certificate established |
| Manufacturer-hosted U.S. manuals in §6 | Document/export dates in the equipment table | Models, ratings, connection/control limits; no measured loss curve or site approval |
| U.S. product pages and `.js` endpoints in §6 | Live snapshot on research date | Advertised exact variants/prices/availability; no delivered quote |
| [Warranty](https://us.ecoflow.com/pages/warranty-policy), [remote support terms](https://us.ecoflow.com/pages/live-installation-terms-conditions) | Warranty updated October 20, 2025 | Coverage/exclusions and remote-only scope; installed service costs unknown |
| [Gateway FAQ](https://us.ecoflow.com/products/smart-gateway), [IQ7X datasheet](https://enphase.com/sites/default/files/2021-05/IQ7X-DS-EN-US.pdf) | Current FAQ; manufacturer datasheet | Connection constraints; no approved outage-PV design for this household |

Large DP3/Ultra X PDFs exceeded the web reader's size limit; the manufacturer PDF copies supplied searchable text. The IQ7X endpoint returned 403 to the web reader. These access limits do not support additional compatibility claims. Model results remain reproducible repository calculations, not certification evidence.

## 15. Acceptance map: retain the issue's full criteria

**Incomplete means the full criterion remains open**, even when this study completes useful parts. “Achieved” below applies only to the complete criterion as written. A model assumption never completes a hardware or site requirement.

### Regulatory and hardware evidence

| Issue criterion | State | Evidence or exact missing input |
|---|---|---|
| Final law, effective date, sunset, aggregate limit, notification; unresolved interpretations stated | **Achieved as documented research, with disclosed text fallback** | §3 and source register; recheck official text before deployment |
| Every candidate classified for exact use/connection; appliance versus parallel separated | **Achieved as an evidence classification** | §5; qualifying candidate status remains not determined, not eligible |
| NEM/CEA treatment and expansion headroom, or prevent purchase while open | **Achieved through purchase deferral** | WAIT recommendation remains. Compensation cases are conditional; conventional expansion needs capacity-method/history/load evidence. Original rooftop records and an individualized utility letter are not SB 868 eligibility conditions |
| Exact U.S. model, listing scope, configuration, controls, dated price/stock, full installation cost | **Incomplete** | §6 documents models/manuals/prices; exact system certificate, firmware tests, complete configuration and installed quote absent |
| Code versus interconnection distinction; electrician-reviewed household-wiring concept before pilot | **Distinction stated; pre-pilot condition unmet** | No household-wiring pilot is underway. An electrician-reviewed diagram and applicable circuit/service/phase/code/permit checks remain necessary before that type of pilot |

### Model and economics

| Issue criterion | State | Evidence or exact missing input |
|---|---|---|
| Baseline/reference reproduction; window, rates, hashes, limitations | **Achieved for the implemented model** | §7; artifact `baseline`, `provenance`, `canonical_no_reserve_benchmarks`; historical residual and exact billing-replication limits stay disclosed; bill evidence supports positive physical interval-net NBC |
| Solar-only, storage-only, combined, selected-load, larger options; standalone/incremental savings | **Incomplete** | Generic families, joint home/balcony cases, and matched increments exist; real selected-appliance/circuit interval energy, surge, access and wiring are absent |
| Conservation, SOC/reserve, charge/discharge/shared caps, annual balance; losses/standby counted | **Achieved for stated interval assumptions** | Model and synthetic tests; measured losses, subinterval flows and real controller performance remain separate unknowns |
| Monthly bills/flows/self-use/sources/cycles/peaks/curtailment/runtime/installed cost/payback/NPV | **Incomplete** | JSON carries modeled accounts and conditional runtime; actual circuit runtime, complete quote, ownership costs, installed payback/NPV absent |
| Missing inputs/DST/zero generation/no load/full-empty/net-credit/simultaneous flows/two-controller tests; byte identity | **Achieved for the model** | Synthetic suite and deterministic generator checks; independent project gates remain needed before publication |

### Decision and pilot

| Issue criterion | State | Evidence or exact missing input |
|---|---|---|
| Buy/pilot/wait/do-not-buy decision with reasons/dependencies | **Achieved** | WAIT for grid-parallel; conditional direct-appliance measurement only; §§1–6 and 13 |
| Maximum worthwhile installed price; each battery/panel increment | **Partly achieved** | Conditional discounted ceilings and increments, including matched joint packages and complete-cost assumptions, exist in §§8–11 without a quote. Supported product-specific ceilings need load/loss/connection evidence and ownership-cost assumptions |
| If justified, pilot with meters/reserve/control/load/success criteria and matched baseline | **Conditional and unexecuted** | Proposed protocol in §13; selected load, duration/budget, exact unit, completed metering and control tests absent. A pilot is not an unconditional prerequisite to a desk recommendation to wait |
| Backup priced separately; circuits established; no unsupported rooftop recharge | **Incomplete** | No rooftop recharge assumed; owner backup value, essential circuits/surges, supported transfer/PV design and outage evidence absent |
| Report/glossary/consistency updates only after study supports claims | **Conditional model results published; purchase recommendation deferred** | The report links the standalone/joint comparisons and states their limits. Exact product eligibility, circuit access, performance, and complete costs remain open; no purchase recommendation follows |

### Additional requested comparisons: present sensitivity and remaining gaps

| Comparison | Exact missing evidence |
|---|---|
| Site-specific south/west/shade/seasonal PV | Chosen placement, measured shade/horizon, orientation/tilt, permitted mounting, appropriate irradiance/yield model |
| Daylight EV or flexible appliances before storage | Vehicle presence, charging sessions, appliance flexibility and measured schedules |
| Adoption after NEM grandfathering | Present-2026 fixed-flow NBT sensitivity is complete in §9 and `present_2026_nbt_sensitivity`. Actual delayed-adoption value stays null: future tariff, equipment price, and supported connection remain unknown |
| Real control interaction and split-phase access | Circuit/leg/CT diagram, supported controller topology, transient/feedback and offline tests |
| Measured equipment and multi-day backup | Usable AC capacity, loss/standby curves, reserve behavior, essential-load traces, surge and outage-weather/design evidence |

**Next action: record the name of one appliance to meter.**
