"""
HIMC data room - cleaning pipeline.

Reads the raw files exactly as supplied, writes tidy, analysis-ready tables to
clean/, a data-quality log (clean/data_quality_log.csv) and a SQLite database
(clean/himc.sqlite) holding every clean table.

Principles
- Never edit the raw files. Every change is logged with the number of rows affected.
- Prefer FLAGGING over deleting. Rows are only dropped when they are certain
  junk (exact duplicates, test patients, system-migration twins).
- Keep the original value next to any corrected value (`*_raw` columns).
- Flags are columns starting `dq_` (1 = issue present).

Run from the repository root:  python3 cleaning/clean_data.py
"""
from __future__ import annotations

import glob
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "clean"
UK = ROOT / "02_hospital_uk_kingsmere"
KE = ROOT / "03_hospital_kenya_kisima"
ID = ROOT / "04_hospital_indonesia_sukamaju"
PROTO = ROOT / "07_prototype_v0_3"

LOG: list[dict] = []


def log(dataset, issue, rows, action, how_found="profiling"):
    LOG.append(dict(dataset=dataset, issue=issue, rows_affected=int(rows), action=action, how_found=how_found))


def read(path, **kw):
    return pd.read_csv(path, dtype=str, keep_default_na=False, **kw)


def num(s):
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def dt(s, fmt=None):
    return pd.to_datetime(s.replace("", np.nan), format=fmt, errors="coerce")


TABLES: dict[str, pd.DataFrame] = {}


def save(name, df):
    sub, _, fname = name.rpartition("/")
    (OUT / sub).mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{name}.csv", index=False)
    TABLES[name.replace("/", "__")] = df


# =============================================================================
# UK - Kingsmere
# =============================================================================
def uk_wards():
    w = read(UK / "wards.csv")
    w["beds_established"] = num(w.beds_established).astype(int)
    save("uk_kingsmere/wards", w)
    return w


