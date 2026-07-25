#!/usr/bin/env python3
"""Lifetime solar payback: cumulative value of actual production vs the install invoice.

Method:
  1. Blended $/kWh TODAY = (no-solar counterfactual bill - with-solar bill) / annual
     production, both computed with the NEM netting model (billing_model_nem.bill) at
     current rates. Computed under BOTH TOU structures: the pre-2026 windows (sop
     midnight-6am, plus 10am-2pm in Mar/Apr only) for historical years, and the current
     windows for the present year. The no-solar load series is the hourly whole-home
     consumption (solar-monitoring consumption meter), re-billed as if all imported.
  2. Each historical year's value = that year's ACTUAL metered production x the
     old-structure blended $/kWh x (that year's utility average residential rate /
     the current average rate). Rate index from published SDG&E average-rate history.
  3. Crossovers: cumulative value vs invoice gross, and vs invoice x 0.70 (30% federal
     ITC for 2019 systems).

Caveat: the rate index is approximate; crossover dates carry roughly +/-10% (a few
months). Inputs: yearly production (monitoring records), install invoice total + date.
"""
INVOICE=37845.0; ITC=0.30
PROD={2020:17373,2021:17421,2022:17749,2023:15570,2024:16654,2025:16509,2026:9893}  # 2026 = Jan-Jul 23
RATE_IDX={2020:32,2021:34,2022:37,2023:41,2024:44,2025:46,2026:48}   # SDGE avg residential c/kWh (approx)
BLENDED_OLD=0.4866   # $/kWh, pre-2026 TOU structure at current rates (see method 1)
BLENDED_NEW=0.3025   # $/kWh, current TOU structure

cum=0.0
print(f"{'year':<6}{'prod kWh':>10}{'value $':>10}{'cum $':>10}")
for y in sorted(PROD):
    bl = BLENDED_NEW if y==2026 else BLENDED_OLD
    v = PROD[y]*bl*RATE_IDX[y]/RATE_IDX[2026]
    cum += v
    marks=[]
    if cum-v < INVOICE*(1-ITC) <= cum: marks.append(f"<- crosses net-of-ITC ${INVOICE*(1-ITC):,.0f}")
    if cum-v < INVOICE <= cum: marks.append(f"<- crosses gross ${INVOICE:,.0f}")
    print(f"{y:<6}{PROD[y]:>10,}{v:>10,.0f}{cum:>10,.0f}  {' '.join(marks)}")
