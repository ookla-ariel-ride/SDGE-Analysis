# private/ — raw inputs (gitignored)

This folder holds the raw, personal input data on the analyst's machine and is **excluded
from the public repo by `.gitignore`** (only this README is committed, as a placeholder).

What lives here locally, and how to gather your own copies:

| Subfolder | Contents |
|---|---|
| `1-raw-data/` | SDG&E Green Button 15-min electric CSV and daily gas CSV; solar-monitoring exports (hourly whole-home consumption, daily production); detailed monthly bill PDFs (electric + gas) |
| `3-analysis-extras/` | As-run script copies with personal file headers |

None of it is needed to read the report — every figure the report uses is available as a
de-identified aggregate in `data/`. To reproduce the analysis with **your own** data, follow
`DATA-SOURCES-CHEATSHEET.md` (what to download and where) and `TECHNICAL.md` (how the
scripts consume it), and keep your raw files in your own gitignored `private/` folder.

**Never `git add -f` anything in this folder.** Raw interval files and bill PDFs contain
names, addresses, and account/meter numbers.
