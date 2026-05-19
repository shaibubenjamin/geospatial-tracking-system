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
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.models import User
from app.routes.auth import get_current_user, get_current_user_optional, require_admin, require_superadmin
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


async def resolve_pid(project_id: Optional[int] = None, db: AsyncSession = Depends(get_db)) -> int:
    """Resolve which project's data to query.

    - Explicit ?project_id=N in the request → that project (lets users view any historical round).
    - Otherwise → the project flagged is_active = TRUE (default behaviour).
    - As a last resort → the lowest-id project (so a fresh DB without an active project still works).
    """
    if project_id is not None:
        return project_id
    res = await db.execute(text("SELECT id FROM geo_projects WHERE is_active = TRUE ORDER BY id LIMIT 1"))
    row = res.fetchone()
    if row:
        return row[0]
    res = await db.execute(text("SELECT id FROM geo_projects ORDER BY id LIMIT 1"))
    row = res.fetchone()
    return row[0] if row else 1


def _scoped_where(pid: int, filters: list, params: dict, alias: str = "") -> str:
    """Build a WHERE clause that always pins the query to a single project.

    Pass an alias when the table is aliased in the FROM/JOIN (e.g. ``"h"`` for ``mda_households h``).
    """
    col = f"{alias}.project_id" if alias else "project_id"
    filters.insert(0, f"{col} = :pid")
    params["pid"] = pid
    return "WHERE " + " AND ".join(filters)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/mda/upload
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_mda(
    file: UploadFile = File(...),
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """
    Replace MDA data for the active project with the uploaded Excel workbook.
    Other projects (other rounds / other states) are untouched.
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
        check_treatment_date_raw = _parse_date(cell(21))
        hq_user_val = _str(cell(37))

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
            "flag_duplicate_gps": False,          # set by SQL after insert
            "flag_gps_outside_lga": False,        # set by SQL after insert
            "flag_gps_outside_ward": False,       # set by SQL after insert
            "flag_gps_outside_state": False,      # set by SQL after insert
            "flag_gps_poor_accuracy": flag_gps_poor_accuracy,
            "flag_gps_zero": flag_gps_zero,
            "flag_after_hours": flag_after_hours,
            "flag_fast_form": flag_fast_form,
            "flag_slow_form": flag_slow_form,
            "flag_sync_lag": flag_sync_lag,
            "flag_refusal": flag_refusal,
            "check_treatment_date": check_treatment_date_raw,
            "hq_user": hq_user_val,
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

        # Resolve the boundary-owning project for the same state (lowest-id project
        # in the same state). For Sokoto that's R4 (id=1) — boundaries are state-level.
        cur.execute("""
            SELECT MIN(p2.id) FROM geo_projects p1
            JOIN geo_projects p2 ON p2.state_name = p1.state_name
            WHERE p1.id = %s
        """, (pid,))
        boundary_pid = (cur.fetchone() or [pid])[0] or pid

        # Delete existing data — scoped to the active project only
        cur.execute("DELETE FROM mda_individuals WHERE project_id = %s", (pid,))
        cur.execute("DELETE FROM mda_households  WHERE project_id = %s", (pid,))

        # Insert households
        if households:
            hh_cols = [
                "project_id",
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
                "flag_duplicate", "flag_duplicate_gps",
                "flag_gps_outside_lga", "flag_gps_outside_ward", "flag_gps_outside_state",
                "flag_gps_poor_accuracy", "flag_gps_zero", "flag_after_hours",
                "flag_fast_form", "flag_slow_form", "flag_sync_lag", "flag_refusal",
                "check_treatment_date", "hq_user",
                "uploaded_at",
            ]

            hh_values = []
            now_str = datetime.utcnow().isoformat()
            for h in households:
                geom_val = h["geom_wkt"]  # WKT string or None
                hh_values.append((
                    pid,
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
                    # flags — must match hh_cols order exactly
                    h["flag_duplicate"], h["flag_duplicate_gps"],
                    h["flag_gps_outside_lga"], h["flag_gps_outside_ward"], h["flag_gps_outside_state"],
                    h["flag_gps_poor_accuracy"], h["flag_gps_zero"], h["flag_after_hours"],
                    h["flag_fast_form"], h["flag_slow_form"], h["flag_sync_lag"], h["flag_refusal"],
                    h["check_treatment_date"], h["hq_user"],
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
                "project_id",
                "hh_formid", "mother_name", "child_name", "dob", "dob_checknote",
                "sex", "height_cm", "age_in_months", "treatment_status",
                "not_treated", "vomit_spill_azt", "child_id_r2",
                "respondent_hh_id", "individual_id", "flag_orphan", "uploaded_at",
            ]
            now_str = datetime.utcnow().isoformat()
            indv_values = [
                (
                    pid,
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

        # GPS outside stated LGA polygon (scoped to this project's households +
        # the state's canonical boundary project)
        cur.execute("""
            UPDATE mda_households h
            SET flag_gps_outside_lga = TRUE
            WHERE h.project_id = %s
              AND h.geom IS NOT NULL AND h.flag_gps_zero = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM lgas l
                  WHERE l.project_id = %s
                    AND UPPER(TRIM(l.lga_name)) = UPPER(TRIM(h.lga))
                    AND ST_Within(h.geom, l.geom)
              )
        """, (pid, boundary_pid))

        # GPS outside any state LGA polygon
        cur.execute("""
            UPDATE mda_households h
            SET flag_gps_outside_state = TRUE
            WHERE h.project_id = %s
              AND h.geom IS NOT NULL AND h.flag_gps_zero = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM lgas l
                  WHERE l.project_id = %s
                    AND ST_Within(h.geom, l.geom)
              )
        """, (pid, boundary_pid))

        # GPS outside any ward polygon
        cur.execute("""
            UPDATE mda_households h
            SET flag_gps_outside_ward = TRUE
            WHERE h.project_id = %s
              AND h.geom IS NOT NULL AND h.flag_gps_zero = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM wards w
                  WHERE w.project_id = %s
                    AND ST_Within(h.geom, w.geom)
              )
        """, (pid, boundary_pid))

        # Duplicate GPS coordinates within this project
        cur.execute("""
            UPDATE mda_households h
            SET flag_duplicate_gps = TRUE
            WHERE h.project_id = %s
              AND h.latitude IS NOT NULL AND h.longitude IS NOT NULL
              AND h.flag_gps_zero = FALSE
              AND EXISTS (
                  SELECT 1 FROM mda_households h2
                  WHERE h2.project_id = %s
                    AND h2.id != h.id
                    AND h2.latitude = h.latitude
                    AND h2.longitude = h.longitude
              )
        """, (pid, pid))

        # Spatially join each household GPS point to the ward boundary → populate ward_name
        cur.execute("""
            UPDATE mda_households h
            SET ward_name = w.ward_name
            FROM wards w
            WHERE h.project_id = %s
              AND w.project_id = %s
              AND ST_Within(h.geom, w.geom)
              AND h.geom IS NOT NULL
              AND h.flag_gps_zero = FALSE
        """, (pid, boundary_pid))

        # Normalise lga names to match shapefile Title Case (e.g. TAMBUWAL → Tambuwal)
        cur.execute("""
            UPDATE mda_households h
            SET lga = w.lga_name
            FROM (SELECT DISTINCT lga_name FROM wards WHERE project_id = %s) w
            WHERE h.project_id = %s
              AND UPPER(TRIM(h.lga)) = UPPER(TRIM(w.lga_name))
              AND h.lga <> w.lga_name
        """, (boundary_pid, pid))

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
    lga:       Optional[str] = None,
    ward:      Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    params: dict = {}
    filters: list = []
    if lga:
        filters.append("lga = :lga")
        params["lga"] = lga
    if ward:
        filters.append("ward_name = :ward")
        params["ward"] = ward
    if date_from: filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date >= :date_from"); params["date_from"] = date_from
    if date_to:   filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date <= :date_to");   params["date_to"]   = date_to
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          COUNT(*) AS total_forms,
          SUM(CASE WHEN flag_duplicate THEN 1 ELSE 0 END) AS duplicates,
          SUM(CASE WHEN flag_duplicate_gps THEN 1 ELSE 0 END) AS duplicate_gps,
          SUM(CASE WHEN flag_gps_outside_lga THEN 1 ELSE 0 END) AS gps_outside_lga,
          SUM(CASE WHEN flag_gps_outside_ward THEN 1 ELSE 0 END) AS gps_outside_ward,
          SUM(CASE WHEN flag_gps_outside_state THEN 1 ELSE 0 END) AS gps_outside_state,
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
          (SELECT COUNT(*) FROM mda_individuals WHERE project_id = :pid) AS total_individuals,
          (SELECT MIN(uploaded_at) FROM mda_households WHERE project_id = :pid) AS data_as_of
        FROM mda_households {where}
    """), params)
    row = result.fetchone()
    if row is None:
        return {
            "total_forms": 0, "duplicates": 0, "duplicate_gps": 0,
            "gps_outside_lga": 0, "gps_outside_ward": 0, "gps_outside_state": 0,
            "gps_poor_accuracy": 0, "gps_zero": 0, "after_hours": 0,
            "fast_forms": 0, "slow_forms": 0, "sync_lag": 0, "refusals": 0,
            "refusal_pct": 0.0, "days_active": 0, "lgas_covered": 0,
            "ra_count": 0, "total_individuals": 0, "data_as_of": None,
        }
    keys = [
        "total_forms", "duplicates", "duplicate_gps",
        "gps_outside_lga", "gps_outside_ward", "gps_outside_state",
        "gps_poor_accuracy", "gps_zero", "after_hours",
        "fast_forms", "slow_forms", "sync_lag",
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
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
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
        WHERE project_id = :pid AND ra_key IS NOT NULL
        GROUP BY data_entry_persons, phone_number_data, lga, date_trt, ra_key
        ORDER BY lga, date_trt, data_entry_persons
    """), {"pid": pid})
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
    lga: Optional[str] = None,
    ward: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    params: dict = {}
    filters: list = []
    if lga:  filters.append("lga = :lga");  params["lga"] = lga
    if ward: filters.append("ward_name = :ward"); params["ward"] = ward
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          lga,
          COUNT(*) AS total_forms,
          SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END) AS refusals,
          ROUND(
            100.0 * SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END)
            / NULLIF(COUNT(*), 0), 1
          ) AS refusal_pct
        FROM mda_households {where}
        GROUP BY lga
        ORDER BY refusal_pct DESC
    """), params)
    rows = result.fetchall()
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
    lga: Optional[str] = None,
    ward: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    params: dict = {}
    filters: list = ["form_duration_min IS NOT NULL"]
    if lga:  filters.append("lga = :lga");  params["lga"] = lga
    if ward: filters.append("ward_name = :ward"); params["ward"] = ward
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          lga,
          COUNT(*) AS total,
          SUM(CASE WHEN flag_fast_form THEN 1 ELSE 0 END) AS fast_count,
          SUM(CASE WHEN flag_slow_form THEN 1 ELSE 0 END) AS slow_count,
          ROUND(AVG(form_duration_min)::numeric, 1) AS avg_min,
          ROUND(MIN(form_duration_min)::numeric, 1) AS min_min,
          ROUND(MAX(form_duration_min)::numeric, 1) AS max_min
        FROM mda_households {where}
        GROUP BY lga
        ORDER BY avg_min ASC
    """), params)
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
# GET /api/mda/submissions/ward  — ward-level drill-down for charts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/submissions/ward")
async def submissions_by_ward(
    lga:  Optional[str] = None,
    ward: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Ward-level submission & treatment counts, used for chart drill-down."""
    filters = ["ward_name IS NOT NULL"]
    params: dict = {}
    if lga:  filters.append("lga = :lga");       params["lga"]  = lga
    if ward: filters.append("ward_name = :ward"); params["ward"] = ward
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          ward_name,
          lga,
          COUNT(*) AS forms,
          COALESCE(SUM(number_of_treated), 0) AS treated,
          COUNT(DISTINCT hq_user) AS teams,
          ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT check_treatment_date), 0), 1) AS avg_per_day
        FROM mda_households {where}
        GROUP BY ward_name, lga
        ORDER BY forms DESC
    """), params)
    keys = ["ward_name", "lga", "forms", "treated", "teams", "avg_per_day"]
    return [dict(zip(keys, row)) for row in result.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/teams-summary  — per-team QC error breakdown
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/teams-summary")
async def qc_teams_summary(
    lga:       Optional[str] = None,
    ward:      Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Per-team: total forms, forms with ≥1 error, error rate, and error type counts."""
    filters: list = ["hq_user IS NOT NULL"]
    params: dict = {}
    if lga:       filters.append("lga = :lga");       params["lga"]  = lga
    if ward:      filters.append("ward_name = :ward"); params["ward"] = ward
    if date_from: filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date >= :date_from"); params["date_from"] = date_from
    if date_to:   filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date <= :date_to");   params["date_to"]   = date_to
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          hq_user,
          lga,
          COUNT(*)                                                        AS total_forms,
          SUM(CASE WHEN (
            flag_duplicate OR flag_duplicate_gps OR flag_gps_outside_lga
            OR flag_gps_poor_accuracy OR flag_gps_zero OR flag_after_hours
            OR flag_fast_form OR flag_slow_form OR flag_sync_lag
          ) THEN 1 ELSE 0 END)                                           AS forms_with_error,
          SUM(CASE WHEN flag_duplicate         THEN 1 ELSE 0 END)        AS dup_forms,
          SUM(CASE WHEN flag_duplicate_gps     THEN 1 ELSE 0 END)        AS dup_gps,
          SUM(CASE WHEN flag_gps_outside_lga   THEN 1 ELSE 0 END)        AS gps_outside_lga,
          SUM(CASE WHEN flag_gps_poor_accuracy THEN 1 ELSE 0 END)        AS poor_gps,
          SUM(CASE WHEN flag_after_hours       THEN 1 ELSE 0 END)        AS after_hours,
          SUM(CASE WHEN flag_fast_form         THEN 1 ELSE 0 END)        AS fast_forms,
          SUM(CASE WHEN flag_slow_form         THEN 1 ELSE 0 END)        AS slow_forms,
          SUM(CASE WHEN flag_refusal           THEN 1 ELSE 0 END)        AS refusals
        FROM mda_households {where}
        GROUP BY hq_user, lga
        ORDER BY forms_with_error DESC, total_forms DESC
    """), params)
    rows = result.fetchall()
    keys = [
        "hq_user","lga","total_forms","forms_with_error",
        "dup_forms","dup_gps","gps_outside_lga","poor_gps",
        "after_hours","fast_forms","slow_forms","refusals",
    ]
    out = []
    for row in rows:
        d = dict(zip(keys, row))
        for k in keys[2:]:
            d[k] = int(d[k] or 0)
        d["error_rate"] = round(100.0 * d["forms_with_error"] / d["total_forms"], 1) if d["total_forms"] else 0
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/gps/geojson
# ─────────────────────────────────────────────────────────────────────────────

async def _gps_geojson(db: AsyncSession, pid: int, where_clause: str, limit: int = 20000):
    """Shared helper for filtered GPS GeoJSON endpoints."""
    result = await db.execute(text(f"""
        SELECT
          formid, lga, data_entry_persons,
          date_trt::text, gps_accuracy, form_duration_min,
          flag_gps_outside_lga, flag_gps_outside_ward,
          flag_gps_outside_state, flag_duplicate_gps,
          latitude, longitude,
          ST_AsGeoJSON(geom)::json AS geometry
        FROM mda_households
        WHERE project_id = :pid AND geom IS NOT NULL AND {where_clause}
        LIMIT {limit}
    """), {"pid": pid})
    rows = result.fetchall()
    features = []
    for row in rows:
        (formid, lga, ra, date_trt, acc, dur,
         out_lga, out_ward, out_state, dup_gps, lat, lon, geometry) = row
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "formid": formid, "lga": lga, "ra": ra,
                "date_trt": date_trt,
                "accuracy": float(acc) if acc else None,
                "duration_min": float(dur) if dur else None,
                "out_lga": bool(out_lga), "out_ward": bool(out_ward),
                "out_state": bool(out_state), "dup_gps": bool(dup_gps),
                "lat": float(lat) if lat else None,
                "lon": float(lon) if lon else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


@router.get("/qc/gps/outside-lga")
async def gps_outside_lga(pid: int = Depends(resolve_pid), db: AsyncSession = Depends(get_db), _u: Optional[User] = Depends(get_current_user_optional)):
    return await _gps_geojson(db, pid, "flag_gps_outside_lga = TRUE")


@router.get("/qc/gps/outside-ward")
async def gps_outside_ward(pid: int = Depends(resolve_pid), db: AsyncSession = Depends(get_db), _u: Optional[User] = Depends(get_current_user_optional)):
    return await _gps_geojson(db, pid, "flag_gps_outside_ward = TRUE")


@router.get("/qc/gps/outside-state")
async def gps_outside_state(pid: int = Depends(resolve_pid), db: AsyncSession = Depends(get_db), _u: Optional[User] = Depends(get_current_user_optional)):
    return await _gps_geojson(db, pid, "flag_gps_outside_state = TRUE")


@router.get("/qc/gps/duplicate")
async def gps_duplicate(pid: int = Depends(resolve_pid), db: AsyncSession = Depends(get_db), _u: Optional[User] = Depends(get_current_user_optional)):
    return await _gps_geojson(db, pid, "flag_duplicate_gps = TRUE")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/overview
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/overview")
async def mda_overview(
    lga:       Optional[str] = None,
    ward:      Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    filters, params = [], {}
    if lga:       filters.append("lga = :lga");       params["lga"]  = lga
    if ward:      filters.append("ward_name = :ward"); params["ward"] = ward
    if date_from: filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date >= :date_from"); params["date_from"] = date_from
    if date_to:   filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date <= :date_to");   params["date_to"]   = date_to
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          COUNT(*) AS total_forms,
          COALESCE(SUM(number_of_treated), 0) AS total_treated,
          COUNT(DISTINCT hq_user) AS teams_active,
          COUNT(DISTINCT check_treatment_date) AS days_active,
          COUNT(DISTINCT lga) AS lgas_covered,
          SUM(CASE WHEN flag_refusal THEN 1 ELSE 0 END) AS refusals,
          SUM(CASE WHEN flag_fast_form THEN 1 ELSE 0 END) AS fast_forms,
          SUM(CASE WHEN flag_after_hours THEN 1 ELSE 0 END) AS after_hours,
          SUM(CASE WHEN flag_gps_outside_lga THEN 1 ELSE 0 END) AS gps_outside_lga,
          SUM(CASE WHEN flag_duplicate_gps THEN 1 ELSE 0 END) AS duplicate_gps,
          (SELECT COALESCE(SUM(total_treated),0) FROM mda_baseline WHERE project_id = :pid) AS baseline_total,
          MIN(check_treatment_date)::text AS campaign_start,
          MAX(check_treatment_date)::text AS campaign_end
        FROM mda_households {where}
    """), params)
    row = result.fetchone()
    if not row or not row[0]:
        return {"total_forms": 0}
    d = dict(zip([
        "total_forms","total_treated","teams_active","days_active",
        "lgas_covered","refusals","fast_forms","after_hours",
        "gps_outside_lga","duplicate_gps","baseline_total",
        "campaign_start","campaign_end"
    ], row))
    for k in ["total_forms","total_treated","teams_active","days_active","lgas_covered",
              "refusals","fast_forms","after_hours","gps_outside_lga","duplicate_gps","baseline_total"]:
        if d[k] is not None: d[k] = int(d[k])
    bl = d["baseline_total"] or 0
    d["coverage_pct"] = round(100.0 * d["total_treated"] / bl, 1) if bl > 0 else 0
    d["total_qc_flags"] = d["after_hours"] + d["fast_forms"] + d["gps_outside_lga"] + d["duplicate_gps"]
    return d


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/trends/daily
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trends/daily")
async def mda_trends_daily(
    lga:       Optional[str] = None,
    ward:      Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    filters = ["check_treatment_date IS NOT NULL"]
    params: dict = {}
    if lga:       filters.append("lga = :lga");       params["lga"]  = lga
    if ward:      filters.append("ward_name = :ward"); params["ward"] = ward
    if date_from: filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date >= :date_from"); params["date_from"] = date_from
    if date_to:   filters.append("(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date <= :date_to");   params["date_to"]   = date_to
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          check_treatment_date::text AS date,
          COUNT(*) AS forms,
          COALESCE(SUM(number_of_treated), 0) AS treated,
          COUNT(DISTINCT hq_user) AS teams
        FROM mda_households {where}
        GROUP BY check_treatment_date
        ORDER BY check_treatment_date
    """), params)
    return [dict(zip(["date","forms","treated","teams"], row)) for row in result.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/teams/performance
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/teams/performance")
async def mda_teams(
    lga:  Optional[str] = None,
    ward: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    filters = ["hq_user IS NOT NULL"]
    params: dict = {}
    if lga:  filters.append("lga = :lga");       params["lga"]  = lga
    if ward: filters.append("ward_name = :ward"); params["ward"] = ward
    where = _scoped_where(pid, filters, params)
    result = await db.execute(text(f"""
        SELECT
          hq_user,
          lga,
          COUNT(*) AS total_forms,
          COUNT(DISTINCT check_treatment_date) AS days_active,
          ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT check_treatment_date), 0), 1) AS avg_per_day,
          COALESCE(SUM(number_of_treated), 0) AS total_treated,
          SUM(CASE WHEN flag_after_hours THEN 1 ELSE 0 END) AS after_hours,
          SUM(CASE WHEN flag_fast_form THEN 1 ELSE 0 END) AS fast_forms
        FROM mda_households {where}
        GROUP BY hq_user, lga
        ORDER BY avg_per_day DESC
    """), params)
    rows = result.fetchall()
    keys = ["hq_user","lga","total_forms","days_active","avg_per_day","total_treated","after_hours","fast_forms"]
    out = []
    for row in rows:
        d = dict(zip(keys, row))
        d["avg_per_day"] = float(d["avg_per_day"] or 0)
        d["meets_target"] = d["avg_per_day"] >= 80
        for k in ["total_forms","days_active","total_treated","after_hours","fast_forms"]:
            if d[k] is not None: d[k] = int(d[k])
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/coverage/lga
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/coverage/lga")
async def mda_coverage_lga(
    lga:       Optional[str] = None,
    ward:      Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Coverage per LGA — driven by baseline, so every LGA in the round's
    target appears even if no field forms have come in yet (coverage_pct=0,
    forms=0, treated=0). This makes "LGAs below 60% → mop-up" and "LGAs on
    target ≥80%" KPIs reflect the full campaign universe, not just whichever
    LGAs happened to submit so far.
    """
    # Filters that apply on the household side of the join (date/ward).
    hh_extra_filters: list = ["h.project_id = :pid"]
    params: dict = {"pid": pid}
    if ward:
        hh_extra_filters.append("h.ward_name = :ward")
        params["ward"] = ward
    if date_from:
        hh_extra_filters.append("(h.completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        hh_extra_filters.append("(h.completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date <= :date_to")
        params["date_to"] = date_to
    hh_on_clause = "h.project_id = :pid AND UPPER(TRIM(h.lga)) = b.lga_key" + "".join(
        " AND " + f for f in hh_extra_filters if f != "h.project_id = :pid"
    )

    # Optional LGA-name filter applies on the baseline (drives which LGAs appear).
    baseline_filter = ""
    if lga:
        baseline_filter = "AND UPPER(TRIM(lga)) = UPPER(TRIM(:lga))"
        params["lga"] = lga

    result = await db.execute(text(f"""
        WITH baseline AS (
          SELECT
            -- Display the baseline-supplied LGA name (title-cased to match boundary names)
            INITCAP(LOWER(TRIM(lga))) AS lga,
            UPPER(TRIM(lga))          AS lga_key,
            SUM(total_treated)        AS baseline_total
          FROM mda_baseline
          WHERE project_id = :pid {baseline_filter}
          GROUP BY INITCAP(LOWER(TRIM(lga))), UPPER(TRIM(lga))
        )
        SELECT
          b.lga,
          COUNT(h.id)                                AS forms,
          COALESCE(SUM(h.number_of_treated), 0)      AS actual_treated,
          b.baseline_total,
          CASE WHEN b.baseline_total > 0
               THEN ROUND(100.0 * COALESCE(SUM(h.number_of_treated), 0) / b.baseline_total, 1)
               ELSE 0 END                            AS coverage_pct,
          COUNT(DISTINCT h.hq_user)                  AS teams,
          COUNT(DISTINCT h.check_treatment_date)     AS days_reported
        FROM baseline b
        LEFT JOIN mda_households h
          ON {hh_on_clause}
        GROUP BY b.lga, b.baseline_total
        ORDER BY coverage_pct DESC, b.lga
    """), params)
    rows = result.fetchall()
    keys = ["lga","forms","actual_treated","baseline_total","coverage_pct","teams","days_reported"]
    return [dict(zip(keys, row)) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/coverage/ward
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/coverage/ward")
async def mda_coverage_ward(
    lga: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Ward-level coverage vs baseline, optionally filtered by LGA."""
    filters: list = []
    params: dict = {}
    if lga:
        filters.append("UPPER(TRIM(h.lga)) = UPPER(TRIM(:lga))")
        params["lga"] = lga
    where = _scoped_where(pid, filters, params, alias="h")
    result = await db.execute(text(f"""
        SELECT
          h.ward_name,
          h.lga,
          COUNT(*) AS forms,
          COALESCE(SUM(h.number_of_treated), 0) AS actual_treated,
          COALESCE(b.baseline_total, 0) AS baseline_total,
          CASE WHEN COALESCE(b.baseline_total, 0) > 0
               THEN ROUND(100.0 * COALESCE(SUM(h.number_of_treated), 0) / b.baseline_total, 1)
               ELSE 0 END AS coverage_pct,
          COUNT(DISTINCT h.hq_user) AS teams,
          COUNT(DISTINCT h.check_treatment_date) AS days_reported
        FROM mda_households h
        LEFT JOIN (
          SELECT UPPER(TRIM(lga)) AS lga, UPPER(TRIM(ward)) AS ward, SUM(total_treated) AS baseline_total
          FROM mda_baseline WHERE project_id = :pid GROUP BY UPPER(TRIM(lga)), UPPER(TRIM(ward))
        ) b ON UPPER(TRIM(h.lga)) = b.lga AND UPPER(TRIM(h.ward_name)) = b.ward
        {where}
        GROUP BY h.ward_name, h.lga, b.baseline_total
        ORDER BY coverage_pct DESC NULLS LAST
    """), params)
    rows = result.fetchall()
    keys = ["ward_name","lga","forms","actual_treated","baseline_total","coverage_pct","teams","days_reported"]
    return [dict(zip(keys, row)) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/individuals/age-summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/individuals/age-summary")
async def individuals_age_summary(
    lga:  Optional[str] = None,
    ward: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Age-band breakdown from mda_individuals, filterable by LGA and ward."""
    hh_filters = ["h.project_id = :pid", "i.project_id = :pid", "h.lga IS NOT NULL"]
    params: dict = {"pid": pid}
    if lga:
        hh_filters.append("h.lga = :lga")
        params["lga"] = lga
    if ward:
        hh_filters.append("h.ward_name = :ward")
        params["ward"] = ward
    hh_where = " AND ".join(hh_filters)

    result = await db.execute(text(f"""
        SELECT
            COUNT(*) FILTER (WHERE i.age_in_months BETWEEN 1 AND 11)                          AS total_1_11,
            COUNT(*) FILTER (WHERE i.age_in_months BETWEEN 1 AND 11  AND i.treatment_status='1') AS treated_1_11,
            COUNT(*) FILTER (WHERE i.age_in_months BETWEEN 12 AND 59)                         AS total_12_59,
            COUNT(*) FILTER (WHERE i.age_in_months BETWEEN 12 AND 59 AND i.treatment_status='1') AS treated_12_59,
            COUNT(*) FILTER (WHERE i.treatment_status='1')                                     AS total_treated,
            COUNT(*)                                                                            AS grand_total
        FROM mda_individuals i
        JOIN mda_households h ON h.formid = i.hh_formid
        WHERE i.age_in_months IS NOT NULL AND i.age_in_months BETWEEN 1 AND 59
          AND {hh_where}
    """), params)
    row = result.fetchone()
    # Baselines: grand total + age-band breakdown (R5+ populates the breakdown columns).
    bl = await db.execute(text("""
        SELECT
          COALESCE(SUM(total_treated), 0)                                          AS total,
          COALESCE(SUM(COALESCE(target_1_11_f, 0) + COALESCE(target_1_11_m, 0)), 0)   AS baseline_1_11,
          COALESCE(SUM(COALESCE(target_12_59_f, 0) + COALESCE(target_12_59_m, 0)), 0) AS baseline_12_59
        FROM mda_baseline
        WHERE project_id = :pid
    """), {"pid": pid})
    bl_row = bl.fetchone()

    treated_1_11   = int(row.treated_1_11 or 0)
    treated_12_59  = int(row.treated_12_59 or 0)
    baseline_1_11  = int(bl_row.baseline_1_11) if bl_row else 0
    baseline_12_59 = int(bl_row.baseline_12_59) if bl_row else 0

    return {
        "total_1_11":    int(row.total_1_11 or 0),
        "treated_1_11":  treated_1_11,
        "total_12_59":   int(row.total_12_59 or 0),
        "treated_12_59": treated_12_59,
        "total_treated": int(row.total_treated or 0),
        "grand_total":   int(row.grand_total or 0),
        "baseline_total": int(bl_row.total) if bl_row else 0,
        # Age-band coverage (populated only if the round's baseline carries per-band targets)
        "baseline_1_11":      baseline_1_11,
        "baseline_12_59":     baseline_12_59,
        "coverage_1_11_pct":  round(100.0 * treated_1_11  / baseline_1_11,  1) if baseline_1_11  > 0 else None,
        "coverage_12_59_pct": round(100.0 * treated_12_59 / baseline_12_59, 1) if baseline_12_59 > 0 else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/qc/heatmap-geojson
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/qc/heatmap-geojson")
async def qc_heatmap_geojson(
    flag: str = "all",
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """GeoJSON of flagged GPS points for heatmap rendering in geo view."""
    where_map = {
        "outside_lga":  "flag_gps_outside_lga = TRUE",
        "outside_ward": "flag_gps_outside_ward = TRUE",
        "duplicate":    "flag_duplicate_gps = TRUE",
        "all": "(flag_gps_outside_lga = TRUE OR flag_gps_outside_ward = TRUE OR flag_duplicate_gps = TRUE)",
    }
    where = where_map.get(flag, where_map["all"])
    result = await db.execute(text(f"""
        SELECT latitude, longitude, lga, hq_user,
               CASE WHEN flag_duplicate_gps      THEN 'duplicate'
                    WHEN flag_gps_outside_lga    THEN 'outside_lga'
                    WHEN flag_gps_outside_ward   THEN 'outside_ward'
                    ELSE 'other' END AS flag_type
        FROM mda_households
        WHERE project_id = :pid
          AND latitude IS NOT NULL AND longitude IS NOT NULL
          AND latitude  BETWEEN 10 AND 16
          AND longitude BETWEEN  3 AND  8
          AND {where}
        LIMIT 15000
    """), {"pid": pid})
    rows = result.fetchall()
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r.longitude), float(r.latitude)]},
            "properties": {"lga": r.lga, "hq_user": r.hq_user, "flag_type": r.flag_type},
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": features}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/teams/movement-geojson
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/teams/movement-geojson")
async def teams_movement_geojson(
    hq_user: Optional[str] = None,
    date: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Timestamped GPS points for team movement visualisation."""
    params: dict = {"pid": pid}
    filters = [
        "project_id = :pid",
        "latitude IS NOT NULL", "longitude IS NOT NULL",
        "latitude BETWEEN 10 AND 16", "longitude BETWEEN 3 AND 8",
        "started_time IS NOT NULL",
    ]
    if hq_user:
        filters.append("hq_user = :hq_user")
        params["hq_user"] = hq_user
    if date:
        filters.append("DATE(started_time AT TIME ZONE 'UTC' + INTERVAL '1 hour') = :date::date")
        params["date"] = date
    where = " AND ".join(filters)
    result = await db.execute(text(f"""
        SELECT latitude, longitude, hq_user, lga, started_time,
               EXTRACT(HOUR FROM started_time AT TIME ZONE 'UTC' + INTERVAL '1 hour') AS local_hour
        FROM mda_households
        WHERE {where}
        ORDER BY hq_user, started_time
        LIMIT 20000
    """), params)
    rows = result.fetchall()
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r.longitude), float(r.latitude)]},
            "properties": {
                "hq_user":      r.hq_user,
                "lga":          r.lga,
                "started_time": r.started_time.isoformat() if r.started_time else None,
                "local_hour":   int(r.local_hour) if r.local_hour is not None else None,
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": features}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/campaign-dates  — dataset date boundaries for filter inputs
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/campaign-dates")
async def campaign_dates(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Returns min/max completed_time dates plus list of all distinct dates."""
    result = await db.execute(text("""
        SELECT
          MIN(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date AS min_date,
          MAX(completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date AS max_date,
          array_agg(DISTINCT (completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date
                    ORDER BY (completed_time AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date) AS dates
        FROM mda_households
        WHERE project_id = :pid AND completed_time IS NOT NULL
    """), {"pid": pid})
    row = result.fetchone()
    if not row or not row[0]:
        return {"min_date": None, "max_date": None, "dates": []}
    return {
        "min_date": str(row[0]),
        "max_date": str(row[1]),
        "dates":    [str(d) for d in (row[2] or [])],
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/geo/completeness
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/geo/completeness")
async def geo_completeness(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns average grid completeness % across all settlements of the selected
    round and count of settlements where completeness >= 60% (considered 'complete').
    Completeness = visited_grids / total_grids * 100 per settlement.

    Scoped to the user's selected project — a round with no analytics yet
    returns zero completeness (not the previous round's numbers).
    """
    result = await db.execute(text("""
        SELECT
          ROUND(100.0 * SUM(visited_grids)::numeric / NULLIF(SUM(total_grids), 0), 1) AS overall_completeness,
          COUNT(*) FILTER (WHERE completeness_pct >= 60)  AS completed_60,
          COUNT(*) FILTER (WHERE completeness_pct >= 70)  AS completed_70,
          COUNT(*) AS total_settlements
        FROM settlement_analytics sa
        WHERE sa.project_id = :pid
    """), {"pid": pid})
    row = result.fetchone()
    if not row:
        return {"overall_completeness": 0.0, "completed_60": 0, "completed_70": 0, "total_settlements": 0}
    return {
        "overall_completeness": float(row[0] or 0),
        "completed_60":         int(row[1] or 0),
        "completed_70":         int(row[2] or 0),
        "total_settlements":    int(row[3] or 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/settlement-status/download
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/settlement-status/download")
async def download_settlement_status(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """Download Excel file for the selected round: LGA, Ward, Settlement,
    Visited (Yes/No), Completeness %."""
    result = await db.execute(text("""
        SELECT
            sa.lga_name,
            sa.ward_name,
            sa.settlement_name,
            CASE WHEN sa.is_visited THEN 'Visited' ELSE 'Not Visited' END AS visit_status,
            ROUND(sa.completeness_pct::numeric, 1) AS completeness_pct,
            sa.visited_grids,
            sa.total_grids,
            sa.point_count
        FROM settlement_analytics sa
        WHERE sa.project_id = :pid
        ORDER BY sa.lga_name, sa.ward_name, sa.settlement_name
    """), {"pid": pid})
    rows = result.fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Settlement Status"
    headers = ["LGA", "Ward", "Settlement", "Status", "Completeness %", "Grids Visited", "Total Grids", "GPS Points"]
    ws.append(headers)

    # Style header row
    from openpyxl.styles import Font, PatternFill, Alignment
    header_fill = PatternFill("solid", fgColor="003D1A")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    green_fill = PatternFill("solid", fgColor="DCFCE7")
    red_fill   = PatternFill("solid", fgColor="FEE2E2")
    for row_data in rows:
        ws.append(list(row_data))
        last_row = ws.max_row
        status_cell = ws.cell(row=last_row, column=4)
        status_cell.fill = green_fill if status_cell.value == "Visited" else red_fill

    # Auto-width columns
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"settlement_status_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/mda/wards
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/wards")
async def get_ward_list(
    lga: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: Optional[User] = Depends(get_current_user_optional),
):
    """
    Return wards derived from GPS spatial intersection with ward boundaries.
    - With ?lga=X  → list of ward names for that LGA
    - Without lga  → dict { lga_name: [ward1, ward2, ...] } for all LGAs
    LGA names are Title Case (normalised to match shapefile).
    """
    if lga:
        result = await db.execute(text("""
            SELECT DISTINCT ward_name
            FROM mda_households
            WHERE project_id = :pid
              AND ward_name IS NOT NULL
              AND lga = :lga
            ORDER BY ward_name
        """), {"pid": pid, "lga": lga})
        return [r[0] for r in result.fetchall()]
    else:
        result = await db.execute(text("""
            SELECT lga, ward_name
            FROM mda_households
            WHERE project_id = :pid AND ward_name IS NOT NULL AND lga IS NOT NULL
            GROUP BY lga, ward_name
            ORDER BY lga, ward_name
        """), {"pid": pid})
        grouped: Dict[str, list] = {}
        for row in result.fetchall():
            lga_key, ward = row[0], row[1]
            if lga_key not in grouped:
                grouped[lga_key] = []
            grouped[lga_key].append(ward)
        return grouped


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/mda/upload-mlos
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload-mlos")
async def upload_mlos(
    file: UploadFile = File(...),
    pid: int = Depends(resolve_pid),
    _super: User = Depends(require_superadmin),
):
    """
    Upload SARMAAN MLOS Excel file (columns: state_name, lga_name, ward_name,
    settlement_name, latitude, longitude, source).
    Each record's lat/lon is spatially joined to the wards boundary table to
    confirm/enrich ward_name from the authoritative polygon geometry.
    Replaces mlos_settlements rows for the active project only.
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "File must be .xlsx")
    raw = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(400, f"Cannot open workbook: {e}")

    ws = wb[wb.sheetnames[0]]
    rows_raw = list(ws.iter_rows(min_row=2, values_only=True))
    wb.close()

    # Detect header row position — first row after header
    records = []
    for row in rows_raw:
        if not row or all(v is None for v in row):
            continue
        state   = _str(row[0])
        lga     = _str(row[1])
        ward_r  = _str(row[2])
        sett    = _str(row[3])
        lat     = _float_safe(row[4])
        lon     = _float_safe(row[5])
        source  = _str(row[6]) if len(row) > 6 else None
        if not lga:
            continue
        geom_wkt = f"SRID=4326;POINT({lon} {lat})" if lat and lon and not (lat == 0 and lon == 0) else None
        records.append((state, lga, ward_r, sett, lat, lon, source, geom_wkt))

    conn = _get_sync_conn()
    try:
        cur = conn.cursor()
        # Find the state's canonical boundary project (lowest-id project for the same state)
        cur.execute("""
            SELECT MIN(p2.id) FROM geo_projects p1
            JOIN geo_projects p2 ON p2.state_name = p1.state_name
            WHERE p1.id = %s
        """, (pid,))
        boundary_pid = (cur.fetchone() or [pid])[0] or pid

        cur.execute("DELETE FROM mlos_settlements WHERE project_id = %s", (pid,))
        if records:
            now_str = datetime.utcnow().isoformat()
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO mlos_settlements
                   (project_id, state_name, lga_name, ward_name_raw, settlement_name,
                    latitude, longitude, source, geom, uploaded_at)
                   VALUES %s""",
                [
                    (pid, s, l, wr, se, la, lo, src, geom, now_str)
                    for s, l, wr, se, la, lo, src, geom in records
                ],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromEWKT(%s),%s)",
                page_size=500,
            )
            # Spatial join: set ward_name from the intersecting ward boundary polygon
            cur.execute("""
                UPDATE mlos_settlements m
                SET ward_name = w.ward_name
                FROM wards w
                WHERE m.project_id = %s
                  AND w.project_id = %s
                  AND m.geom IS NOT NULL
                  AND ST_Within(m.geom, w.geom)
            """, (pid, boundary_pid))
            # For records outside any boundary, fall back to ward_name_raw
            cur.execute("""
                UPDATE mlos_settlements
                SET ward_name = ward_name_raw
                WHERE project_id = %s
                  AND ward_name IS NULL AND ward_name_raw IS NOT NULL
            """, (pid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"DB error: {e}")
    finally:
        conn.close()

    return {"rows_inserted": len(records)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/mda/upload-baseline
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload-baseline")
async def upload_baseline(
    file: UploadFile = File(...),
    pid: int = Depends(resolve_pid),
    _super: User = Depends(require_superadmin),
):
    """Upload a baseline xlsx — replaces baseline rows for the active project only.

    Auto-detects two formats:

    1. **Legacy (R4 settlement pivot)** — first sheet has columns:
       ``state, lga, ward, settlement, total_treated``

    2. **R5 ward-level target** — workbook contains a 'Raw Data' sheet with columns:
       ``lga, wardname, T_1_11_Months_Female, T_1_11_Months_Male,
       T_12_59_Months_Female, T_12_59_Months_Male``
       (with two trailing unnamed totals columns we ignore). The age/sex columns
       are persisted; ``total_treated`` is computed as their sum so the existing
       coverage queries continue to work unchanged.
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "File must be .xlsx")
    raw = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:
        raise HTTPException(400, f"Cannot open workbook: {e}")

    def strip_q(v):
        if v is None: return None
        s = str(v).strip().strip("'").strip()
        return s if s else None

    def to_int(v):
        if v is None: return None
        try: return int(float(str(v)))
        except Exception: return None

    # Detect the R5 'Raw Data' sheet
    raw_sheet = next((s for s in wb.sheetnames if s.strip().lower() == "raw data"), None)
    records: list = []  # tuples of (state, lga, ward, settlement, total, t1_11_f, t1_11_m, t12_59_f, t12_59_m)

    if raw_sheet:
        # ── R5 format: ward-level with age/sex breakdown ───────────────────
        ws = wb[raw_sheet]
        rows = list(ws.iter_rows(values_only=True))
        header = [str(c or "").strip().lower() for c in (rows[0] if rows else [])]

        def col(name: str) -> int | None:
            try: return header.index(name)
            except ValueError: return None

        i_lga, i_ward = col("lga"), col("wardname")
        i_1_11_f  = col("t_1_11_months_female")
        i_1_11_m  = col("t_1_11_months_male")
        i_12_59_f = col("t_12_59_months_female")
        i_12_59_m = col("t_12_59_months_male")

        if i_lga is None or i_ward is None or None in (i_1_11_f, i_1_11_m, i_12_59_f, i_12_59_m):
            raise HTTPException(400, f"'Raw Data' sheet is missing expected columns. Found: {header}")

        for row in rows[1:]:
            lga = strip_q(row[i_lga]) if i_lga < len(row) else None
            ward = strip_q(row[i_ward]) if i_ward < len(row) else None
            if not lga:
                continue
            t_1_11_f  = to_int(row[i_1_11_f])  or 0
            t_1_11_m  = to_int(row[i_1_11_m])  or 0
            t_12_59_f = to_int(row[i_12_59_f]) or 0
            t_12_59_m = to_int(row[i_12_59_m]) or 0
            total = t_1_11_f + t_1_11_m + t_12_59_f + t_12_59_m
            # Normalise LGA/ward names to title case to align with boundary tables
            lga_norm  = lga.title()
            ward_norm = ward.title() if ward else None
            records.append(("Sokoto", lga_norm, ward_norm, None, total,
                            t_1_11_f, t_1_11_m, t_12_59_f, t_12_59_m))
    else:
        # ── Legacy format: settlement pivot ────────────────────────────────
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        for row in rows:
            state = strip_q(row[0]) if len(row) > 0 else None
            lga = strip_q(row[1]) if len(row) > 1 else None
            ward = strip_q(row[2]) if len(row) > 2 else None
            sett = strip_q(row[3]) if len(row) > 3 else None
            total = to_int(row[4]) if len(row) > 4 else None
            if lga and total is not None:
                records.append((state, lga, ward, sett, total, None, None, None, None))

    wb.close()

    conn = _get_sync_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM mda_baseline WHERE project_id = %s", (pid,))
        if records:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO mda_baseline
                   (project_id, state, lga, ward, settlement, total_treated,
                    target_1_11_f, target_1_11_m, target_12_59_f, target_12_59_m)
                   VALUES %s""",
                [(pid, s, l, w, se, t, t1f, t1m, t2f, t2m) for s, l, w, se, t, t1f, t1m, t2f, t2m in records],
                page_size=500,
            )
        conn.commit()
    except Exception as e:
        conn.rollback(); raise HTTPException(500, f"DB error: {e}")
    finally:
        conn.close()

    return {
        "rows_inserted": len(records),
        "format": "r5_ward_target" if raw_sheet else "legacy_settlement_pivot",
    }