def uk_spells(wards):
    ds = "uk.spells"
    files = sorted(glob.glob(str(UK / "inpatient_spells_*.csv")))
    s = pd.concat([read(f).assign(source_file=Path(f).name) for f in files], ignore_index=True)
    n0 = len(s)

    # 1. exact duplicate rows
    d = s.drop(columns="source_file").duplicated()
    log(ds, "Exact duplicate rows (same spell_id, every field identical)", d.sum(), "Dropped")
    s = s[~d]

    # 2. test patients
    test = s.specialty.eq("TEST") | s.admission_ward.isin(["TEST", "ZZTEST"]) | s.spell_id.str[1:3].eq("99")
    log(ds, "Test/dummy patients (specialty TEST, ward TEST/ZZTEST, age 121/126, spell ids *99xxxxxx)", test.sum(), "Dropped")
    s = s[~test]

    # 3. migration twins: patients in hospital over EPR go-live appear once in PAS and once in EPR
    key = ["patient_pseudo_id", "admission_datetime"]
    both = s.groupby(key).source_system.transform("nunique") > 1
    twin_L = both & s.source_system.eq("LegacyPAS")
    log(ds, "Migration twins: stay present in both LegacyPAS and CoreliaEPR (go-live 11/12 Oct 2024)", twin_L.sum(),
        "Dropped the PAS copy, kept the EPR copy (identical dates; EPR has the final ward)", "data notes #8 + check")
    s = s[~twin_L].copy()

    # parse
    for c in ["ed_arrival_datetime", "decision_to_admit_datetime", "admission_datetime", "discharge_datetime"]:
        s[c] = dt(s[c])
    for c in ["age_at_admission", "imd_decile", "charlson_comorbidity_count", "frailty_score_cfs",
              "critical_care_nights", "ward_moves", "nights_as_outlier", "los_nights"]:
        s[c] = num(s[c])
    s["discharge_ready_date"] = dt(s.discharge_ready_date)

    # 4. ward codes -> reporting codes
    m = {**dict(zip(wards.legacy_pas_code, wards.ward_code)), **dict(zip(wards.epr_location_code, wards.ward_code))}
    for c in ["admission_ward", "discharge_ward"]:
        s[c + "_raw"] = s[c]
        s[c] = s[c].map(m)
    log(ds, "Ward codes differ between PAS (WD01, AMU1, AEC...) and EPR (KRI-W01...)", len(s),
        "Mapped both to reporting ward codes from wards.csv; originals kept in *_raw", "data notes #1")
    spec = dict(zip(wards.ward_code, wards.specialty_group))
    s["admission_ward_group"] = s.admission_ward.map(spec)

    # 5. admission method: free text (PAS) vs national code (EPR)
    txt = {
        "emergency-a&e": "21", "emergency-gpreferral": "22", "emergency-outpatientclinic": "24",
        "emergency-transferfromotherprovider": "2B", "maternity-ante-partum": "31",
        "elective-waitinglist": "11", "birthinthisprovider": "82",
    }
    lab = {"11": "Elective: waiting list", "21": "Emergency: via ED", "22": "Emergency: via GP",
           "24": "Emergency: via consultant clinic", "2B": "Emergency: transfer from other hospital",
           "31": "Maternity: before delivery", "82": "Birth in this hospital"}
    s["admission_method_raw"] = s.admission_method
    norm = s.admission_method.str.lower().str.replace(" ", "", regex=False)
    s["admission_method_code"] = np.where(s.admission_method.isin(lab), s.admission_method, norm.map(txt))
    s["admission_method"] = s.admission_method_code.map(lab)
    log(ds, "Admission method: 20 hand-typed spellings in PAS ('EMERGENCY - A&E', 'Emergency-A & E'...) vs national codes in EPR",
        len(s), f"Harmonised to national code + label; unmapped: {s.admission_method_code.isna().sum()}", "data notes #1")

    # 6. discharge before admission
    s["dq_pas_default_discharge_time"] = ((s.source_system == "LegacyPAS")
                                          & (s.discharge_datetime.dt.strftime("%H:%M") == "00:00")).astype(int)
    log(ds, "PAS discharge time defaulted to 00:00 when clerk typed only a date", s.dq_pas_default_discharge_time.sum(),
        "Flagged dq_pas_default_discharge_time; use discharge_date not the time", "data notes #5")
    neg = s.discharge_datetime < s.admission_datetime
    same_day = neg & (s.discharge_datetime.dt.normalize() == s.admission_datetime.dt.normalize())
    s.loc[same_day, "discharge_datetime"] = s.loc[same_day, "admission_datetime"]
    log(ds, "Discharge 00:00 default made same-day discharges appear to leave before arriving", same_day.sum(),
        "Set discharge_datetime = admission_datetime (date unchanged)")
    bad = neg & ~same_day
    s["dq_discharge_before_admission"] = bad.astype(int)
    log(ds, "Discharge date earlier than admission date (genuine date error, e.g. admitted 2023-01-30 discharged 2022-06-12)",
        bad.sum(), "Flagged dq_discharge_before_admission; discharge left as recorded - exclude from LOS work")

    # 7. PAS stays with no discharge but admitted long before cut-off
    lost = s.discharge_datetime.isna() & (s.source_system == "LegacyPAS")
    s["dq_pas_lost_discharge"] = lost.astype(int)
    log(ds, "PAS stays admitted 2023-24 with no discharge date and never migrated to EPR (discharge never keyed)",
        lost.sum(), "Flagged dq_pas_lost_discharge; discharge_date_imputed = admission date + los_nights")
    s["still_in_hospital_at_cutoff"] = (s.discharge_datetime.isna() & ~lost).astype(int)
    log(ds, "EPR stays with no discharge = still in hospital at 31 Oct 2026 cut-off", s.still_in_hospital_at_cutoff.sum(),
        "Kept; flagged still_in_hospital_at_cutoff (right-censored LOS)", "data notes #2")

    s["admission_date"] = s.admission_datetime.dt.normalize()
    s["discharge_date"] = s.discharge_datetime.dt.normalize()
    s["discharge_date_imputed"] = s.discharge_date
    s.loc[lost, "discharge_date_imputed"] = s.loc[lost, "admission_date"] + pd.to_timedelta(s.loc[lost, "los_nights"], unit="D")

    calc_los = (s.discharge_date - s.admission_date).dt.days
    s["dq_los_mismatch"] = ((calc_los != s.los_nights) & calc_los.notna() & s.los_nights.notna()).astype(int)
    log(ds, "los_nights disagrees with admission/discharge dates", s.dq_los_mismatch.sum(), "Flagged dq_los_mismatch")

    # 8. ED timing anomalies
    s["dq_admit_before_dta"] = (s.admission_datetime < s.decision_to_admit_datetime).astype(int)
    log(ds, "Reached a bed before the decision to admit", s.dq_admit_before_dta.sum(), "Flagged dq_admit_before_dta")
    s["dq_dta_0730_default"] = (s.decision_to_admit_datetime.dt.strftime("%H:%M") == "07:30").astype(int)
    log(ds, "Decision-to-admit time exactly 07:30 ~8x more often than chance (likely default/handover time)",
        s.dq_dta_0730_default.sum(), "Flagged dq_dta_0730_default; treat ED wait for these as unreliable")
    s["ed_wait_dta_to_bed_hours"] = ((s.admission_datetime - s.decision_to_admit_datetime).dt.total_seconds() / 3600).round(2)

    # 9. May 2026 downtime
    dwn = s.admission_date.between("2026-05-12", "2026-05-16")
    s["dq_downtime_may2026"] = dwn.astype(int)
    log(ds, "EPR downtime 12-16 May 2026: admissions back-entered from paper, ~40% fewer than normal, many times rounded to the hour",
        dwn.sum(), "Flagged dq_downtime_may2026; daily counts for these days are under-counts", "data notes #7 + check")

    # 10. SDEC recording change
    s["is_sdec"] = s.same_day_emergency_care.eq("Y").astype(int)
    log(ds, "SDEC stays recorded as spells ~900/month until Feb 2025, ~140/month after unit expanded 3 Mar 2025 (recording change, not demand)",
        s.is_sdec.sum(), "Added is_sdec and is_bedded_admission; exclude SDEC for any trend over time", "data notes #11 + check")
    s["is_ed_boarding_only"] = s.admission_ward.eq("EDB").astype(int)
    s["is_bedded_admission"] = ((s.is_sdec == 0) & (s.is_ed_boarding_only == 0)).astype(int)

    # 11. coding lag + other completeness
    recent = s.primary_diagnosis_group.eq("") & (s.discharge_date >= "2026-08-15")
    log(ds, "Diagnosis group blank for recent discharges (clinical coding 6-8 weeks behind)", recent.sum(),
        "Left blank; flag dq_not_yet_coded", "data notes #3")
    s["dq_not_yet_coded"] = (s.primary_diagnosis_group.eq("") & (s.discharge_date.isna() | (s.discharge_date >= "2026-08-15"))).astype(int)
    blanks_eth = s.ethnic_category.isin(["", "Not stated", "Not known"])
    s["ethnic_category"] = s.ethnic_category.replace("", "Not recorded")
    log(ds, "Ethnicity blank/'Not stated'/'Not known' (worse in PAS)", blanks_eth.sum(), "Blank recoded to 'Not recorded'", "data notes #4")
    s["sex"] = s.sex.replace({"U": "Unknown"})
    s["fin_year"] = s.source_file.str.extract(r"spells_(\d{4}-\d{2})")[0]

    s["age_band"] = pd.cut(s.age_at_admission, [-1, 17, 64, 74, 84, 200], labels=["0-17", "18-64", "65-74", "75-84", "85+"]).astype(str)
    s["imd_quintile"] = np.ceil(s.imd_decile / 2)

    order = ["spell_id", "patient_pseudo_id", "source_system", "fin_year", "admission_type", "admission_method_code",
             "admission_method", "admission_method_raw", "is_sdec", "is_ed_boarding_only", "is_bedded_admission",
             "admission_source", "ed_arrival_datetime", "decision_to_admit_datetime", "admission_datetime", "admission_date",
             "ed_wait_dta_to_bed_hours", "admission_ward", "admission_ward_group", "discharge_ward", "specialty",
             "primary_diagnosis_group", "age_at_admission", "age_band", "sex", "ethnic_category", "imd_decile", "imd_quintile",
             "postcode_district", "charlson_comorbidity_count", "frailty_score_cfs", "critical_care_nights", "ward_moves",
             "nights_as_outlier", "discharge_ready_date", "discharge_datetime", "discharge_date", "discharge_date_imputed",
             "discharge_destination", "discharge_pathway", "los_nights", "still_in_hospital_at_cutoff", "tci_id",
             "admission_ward_raw", "discharge_ward_raw", "source_file"]
    s = s[order + sorted(c for c in s.columns if c.startswith("dq_"))]
    log(ds, "Row count", len(s), f"{n0} raw rows -> {len(s)} clean stays")
    save("uk_kingsmere/spells", s)
    return s


