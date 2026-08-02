# Glossary

Plain-English definitions of every term of art used in this home-energy analysis (the report in `index.html` and its companion docs), written for homeowners rather than engineers; terms link to authoritative public sources where available.

---

## Billing, rates & tariffs

**AB 205 fixed charge (income-graduated fixed charge)** — The 2022 California law, implemented by CPUC Decision D.24-05-028, that restructured residential electric bills around a flat monthly fixed charge (with income-based CARE/FERA discounts) offset by lower per-kWh rates. For SDG&E it took effect as the $24.15/month Base Services Charge billed from October 2025 — it is already in this report's bills and model, not a looming future scenario. [CPUC Decision D.24-05-028](https://docs.cpuc.ca.gov/PublishedDocs/Published/G000/M531/K686/531686019.PDF)

**Avoided Cost Calculator (ACC)** — The CPUC's official hourly model of what a unit of customer generation actually saves the grid; it sets the export credit values under the Net Billing Tariff. This report prices its NEM 2.0 grandfathering value and the battery's NBT-era marginal value against SDG&E's own real, hourly ACC-derived export-pricing table (`analysis/nem3_grandfathering.py`); a flat 3–8¢/kWh bracket remains in use only as a supplementary sensitivity check for the battery figures (`extended_findings.py`'s `nbt_2039`). [CPUC: Avoided Cost Calculator](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/demand-side-management/energy-efficiency/idsm)

**Base Services Charge (BSC)** — A fixed charge of about $0.79 per day that every SDG&E residential customer pays just for being connected, regardless of how much energy is used. Because all residential plans now carry the same BSC, it doesn't affect which plan is cheapest. [SDG&E: electric billing & the BSC](https://www.sdge.com/electric-billing)

**Baseline allowance** — A monthly amount of energy priced at the lowest tier, set by your climate zone, the season, and your billing-cycle length. It matters on tiered plans; the EV plans in this report don't use it. [SDG&E baseline allowance calculator](https://www.sdge.com/baseline-allowance-calculator)

