# private/

Put raw data here: Green Button 15-minute CSV exports, PDF bills, and anything
else carrying the account holder's name, service address, or account/meter
numbers.

Everything in this folder except this README is ignored by git (see the
repo-root `.gitignore`) and must never be committed. The analysis script reads
the raw CSV from here and writes only aggregated outputs to `data/`.