def uk_sitrep():
    ds = "uk.sitrep"
    r = pd.read_csv(UK / "site_daily_sitrep.csv")
    dup = r.date.duplicated(keep=False)
    log(ds, "2025-03-20 entered twice with different discharges (69 vs 73) and nctr_not_discharged (43 vs 40)", dup.sum(),
        "Kept the second (later) row; flagged dq_duplicate_day")
    r["dq_duplicate_day"] = 0
    r.loc[r.date.isin(r.date[dup]), "dq_duplicate_day"] = 1
    r = r.drop_duplicates("date", keep="last").copy()
    r["dq_typo_corrected"] = ""
    fixes = [("ed_attendances", 1000), ("adult_ga_beds_occupied", 1000), ("nctr_patients_0800", 500)]
    for col, thr in fixes:
        bad = r[col] > thr
        for i in r.index[bad]:
            r.loc[i, "dq_typo_corrected"] += f"{col}:{r.loc[i, col]:.0f}->{r.loc[i, col] / 10:.0f};"
        r.loc[bad, col] = r.loc[bad, col] / 10
        log(ds, f"Typing error, extra digit in {col} ({', '.join(r.loc[bad, 'date'])})", bad.sum(),
            "Divided by 10 (value then matches neighbouring days); original noted in dq_typo_corrected", "outlier scan")
    miss = r[["patients_awaiting_bed_0800", "ambulance_handovers_over_60min", "nctr_patients_0800",
              "nctr_not_discharged", "medical_outliers"]].isna().any(axis=1)
    log(ds, "Blank cells in manual return (weekend single-person entry)", miss.sum(), "Left blank (not imputed)", "data notes #9")
    r["ga_occupancy_pct_recalc"] = ((r.adult_ga_beds_occupied + r.paediatric_beds_occupied)
                                    / (r.adult_ga_beds_open + r.paediatric_beds_open) * 100).round(1)
    r["adult_ga_occupancy_pct"] = (r.adult_ga_beds_occupied / r.adult_ga_beds_open * 100).round(1)
    r["adult_patients_incl_corridor"] = r.adult_ga_beds_occupied + r.patients_in_temporary_escalation_spaces
    r["date"] = pd.to_datetime(r.date)
    return r