**California Climate Credit** — A credit (roughly $40–80) applied automatically to California utility bills about twice a year, funded by the state's carbon cap-and-trade program. It reduces what you pay out of pocket but isn't a usage charge, so this report tracks it separately. [CPUC: California Climate Credit](https://www.cpuc.ca.gov/consumer-support/financial-assistance-savings-and-discounts/california-climate-credit)

**CCA (Community Choice Aggregation)** — A program that lets cities buy electricity on behalf of their residents instead of the utility doing it. The utility (SDG&E) still delivers the power and sends the bill; only the "generation" line changes. [CalCCA](https://cal-cca.org)

**CEA (Clean Energy Alliance)** — The CCA serving several North San Diego County cities; it is this home's generation provider. Its "Clean Impact Plus" product is the specific offering shown on the bills. [Clean Energy Alliance](https://thecleanenergyalliance.org)

**Climate zone** — SDG&E divides its territory into Coastal, Inland, Mountain, and Desert zones, which set the baseline allowance (milder zones get smaller allowances). This home is in the Coastal zone. [SDG&E climate zone map](https://www.sdge.com/baseline-allowance-calculator)

**EECC (Electric Energy Commodity Cost, "bundled generation")** — SDG&E's own price for the energy itself, charged to customers who have *not* switched to a CCA. "Bundled" means SDG&E supplies both delivery and generation. [SDG&E total electric rates](https://www.sdge.com/total-electric-rates)

**Grandfathering** — Being allowed to stay on an old, more favorable set of rules after the rules change for new customers. This home's solar is grandfathered on NEM 2.0 for about 20 years from its 2019 turn-on date — worth **$2,103.58–$2,455.64 per year** versus the current Net Billing Tariff, re-billing the same measured year against SDG&E's real hourly export-pricing table (the range reflects a genuine, unresolved question about this home's CCA generation credit, not uncertainty in its own usage data). [CPUC: NEM and Net Billing](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/demand-side-management/customer-generation/net-energy-metering-and-net-billing)

**kWh vs kW** — A kilowatt (kW) is a *rate* of energy use, like speed; a kilowatt-hour (kWh) is an *amount* of energy, like distance traveled. Running a 1 kW appliance for one hour uses 1 kWh. [EIA: measuring electricity](https://www.eia.gov/energyexplained/electricity/measuring-electricity.php)

**NBT (Net Billing Tariff) / Solar Billing Plan** — The rules for California solar systems connected since April 2023 (sometimes called "NEM 3.0"). Exports are credited at the grid's "avoided cost" — often just 3–8¢/kWh — instead of near-retail rates, which is why keeping NEM 2.0 status matters so much. [CPUC: NEM and Net Billing](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/demand-side-management/customer-generation/net-energy-metering-and-net-billing)

**NEM 1.0 / NEM 2.0 (Net Energy Metering)** — Older solar billing rules under which energy you export to the grid earns credits at close to the full retail rate. NEM 2.0 (this home's version) deducts small "non-bypassable charges" (~2.1¢/kWh) from export credits and requires a time-of-use plan. [CPUC: NEM and Net Billing](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/demand-side-management/customer-generation/net-energy-metering-and-net-billing)

**Net metering true-up / relevant period** — Solar customers' energy charges and export credits accumulate on a running ledger over a 12-month "relevant period," then settle in one annual "true-up" statement. Monthly bills during the year mostly collect fixed and non-bypassable charges. [SDG&E: net energy metering](https://www.sdge.com/more-information/customer-generation)

**Non-bypassable charges (NBC)** — A few cents per kWh that fund public programs (low-income assistance, efficiency, nuclear decommissioning, the state wildfire fund) and must be paid on all grid imports — they can't be offset by solar export credits. The wildfire fund charge is one of these line items. [CPUC: NEM and Net Billing](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/demand-side-management/customer-generation/net-energy-metering-and-net-billing)

**On-peak / off-peak / super-off-peak** — The three price windows on SDG&E's time-of-use plans. On-peak (4–9pm daily) is the most expensive (60–87¢/kWh all-in on this home's plan); super-off-peak (overnight, plus weekday 10am–2pm on the EV plans) is the cheapest (~12.5¢); off-peak is everything in between (~51¢ here). [SDG&E pricing plans](https://www.sdge.com/residential/pricing-plans)

**PCIA (Power Charge Indifference Adjustment)** — A per-kWh "exit fee" CCA customers pay SDG&E to cover long-term power contracts the utility signed before they left. It keeps remaining bundled customers from bearing those legacy costs alone. [CPUC: PCIA](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/electric-costs/power-charge-indifference-adjustment)

**Plan names (EV-TOU-5, EV-TOU-2, TOU-DR1, TOU-DR2, TOU-DR-P, TOU-ELEC, DR)** — SDG&E's residential rate plans. The "EV" plans offer very cheap super-off-peak power for overnight vehicle charging; the "TOU-DR" plans are general time-of-use plans; DR is the old tiered (non-TOU) plan, which NEM 2.0 customers can't use. [SDG&E pricing plans](https://www.sdge.com/residential/pricing-plans)

**Rate escalation** — The assumed yearly percentage increase in electricity prices. SDG&E's recent history has run in the high single digits per year, which shortens battery and solar payback estimates.

**Rate vintage** — Which edition of the rate tables a dollar figure was computed with. This report's model uses June 2026 rates, while the actual bills were rendered largely on cheaper 2025 rates — that vintage gap, not a modeling error, explains why modeled totals run higher than the real bills.

**Reduce Your Use / demand-response events** — Occasional utility-called days (on plans like TOU-DR-P) when customers pay a steep surcharge (~$1.16/kWh) for use during the event window, in exchange for lower rates the rest of the year. [SDG&E pricing plans](https://www.sdge.com/residential/pricing-plans)

**Therm** — The billing unit for natural gas, roughly the energy in 100 cubic feet of gas (about 29 kWh of heat). This home uses ~342 therms/yr, mostly for winter space heating. [EIA: energy units & calculators](https://www.eia.gov/energyexplained/units-and-calculators/)

**TOU (time-of-use)** — Any rate plan where the price per kWh depends on the time of day and season rather than being flat. The whole strategy of this report — charge the EV overnight or midday, avoid 4–9pm — comes from exploiting TOU price differences. [SDG&E pricing plans](https://www.sdge.com/residential/pricing-plans)

**UDC (Utility Distribution Company, "delivery")** — SDG&E in its role as owner of the wires: the delivery portion of each kWh's price. Every customer pays UDC delivery charges regardless of who supplies the generation. [SDG&E rates & regulations](https://www.sdge.com/rates-and-regulations)

---

## Solar & hardware

**Capacity factor** — Actual annual energy output divided by what the system would make running at full rated power 24/7. This array's ~18.7% is healthy for rooftop solar (the sun is only up part of the day). [Wikipedia: capacity factor](https://en.wikipedia.org/wiki/Capacity_factor)

**Clipping** — When panels can momentarily produce more DC power than the inverter's AC limit, the inverter "clips" the excess and it's lost. This system's 5-minute data shows no meaningful clipping — peak output stays ~10% below the inverter ceiling.

**CT (current transformer) / consumption metering** — Small clamp sensors on the home's wiring that let the solar gateway measure whole-home usage, not just solar production. Having them is what made the load and battery analysis in this report possible.

**DC vs AC rating** — Panels are rated in DC watts (here 10,050 W); inverters cap output in AC watts (here ~9,450 W). A modest DC-over-AC ratio is normal design, since panels rarely hit their lab-rated maximum on a roof.

**Degradation** — The slow decline in panel output with age, typically ~0.5–1%/yr. Six years of records show this array aging normally with no failing equipment.

**Enphase Enlighten** — The monitoring website/app for Enphase solar systems, showing per-panel production; the source of this report's hourly consumption and daily production data. Other brands (SolarEdge, Tesla, SMA) have equivalents. [Enphase Enlighten](https://enlighten.enphaseenergy.com)

**Microinverter** — A small inverter mounted under each solar panel that converts that one panel's DC power to household AC, instead of one big central inverter for the whole array. Each panel operates and reports independently. [Wikipedia: solar micro-inverter](https://en.wikipedia.org/wiki/Solar_micro-inverter)

**PTO (permission to operate)** — The utility's formal green light to switch on a new solar system, after inspection and paperwork. The PTO date (here December 2019) starts the clock on NEM grandfathering. [SDG&E customer generation](https://www.sdge.com/more-information/customer-generation)

**PVOutput** — A free public website where solar owners publish their systems' output; it provides this report's independent multi-year production record. [pvoutput.org](https://pvoutput.org)

**Rule 21 (interconnection)** — The CPUC tariff governing how customer solar and battery systems connect to the utility grid. Relevant here because a battery interconnected under a non-export agreement needs a Rule 21 modification before it can export in a virtual power plant; storage additions do not affect NEM 2.0 grandfathering either way. [CPUC: Rule 21 interconnection](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/infrastructure/rule-21-interconnection)

**SAM 8760** — An Enphase report of hourly whole-home consumption for a calendar year — 8,760 values, one per hour (24 × 365). Named for NREL's System Advisor Model, which uses this format. [NREL SAM](https://sam.nrel.gov)

**Self-consumption vs export** — Solar energy used inside the house the moment it's made (full retail value) versus surplus sent to the grid (credited at lower export rates). This home exports 60% of its production — the central timing mismatch the report addresses.

**Soiling** — Dirt, dust, and grime on panels that blocks sunlight and cuts output until rain or a cleaning washes it off. This report measures soiling here at roughly 0.5–2.4% lost per dry month. [Wikipedia: soiling (solar energy)](https://en.wikipedia.org/wiki/Soiling_(solar_energy))

**Specific yield** — Annual production per kW of installed panels (kWh/kW/yr), which lets you compare systems of different sizes. This array's 1,642 kWh/kW/yr is solid for a coastal roof.

**V2H (vehicle-to-home)** — Future capability for an EV to power the house from its own battery. Relevant because a car's 60–100 kWh pack dwarfs any wall battery and would change the storage math.

---

## Batteries & storage

**Arbitrage** — Buying (or storing) energy when it's cheap and using it when it's expensive. A home battery earns most of its keep by storing ~8–14¢ energy and displacing 51–87¢ grid imports.

**Cycle** — One full charge-and-discharge of the battery's capacity. Warranties typically assume about one cycle per day; the recommended dispatch here runs ~1.0 cycle/day.

**Dispatch policy (evening-only / two-window / price-aware)** — The rules that decide *when* the battery discharges. Evening-only covers just the 4–9pm peak; two-window adds the morning shoulder; price-aware discharges against every import priced above what the stored energy cost — worth ~$600/yr more than evening-only here, from settings alone.

**DSGS (Demand Side Grid Support)** — A California Energy Commission program that pays customers — including home-battery owners enrolled through their manufacturer's virtual power plant — for reducing load or exporting during summer grid emergencies. It is the one live battery revenue program in this territory, and it explicitly permits stacking with time-of-use bill savings. A real household enrolls through exactly one of ~14 anonymized VPP aggregations, not all of them, so this was backtested per aggregation, not as a single number: against the real 2025 event calendar and this household's own measured load, a hypothetical Powerwall 3 would have net-earned **$97–$213** across the 14 individual aggregation schedules, over the observed 2025-07-24–2025-10-30 partial season (not an annual figure; 20% reserve assumption; every 2025 event hour was a mandatory monthly test, not a real grid emergency; July 2025 is excluded from revenue since it has event hours on both sides of the measured window and can't be validly priced from an incomplete subset). The all-aggregations union figure often cited as a single point estimate, $139.95, sits inside that range, not above it. A full-season or annual figure is not determined, so both are reported as an amount DSGS would add on top of the battery's own arbitrage payback, never combined into a payback-year figure. The CEC's 2026-season restriction is on aggregators, not households — it freezes which VPP aggregators may participate (those active in October 2025), not whether a new household's battery can join one that already qualifies; each qualifying aggregator's 2026 payment is separately capped at its own October-2025 share of program funds, a disincentive to add new sites but not a rule against it. Whether this household's own aggregator would accept a new enrollment is not determined from the public, anonymized CEC data. [CEC: Demand Side Grid Support Program](https://www.energy.ca.gov/programs-and-topics/programs/demand-side-grid-support-program)

**ELRP (Emergency Load Reduction Program)** — A CPUC demand-response pilot paying roughly $2/kWh for load reduction or battery export during called grid-emergency events. The Tesla–SDG&E ELRP virtual power plant closed to new enrollment in 2024, so it counts $0 in this report's battery revenue survey. [CPUC: ELRP](https://www.cpuc.ca.gov/elrp)

**Powerwall 3 (PW3)** — Tesla's current home battery (13.5 kWh storage, 11.5 kW output, expandable), used as the reference hardware in this report's simulations. Enphase's IQ Battery line is the modeled alternative. [Tesla Powerwall](https://www.tesla.com/powerwall)

**Round-trip efficiency** — The fraction of energy put into a battery that comes back out, after charging and inverter losses; modeled here at 90% (store 10 kWh, get back 9).

**State of charge (SOC)** — How full the battery is right now, as a percentage. Outage-endurance figures depend on the SOC when the power goes out. [Wikipedia: state of charge](https://en.wikipedia.org/wiki/State_of_charge)

**VPP (virtual power plant)** — A fleet of home batteries (or other flexible resources) coordinated by software to act like a single power plant, discharging together during grid events in exchange for payments to each owner. Here it is the enrollment vehicle for DSGS: Tesla's app aggregates Powerwalls and passes the program credits through. [Tesla: Virtual Power Plant](https://www.tesla.com/support/energy/virtual-power-plant)

---

## Grid & markets

**Average emissions rate** — The grid-wide average kg CO₂ per MWh generated at a given time, computed from every generation source dispatched in that period — what `carbon_fullyear.py` actually computes from CAISO's public hourly CO₂-by-source and demand data. It answers "how clean is the grid mix right now," not "what happens if I add or remove one more kWh of demand" — that second question needs the marginal rate below. [CAISO emissions](https://www.caiso.com/todays-outlook/emissions)

**CAISO (California Independent System Operator)** — The nonprofit that runs California's high-voltage grid and wholesale electricity market, and publishes real-time data on demand, supply, and emissions. This report's grid-carbon numbers come from its public "Today's Outlook" data. [CAISO Today's Outlook](https://www.caiso.com/todays-outlook)

**Duck curve** — The shape of California's daily grid demand after subtracting solar: a midday belly (lots of sun) and a steep evening neck (sun sets while demand peaks). It explains why midday power is cheap and clean while 4–9pm is expensive. [DOE: confronting the duck curve](https://www.energy.gov/eere/articles/confronting-duck-curve-how-address-over-generation-solar-energy)

**Grid carbon intensity** — How much CO₂ is emitted per unit of grid electricity (kg CO₂/MWh), which varies by hour. Measured from CAISO data, midday grid power here is about 2.2× cleaner than overnight power. [CAISO emissions](https://www.caiso.com/todays-outlook/emissions)

**Marginal emissions rate** — The emissions rate of the specific generating resource that would ramp up or down to meet one more (or one less) kWh of demand at a given moment — usually whichever fuel sits at the margin, often natural gas even in hours when the average mix looks clean (midday, when solar supplies most average generation but a load spike gets met by a gas peaker). This report does not compute a marginal rate: CAISO's public "Today's Outlook" history endpoints — the only source `carbon_fullyear.py` fetches (`carbon_dispatch_tradeoff.py` only reads its already-committed output, `data/caiso_hourly_intensity.csv`) — don't publish one. A marginal accounting would likely show the day/night carbon gap this report measures as even larger, not smaller, since gas is disproportionately the marginal resource at exactly the hours (overnight) where this report already reports the dirtiest average intensity. [WattTime: what is marginal emissions](https://www.watttime.org/aer/what-is-marginal-emissions/)

**PSPS (Public Safety Power Shutoff)** — A deliberate, pre-announced utility shutoff during dangerous fire weather, concentrated in high fire-threat districts. Coastal urban circuits like this home's sit outside those tiers — the December 2024 PSPS added roughly zero coastal outage minutes — which is why this report prices resilience low here. [CPUC: PSPS](https://www.cpuc.ca.gov/psps/)

**SAIDI / SAIFI** — The standard utility reliability indices (defined by IEEE Standard 1366): SAIDI is the average outage minutes per customer per year, SAIFI the average number of interruptions per customer. Read from SDG&E's Electric System Reliability Annual Report, they turn "what is backup worth?" into an expected outage-hours-per-year figure (~1–2.5 h/yr for this coastal district). [CPUC: electric reliability reports](https://www.cpuc.ca.gov/industries-and-topics/electrical-energy/infrastructure/electric-reliability)

---

## Home loads & electrification

**Degree-days (CDD/HDD)** — A weather yardstick: each degree the day's average temperature sits above 65°F adds one cooling degree-day (CDD); below 65°F, one heating degree-day (HDD). They let the report estimate how much of the bill is air conditioning and predict hot-vs-mild-summer costs. [EIA: degree-days](https://www.eia.gov/energyexplained/units-and-calculators/degree-days.php)

**Electrification** — Replacing gas appliances (water heater, furnace) with efficient electric ones. Here the heat-pump water heater is the one swap that pencils out, best done when the old unit fails.

**Electrification dividend** — This report's term for the money already being saved by driving on electricity instead of gasoline: the measured home-charging cost (plus estimated supercharging) versus what the same miles would cost at the cited state gasoline price and fleet fuel economy — about $3,230/yr here today, rising toward ~$4,440/yr once all charging lands super-off-peak.

**HPWH (heat-pump water heater)** — A water heater that moves heat from the surrounding air into the tank rather than making heat directly, using roughly a third the energy of a standard electric unit. On a midday timer it can run largely on this home's surplus solar. [DOE: heat pump water heaters](https://www.energy.gov/energysaver/heat-pump-water-heaters)

**Phantom load / always-on baseload** — Power the house draws around the clock even when "nothing" is on: refrigeration, pool pumps, chargers, standby electronics. This home's overnight floor is ~1 kW (~$1,800/yr gross), though only part of that is realistically recoverable.

**Pre-cooling** — Running the A/C hard during cheap midday hours (often on your own solar) so the house coasts through the expensive 4–9pm window with the thermostat eased up.

---

## Electrical service & panel

*Section numbers in this group follow the 2020 National Electrical Code (NFPA 70). Earlier and later editions renumber and reword some of them.*

**AHJ (authority having jurisdiction)** — Whoever actually enforces the electrical code where you live: usually the city or county building department and its inspector. Code sections that say a method is allowed "where acceptable to the authority having jurisdiction" mean the inspector can decline your evidence, so a calculation done at home is a scoping exercise until they sign it.

**Backfeed breaker** — A breaker that carries power *into* the panel instead of out to a load — the one a solar inverter or a battery connects through. Both its rating and its position on the busbar matter, because the bar has to carry the utility's supply and the backfed supply at the same time. The 120% busbar rule turns on where it sits and on 125% of the inverter's output current rather than on the breaker's own rating; the sum-of-breakers alternative counts the rating. Changing it is the kind of change Rule 21 interconnection paperwork covers.

**Existing-dwelling load calculation** — Sizing a service from what a house has actually drawn rather than from what its equipment could theoretically draw. The paper route — the standard method in Article 220 Part III, or the optional dwelling-unit calculation in NEC 220.82 — adds up nameplate ratings and applies fixed demand factors, assumed diversity, since not everything runs at once. The existing-dwelling method (NEC 220.87) replaces that assumption with measurement: real recorded demand from the meter. For a house with a year of interval data the measured route is both easier and tighter, because the assumed factors are deliberately conservative.

**MCA (minimum circuit ampacity)** — The current a piece of equipment's branch circuit must be able to carry, printed on the appliance's nameplate (heat pump, heat-pump water heater, EV charger). It already includes the code's continuous-load margin, so it runs above the appliance's actual running current. It is the figure you add to an existing load when checking whether a new appliance fits the service.

**NEC 220.87 ("Determining Existing Loads")** — The code section that lets an existing dwelling's calculated load be taken from metered history instead of a nameplate tally. The main route, 220.87(1), uses the maximum demand data for a 1-year period; that demand at 125% plus the new load must not exceed the ampacity of the feeder or the rating of the service, and the feeder or service must carry the overcurrent protection required by 240.4 and 230.90 respectively. An Exception permits a maximum demand continuously recorded over a minimum 30-day period at 15-minute intervals where a year of data is not available — but that Exception is not permitted if the feeder or service has a renewable energy system (solar photovoltaic or wind electric) or employs any form of peak load shaving. A house with solar therefore has no 30-day shortcut and needs the full year, which is what utility interval data gives you. Whether the record you bring counts as acceptable evidence is still the inspector's call under the code's general meaning of *approved*, not a condition written into this section. [NFPA 70 (National Electrical Code)](https://www.nfpa.org/codes-and-standards/nfpa-70-standard-development/70)

**NEC 705.12(B)(3)(2) (the "120% busbar rule")** — The limit on how much backfed solar or battery current a panel can accept: 125% of the power source's output circuit current plus the rating of the overcurrent device protecting the busbar (the main breaker) may not exceed 120% of the busbar's ampacity, and the two sources must sit at opposite ends of a bus that carries loads. The 20% allowance exists because supply from both ends never fully stacks on any one span of bar. Older editions counted the backfeed breaker's rating rather than 125% of source current; since a breaker is sized at or above that figure and rounded up to a standard size, the old form is the stricter of the two and still turns up on worksheets. [NFPA 70 (National Electrical Code)](https://www.nfpa.org/codes-and-standards/nfpa-70-standard-development/70)

**NEC 705.12(B)(3)(3) (the "sum of breakers" rule)** — The alternative to the 120% rule: the ampere ratings of all overcurrent devices on the panelboard, load devices as well as supply devices, excluding the one protecting the busbar, added up must not exceed the busbar's ampacity. It sets no requirement about where the backfeed sits, but because it counts the load breakers too, a fully populated house panel rarely passes it. A third option, 705.12(B)(3)(1), requires 125% of the source's output circuit current plus the rating of the overcurrent device protecting the busbar to stay within the busbar's ampacity — no opposite-end condition, and no 20% allowance either. [NFPA 70 (National Electrical Code)](https://www.nfpa.org/codes-and-standards/nfpa-70-standard-development/70)

**Noncoincident loads (NEC 220.60)** — Loads that cannot run at the same time — an A/C compressor and an electric furnace, say — of which only the largest is counted in a load calculation. The rule has a second half: if a motor or air-conditioning load is part of the noncoincident load and is not the largest of them, 125% of either the motor load or the air-conditioning load, whichever is larger, is used in the calculation. Measured demand data settles which loads actually coincide instead of leaving it to assumption.

---

## Money & incentives

**ITC (Investment Tax Credit)** — The federal tax credit (30% for this system's 2019 vintage) for solar and battery purchases; it expired for residential systems at the end of 2025, so this report's battery math assumes no ITC. [IRS: Residential Clean Energy Credit](https://www.irs.gov/credits-deductions/residential-clean-energy-credit)

**NPV (net present value)** — The value today of a stream of future savings, after discounting because a dollar later is worth less than a dollar now. A positive NPV means the investment beats the chosen discount rate. [Wikipedia: net present value](https://en.wikipedia.org/wiki/Net_present_value)

**SGIP (Self-Generation Incentive Program)** — California's rebate program for home batteries (~$200/kWh in the tier once relevant here). Its general-market budget closed December 31, 2025, so this report's battery math counts no SGIP; the remaining budgets serve income-qualified equity and resilience customers. [SGIP program site](https://www.selfgenca.com)

**Simple payback** — Purchase price divided by annual savings: the years to break even, ignoring interest, discounting, and rate increases. Crude but honest — this report quotes it alongside NPV.

---

## Methods, data & statistics

**Clear-sky normalization** — Dividing each day's solar output by what a perfectly clear day would theoretically produce (via the Haurwitz model), so weather is factored out and effects like soiling become visible.

**Counterfactual** — A "what if" version of the year — e.g., the same house with no solar, or with the EV charged at better times — re-billed under the same rules so the difference isolates one change's dollar value.

**Diff-in-differences (difference-in-differences)** — Comparing the before-vs-after change in the year something happened (the 2024 panel cleaning) against the same calendar window in normal years, so seasonal decline doesn't get mistaken for the cleaning's effect. [Wikipedia: difference in differences](https://en.wikipedia.org/wiki/Difference_in_differences)

**Green Button data** — A standardized download of your own detailed meter data (here, 15-minute electric imports/exports) available from your utility's website. It is the raw material for this entire analysis. [Green Button](https://www.greenbuttondata.org)

**Interval data** — Meter readings recorded in short fixed steps (15 minutes here) rather than monthly totals, making it possible to price every slice of the day under any rate plan.

**Measured / modeled / estimated** — This report's three confidence labels: **measured** = read directly from meters, bills, or multi-source-verified records; **modeled** = computed with billing/dispatch engines validated against the actual bills; **estimated** = built on approximate inputs, so treat as order-of-magnitude.

**Monte Carlo** — Running a calculation thousands of times with the uncertain inputs randomly varied, to see the range of plausible outcomes rather than one number — used here for a conservative battery-payback range. [Wikipedia: Monte Carlo method](https://en.wikipedia.org/wiki/Monte_Carlo_method)

**Weather normalization** — Adjusting energy figures for how hot, cold, or cloudy a period was (via degree-days or clear-sky models) so that year-to-year comparisons reflect the equipment and behavior, not the weather.

**Charge Stats (Tesla app)** — the per-vehicle charging summary in the Tesla mobile app: trailing-12-month energy added, split by location (home / Supercharger / other) and TOU bucket. Reports battery-side kWh (energy into the pack), which runs 8–12% below wall-side meter readings due to charging losses. [Tesla: charging support](https://www.tesla.com/support/charging)

**Wall Connector** — Tesla's hardwired home charging unit. Networked models log per-day delivered energy (wall-side kWh), exportable from the app/portal; note its uploads can batch under poor connectivity, shifting energy across the final days of an export. [Tesla: Wall Connector](https://www.tesla.com/support/wall-connector)

**Rated miles** — the range figure a Tesla adds per kWh charged at its EPA-rated efficiency. Real-world consumption typically achieves ~80–90% of rated, so "miles added" in Charge Stats exceeds odometer miles driven.

**MPPT (maximum power point tracking)** — the electronics that continuously adjust a panel's operating voltage to extract the most power available; microinverters do this per panel, so one shaded module doesn't drag down the rest.

**AC coupling** — connecting a battery to the home's AC wiring alongside an existing solar inverter system, rather than sharing the solar DC bus. Lets a battery like the Powerwall 3 work with any existing microinverter fleet without touching the array.

**Rate Relief Credit** — a per-kWh bill credit some CCAs apply to certain products. Whether it applies is read off the detailed bill, not assumed — in this analysis the bills showed it does not apply to this account's product. [Clean Energy Alliance](https://thecleanenergyalliance.org)

**Supercharger** — Tesla's DC fast-charging network; energy delivered there appears in the car's Charge Stats but never on the home meter, which is why it's separated in the EV-fleet validation. [Tesla Supercharger](https://www.tesla.com/supercharger)

**Tornado (sensitivity) analysis** — Recomputing a result — here the battery payback — while swinging one input at a time through its plausible range, then ranking the inputs by how far each moves the answer (the sorted bar chart resembles a tornado). It shows dispatch policy and the install quote dominate this battery's payback, ahead of rate escalation and the EV-fix interaction. DSGS revenue is deliberately not one of these levers — it's a partial-season backtest, not a full annual figure, so it's reported separately as an additive dollar amount rather than folded into a payback-year swing. [Wikipedia: tornado diagram](https://en.wikipedia.org/wiki/Tornado_diagram)
