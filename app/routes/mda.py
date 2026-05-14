"""
app/routes/mda.py — MDA Data Quality Check System
Upload endpoint + QC query endpoints for Mass Drug Administration data.
"""
import io
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
import openpyxl
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.models import User
from app.routes.auth import get_current_user, require_admin
from app.config import DATABASE_URL_SYNC

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mda", tags=["mda"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _str(val) -> Optional[str]:
    """Convert cell value to stripped string or None."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s not in ("", "---", "None") else None


def _int_safe(val) -> Optional[int]:
    try:
        return int(float(str(val)))
    except (TypeError, ValueError):
        return None


def _float_safe(val) -> Optional[float]:
    try:
        return float(str(val))
    except (TypeError, ValueError):
        return None


def _parse_gps(raw: Optional[str]):
    """
    Parse '12.7209383 5.0116833 343.2 4.58' → (lat, lon, alt, accuracy).
    Returns (None, None, None, None) on any error.
    """
    if not raw:
        return None, None, None, None
    parts = str(raw).strip().split()
    try:
        lat = float(parts[0])
        lon = float(parts[1])
        alt = float(parts[2]) if len(parts) > 2 else None
        acc = float(parts[3]) if len(parts) > 3 else None
        return lat, lon, alt, acc
    except (IndexError, ValueError):
        return None, None, None, None


def _parse_dt(val) -> Optional[datetime]:
    """Parse a datetime cell to UTC-aware datetime or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    s = str(val).strip()
    if not s or s in ("---", "None"):
        return None
    # Try common ISO formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _parse_date(val) -> Optional[str]:
    """Return ISO date string or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    s = str(val).strip()
    if not s or s in ("---", "None"):
        return None
    # strip time portion if present
    return s.split("T")[0].split(" ")[0] if len(s) >= 10 else None


def _get_sync_conn():
    """Open a synchronous psycopg2 connection using DATABASE_URL_SYNC."""
    return psycopg2.connect(DATABASE_URL_SYNC)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/mda/upload
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_mda(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Replace all MDA data with the uploaded Excel workbook.
    Parses Forms sheet (households) and Repeat-group_indv sheet (individuals).
    Returns QC flag summary counts.
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be an .xlsx workbook")

    raw_bytes = await file.read()

    # ── Parse workbook ──────────────────────────────────────────────────────
    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(raw_bytes), read_only=True, data_only=True
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot open workbook: {exc}")

    sheet_names = wb.sheetnames
    if "Forms" not in sheet_names:
        raise HTTPException(status_code=400, detail="Workbook must contain a 'Forms' sheet")

    # ── Parse Forms (households) ─────────────────────────────────────────────
    ws_forms = wb["Forms"]
    households: List[Dict[str, Any]] = []
    seen_formids: Dict[str, int] = {}   # formid → first occurrence index

    rows_forms = list(ws_forms.iter_rows(values_only=True))
    # Row 0 is the header — skip it
    for row_idx, row in enumerate(rows_forms[1:], start=1):
        def cell(idx):
            try:
                return row[idx]
            except IndexError:
                return None

        formid = _str(cell(1))
        if not formid:
            continue  # skip blank rows

        teamcode       = _str(cell(2))
        data_type      = _str(cell(3))
        trt_day        = _str(cell(4))
        data_entry_persons = _str(cell(5))
        phone_number_data  = _str(cell(6))
        admin3_code    = _str(cell(8))
        admin5_code    = _str(cell(10))
        consent_trt    = _str(cell(11))
        reasons_for_refusal        = _str(cell(12))
        others_reasons_for_refusal = _str(cell(13))
        gps_raw        = _str(cell(14))
        date_trt_raw   = _parse_date(cell(15))
        hh_num         = _str(cell(16))
        lga            = _str(cell(22))
        hh_seq         = _str(cell(27))
        serial_number_hh_id = _str(cell(28))
        number_of_treated   = _int_safe(cell(30))
        housemarking_code   = _str(cell(31))
        completed_time = _parse_dt(cell(32))
        started_time   = _parse_dt(cell(33))
        username       = _str(cell(34))
        received_on    = _parse_dt(cell(35))

        # Normalize RA name
        name_norm = data_entry_persons.lower().strip() if data_entry_persons else None
        ra_key = f"{name_norm}|{phone_number_data}" if name_norm and phone_number_data else None

        # GPS
        lat, lon, _alt, accuracy = _parse_gps(gps_raw)

        # Durations
        form_duration_min = None
        if started_time and completed_time:
            delta = (completed_time - started_time).total_seconds()
            form_duration_min = round(delta / 60.0, 2)

        sync_lag_hours = None
        if completed_time and received_on:
            delta = (received_on - completed_time).total_seconds()
            sync_lag_hours = round(delta / 3600.0, 2)

        # QC flags
        flag_gps_poor_accuracy = bool(accuracy is not None and accuracy > 20)
        flag_gps_zero = bool(
            lat is not None and lon is not None and lat == 0.0 and lon == 0.0
        )

        # After-hours: convert UTC to UTC+1 before checking
        flag_after_hours = False
        if started_time:
            local_hour = (started_time + timedelta(hours=1)).hour
            flag_after_hours = local_hour < 6 or local_hour >= 19

        flag_fast_form = bool(form_duration_min is not None and form_duration_min < 5)
        flag_slow_form = bool(form_duration_min is not None and form_duration_min > 60)
        flag_sync_lag  = bool(sync_lag_hours is not None and sync_lag_hours > 48)
        flag_refusal   = consent_trt == "0"

        # Duplicate detection (set to True for subsequent occurrences)
        flag_duplicate = formid in seen_formids
        if not flag_duplicate:
            seen_formids[formid] = row_idx

        # Geometry WKT
        geom_wkt = None
        if lat is not None and lon is not None and not flag_gps_zero:
            geom_wkt = f"SRID=4326;POINT({lon} {lat})"

        households.append({
            "formid": formid,
            "username": username,
            "teamcode": teamcode,
            "data_type": data_type,
            "data_entry_persons": data_entry_persons,
            "data_entry_persons_norm": name_norm,
            "phone_number_data": phone_number_data,
            "ra_key": ra_key,
            "lga": lga,
            "admin3_code": admin3_code,
            "admin5_code": admin5_code,
            "trt_day": trt_day,
            "date_trt": date_trt_raw,
            "consent_trt": consent_trt,
            "reasons_for_refusal": reasons_for_refusal,
            "others_reasons_for_refusal": others_reasons_for_refusal,
            "hh_num": hh_num,
            "hh_seq": hh_seq,
            "serial_number_hh_id": serial_number_hh_id,
            "number_of_treated": number_of_treated,
            "housemarking_code": housemarking_code,
            "gps_raw": gps_raw,
            "latitude": lat,
            "longitude": lon,
            "gps_accuracy": accuracy,
            "geom_wkt": geom_wkt,
            "started_time": started_time.isoformat() if started_time else None,
            "completed_time": completed_time.isoformat() if completed_time else None,
            "received_on": received_on.isoformat() if received_on else None,
            "form_duration_min": form_duration_min,
            "sync_lag_hours": sync_lag_hours,
            "flag_duplicate": flag_duplicate,
            "flag_gps_outside_lga": False,        # set by SQL after insert
            "flag_gps_poor_accuracy": flag_gps_poor_accuracy,
            "flag_gps_zero": flag_gps_zero,
            "flag_after_hours": flag_after_hours,
            "flag_fast_form": flag_fast_form,
            "flag_slow_form": flag_slow_form,
            "flag_sync_lag": flag_sync_lag,
            "flag_refusal": flag_refusal,
        })

    # ── Parse Repeat-group_indv (individuals) ────────────────────────────────
    individuals: List[Dict[str, Any]] = []
    valid_hh_formids = {h["formid"] for h in households if not h["flag_duplicate"]}

    if "Repeat- group_indv" in sheet_names:
        ws_indv = wb["Repeat- group_indv"]
        rows_indv = list(ws_indv.iter_rows(values_only=True))
        for row in rows_indv[1:]:
            def icell(idx):
                try:
                    return row[idx]
                except IndexError:
                    return None

            hh_formid   = _str(icell(17))
            if not hh_formid:
                continue

            mother_name       = _str(icell(3))
            child_name        = _str(icell(4))
            dob               = _parse_date(icell(5))
            dob_checknote     = _str(icell(6))
            sex               = _str(icell(7))
            height_cm         = _str(icell(8))
            treatment_status  = _str(icell(9))
            not_treated       = _str(icell(10))
            vomit_spill_azt   = _str(icell(11))
            child_id_r2       = _str(icell(12))
            respondent_hh_id  = _str(icell(16))
            age_in_months     = _int_safe(icell(18))
            individual_id     = _str(icell(20))

            flag_orphan = hh_formid not in valid_hh_formids

            individuals.append({
                "hh_formid": hh_formid,
                "mother_name": mother_name,
                "child_name": child_name,
                "dob": dob,
                "dob_checknote": dob_checknote,
                "sex": sex,
                "height_cm": height_cm,
                "age_in_months": age_in_months,
                "treatment_status": treatment_status,
                "not_treated": not_treated,
                "vomit_spill_azt": vomit_spill_azt,
                "child_id_r2": child_id_r2,
                "respondent_hh_id": respondent_hh_id,
                "individual_id": individual_id,
                "flag_orphan": flag_orphan,
            })

    wb.close()

    # ── Bulk insert via psycopg2 ─────────────────────────────────────────────
    conn = _get_sync_conn()
    try:
        cur = conn.cursor()

        # Delete existing data
        cur.execute("DELETE FROM mda_individuals")
        cur.execute("DELETE FROM mda_households")

        # Insert households
        if households:
            hh_cols = [
                "formid", "username", "teamcode", "data_type",
                "data_entry_persons", "data_entry_persons_norm",
                "phone_number_data", "ra_key", "lga",
                "admin3_code", "admin5_code", "trt_day", "date_trt",
                "consent_trt", "reasons_for_refusal", "others_reasons_for_refusal",
                "hh_num", "hh_seq", "serial_number_hh_id", "number_of_treated",
                "housemarking_code", "gps_raw", "latitude", "longitude",
                "gps_accuracy", "geom",
                "started_time", "completed_time", "received_on",
                "form_duration_min", "sync_lag_hours",
                "flag_duplicate", "flag_gps_outside_lga", "flag_gps_poor_accuracy",
                "flag_gps_zero", "flag_after_hours", "flag_fast_form",
                "flag_slow_form", "flag_sync_lag", "flag_refusal",
                "uploaded_at",
            ]

            hh_values = []
            now_str = datetime.utcnow().isoformat()
            for h in households:
                geom_val = h["geom_wkt"]  # WKT string or None
                hh_values.append((
                    h["formid"], h["username"], h["teamcode"], h["data_type"],
                    h["data_entry_persons"], h["data_entry_persons_norm"],
                    h["phone_number_data"], h["ra_key"], h["lga"],
                    h["admin3_code"], h["admin5_code"], h["trt_day"], h["date_trt"],
                    h["consent_trt"], h["reasons_for_refusal"], h["others_reasons_for_refusal"],
                    h["hh_num"], h["hh_seq"], h["serial_number_hh_id"], h["number_of_treated"],
                    h["housemarking_code"], h["gps_raw"], h["latitude"], h["longitude"],
                    h["gps_accuracy"], geom_val,
                    h["started_time"], h["completed_time"], h["received_on"],
                    h["form_duration_min"], h["sync_lag_hours"],
                    h["flag_duplicate"], h["flag_gps_outside_lga"], h["flag_gps_poor_accuracy"],
                    h["flag_gps_zero"], h["flag_after_hours"], h["flag_fast_form"],
                    h["flag_slow_form"], h["flag_sync_lag"], h["flag_refusal"],
                    now_str,
                ))

            col_str = ", ".join(hh_cols)
            # Build placeholders; geom column uses ST_GeomFromEWKT
            placeholders = []
            for col in hh_cols:
                if col == "geom":
                    placeholders.append("ST_GeomFromEWKT(%s)")
                else:
                    placeholders.append("%s")
            ph_str = ", ".join(placeholders)

            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO mda_households ({col_str}) VALUES %s",
                hh_values,
                template=f"({ph_str})",
                page_size=500,
            )

        # Insert individuals
        if individuals:
            indv_cols = [
                "hh_formid", "mother_name", "child_name", "dob", "dob_checknote",
                "sex", "height_cm", "age_in_months", "treatment_status",
                "not_treated", "vomit_spill_azt", "child_id_r2",
                "respondent_hh_id", "individual_id", "flag_orphan", "uploaded_at",
            ]
            now_str = datetime.utcnow().isoformat()
            indv_values = [
                (
                    i["hh_formid"], i["mother_name"], i["child_name"],
                    i["dob"], i["dob_checknote"], i["sex"], i["height_cm"],
                    i["age_in_months"], i["treatment_status"], i["not_treated"],
                    i["vomit_spill_azt"], i["child_id_r2"], i["respondent_hh_id"],
                    i["individual_id"], i["flag_orphan"], now_str,
                )
                for i in individuals
            ]
            col_str = ", ".join(indv_cols)
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO mda_individuals ({col_str}) VALUES %s",
                indv_values,
                page_size=1000,
            )

        # Set flag_gps_outside_lga via spatial join
        cur.execute("""
            UPDATE mda_households h
            SET flag_gps_outside_lga = TRUE
            WHERE h.geom IS NOT NULL
              AND h.flag_gps_zero = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM lgas l
                  WHERE l.project_id = 1
                    AND UPPER(TRIM(l.lga_name)) = UPPER(TRIM(h.lga))
                    AND ST_Within(h.geom, l.geom)
              )
        """)

        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.exception("MDA bulk insert failed")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")
    finally:
        conn.close()

    # ── Compute QC flag counts to return ────────────────────────────────────
    qc_flags = {
        "duplicates":        sum(1 for h in households if h["flag_duplicate"]),
        "gps_poor_accuracy": sum(1 for h in households if h["flag_gps_poor_accuracy"]),
        "gps_zero":          sum(1 for h in households if h["flag_gps_zero"]),
        "after_hours":       sum(1 for h in households if h["flag_after_hours"]),
        "fast_forms":        sum(1 for h in households if h["flag_fast_form"]),
        "slow_forms":        sum(1 for h in households if h["flag_slow_form"]),
        "sync_lag":          sum(1 for h in households if h["flag_sync_lag"]),
        "refusals":          sum(1 for h in households if h["flag_refusal"]),
        "orphan_individuals":sum(1 for i in individuals if i["flag_orphan"]),
    }

    return {
        "households": len(households),
        "individuals": len(individuals),
        "qc_flags": qc_flags,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/summary")
async def qc_summary(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(text("""
        SELECT
          COUNT(*) AS total_forms,
          SUM(CASE WHEN flag_duplicate THEN 1 ELSE 0 END) AS duplicates,
          SUM(CASE WHEN flag_gps_outside_lga THEN 1 ELSE 0 END) AS gps_outside_lga,
          SUM(CASE WHEN flag_gps_poor_accuracy THEN 1 ELSE 0 END) AS gps_poor_accuracy,
          SUM(CASE WHEN flag_gps_zero THEN 1 ELSE 0 END) AS gps_zero,
          SUM(CASE WHEN flag_after_hours THEN 1 ELSE 0 END) AS after_hours,
          SUM(CASE WHEN flag_fast_form THEN 1 ELSE 0 END) AS fast_forms,
          SUM(CASE WHEN flag_slow_form THEN 1 ELSE 0 END) AS slow_forms,
          SUM(CASE WHEN flag_sync_lag THEN 1 ELSE 0 END) AS sync_lag,
          SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END) AS refusals,
          COUNT(*) FILTER (WHERE flag_refusal) * 100.0 / NULLIF(COUNT(*), 0) AS refusal_pct,
          COUNT(DISTINCT date_trt) AS days_active,
          COUNT(DISTINCT lga) AS lgas_covered,
          COUNT(DISTINCT ra_key) AS ra_count,
          (SELECT COUNT(*) FROM mda_individuals) AS total_individuals,
          (SELECT MIN(uploaded_at) FROM mda_households) AS data_as_of
        FROM mda_households
    """))
    row = result.fetchone()
    if row is None:
        return {
            "total_forms": 0, "duplicates": 0, "gps_outside_lga": 0,
            "gps_poor_accuracy": 0, "gps_zero": 0, "after_hours": 0,
            "fast_forms": 0, "slow_forms": 0, "sync_lag": 0, "refusals": 0,
            "refusal_pct": 0.0, "days_active": 0, "lgas_covered": 0,
            "ra_count": 0, "total_individuals": 0, "data_as_of": None,
        }
    keys = [
        "total_forms", "duplicates", "gps_outside_lga", "gps_poor_accuracy",
        "gps_zero", "after_hours", "fast_forms", "slow_forms", "sync_lag",
        "refusals", "refusal_pct", "days_active", "lgas_covered", "ra_count",
        "total_individuals", "data_as_of",
    ]
    data = dict(zip(keys, row))
    # Ensure numeric types are JSON-safe
    for k in keys[:-1]:
        if data[k] is not None:
            data[k] = float(data[k]) if k == "refusal_pct" else int(data[k])
    if data["data_as_of"] is not None:
        data["data_as_of"] = str(data["data_as_of"])
    return data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/ra-performance
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/ra-performance")
async def qc_ra_performance(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(text("""
        SELECT
          data_entry_persons,
          phone_number_data,
          lga,
          date_trt::text AS date_trt,
          ra_key,
          COUNT(*) AS forms_submitted,
          SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END) AS refusals,
          ROUND(AVG(form_duration_min)::numeric, 1) AS avg_duration_min
        FROM mda_households
        WHERE ra_key IS NOT NULL
        GROUP BY data_entry_persons, phone_number_data, lga, date_trt, ra_key
        ORDER BY lga, date_trt, data_entry_persons
    """))
    rows = result.fetchall()
    keys = [
        "data_entry_persons", "phone_number_data", "lga", "date_trt",
        "ra_key", "forms_submitted", "refusals", "avg_duration_min",
    ]
    out = []
    for row in rows:
        d = dict(zip(keys, row))
        d["forms_submitted"] = int(d["forms_submitted"] or 0)
        d["refusals"] = int(d["refusals"] or 0)
        d["avg_duration_min"] = float(d["avg_duration_min"]) if d["avg_duration_min"] is not None else None
        d["meets_target"] = d["forms_submitted"] >= 10
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/refusals-by-lga
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/refusals-by-lga")
async def qc_refusals_by_lga(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(text("""
        SELECT
          lga,
          COUNT(*) AS total_forms,
          SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END) AS refusals,
          ROUND(
            100.0 * SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END)
            / NULLIF(COUNT(*), 0), 1
          ) AS refusal_pct
        FROM mda_households
        GROUP BY lga
        ORDER BY refusal_pct DESC
    """))
    rows = result.fetchall()
    keys = ["lga", "total_forms", "refusals", "refusal_pct"]
    return [
        {
            "lga": r[0],
            "total_forms": int(r[1] or 0),
            "refusals": int(r[2] or 0),
            "refusal_pct": float(r[3] or 0),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/duration-by-lga
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/duration-by-lga")
async def qc_duration_by_lga(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(text("""
        SELECT
          lga,
          COUNT(*) AS total,
          SUM(CASE WHEN flag_fast_form THEN 1 ELSE 0 END) AS fast_count,
          SUM(CASE WHEN flag_slow_form THEN 1 ELSE 0 END) AS slow_count,
          ROUND(AVG(form_duration_min)::numeric, 1) AS avg_min,
          ROUND(MIN(form_duration_min)::numeric, 1) AS min_min,
          ROUND(MAX(form_duration_min)::numeric, 1) AS max_min
        FROM mda_households
        WHERE form_duration_min IS NOT NULL
        GROUP BY lga
        ORDER BY avg_min ASC
    """))
    rows = result.fetchall()
    return [
        {
            "lga": r[0],
            "total": int(r[1] or 0),
            "fast_count": int(r[2] or 0),
            "slow_count": int(r[3] or 0),
            "avg_min": float(r[4]) if r[4] is not None else None,
            "min_min": float(r[5]) if r[5] is not None else None,
            "max_min": float(r[6]) if r[6] is not None else None,
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/gps/geojson
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/gps/geojson")
async def qc_gps_geojson(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(text("""
        SELECT
          formid,
          lga,
          ra_key,
          data_entry_persons,
          date_trt::text AS date_trt,
          flag_gps_outside_lga,
          flag_gps_poor_accuracy,
          gps_accuracy,
          form_duration_min,
          ST_AsGeoJSON(geom)::json AS geometry
        FROM mda_households
        WHERE geom IS NOT NULL
        LIMIT 50000
    """))
    rows = result.fetchall()
    features = []
    for row in rows:
        (formid, lga, ra_key, data_entry_persons, date_trt,
         flag_gps_outside_lga, flag_gps_poor_accuracy,
         gps_accuracy, form_duration_min, geometry) = row

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "formid": formid,
                "lga": lga,
                "ra_key": ra_key,
                "data_entry_persons": data_entry_persons,
                "date_trt": date_trt,
                "flag_gps_outside_lga": bool(flag_gps_outside_lga),
                "flag_gps_poor_accuracy": bool(flag_gps_poor_accuracy),
                "gps_accuracy": float(gps_accuracy) if gps_accuracy is not None else None,
                "form_duration_min": float(form_duration_min) if form_duration_min is not None else None,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }
