# SB 868: qualifying plug-in PV needs no utility approval; equipment evidence remains open

**Decision as of 2026-10-22: WAIT for a grid-parallel purchase.** Exact system certification, household connection, supported performance, and full installed costs remain unresolved. SB 868 bars prior SDG&E approval for qualifying devices once its exemption takes effect on January 1, 2027. NEM/CEA compensation affects the economics; it is a separate question from statutory eligibility.

A selected-appliance pilot can answer useful questions with a borrowed or returnable supported battery if the owner later chooses that pilot. The household owns no battery. Selected-load measurements can improve the desk evaluation without a battery purchase or field pilot. Conditional modeled price ceilings do not establish a purchase budget.

This study separates electricity savings from backup. It does not authorize purchases, utility applications, or electrical work. Publication of portable-study recommendations remains deferred. The existing report and glossary include the supported tariff qualifications. Issue #304 remains incomplete where the acceptance map names missing evidence.

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

The model prices hardware **after the free EV schedule change**. That free change saves a modeled **$1,220.85/year**. Hardware never receives credit for that saving.

## 3. Law: qualifying devices need no prior utility approval from January 1, 2027

SB 868 is Chapter 985, signed/filed September 30, 2026. The Governor lists the signature. The official text endpoint returned HTTP 403; the chaptered text came from the disclosed [LegiScan mirror](https://legiscan.com/CA/text/SB868/2025). The ordinary **January 1, 2027** effective date follows from the [Secretary of State's rule](https://www.sos.ca.gov/administration/bill-chapters). The exemption is not effective on this study date. [Governor's signed-bill list](https://www.gov.ca.gov/2026/09/30/governor-newsom-issues-legislative-update-9-30-2026/)

Section 8530(c) defines qualifying photovoltaic equipment or combinations through six conditions. They cover receptacle connection, onsite-consumption intent, current electrical-code compliance, NRTL plug-in-PV-system certification, and certified outage isolation. The limit is **1,200 W aggregate AC per dwelling**, across qualifying devices. NRTL means nationally recognized testing laboratory. [Chaptered text, §8530(c)](https://legiscan.com/CA/text/SB868/2025)

Section 8531(a) exempts qualifying devices from all interconnection requirements imposed by state law, the CPUC, and utility rules, including interconnection agreements. Section 8531(b) bars prior utility approval, device-related fees, and extra utility-required controls or equipment. Utilities may require simple online notification of address, make, model, and size. **Notification cannot require approval.** [Chaptered text, §8531(a)–(d)](https://legiscan.com/CA/text/SB868/2025)

Existing rooftop PV does not disqualify a household under these conditions. The hardwired rooftop array is distinct from the receptacle-connected devices that the definition covers. Original rooftop capacity and an individualized utility letter are not additional statutory eligibility conditions. Exact export compensation and battery-only qualification remain **not determined**. These questions do not remove the approval exemption. [Chaptered text, §§8530–8531](https://legiscan.com/CA/text/SB868/2025)

Section 8531 repeals January 1, 2030. Section 8532 then restricts sales of receptacle-connected PV devices that fail its specified code, certification, and isolation conditions. Neither automatic shutdown nor permanent exemption follows. Local code, fire, siting, mounting, and circuit checks remain separate. Recheck the [official chapter](https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=202520260SB868) before deployment.

No SB 868 notification form or implementation notice appeared in the checked SDG&E pages and official-domain research. That limited search does not prove absence or add an approval requirement. A vendor article is not authority for broader permit relief.

## 4. NEM protection needs the exact use and connection

The current compiled [NEM-ST tariff](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE) was retrieved on 2026-10-22. Sheet dates vary. SC 9(b), sheet 27, effective February 14, 2024, governs conventional expansion. Its limit is the greater of **10% of original interconnected nameplate capacity or 1 kW**, subject to onsite-load limits. Larger additions have separate-tariff or other authorized pathways.

The 1 kW floor allows a small-addition screen without the original capacity figure. Prior expansion can consume that allowance, so 1 kW of remaining headroom is not established. Original capacity, the applicable capacity method, expansion history, and onsite load determine the exact remaining allowance. This conventional rule is separate from SB 868's 1,200 W portable-device limit and approval exemption. [NEM-ST, SC 9(b)](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

SC 9(d), sheet 28, effective September 3, 2024, preserves the remaining legacy period for storage-only additions. Its **SGIP exception matters**. SGIP means Self-Generation Incentive Program. Under this exception, specified reservations accepted on/after June 4, 2024 require NBT transition, except the stated equity categories. An incentive cannot enter the purchase case without its tariff consequences. [NEM-ST, SC 9](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

SC 10 measures separate-inverter storage by AC nameplate power. Solar-paired storage at 10 kW or less uses small-storage rules, including an optional monthly export-credit cap. Larger storage needs the applicable metering/control route. Grid-charged exports do not receive unrestricted NEM credit by assumption. The printed 150%-of-PV limit suspension ends August 16, 2025; a later extension was not established. [NEM-ST, SC 10](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

[Rule 21](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=96&utilId=SDGE) covers NEM and non-export facilities. Parallel operation exceeds 60 cycles/one second. Storage charging remains subject to load rules. **Non-export alone does not remove interconnection requirements.** Transfer-isolated wiring needs its own topology check. Use the [SDG&E installation process](https://www.sdge.com/solar/solar-and-battery-installation-center) outside an established exemption.

[CEA Personal Impact](https://thecleanenergyalliance.org/personal-impact/) accumulates generation charges and credits through the annual relevant period. Its published net-surplus rate is $0.06/kWh; direct payment starts at $100. Those terms do not establish SB 868 compensation. This study keeps avoided CEA imports, forgone existing-PV credit, and hypothetical new-export credit separate. San Diego Community Power programs do not establish this CEA household's eligibility.

The tariff prices NBC from consumption net of exports within each metered interval, without cross-interval credit offsets. The canonical model instead charges NBC on its gross import channel. Its fifteen-minute channels need reconciliation with the utility's billing interval and channel definitions. They cannot resolve subinterval timing. All savings and ceilings here retain that disclosed model convention; reproduction alone does not establish its tariff accuracy. [NEM-ST, SC 1(f) and 3(d)](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE)

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

All product observations below date to **2026-10-22**. Manual dates identify the checked document, not current firmware. Regional availability and accessories need separate checks.

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

The comparison uses **2025-07-24 through 2026-07-23**, **35,040 meter intervals**, and frozen **June 2026 EV-TOU-5/CEA Clean Impact Plus rates**. It does not forecast 2027 prices. The unchanged-behavior modeled bill is $4,904.13; after the free fix it is $3,683.28. These are model levels, not actual billed costs.

Every bill and savings figure retains the model's gross-import NBC convention. NEM-ST instead specifies consumption net of exports within each metered interval. The meter-channel interpretation remains open, as §4 explains. These calculations are conditional estimates, not an actual-bill reconciliation.

The historical statement windows differ from the interval year. The documented historical residual remains unexplained; see [TECHNICAL.md, historical bill reconciliation](../TECHNICAL.md). Frozen rates also differ from historical rates. Hardware deltas compare complete bills on the same model basis; they do not repair historical reconciliation.

### Reproduction evidence

| Input/evidence | SHA256 recorded in `provenance` |
|---|---|
| Staged meter series | `c86bec4088e8739198402d9db5989156c2f7561aa2e6a4eaab73b8e591a450e8` |
| SAM A | `13a4f04d28852871adaeb0de97fcefe29ef3f11e90d74bb067e273f7642cd637` |
| SAM B | `98dd23beb5f50f2996c1c5a01ff59243bf3a333fb748bad01031f19a0353b094` |
| Canonical rates | `cdebce2656d00a29ff9d9575a7d40ba9bfe7c677eb7b1df26ba3f73c481e804c` |
| Public equipment parameters | `3a2965954f9127925edf79f7855b0e1342ad9d07fb8b25bceae80905779583b8` |

The JSON also hashes both production evidence artifacts and the canonical sizing reference. Raw meter/household records stay private.

The PV proxy follows existing rooftop production. It is not a measured portable south-facing system. A two-hour delay is a hypothetical west/delayed sensitivity, not an irradiance model. Shade and winter reductions are assumptions. Both DST transition dates have zero added PV; retained hourly production spreads across meter rows. Every meter row represents 0.25 elapsed hours. Site-specific orientation, mounting, shade, and DC-coupled clipping recovery remain unavailable.

The new base battery assumptions are **20% reserve, 85% round-trip efficiency, and 10 W continuous AC standby per modeled controller**. Those are assumptions, not product measurements. The simple schedule charges allowed surplus solar and super-off-peak grid energy; it discharges off/on-peak. It does not claim optimality or proven firmware support.

The physical model keeps imports, existing exports, added exports, direct PV use, charging sources, losses, curtailment, and SOC separate. SOC means stored DC energy. Each controller uses remaining load and solar. PV plus battery discharge shares the stated new-system AC ceiling. Batteries do not intentionally export. Annual SOC converges before billing; no initial energy subsidy enters savings.

### The preliminary optimistic screen answers a different question

The canonical price-aware screen assumes **90% efficiency, no reserve, no standby, full nominal capacity, and household load access**. Its 1.92 kWh/800 W case saves $524.42/year. A matching simple-schedule case saves $438.12/year before the new reserve/loss/standby assumptions. The STREAM-shaped revised 800 W case saves $295.54/year; the default 360 W case saves $255.68/year.

These results differ by control policy and assumptions. The canonical screen is not a product promise or a proven upper bound. The first preliminary capacity increment also changes power. The revised fixed-power capacity sweep below avoids that particular confounding change.

| Canonical optimistic capacity | Discharge / charge kW | Annual modeled saving |
|---|---|---|
| 1.92 kWh | 0.8 / 1.05 | $524.42 |
| 3.84 kWh | 1.2 / 1.2 | $960.40 |
| 5.76 kWh | 1.2 / 1.2 | $1,261.66 |
| 7.68 kWh | 1.2 / 1.2 | $1,461.62 |
| 11.52 kWh | 1.2 / 1.2 | $1,559.76 |
| 4.096 kWh | 4 / 1.8 | $1,066.84 |
| 6.144 kWh | 7.2 / 1.8 | $1,456.96 |
| 12.288 kWh | 7.2 / 1.8 | $2,284.30 |
| 13.5 kWh | 11.5 / 5 | $2,380.51 |

The 13.5 kWh canonical row reproduces the committed benchmark within $0.002. It remains hypothetical ownership. Hardware standalone payback cannot include the free fix.

## 8. Revised storage results: a conditional ceiling for each configuration

Every result below assumes access to the modeled household load. A directly connected refrigerator or office cannot claim these savings. Output/charge ratings in this table describe model inputs, not approved product installations.

The annual savings and price ceilings retain the NBC convention and unresolved meter-interval interpretation stated in §§4 and 7.

Five- and ten-year columns are **discounted break-even installed-price ceilings before unknown ownership costs**. They use 5% real discount, 1% annual savings decline, and zero real tariff escalation. Installed price, installed-price NPV, and actual asset payback remain unknown.

| Scenario | Capacity; discharge/charge caps | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|---|
| STREAM-shaped default, `stream_single_default_360w` | 1.92 kWh; 0.36/1.05 kW | $255.68 | $1,086.12 | $1,895.41 |
| STREAM-shaped conditional, `stream_single_conditional_800w` | 1.92 kWh; 0.8/1.05 kW | $295.54 | $1,255.43 | $2,190.89 |

The conditional output change adds only **$39.86/year**, with ceilings of **$169.31 over five years** and **$295.48 over ten years**. Those ceilings apply to the extra circuit/control cost, subject to all eligibility gates.

### Capacity at fixed generic 1.2 kW discharge/charge power

| Scenario | Capacity; discharge/charge caps | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|---|
| `battery_1.92kwh` | 1.92 kWh; 1.2/1.2 kW | $288.79 | $1,226.74 | $2,140.82 |
| `battery_3.84kwh` | 3.84 kWh; 1.2/1.2 kW | $666.84 | $2,832.66 | $4,943.34 |
| `battery_5.76kwh` | 5.76 kWh; 1.2/1.2 kW | $939.76 | $3,992.01 | $6,966.56 |
| `battery_7.68kwh` | 7.68 kWh; 1.2/1.2 kW | $1,146.93 | $4,872.06 | $8,502.36 |
| `battery_11.52kwh` | 11.52 kWh; 1.2/1.2 kW | $1,378.52 | $5,855.84 | $10,219.18 |

The 1.92 kWh/1.2 kW row is generic. It exceeds one STREAM Ultra's stated single-unit feed-in power. A single controller's 10 W standby across this capacity sweep does not prove actual multi-unit standby. Measure all units before product sizing.

### Larger, properly connected hypothetical storage

| Scenario | Capacity; discharge/charge caps | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|---|
| `large_4.096kwh_4kw` | 4.096 kWh; 4/1.8 kW | $709.55 | $3,014.12 | $5,260.02 |
| `large_6.144kwh_7.2kw` | 6.144 kWh; 7.2/1.8 kW | $1,071.52 | $4,551.73 | $7,943.35 |
| `large_12.288kwh_7.2kw` | 12.288 kWh; 7.2/1.8 kW | $1,875.01 | $7,964.87 | $13,899.70 |
| `large_12.288kwh_12kw` | 12.288 kWh; 12/1.8 kW | $1,875.13 | $7,965.37 | $13,900.58 |
| `large_13.5kwh_11.5kw` | 13.5 kWh; 11.5/5 kW | $1,965.34 | $8,348.59 | $14,569.33 |
| `large_27kwh_11.5kw` | 27 kWh; 11.5/8 kW | $2,491.56 | $10,583.91 | $18,470.25 |

The 4.096/6.144/12.288 kWh cases assume **1.8 kW charging**. This is not every connection's charging maximum. The 12.288 kWh/7.2 kW row is a DPU-shaped expansion comparison, not an Ultra X claim. The separate 12 kW row tests Ultra X-shaped output. The Powerwall-shaped 13.5 kWh case charges at 5 kW; the 27 kWh case assumes 8 kW charging.

### Extra batteries must earn their own cost

The following are integrated rebill differences, not sums of independent savings. Extra battery cost must fit its own ceiling.

| Increment | Extra annual saving | 5-year extra-cost ceiling | 10-year extra-cost ceiling |
|---|---|---|---|
| 1.92 → 3.84 kWh; fixed 1.2 kW | $378.05 | $1,605.91 | $2,802.52 |
| 3.84 → 5.76 kWh; fixed 1.2 kW | $272.92 | $1,159.35 | $2,023.22 |
| 5.76 → 7.68 kWh; fixed 1.2 kW | $207.17 | $880.05 | $1,535.80 |
| 7.68 → 11.52 kWh; fixed 1.2 kW | $231.59 | $983.78 | $1,716.82 |
| 6.144 → 12.288 kWh; fixed 7.2/1.8 kW | $803.49 | $3,413.14 | $5,956.35 |
| 13.5 → 27 kWh; charge power also rises 5 → 8 kW | $526.22 | $2,235.32 | $3,900.92 |

At the advertised $1,399 per unit, the last generic 3.84 kWh increment costs $2,798 in equipment alone. Its modeled equipment-only simple marginal payback is **12.08 years**. Tax, connection, subscriptions, and replacement can extend that period. It differs from the preliminary optimistic screen; neither is an installed quote.

### A small battery after a hypothetical home battery has lower value

| Controller priority | Total annual hardware saving | Small-system increment versus home battery | 5-year increment ceiling | 10-year increment ceiling |
|---|---|---|---|---|
| Home battery first | $2,197.37 | $232.03 | $985.64 | $1,720.07 |
| Small battery first | $2,250.07 | $284.73 | $1,209.49 | $2,110.72 |

These are alternative orders for one shared household. They are not additive. Sequential priority represents competition for energy/load, not proof that real CT controllers coordinate or remain stable. No home battery exists today, so this is not an installed-baseline purchase case.

## 9. New PV value depends strongly on incremental credit

The DC assumption is 1.25 times the AC cap. It is a scenario sizing choice. The AC-coupled proxy does not recover clipped DC energy directly into a battery.

“Credited” grants the existing NEM treatment to incremental meter exports as a hypothetical case. “Zero credit” removes credit from **all incremental meter exports**, including displaced rooftop exports. Baseline rooftop exports keep credit unless storage consumes them. This counterfactual allocation does not prove legal settlement or identify electrons.

“Zero export” curtails only new residual PV. It allows existing rooftop exports. It assumes perfect incremental control; it is not whole-site zero-export or proven product behavior. Zero-credit and zero-export happen to give equal bills in these cases, but physical exports and curtailment differ.

### Solar without storage

| AC cap / DC size | Credited annual saving | Zero-credit annual saving | Incremental zero-export annual saving |
|---|---|---|---|
| 0.4 kW / 0.5 kW | $237.97 | $56.71 | $56.71 |
| 0.8 kW / 1 kW | $474.96 | $104.12 | $104.12 |
| 1.2 kW / 1.5 kW | $711.72 | $146.87 | $146.87 |

Source IDs: `pv_{AC}kw_{treatment}`. The 0.8 kW AC/1 kW DC credited ceiling is **$2,017.59 over five years**, or **$3,520.94 over ten years**. Without incremental export credit, it falls to **$442.30** or **$771.87**. The same low ceilings apply to the perfect incremental-zero-export case. Preserving existing NEM matters more than assuming new exports receive its credits.

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
| 0.4 kW / 0.5 kW | $655.05 | $497.00 | $497.00 |
| 0.8 kW / 1 kW | $1,058.85 | $743.52 | $743.52 |
| 1.2 kW / 1.5 kW | $1,358.72 | $888.36 | $888.36 |

Source IDs: `combined_{AC}kw_{treatment}`. A larger cap changes both usable PV and discharge opportunities. Do not treat differences across these rows as panel-only value.

### Panel-only increments with a fixed 1.2 kW shared cap

These credited cases keep the same 3.84 kWh battery and output ceiling. They price new panels against storage using the existing array.

| Added panels | Extra annual saving | 5-year extra-cost ceiling | 10-year extra-cost ceiling |
|---|---|---|---|
| No new panels → 0.5 kW DC | $232.74 | $988.66 | $1,725.34 |
| 0.5 → 1 kW DC | $230.71 | $980.06 | $1,710.32 |
| 1 → 1.5 kW DC | $228.43 | $970.34 | $1,693.36 |

These panels require their own installed quotes and lawful export treatment. The JSON also reports PV-only increments under each export treatment. Direct daytime EV/appliance flexibility remains unavailable: vehicle presence and selected-load timing were not measured. Future adoption after NEM ends also remains unavailable; no known future tariff enters this model.

## 10. Physical flows and monthly bills are visible

The generic `battery_3.84kwh` case illustrates source accounting. Its annual modeled charges are **1,282.57 kWh from the grid** and **693.93 kWh from existing solar**. It serves **1,680.03 kWh**, with **296.48 kWh conversion loss** plus **87.60 kWh continuous AC standby**. These are modeled flows, not measured product performance.

Its modeled equivalent full cycles are **1.30/day**. Its peak import rises from **20.16 to 20.17 kW**. The 13.5 kWh scheduled case reaches **22.83 kW**. Cheap-hour charging does not prove service headroom. An electrician needs the actual simultaneous loads and charging topology.

| Bill component, generic 3.84 kWh case | Modeled annual saving; negative means added cost |
|---|---|
| `delivery_usd` | $365.03 |
| `generation_usd` | $311.55 |
| `pcia_usd` | $-16.26 |
| `nbc_usd` | $6.51 |
| `fixed_usd` | $0.00 |

PCIA is the power charge indifference adjustment. NBC means non-bypassable charges. The NBC row uses modeled gross imports; it does not establish the tariff's metered-interval energy base. The unchanged daily electric charge and gas costs do not disappear through storage.

### Monthly modeled electric bills: comparison, not statement totals

The endpoints are partial calendar months. All scenarios' complete bill components, physical/credited exports, charging sources, cycles, curtailment, and peak imports appear in the JSON monthly records.

| Calendar month | After free fix, before hardware | Generic 3.84 kWh battery |
|---|---|---|
| 2025-07 | $88.47 | $70.13 |
| 2025-08 | $502.67 | $432.96 |
| 2025-09 | $542.65 | $472.58 |
| 2025-10 | $433.70 | $356.86 |
| 2025-11 | $287.38 | $245.89 |
| 2025-12 | $344.59 | $296.04 |
| 2026-01 | $314.06 | $263.45 |
| 2026-02 | $230.50 | $188.33 |
| 2026-03 | $216.21 | $164.28 |
| 2026-04 | $142.12 | $94.49 |
| 2026-05 | $131.02 | $88.16 |
| 2026-06 | $264.12 | $201.39 |
| 2026-07 | $185.80 | $141.87 |

Synthetic tests cover conservation, reserve/power/shared caps, empty/full batteries, absent load/generation, simultaneous meter channels, annual SOC, DST, missing inputs, and controller overlap. The generator fails on absent required evidence or failed production checks. Reproduction commands and pipeline gates appear in [TECHNICAL.md](../TECHNICAL.md). A successful model run does not establish product eligibility or measured circuit performance.

## 11. Financial sensitivity: ceilings precede the missing quote

For annual hardware saving `S`, the pre-cost ceiling for `N` years is `sum(S × 0.99^(y−1) / 1.05^y, y=1..N)`. NPV subtracts the complete initial cost and discounted future ownership costs. Simple asset payback divides complete hardware cost by hardware-only annual saving. Marginal payback uses only the increment's cost and saving. These are distinct from package savings that include free behavior.

The [IRS](https://www.irs.gov/credits-deductions/residential-clean-energy-credit) states that property placed in service after December 31, 2025 does not qualify for homeowner Section 25D. This study uses **zero federal homeowner credit**. Other incentives, VPP eligibility/value, and monetary backup value remain unknown. No credit fills the quote gap.

### Assumed complete-cost ladder for `battery_3.84kwh`

These are assumed costs, not quotes. Higher rungs also show cost-overrun exposure.

| Assumed complete installed cost | Simple asset payback | 5-year NPV | 10-year NPV |
|---|---|---|---|
| $1,000.00 | 1.50 years | $1,832.66 | $3,943.34 |
| $2,500.00 | 3.75 years | $332.66 | $2,443.34 |
| $5,000.00 | 7.50 years | $-2,167.34 | $-56.66 |
| $7,500.00 | 11.25 years | $-4,667.34 | $-2,556.66 |

### Hypothetical tariff and availability stresses

The tariff stresses rebill both baseline and hardware flows. They scale non-super-off-peak delivery/generation spreads around the same-season super-off-peak rate. They keep physical schedule flows, NBC, PCIA, and fixed charges unchanged. They are not future rate forecasts.

| Spread relative to frozen case | Annual saving | 5-year ceiling | 10-year ceiling |
|---|---|---|---|
| 0.5× | $313.76 | $1,332.82 | $2,325.94 |
| 0.75× | $490.30 | $2,082.74 | $3,634.64 |
| 1.25× | $843.37 | $3,582.57 | $6,252.04 |

An 80% cashflow availability assumption lowers the generic 3.84 kWh ceilings to **$2,266.12/$3,954.67** for five/ten years. A separate physical scenario disconnects the battery for each month's first three days. It saves **$599.73/year**; this is not the same operation as a cashflow multiplier.

At fixed 3.84 kWh, reserve assumptions of 10/20/30% give **$743.27/$666.84/$582.28 per year**. Efficiency assumptions of 80/85/90% give **$631.50/$666.84/$700.38**. Standby assumptions of 0/10/30 W give **$697.86/$666.84/$603.06**. None substitutes for measured hardware losses.

Solar-only, grid-only, and mixed charging give **$436.31/$751.88/$666.84 per year** for that case. A simple mixed controller can perform worse than grid-only because it diverts credited exports and follows different charging priorities. This is a controller result, not a claim that solar charging is intrinsically inferior.

### The 2030 transition needs a cost case, not an assumed shutdown

Assume hypothetical 2027 commissioning. Continued lawful operation without extra cost keeps the generic 3.84 kWh ceilings at **$2,832.66/$4,943.34**. An assumed $500 compliance cost in year four lowers them to **$2,421.30/$4,531.99**. An assumed $1,500 cost lowers them to **$1,598.60/$3,709.29**.

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

All entries were checked on 2026-10-22. Statute text, current tariffs, manuals, advertisements, and measured household performance have different authority.

### Regulatory and settlement sources

| Source | Version/date evidence | Supports; limitation |
|---|---|---|
| [Official SB 868 endpoint](https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=202520260SB868), [chaptered mirror](https://legiscan.com/CA/text/SB868/2025), [Governor](https://www.gov.ca.gov/2026/09/30/governor-newsom-issues-legislative-update-9-30-2026/) | Chapter 985; September 30, 2026 | Final text fallback disclosed; official endpoint inaccessible |
| [Secretary of State](https://www.sos.ca.gov/administration/bill-chapters) | General effective-date rule | January 2027 inference; no household interpretation |
| [NEM-ST](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=871&utilId=SDGE), [Rule 21](https://tariffsprd.sdge.com/view/tariff/?bookId=ELEC&tarfKey=96&utilId=SDGE) | Current compiled PDFs; relevant sheet dates stated above | Existing tariff pathways; no established exact SB 868 household treatment |
| [CEA Personal Impact](https://thecleanenergyalliance.org/personal-impact/) | Current program page | Existing settlement terms; no new-device compensation determination |
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
| Baseline/reference reproduction; window, rates, hashes, limitations | **Achieved for the implemented model** | §7; artifact `baseline`, `provenance`, `canonical_no_reserve_benchmarks`; historical residual and gross-import NBC convention stay disclosed; tariff reconciliation remains open |
| Solar-only, storage-only, combined, selected-load, larger options; standalone/incremental savings | **Incomplete** | Generic families and increments exist; real selected-appliance/circuit interval energy, surge, access and wiring are absent |
| Conservation, SOC/reserve, charge/discharge/shared caps, annual balance; losses/standby counted | **Achieved for stated interval assumptions** | Model and synthetic tests; measured losses, subinterval flows and real controller performance remain separate unknowns |
| Monthly bills/flows/self-use/sources/cycles/peaks/curtailment/runtime/installed cost/payback/NPV | **Incomplete** | JSON carries modeled accounts and conditional runtime; actual circuit runtime, complete quote, ownership costs, installed payback/NPV absent |
| Missing inputs/DST/zero generation/no load/full-empty/net-credit/simultaneous flows/two-controller tests; byte identity | **Achieved for the model** | Synthetic suite and deterministic generator checks; independent project gates remain needed before publication |

### Decision and pilot

| Issue criterion | State | Evidence or exact missing input |
|---|---|---|
| Buy/pilot/wait/do-not-buy decision with reasons/dependencies | **Achieved** | WAIT for grid-parallel; conditional direct-appliance measurement only; §§1–6 and 13 |
| Maximum worthwhile installed price; each battery/panel increment | **Partly achieved** | Conditional discounted ceilings and increments exist in §§8–11 without a quote. Supported product-specific ceilings need load/loss/connection evidence and ownership-cost assumptions |
| If justified, pilot with meters/reserve/control/load/success criteria and matched baseline | **Conditional and unexecuted** | Proposed protocol in §13; selected load, duration/budget, exact unit, completed metering and control tests absent. A pilot is not an unconditional prerequisite to a desk recommendation to wait |
| Backup priced separately; circuits established; no unsupported rooftop recharge | **Incomplete** | No rooftop recharge assumed; owner backup value, essential circuits/surges, supported transfer/PV design and outage evidence absent |
| Report/glossary/consistency updates only after study supports claims | **Portable-study publication deferred** | Portable-study prerequisites remain open. The existing report and glossary include supported tariff qualifications; those changes do not establish portable-equipment eligibility |

### Additional requested comparisons that remain unavailable

| Comparison | Exact missing evidence |
|---|---|
| Site-specific south/west/shade/seasonal PV | Chosen placement, measured shade/horizon, orientation/tilt, permitted mounting, appropriate irradiance/yield model |
| Daylight EV or flexible appliances before storage | Vehicle presence, charging sessions, appliance flexibility and measured schedules |
| Adoption after NEM grandfathering | Exact future tariff and supported connection remain unknown. A separately modeled, clearly labeled present-NBT counterfactual could still support sensitivity analysis |
| Real control interaction and split-phase access | Circuit/leg/CT diagram, supported controller topology, transient/feedback and offline tests |
| Measured equipment and multi-day backup | Usable AC capacity, loss/standby curves, reserve behavior, essential-load traces, surge and outage-weather/design evidence |

**Next action: record the name of one appliance to meter.**
