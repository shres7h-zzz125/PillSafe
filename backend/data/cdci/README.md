# NRCeS Common Drug Codes for India (CDCI) Flat Files

Place the official **Common Drug Codes for India (Flat Files Package)** TSV files downloaded from [nrces.in](https://www.nrces.in/) inside this directory (`backend/data/cdci/`).

These files are intentionally git-ignored (`.gitignore`) so licensed government release archives are never committed to source control.

Expected TSV master files (filenames from the NRCeS Flat Files package, case-insensitive / flexible matching supported):
- Substance Master (`*substance*.tsv` or `*substance*.txt`)
- Generic Master (`*generic*.tsv` or `*generic*.txt`)
- Brand Master (`*brand*.tsv` or `*brand*.txt`)
- Product / Branded Medicine Master (`*product*.tsv` or `*branded*.tsv`)
- Drug Form Master (`*form*.tsv` or `*dose*form*.tsv`)
- Supplier Master (`*supplier*.tsv`)
- Optional `release_info.json` or `License.txt` / release notes (used for dataset version/date attribution)