def uk_daily_panel(sitrep, spells):
    """One row per day: SitRep + external signals + calendar + counts from stays."""
    ex = pd.read_csv(UK / "external_signals_daily.csv", parse_dates=["date"])
    cal = pd.read_csv(UK / "calendar_events.csv", parse_dates=["start_date", "end_date"])
    days = pd.DataFrame({"date": pd.date_range("2023-04-01", "2026-10-31")})
    for et in ["Industrial action", "Ward closure", "IT incident", "Major incident"]:
        days["cal_" + et.lower().replace(" ", "_")] = 0
        for _, e in cal[cal.event_type.str.startswith(et)].iterrows():
            end = e.end_date if pd.notna(e.end_date) else days.date.max()
            days.loc[days.date.between(e.start_date, end), "cal_" + et.lower().replace(" ", "_")] = 1
    ex = ex.rename(columns={c: "ext_" + c for c in ex.columns if c != "date"})
    ex["ext_bank_holiday"] = ex.ext_bank_holiday.notna().astype(int)
    ex["ext_industrial_action"] = ex.ext_industrial_action.notna().astype(int)

    b = spells[spells.is_bedded_admission == 1]
    adm = b.groupby(["admission_date", "admission_type"]).size().unstack(fill_value=0)
    adm.columns = ["spells_adm_" + c.lower().replace(" / ", "_").replace(" ", "_") for c in adm.columns]
    dis = b.groupby("discharge_date_imputed").size().rename("spells_discharges")
    # midnight occupancy from stays (bedded, excl. maternity/neonatal) - comparable across systems
    a = b[~b.admission_type.isin(["Maternity", "Birth / neonatal"])]
    start = a.admission_date.values.astype("datetime64[D]")
    end = a.discharge_date_imputed.fillna(pd.Timestamp("2026-11-01")).values.astype("datetime64[D]")
    rng = pd.date_range("2023-04-01", "2026-10-31").values.astype("datetime64[D]")
    occ = np.searchsorted(np.sort(start), rng, side="right") - np.searchsorted(np.sort(end), rng, side="right")
    occ = pd.Series(occ, index=pd.to_datetime(rng), name="spells_patients_midnight_non_maternity")

    p = (days.merge(sitrep, on="date", how="left").merge(ex, on="date", how="left")
         .merge(adm, left_on="date", right_index=True, how="left")
         .merge(dis, left_on="date", right_index=True, how="left")
         .merge(occ, left_on="date", right_index=True, how="left"))
    p["dq_downtime_may2026"] = p.date.between("2026-05-12", "2026-05-16").astype(int)
    p["is_weekend"] = p.date.dt.dayofweek.ge(5).astype(int)
    p["fin_year"] = np.where(p.date.dt.month >= 4, p.date.dt.year, p.date.dt.year - 1)
    p["fin_year"] = p.fin_year.astype(str) + "-" + (p.fin_year + 1).astype(str).str[2:]
    p["surveillance_known_on_day"] = (pd.to_datetime(p.ext_surveillance_published_date) <= p.date).astype(int)
    log("uk.daily_panel", "Surveillance figures are published ~4 days after the week they describe", (p.surveillance_known_on_day == 0).sum(),
        "Added surveillance_known_on_day - a forecast may only use values already published (avoid leakage)", "check")
    save("uk_kingsmere/daily_site_panel", p)


def uk_other(wards):
    c = pd.read_csv(UK / "ward_daily_census.csv")
    c = c.merge(wards[["ward_code", "ward_name", "specialty_group", "counts_as_adult_general_and_acute"]], on="ward_code", how="left")
    c["occupancy_pct_of_open"] = (c.patients_at_midnight / c.beds_open.replace(0, np.nan) * 100).round(1)
    c["patients_over_open_beds"] = (c.patients_at_midnight - c.beds_open).clip(lower=0)
    c["dq_w15_service_change"] = ((c.ward_code == "W15") & (c.date >= "2025-09-01")).astype(int)
    log("uk.ward_census", "Ward W15 changed from elective orthopaedics to general/elderly medicine on 1 Sep 2025",
        c.dq_w15_service_change.sum(), "Added specialty_group_actual (W15 = medical from 2025-09-01)", "wards.csv notes")
    c["specialty_group_actual"] = np.where(c.dq_w15_service_change == 1, "medical", c.specialty_group)
    log("uk.ward_census", "Patients above open beds (AMU, children's, maternity, neonatal) - allowed by full-capacity protocol",
        (c.patients_over_open_beds > 1).sum(), "Kept; added patients_over_open_beds", "data dictionary")
    save("uk_kingsmere/ward_daily_census", c)

    n = pd.read_csv(UK / "nurse_staffing_ward_shift.csv")
    n["rn_fill_rate_pct"] = ((n.rn_planned_hours - n.rn_unfilled_hours) / n.rn_planned_hours * 100).round(1)
    n["hca_fill_rate_pct"] = ((n.hca_planned_hours - n.hca_unfilled_hours) / n.hca_planned_hours.replace(0, np.nan) * 100).round(1)
    n["patients_per_rn_on_shift"] = (n.patients_at_shift_start / ((n.rn_planned_hours - n.rn_unfilled_hours + n.rn_redeployed_hours) / 11.5).replace(0, np.nan)).round(2)
    save("uk_kingsmere/nurse_staffing_ward_shift", n)

    t = pd.read_csv(UK / "temporary_staffing_requests.csv")
    t["dq_lead_time_1344h_default"] = (t.lead_time_hours == 1344).astype(int)
    log("uk.temp_staffing", "858 vacancy requests with lead time exactly 1,344 h (56 days) logged at 07:00/19:00 - block bookings, not real lead time",
        t.dq_lead_time_1344h_default.sum(), "Flagged dq_lead_time_1344h_default", "profiling")
    t["lead_time_band"] = pd.cut(t.lead_time_hours, [-1, 24, 72, 168, 10_000], labels=["<24h", "1-3 days", "3-7 days", ">7 days"]).astype(str)
    t["filled"] = t.outcome.str.startswith("Filled").astype(int)
    log("uk.temp_staffing", "No requests before 1 Apr 2025 (booking system replaced; history not migrated)", 0,
        "Note only: do not compare with earlier years", "data notes")
    save("uk_kingsmere/temporary_staffing_requests", t)

    x = read(UK / "elective_tci_list.csv")
    x["is_cancelled"] = x.outcome.str.startswith("Cancelled").astype(int)
    x["is_hospital_cancellation_nonclinical"] = x.outcome.str.contains("by hospital").astype(int)
    x["is_bed_related_cancellation"] = x.cancellation_reason.isin(
        ["Bed pressures - cancelled day before", "No ward bed available", "No critical care bed available"]).astype(int)
    x["dq_booked_before_added_to_list"] = (x.date_booked < x.date_added_to_waiting_list).astype(int)
    log("uk.elective_tci", "date_booked earlier than date_added_to_waiting_list", x.dq_booked_before_added_to_list.sum(), "Flagged")
    log("uk.elective_tci", "Admitted bookings whose spell_id is not in the stay files", 10, "Left as is (information team says a few are missing)")
    save("uk_kingsmere/elective_tci_list", x)

    for f in ["flow_incident_log", "ops_actions_log", "calendar_events", "external_signals_daily"]:
        save(f"uk_kingsmere/{f}", read(UK / f"{f}.csv"))


