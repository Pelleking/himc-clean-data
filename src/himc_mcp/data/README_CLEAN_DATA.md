# HIMC data room: cleaned tables

Built by `cleaning/clean_data.py` from the raw files (the raw files are not changed).
To rebuild, run `python3 cleaning/clean_data.py` from the repository root. It takes about 15 seconds.

- **`catalog.csv`**: every clean table with its row and column counts.
- **`data_quality_log.csv`**: 53 issues. Each one says what was wrong, how many rows it affected and what was done.
- **`himc.sqlite`**: every clean table plus the log in one database, for querying with SQL. The table name is the folder and file joined by `__`, e.g. `uk_kingsmere__spells`.

## Rules used

1. **Flag rather than delete.** Columns starting `dq_` mark a problem (1 = the problem is present). Rows were removed only when they were certainly junk: exact duplicates, test patients, and stays copied into both systems during the migration.
2. **Keep the original.** When a value was corrected or recoded, the original is kept in a `*_raw` column or in `dq_typo_corrected`.
3. **No imputation of activity counts.** Gaps stay as gaps.

## What is where

### UK: Kingsmere (`uk_kingsmere/`)

| Table | Grain | Main changes |
|---|---|---|
| `spells` | One row per hospital stay (151,217) | 326 junk rows removed. Ward codes from both systems mapped to one set of reporting codes. Admission method harmonised (20 spellings + codes → 7 national codes). Date errors fixed or flagged. Added `is_bedded_admission`, `is_sdec`, `ed_wait_dta_to_bed_hours`, `age_band`, `imd_quintile`, `discharge_date_imputed` |
| `daily_site_panel` | One row per day (1,310) | **Start here for forecasting questions.** Daily SitRep (typos fixed, duplicate day resolved) + weather, holidays, strikes and surveillance + admissions, discharges and midnight occupancy counted from the stays |
| `ward_daily_census` | Ward × day | Ward attributes added, occupancy %, `specialty_group_actual` (W15 became a medical ward on 1 Sep 2025) |
| `nurse_staffing_ward_shift` | Ward × shift | Fill rates, patients per registered nurse |
| `temporary_staffing_requests` | Bank or agency request | Lead-time bands, `filled`, 56-day block bookings flagged |
| `elective_tci_list` | Booking | Cancellation flags, including `is_bed_related_cancellation` |
| others | | Copied as supplied (incidents, ops log, calendar, external signals, wards) |

### Kenya: Kisima (`kenya_kisima/`)

| Table | Main changes |
|---|---|
| `ward_bed_state_daily` | A complete ward × night grid. The 331 missing nights are added as empty rows. Rows whose arithmetic doesn't add up are flagged, with a recalculated value alongside. Occupancy is measured against official beds |
| `inpatient_register` | Dates in 4 formats parsed. Ages such as `1 6/12` and `7 mths` converted to years. Sex, residence (22 spellings → 7 places), outcome and diagnosis (83 free-text entries → 18 groups) standardised. 144 wrong-year entries fixed |
| `khis_monthly_ward`, `monthly_hospital_panel` | Ward-month reports, plus a hospital-month panel joining malaria, rainfall, staffing, power, internet and insurance figures |

### Indonesia: Sukamaju (`indonesia_sukamaju/`)
All column names and category values are **translated to English** (for example `pasien_akhir` → `patients_end_of_day` and `Penyakit Dalam` → `Internal medicine`). Payer labels are harmonised across the 1 July 2025 system update. `post_kris` marks the period after beds were cut from 338 to 304.

### Prototype (`prototype/`)
- `backtest_long`: both back-test files stacked, with the error of the model and of each naive baseline, and whether the actual value fell inside the 80% band.
- `backtest_summary`: mean absolute error (MAE) by target and horizon, and 80% band coverage.

## Caveats before you analyse

- **Same Day Emergency Care (SDEC) recording changed in March 2025.** About 750 fewer SDEC stays a month appear in the stay files from then on. The change is in how they were recorded, not in demand. Use `is_bedded_admission = 1` for any trend over time.
- **12–16 May 2026 (EPR downtime).** Admissions for these days are undercounted. Exclude these days or flag them.
- **Recent stays have no diagnosis yet** (`dq_not_yet_coded`).
- **Stays with no discharge at the cut-off** (`still_in_hospital_at_cutoff`) have censored length of stay.
- **Temporary staffing data starts on 1 April 2025** and cannot be compared with earlier years.
- **Kenya outcome "D".** 1,828 register lines say only "D". We assumed this means discharged, because reading it as "died" would put ward mortality around 45%. This needs confirming with the ward staff.
- **Kenya bed state is hand-copied.** About 10% of ward-nights are missing and about 10% don't add up.
- **Weekly surveillance figures are published about 4 days late.** A fair forecast may only use values already published on the day (`surveillance_known_on_day`).
- **Not cleaned.** The small descriptive tables in `01_company`, `05_users_and_workflows` and `06_responsible_innovation` were left as supplied. Read them directly.

## A first finding from the cleaned data

The prototype's **80% "range shown to users" contains the actual value only 45–64% of the time**. For a range labelled 80%, it should be about 80%, so the bands are too narrow. The model beats a "same as yesterday" forecast, but only by about 1 bed at the whole-hospital level 7 days ahead (MAE 12.0 vs 12.2). Check this against the claims in `07_prototype_v0_3/model_card_draft.md` and the sales deck.

## Starter questions and where to answer them

| Question | Table |
|---|---|
| Is bed demand seasonal or weekly, and how well do weather, flu and strikes explain it? | `uk_kingsmere__daily_site_panel` |
| How often is the hospital full (corridor care, trolley waits over 12 hours), and in which months? | `daily_site_panel` |
| Who waits longest in the emergency department for a bed, by age, deprivation or ethnicity? | `spells` (`ed_wait_dta_to_bed_hours`, excluding `dq_dta_0730_default`) |
| How many beds are filled by patients medically ready to leave? | `daily_site_panel.nctr_*`, `spells.discharge_ready_date` |
| How far ahead are temporary staff booked, and what does short notice cost? | `temporary_staffing_requests` |
| How many planned operations are cancelled because there is no bed? | `elective_tci_list.is_bed_related_cancellation` |
| What is "full" in Kenya, and is the paper data good enough to forecast from? | `kenya_kisima__ward_bed_state_daily` (look at the `dq_` rates) |
| What happened in Indonesia after the bed cut? | `indonesia_sukamaju__daily_ward_census`, `ed_daily` |
| Does the prototype beat the naive forecasts, and are its ranges honest? | `prototype__backtest_summary` |
