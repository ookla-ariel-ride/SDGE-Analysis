#!/bin/bash
# stage-private-data.sh SOURCE_WORKING_COPY DEST_CLONE
# ---------------------------------------------------------------------------
# Copies the gitignored PRIVATE inputs a full pipeline run needs from an
# existing working copy into a fresh clone, so the documented private/verify
# reproduction flow (CLAUDE.md "Commands") can run there. Copies NO secrets
# (.env is never touched) and nothing this script stages is ever committed —
# every destination is inside the clone's gitignored private/ tree.
#
# What the pipeline needs and why:
#   private/household.yaml                    intake file (analysis/household.py)
#   private/1-raw-data/Electric_15_Minute_*.csv  Green Button 15-min interval data
#   private/1-raw-data/enphase_sam8760_2025.csv  Enphase SAM hourly load, full year
#   private/1-raw-data/enphase_sam8760_2026.csv  Enphase SAM hourly load, partial year
#   private/1-raw-data/gas.csv                gas Green Button daily therms
#   private/1-raw-data/caiso_raw/             (optional) CAISO day-cache; without it
#                                             carbon_fullyear.py rebuilds exactly from
#                                             the committed data/caiso_hourly_intensity.csv
#   private/verify/usage.csv, samA.csv, samB.csv  the sandbox copies the verify flow
#                                             expects (samA=partial year, samB=full year)
# ---------------------------------------------------------------------------
set -euo pipefail
SRC="${1:?usage: stage-private-data.sh SOURCE_WORKING_COPY DEST_CLONE}"
DST="${2:?usage: stage-private-data.sh SOURCE_WORKING_COPY DEST_CLONE}"

mkdir -p "$DST/private/1-raw-data" "$DST/private/verify"

cp "$SRC/private/household.yaml"                      "$DST/private/household.yaml"
cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST/private/1-raw-data/"
cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$DST/private/1-raw-data/"
cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$DST/private/1-raw-data/"
cp "$SRC/private/1-raw-data/gas.csv"                  "$DST/private/1-raw-data/"

cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST/private/verify/usage.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$DST/private/verify/samA.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$DST/private/verify/samB.csv"

if [ -d "$SRC/private/1-raw-data/caiso_raw" ] && \
   ls "$SRC"/private/1-raw-data/caiso_raw/caiso_co2_*.csv >/dev/null 2>&1; then
  cp -R "$SRC/private/1-raw-data/caiso_raw" "$DST/private/1-raw-data/"
fi

echo "staged into $DST/private/ — nothing outside the gitignored tree was written"