# =============================================================================
# Kenya - Kisima
# =============================================================================
def ke_bed_state():
    ds = "ke.bed_state"
    b = pd.read_csv(KE / "ward_bed_state_daily_transcribed.csv", parse_dates=["date"])
    grid = pd.MultiIndex.from_product([pd.date_range("2026-02-01", "2026-10-31"), sorted(b.ward_code.unique())],
                                      names=["date", "ward_code"]).to_frame(index=False)
    miss = len(grid) - len(b)
    b = grid.merge(b, on=["date", "ward_code"], how="left")
    b["official_beds"] = b.groupby("ward_code").official_beds.transform("max")
    b["dq_missing_day"] = b.patients_remaining_midnight.isna().astype(int)
    log(ds, "Ward-nights with no page in the book (missing photos/pages)", miss, "Added as empty rows, dq_missing_day=1")
    calc = b.patients_brought_forward + b.admissions - b.discharges - b.deaths - b.absconded - b.referred_out
    b["dq_balance_mismatch"] = ((calc != b.patients_remaining_midnight) & b.patients_remaining_midnight.notna()).astype(int)
    b["patients_remaining_midnight_recalc"] = calc
    log(ds, "Brought forward + in - out != remaining at midnight (arithmetic or transcription error)", b.dq_balance_mismatch.sum(),
        "Flagged; recalculated value kept alongside")
    b = b.sort_values(["ward_code", "date"])
    prev = b.groupby("ward_code").patients_remaining_midnight.shift()
    b["dq_bf_not_prev_midnight"] = ((prev != b.patients_brought_forward) & prev.notna() & b.patients_brought_forward.notna()).astype(int)
    log(ds, "Brought-forward does not equal the previous night's remaining", b.dq_bf_not_prev_midnight.sum(), "Flagged")
    b["dq_transcription_note"] = b.transcription_note.fillna("").ne("").astype(int)
    log(ds, "Typist flagged legibility (faint, torn, two entries, totals missing)", b.dq_transcription_note.sum(), "Flagged")
    b["occupancy_pct_official_beds"] = (b.patients_remaining_midnight / b.official_beds * 100).round(1)
    b["patients_per_night_nurse"] = (b.patients_remaining_midnight / b.nurses_on_night_duty.replace(0, np.nan)).round(1)
    log(ds, "nurses_on_night_duty blank or 0", (b.nurses_on_night_duty.fillna(0) == 0).sum() - miss,
        "Left as is; 0 is often a blank written as 0 - check before use")
    save("kenya_kisima/ward_bed_state_daily", b)


def ke_register():
    ds = "ke.register"
    r = read(KE / "inpatient_register_sample_apr_jun_2026.csv").drop(columns=["patient_name", "ip_number", "next_of_kin_phone"])

    def pdate(x):
        x = x.strip()
        for f in ["%d/%m/%Y", "%d/%m/%y", "%d-%b-%y", "%d.%m.%Y"]:
            try:
                return pd.to_datetime(x, format=f)
            except ValueError:
                pass
        return pd.NaT

    r["admission_date"] = r.date_of_admission_as_written.map(pdate)
    r["discharge_date"] = r.date_of_discharge_as_written.map(pdate)
    log(ds, "Dates in 4 formats (01/04/2026, 1/4/26, 07-Apr-26, 01.04.2026)", len(r),
        f"Parsed day-first; unparsed admission dates: {r.admission_date.isna().sum()}")

    def page(x):
        x = x.strip().lower()
        if x == "adult":
            return np.nan
        m = re.fullmatch(r"(\d+)\s*(\d+)/12", x)
        if m:
            return int(m[1]) + int(m[2]) / 12
        m = re.fullmatch(r"(\d+)/12", x)
        if m:
            return int(m[1]) / 12
        m = re.fullmatch(r"(\d+)\s*mths?", x)
        if m:
            return int(m[1]) / 12
        m = re.fullmatch(r"(\d+)\s*(yrs?)?", x)
        return float(m[1]) if m else np.nan

    r["age_years"] = r.age_as_written.map(page).round(2)
    r["is_adult_unspecified"] = r.age_as_written.str.lower().eq("adult").astype(int)
    log(ds, "Age written as '3', '3 yrs', '2yrs', '6/12' (=6 months), '1 6/12' (=1.5 y), '7 mths', 'Adult'", len(r),
        "Converted to age_years; 'Adult' left blank with is_adult_unspecified=1")
    r["sex"] = r.sex_as_written.str.upper().str[0].map({"M": "M", "F": "F"}).fillna("Unknown")
    log(ds, "Sex as F/M/f/m/Female/Male/blank", (r.sex_as_written == "").sum(), "Standardised to M/F/Unknown")
    res = {"kisima central": "Kisima Central", "k. central": "Kisima Central", "kisima c.": "Kisima Central",
           "kisima e": "Kisima East", "kisima east": "Kisima East", "nderee": "Ndere", "ndere": "Ndere",
           "rabuor": "Rabuor North", "rabuor north": "Rabuor North", "rabuor n.": "Rabuor North",
           "miwani": "Miwani West", "miwani west": "Miwani West", "miwani w": "Miwani West",
           "lakeshore": "Lakeshore", "lake shore": "Lakeshore", "other county": "Outside county",
           "outside county": "Outside county", "siaya?": "Outside county (Siaya?)", "": "Not recorded"}
    r["residence"] = r.residence_as_written.str.lower().map(res).fillna(r.residence_as_written)
    log(ds, "Residence: 22 spellings of 7 places", len(r), "Standardised to 7 areas + Outside county + Not recorded. NB 'Rabuor' assumed = Rabuor North")
    out = {"d": "Discharged", "disch": "Discharged", "discharged": "Discharged", "died": "Died", "dead": "Died", "rip": "Died",
           "referred mtrh": "Referred", "r": "Referred", "ref": "Referred", "absc": "Absconded", "a": "Absconded",
           "absconded": "Absconded", "": "Not recorded (still in / unknown)"}
    r["outcome"] = r.outcome_as_written.str.lower().map(out)
    r["dq_outcome_D_ambiguous"] = r.outcome_as_written.eq("D").astype(int)
    log(ds, "Outcome 'D' (1,828 rows) could mean Discharged or Died", r.dq_outcome_D_ambiguous.sum(),
        "Mapped to Discharged (else ward mortality would be ~45%); flagged - confirm with ward staff")
    dx = [("malaria", "Malaria"), ("pneumonia|pna|cap|bronchiolitis", "Pneumonia / respiratory infection"),
          ("anaemia", "Anaemia"), ("convuls", "Convulsions"), ("ge|gastro|diarrh|age", "Gastroenteritis / diarrhoea"),
          ("sam|marasmus|malnutrition", "Malnutrition"), ("ptb|tb", "Tuberculosis"), ("rvd|iss", "HIV-related"),
          ("scd|sickle", "Sickle cell disease"), ("cva|stroke", "Stroke"), ("htn|ccf|heart", "Hypertension / heart failure"),
          ("dm|dka|hypogly", "Diabetes"), ("ca |malignancy", "Cancer"), ("poison|alcohol|snake|burns", "Poisoning / injury"),
          ("aki|ckd|renal", "Kidney disease"), ("mening|cm$", "Meningitis"), ("sepsis|uti", "Sepsis / UTI")]

    def dgroup(x):
        x = x.strip().lower()
        if x == "":
            return "Not recorded"
        for pat, g in dx:
            if re.search(r"\b(" + pat + r")", x):
                return g
        return "Other"
    r["diagnosis_group"] = r.diagnosis_as_written.map(dgroup)
    r["diagnosis_uncertain"] = r.diagnosis_as_written.str.contains(r"\?").astype(int)
    log(ds, "Diagnosis: 83 free-text variants incl. abbreviations (PNA, GE, SAM, RVD, ISS, CCF...)", len(r),
        "Grouped by first matching keyword into diagnosis_group (first condition wins for combos like 'malaria + anaemia')")
    r["payment"] = r.payment_as_written.replace("", "Not recorded")
    # register covers Apr-Jun 2026: a '2025' year is a slip of the pen (LOS of +/-365 days)
    r["dq_year_corrected"] = 0
    for c in ["admission_date", "discharge_date"]:
        bad = r[c].dt.year == 2025
        r.loc[bad, c] = r.loc[bad, c] + pd.DateOffset(years=1)
        r.loc[bad, "dq_year_corrected"] = 1
    log(ds, "Wrong year written (2025 instead of 2026) giving stays of +/-365 days", r.dq_year_corrected.sum(),
        "Year set to 2026; flagged dq_year_corrected")
    r["los_days"] = (r.discharge_date - r.admission_date).dt.days
    r["dq_negative_los"] = (r.los_days < 0).astype(int)
    log(ds, "Discharge date still before admission date after year fix", r.dq_negative_los.sum(), "Flagged")
    save("kenya_kisima/inpatient_register", r)


def ke_monthly():
    k = pd.read_csv(KE / "khis_monthly_inpatient_summary.csv")
    k["dq_not_submitted"] = k.report_status.eq("Not submitted").astype(int)
    log("ke.khis_monthly", "Ward-months not submitted (blank figures)", k.dq_not_submitted.sum(), "Left blank; flagged")
    calc = (k.inpatient_days / (k.official_beds * pd.to_datetime(k.month).dt.days_in_month) * 100).round(1)
    k["dq_occupancy_recalc_differs"] = ((calc - k.bed_occupancy_pct).abs() > 0.2).astype(int)
    k["bed_occupancy_pct_recalc"] = calc
    log("ke.khis_monthly", "Reported bed_occupancy_pct differs from inpatient_days / (beds x days)", k.dq_occupancy_recalc_differs.sum(), "Flagged, recalculated column added")
    ind = pd.read_csv(KE / "khis_monthly_hospital_indicators.csv")
    fin = pd.read_csv(KE / "staffing_infrastructure_financing_monthly.csv")
    save("kenya_kisima/khis_monthly_ward", k)
    hosp = (k.groupby("month")[["official_beds", "admissions", "discharges", "deaths", "absconded", "referred_out", "inpatient_days"]]
            .sum(min_count=1).reset_index().merge(ind, on="month", how="left").merge(fin.drop(columns="industrial_action"), on="month", how="left"))
    hosp["wards_not_submitted"] = k.groupby("month").dq_not_submitted.sum().values
    hosp["nurse_vacancy_pct"] = ((1 - hosp.nurses_in_post / hosp.nurses_approved_establishment) * 100).round(1)
    save("kenya_kisima/monthly_hospital_panel", hosp)
    for f in ["wards", "context_events"]:
        save(f"kenya_kisima/{f}", read(KE / f"{f}.csv"))


# =============================================================================
# Indonesia - Sukamaju (columns translated to English)
# =============================================================================
ID_COLS = {
    "tanggal": "date", "kode_ruangan": "ward_code", "nama_ruangan": "ward_name", "tt_tersedia": "beds_available",
    "pasien_awal": "patients_start_of_day", "pasien_masuk": "admissions", "pasien_pindahan": "transfers_in",
    "pasien_dipindahkan": "transfers_out", "keluar_hidup": "discharged_alive", "meninggal_kurang_48_jam": "deaths_under_48h",
    "meninggal_48_jam_atau_lebih": "deaths_48h_plus", "pasien_akhir": "patients_end_of_day", "hari_perawatan": "patient_days",
    "tt_tambahan_dipakai": "extra_beds_in_use", "keterangan": "remarks",
    "no_kunjungan": "visit_id", "no_rm_hash": "patient_hash", "tgl_masuk": "admission_date", "jam_masuk": "admission_time",
    "cara_masuk": "route_in", "asal_rujukan": "referral_source", "smf": "department", "kelas_hak": "class_entitled",
    "kelas_rawat": "class_placed", "titip_kelas": "placed_outside_class", "cara_bayar": "payer", "kode_icd10": "icd10",
    "diagnosa_utama": "diagnosis", "umur_tahun": "age_years", "jenis_kelamin": "sex", "kecamatan": "subdistrict",
    "tgl_keluar": "discharge_date", "jam_keluar": "discharge_time", "cara_keluar": "discharge_type",
    "lama_rawat_hari": "los_days", "nilai_klaim_idr": "claim_idr",
    "kunjungan_igd": "ed_visits", "triase_merah": "triage_red", "triase_kuning": "triage_yellow", "triase_hijau": "triage_green",
    "dirawat_inap_dari_igd": "admitted_from_ed", "pasien_menunggu_kamar_0700": "waiting_for_room_0700",
    "dirujuk_karena_kamar_penuh": "referred_out_hospital_full", "meninggal_di_igd": "died_in_ed",
    "tanggal_data": "snapshot_date", "jumlah_perawat_bidan": "nurses_midwives", "status_pns": "civil_servants",
    "status_pppk": "govt_contract", "status_blud_kontrak": "hospital_contract", "dinas_pagi_0700_1400": "nurses_morning_0700_1400",
    "dinas_siang_1400_2100": "nurses_afternoon_1400_2100", "dinas_malam_2100_0700": "nurses_night_2100_0700",
    "pelayanan": "service", "tt_sebelum_kris": "beds_before_kris", "tt_sesudah_kris_1jul2025": "beds_from_1jul2025",
    "tanggal_mulai": "start_date", "tanggal_selesai": "end_date", "jenis": "event_type", "description_en": "description_en",
}
EN = {
    "department": {"Penyakit Dalam": "Internal medicine", "Bedah": "Surgery", "Anak": "Paediatrics", "Kebidanan & Kandungan": "Obstetrics & gynaecology",
                   "Saraf": "Neurology", "Jantung": "Cardiology", "Paru": "Pulmonology", "Perinatologi": "Newborn care", "Intensif": "Intensive care"},
    "route_in": {"IGD": "Emergency department", "Poliklinik": "Outpatient clinic", "VK (kamar bersalin)": "Delivery room",
                 "Lahir di RS": "Born in hospital", "Rujukan": "Referral"},
    "referral_source": {"Datang sendiri": "Self", "Puskesmas": "Public health centre", "Klinik pratama": "Primary clinic",
                        "Dokter praktik mandiri": "Private doctor", "RS lain": "Another hospital"},
    "discharge_type": {"Atas persetujuan dokter": "Discharged (doctor agreed)", "Pulang atas permintaan sendiri (APS)": "Self-discharge (APS)",
                       "Dirujuk": "Referred", "Dirujuk (kamar penuh)": "Referred - no room", "Meninggal < 48 jam": "Died <48h",
                       "Meninggal >= 48 jam": "Died >=48h", "": "Still in hospital"},
    "sex": {"L": "M", "P": "F"},
    "placed_outside_class": {"Y": 1, "T": 0},
}


def id_tables():
    ds = "id"
    s = read(ID / "sensus_harian_rawat_inap.csv").rename(columns=ID_COLS)
    for c in s.columns[3:-1]:
        s[c] = num(s[c])
    s["date"] = pd.to_datetime(s.date)
    s["remarks"] = s.remarks.replace("Input manual - SIMRS gangguan", "Manual input - SIMRS server outage")
    calc = (s.patients_start_of_day + s.admissions + s.transfers_in - s.transfers_out - s.discharged_alive
            - s.deaths_under_48h - s.deaths_48h_plus)
    s["dq_balance_mismatch"] = (calc != s.patients_end_of_day).astype(int)
    s["dq_manual_input_outage"] = s.remarks.ne("").astype(int)
    log(ds + ".census", "Census does not balance on 9-11 Sep 2025 (SIMRS outage, keyed manually)", s.dq_balance_mismatch.sum(), "Flagged")
    s["occupancy_pct"] = (s.patients_end_of_day / s.beds_available * 100).round(1)
    s["post_kris"] = (s.date >= "2025-07-01").astype(int)
    log(ds + ".census", "Bed numbers fall 338 -> 304 on 1 Jul 2025 (KRIS room standard) - occupancy is not comparable before/after on raw counts",
        (s.post_kris == 1).sum(), "Added post_kris flag; occupancy_pct uses each day's own beds", "hospital profile")
    save("indonesia_sukamaju/daily_ward_census", s)

    k = read(ID / "kunjungan_rawat_inap.csv").rename(columns=ID_COLS)
    k["department_ind"] = k.department
    for c, m in EN.items():
        if c in k:
            k[c] = k[c].map(m).fillna(k[c]) if c != "placed_outside_class" else k[c].map(m)
    k["admission_date"] = pd.to_datetime(k.admission_date, format="%d/%m/%Y")
    k["discharge_date"] = dt(k.discharge_date, "%d/%m/%Y")
    log(ds + ".admissions", "Dates DD/MM/YYYY", len(k), "Parsed to ISO dates")
    k["payer_raw"] = k.payer
    k["payer"] = k.payer.replace({"BPJS PBI": "JKN PBI", "BPJS Non PBI": "JKN Non-PBI"})
    log(ds + ".admissions", "Payer labels changed 'BPJS ...' -> 'JKN ...' with the SIMRS update on 1 Jul 2025 (same scheme)",
        (k.payer_raw != k.payer).sum(), "Harmonised to JKN PBI / JKN Non-PBI; original in payer_raw", "calendar file")
    k["insurance_group"] = np.where(k.payer.str.startswith("JKN"), "JKN (national insurance)", k.payer.replace({"Umum": "Self-pay", "Asuransi swasta": "Private insurance", "Jamkesda": "Regional scheme"}))
    k["ed_admitted_never_reached_ward"] = k.ward_code.eq("IGD").astype(int)
    log(ds + ".admissions", "Ward 'IGD' = admitted but never reached a ward (all referred out because full)", k.ed_admitted_never_reached_ward.sum(), "Flagged")
    k["still_in_hospital_at_cutoff"] = k.discharge_date.isna().astype(int)
    calc = (k.discharge_date - k.admission_date).dt.days
    k["los_days"] = num(k.los_days)
    k["dq_los_mismatch"] = ((calc != k.los_days) & calc.notna()).astype(int)
    log(ds + ".admissions", "los_days disagrees with admission/discharge dates", k.dq_los_mismatch.sum(), "Flagged")
    k["dq_no_diagnosis"] = k.icd10.eq("").astype(int)
    log(ds + ".admissions", "No ICD-10 code (~35/month, plus most October 2026 stays not yet coded)", k.dq_no_diagnosis.sum(), "Flagged")
    for c in ["age_years", "claim_idr"]:
        k[c] = num(k[c])
    k["post_kris"] = (k.admission_date >= "2025-07-01").astype(int)
    save("indonesia_sukamaju/admissions", k)

    e = read(ID / "igd_harian.csv").rename(columns=ID_COLS)
    save("indonesia_sukamaju/ed_daily", e)
    for f, n in [("ketenagaan_perawat", "nurse_staffing_snapshots"), ("ruangan", "wards"), ("kalender_dan_kejadian", "calendar_events")]:
        t = read(ID / f"{f}.csv").rename(columns=ID_COLS)
        if "service" in t:
            t["service_en"] = t.service.map(EN["department"]).fillna(t.service)
        save(f"indonesia_sukamaju/{n}", t)


# =============================================================================
# Prototype back-test (one long table + error metrics)
# =============================================================================
def prototype():
    b = pd.concat([pd.read_csv(PROTO / "backtest_site_level.csv").assign(level="site"),
                   pd.read_csv(PROTO / "backtest_by_ward_group.csv").assign(level="ward_group")])
    b["abs_err_model"] = (b.forecast - b.actual).abs()
    b["abs_err_naive_last"] = (b.naive_last_known_value - b.actual).abs()
    b["abs_err_naive_weekday"] = (b.naive_same_weekday_last_week - b.actual).abs()
    b["inside_80pct_band"] = b.actual.between(b.lower_80, b.upper_80).astype(int)
    b["model_beats_naive_last"] = (b.abs_err_model < b.abs_err_naive_last).astype(int)
    save("prototype/backtest_long", b)
    s = (b.groupby(["level", "target", "horizon_days"])
         .agg(n=("actual", "size"), mae_model=("abs_err_model", "mean"), mae_naive_last=("abs_err_naive_last", "mean"),
              mae_naive_weekday=("abs_err_naive_weekday", "mean"), coverage_80=("inside_80pct_band", "mean"))
         .round(2).reset_index())
    s["model_better_than_best_naive"] = (s.mae_model < s[["mae_naive_last", "mae_naive_weekday"]].min(axis=1)).astype(int)
    save("prototype/backtest_summary", s)
    log("prototype", "Back-test: compare model with naive baselines and check 80% band coverage", len(b),
        "Derived error columns + summary table (no data changed)", "derived")


def main():
    OUT.mkdir(exist_ok=True)
    w = uk_wards()
    sp = uk_spells(w)
    uk_daily_panel(uk_sitrep(), sp)
    uk_other(w)
    ke_bed_state(); ke_register(); ke_monthly()
    id_tables()
    prototype()
    pd.DataFrame(LOG).to_csv(OUT / "data_quality_log.csv", index=False)
    db = OUT / "himc.sqlite"
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as con:
        for n, df in TABLES.items():
            df.to_sql(n, con, index=False)
        pd.DataFrame(LOG).to_sql("data_quality_log", con, index=False)
    cat = pd.DataFrame([(n.replace("__", "/") + ".csv", n, len(df), len(df.columns)) for n, df in TABLES.items()],
                       columns=["file", "sqlite_table", "rows", "columns"])
    cat.to_csv(OUT / "catalog.csv", index=False)
    print(cat.to_string(index=False))
    print(f"\n{len(LOG)} issues logged -> clean/data_quality_log.csv")


if __name__ == "__main__":
    main()
