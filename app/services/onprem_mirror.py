"""Reverse-direction mirror: source DB → on-prem Postgres.

Dev-environment-only convenience. When ``ONPREM_BACKUP_DATABASE_URL`` is set,
a superadmin can fire this from the admin panel to push recently-synced MDA
data back to the on-prem ``10.11.52.96`` instance (which is reachable only
over the company VPN).

Design notes
------------
- **Watermark-based incremental mirror.** Per project, we track the maximum
  ``uploaded_at`` of the rows shipped over in the last successful run
  (``onprem_mirror_state.last_mirror_at``). Each new run pulls rows strictly
  newer than that.

- **Per-table natural keys.** Households upsert on ``formid``; baseline upserts
  on ``(project_id, lga, ward, settlement)``. Individuals are replaced en-bloc
  per touched household (delete-then-insert by ``hh_formid``) since they have
  no single natural key — same pattern the CommCare sync uses.

- **All-or-nothing.** Source-side watermark only advances if the target-side
  transaction commits. A failed run leaves both sides untouched.

- **Geometry.** Serialised on the source as EWKB hex and rehydrated on the
  target with ``ST_GeomFromEWKB`` — sidesteps any binary-protocol mismatch
  between psycopg2 and PostGIS without depending on driver type adapters.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from app.config import DATABASE_URL_SYNC, ONPREM_BACKUP_DATABASE_URL

logger = logging.getLogger(__name__)


# Columns whose VALUES come straight from a SELECT row. The geom column is
# handled separately because it needs ST_GeomFromEWKB() wrapping on the way in.

HOUSEHOLD_PLAIN_COLS = [
    "project_id", "formid", "username", "teamcode", "data_type",
    "data_entry_persons", "data_entry_persons_norm", "phone_number_data",
    "ra_key", "lga", "admin3_code", "admin5_code", "trt_day", "date_trt",
    "consent_trt", "reasons_for_refusal", "others_reasons_for_refusal",
    "hh_num", "hh_seq", "serial_number_hh_id", "number_of_treated",
    "housemarking_code", "gps_raw", "latitude", "longitude", "gps_accuracy",
    "started_time", "completed_time", "received_on",
    "form_duration_min", "sync_lag_hours",
    "flag_duplicate", "flag_duplicate_gps", "flag_gps_outside_lga",
    "flag_gps_outside_ward", "flag_gps_outside_state",
    "flag_gps_poor_accuracy", "flag_gps_zero", "flag_after_hours",
    "flag_fast_form", "flag_slow_form", "flag_sync_lag", "flag_refusal",
    "check_treatment_date", "hq_user", "ward_name", "uploaded_at",
]

INDIVIDUAL_PLAIN_COLS = [
    "project_id", "hh_formid", "mother_name", "child_name", "dob",
    "dob_checknote", "sex", "height_cm", "age_in_months",
    "treatment_status", "not_treated", "vomit_spill_azt", "child_id_r2",
    "respondent_hh_id", "individual_id", "flag_orphan", "uploaded_at",
]

BASELINE_PLAIN_COLS = [
    "project_id", "state", "lga", "ward", "settlement", "total_treated",
    "target_1_11_f", "target_1_11_m", "target_12_59_f", "target_12_59_m",
    "uploaded_at",
]


def _check_available() -> None:
    if not ONPREM_BACKUP_DATABASE_URL:
        raise RuntimeError(
            "ONPREM_BACKUP_DATABASE_URL is not set — on-prem mirror is disabled."
        )


# ── Target-side schema bootstrap ─────────────────────────────────────────────
#
# The on-prem database (e.g. mda_pipeline) is not guaranteed to carry the
# dashboard schema. On first mirror run we lazily create exactly the three
# tables we write to. Idempotent: subsequent runs no-op.

_TARGET_DDL = [
    # PostGIS is required for the geom column on mda_households. CREATE
    # EXTENSION needs superuser; we run it in its own short-lived txn so a
    # permission failure on the extension doesn't poison the schema work.
    'CREATE EXTENSION IF NOT EXISTS postgis',

    # mda_households — the geom column needs the PostGIS extension above.
    """
    CREATE TABLE IF NOT EXISTS mda_households (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        formid TEXT UNIQUE NOT NULL,
        username TEXT,
        teamcode TEXT,
        data_type TEXT,
        data_entry_persons TEXT,
        data_entry_persons_norm TEXT,
        phone_number_data TEXT,
        ra_key TEXT,
        lga TEXT,
        admin3_code TEXT,
        admin5_code TEXT,
        trt_day TEXT,
        date_trt DATE,
        consent_trt TEXT,
        reasons_for_refusal TEXT,
        others_reasons_for_refusal TEXT,
        hh_num TEXT,
        hh_seq TEXT,
        serial_number_hh_id TEXT,
        number_of_treated INTEGER,
        housemarking_code TEXT,
        gps_raw TEXT,
        latitude DOUBLE PRECISION,
        longitude DOUBLE PRECISION,
        gps_accuracy DOUBLE PRECISION,
        geom geometry(Point, 4326),
        started_time TIMESTAMPTZ,
        completed_time TIMESTAMPTZ,
        received_on TIMESTAMPTZ,
        form_duration_min DOUBLE PRECISION,
        sync_lag_hours DOUBLE PRECISION,
        flag_duplicate BOOLEAN DEFAULT FALSE,
        flag_duplicate_gps BOOLEAN DEFAULT FALSE,
        flag_gps_outside_lga BOOLEAN DEFAULT FALSE,
        flag_gps_outside_ward BOOLEAN DEFAULT FALSE,
        flag_gps_outside_state BOOLEAN DEFAULT FALSE,
        flag_gps_poor_accuracy BOOLEAN DEFAULT FALSE,
        flag_gps_zero BOOLEAN DEFAULT FALSE,
        flag_after_hours BOOLEAN DEFAULT FALSE,
        flag_fast_form BOOLEAN DEFAULT FALSE,
        flag_slow_form BOOLEAN DEFAULT FALSE,
        flag_sync_lag BOOLEAN DEFAULT FALSE,
        flag_refusal BOOLEAN DEFAULT FALSE,
        check_treatment_date DATE,
        hq_user TEXT,
        ward_name TEXT,
        uploaded_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mirror_hh_project ON mda_households (project_id)",
    "CREATE INDEX IF NOT EXISTS idx_mirror_hh_formid ON mda_households (formid)",

    # mda_individuals — children rows
    """
    CREATE TABLE IF NOT EXISTS mda_individuals (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        hh_formid TEXT,
        mother_name TEXT,
        child_name TEXT,
        dob DATE,
        dob_checknote TEXT,
        sex TEXT,
        height_cm TEXT,
        age_in_months INTEGER,
        treatment_status TEXT,
        not_treated TEXT,
        vomit_spill_azt TEXT,
        child_id_r2 TEXT,
        respondent_hh_id TEXT,
        individual_id TEXT,
        flag_orphan BOOLEAN DEFAULT FALSE,
        uploaded_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mirror_ind_hh ON mda_individuals (project_id, hh_formid)",

    # mda_baseline — per-settlement target population
    """
    CREATE TABLE IF NOT EXISTS mda_baseline (
        id SERIAL PRIMARY KEY,
        project_id INTEGER,
        state TEXT,
        lga TEXT,
        ward TEXT,
        settlement TEXT,
        total_treated INTEGER,
        target_1_11_f INTEGER,
        target_1_11_m INTEGER,
        target_12_59_f INTEGER,
        target_12_59_m INTEGER,
        uploaded_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mirror_bl_project ON mda_baseline (project_id)",
]


def _ensure_target_schema(tgt) -> None:
    """Create the three mirror target tables on the on-prem DB if missing.

    PostGIS extension is created in its own short-lived transaction so that a
    permission failure on CREATE EXTENSION doesn't abort the whole bootstrap.
    Most on-prem Postgres instances already have PostGIS installed; this is
    just an extra safety net for fresh targets.
    """
    # Extension first, in its own connection so the failure path is isolated
    try:
        ext_conn = psycopg2.connect(ONPREM_BACKUP_DATABASE_URL, connect_timeout=20)
        ext_conn.autocommit = True
        with ext_conn.cursor() as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        ext_conn.close()
    except Exception as e:
        logger.info("Skipping CREATE EXTENSION postgis (likely already installed or unprivileged): %s",
                    str(e).splitlines()[0][:120])

    # Tables + indexes — these run inside the caller's main txn
    cur = tgt.cursor()
    for stmt in _TARGET_DDL[1:]:   # skip the extension; we handled it above
        cur.execute(stmt)


# ── Progress reporting (mirrors the CommCare-sync progress UX) ───────────────
#
# Steps are deliberately small so the progress bar moves visibly: bootstrap,
# read each table, write each table, advance watermark. Step labels are
# human-readable and surfaced to the admin panel via get_state_for_ui.

_MIRROR_STEPS: List[str] = [
    "Connecting to on-prem",        # 1
    "Bootstrapping target schema",  # 2
    "Reading households",            # 3
    "Reading individuals",           # 4
    "Reading baseline",              # 5
    "Writing households",            # 6
    "Writing individuals",           # 7
    "Writing baseline",              # 8
    "Advancing watermark",           # 9
]
_MIRROR_TOTAL = len(_MIRROR_STEPS)


def _set_progress(src_cur, project_id: int, step: int, label: Optional[str] = None) -> None:
    """Update the per-project mirror progress counter so the UI can poll it."""
    src_cur.execute(
        """
        UPDATE onprem_mirror_state
        SET last_progress_step  = %s,
            last_progress_total = %s,
            last_progress_label = %s
        WHERE project_id = %s
        """,
        (step, _MIRROR_TOTAL, label, project_id),
    )


def _get_state(src_cur, project_id: int) -> Tuple[Optional[datetime], int]:
    """Return (last_mirror_at, last_row_count) for the project, or (None, 0)."""
    src_cur.execute(
        "SELECT last_mirror_at, COALESCE(last_row_count, 0) "
        "FROM onprem_mirror_state WHERE project_id = %s",
        (project_id,),
    )
    row = src_cur.fetchone()
    if not row:
        return None, 0
    return row[0], row[1]


def _set_state_running(src_cur, project_id: int) -> None:
    src_cur.execute(
        """
        INSERT INTO onprem_mirror_state
              (project_id, last_run_at, last_status, last_error)
        VALUES (%s, NOW(), 'running', NULL)
        ON CONFLICT (project_id) DO UPDATE
        SET last_run_at = EXCLUDED.last_run_at,
            last_status = 'running',
            last_error  = NULL
        """,
        (project_id,),
    )


def _set_state_done(
    src_cur,
    project_id: int,
    *,
    status: str,
    error: Optional[str],
    rows: int,
    new_watermark: Optional[datetime],
) -> None:
    # Terminal status — clear progress fields so the UI hides the bar.
    if new_watermark is not None:
        src_cur.execute(
            """
            UPDATE onprem_mirror_state
            SET last_status         = %s,
                last_error          = %s,
                last_row_count      = %s,
                last_mirror_at      = %s,
                last_progress_step  = NULL,
                last_progress_total = NULL,
                last_progress_label = NULL
            WHERE project_id = %s
            """,
            (status, error, rows, new_watermark, project_id),
        )
    else:
        src_cur.execute(
            """
            UPDATE onprem_mirror_state
            SET last_status         = %s,
                last_error          = %s,
                last_row_count      = %s,
                last_progress_step  = NULL,
                last_progress_total = NULL,
                last_progress_label = NULL
            WHERE project_id = %s
            """,
            (status, error, rows, project_id),
        )


def _select_households_since(
    src_cur, project_id: int, since: Optional[datetime]
) -> List[Tuple]:
    """Read new/updated household rows from the source DB.

    Always returns rows in (col1, col2, ..., geom_ewkb_hex) order so the writer
    can splice geometry into its INSERT without re-aligning columns.
    """
    cols_sql = ", ".join(HOUSEHOLD_PLAIN_COLS) + \
        ", encode(ST_AsEWKB(geom), 'hex') AS geom_hex"
    if since is None:
        src_cur.execute(
            f"SELECT {cols_sql} FROM mda_households "
            f"WHERE project_id = %s ORDER BY uploaded_at",
            (project_id,),
        )
    else:
        src_cur.execute(
            f"SELECT {cols_sql} FROM mda_households "
            f"WHERE project_id = %s AND uploaded_at > %s "
            f"ORDER BY uploaded_at",
            (project_id, since),
        )
    return src_cur.fetchall()


def _select_individuals_for_households(
    src_cur, project_id: int, hh_formids: List[str]
) -> List[Tuple]:
    if not hh_formids:
        return []
    cols_sql = ", ".join(INDIVIDUAL_PLAIN_COLS)
    src_cur.execute(
        f"SELECT {cols_sql} FROM mda_individuals "
        f"WHERE project_id = %s AND hh_formid = ANY(%s)",
        (project_id, hh_formids),
    )
    return src_cur.fetchall()


def _select_baseline_since(
    src_cur, project_id: int, since: Optional[datetime]
) -> List[Tuple]:
    cols_sql = ", ".join(BASELINE_PLAIN_COLS)
    if since is None:
        src_cur.execute(
            f"SELECT {cols_sql} FROM mda_baseline WHERE project_id = %s",
            (project_id,),
        )
    else:
        src_cur.execute(
            f"SELECT {cols_sql} FROM mda_baseline "
            f"WHERE project_id = %s AND uploaded_at > %s",
            (project_id, since),
        )
    return src_cur.fetchall()


def _upsert_households(tgt_cur, rows: List[Tuple]) -> None:
    if not rows:
        return
    plain = HOUSEHOLD_PLAIN_COLS
    placeholders = ", ".join(["%s"] * len(plain))
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in plain if c not in ("project_id", "formid")
    )
    sql = (
        f"INSERT INTO mda_households ({', '.join(plain)}, geom) VALUES "
        f"({placeholders}, "
        f"  CASE WHEN %s IS NULL THEN NULL "
        f"       ELSE ST_GeomFromEWKB(decode(%s, 'hex')) END) "
        f"ON CONFLICT (formid) DO UPDATE SET {update_set}, "
        f"geom = EXCLUDED.geom"
    )
    payload = []
    for row in rows:
        # Last element is geom_hex; everything before it lines up with HOUSEHOLD_PLAIN_COLS.
        *plain_vals, geom_hex = row
        payload.append((*plain_vals, geom_hex, geom_hex))
    psycopg2.extras.execute_batch(tgt_cur, sql, payload, page_size=500)


def _replace_individuals(
    tgt_cur, project_id: int, hh_formids: List[str], rows: List[Tuple]
) -> None:
    """Delete-then-insert all individuals for the touched households."""
    if not hh_formids:
        return
    tgt_cur.execute(
        "DELETE FROM mda_individuals "
        "WHERE project_id = %s AND hh_formid = ANY(%s)",
        (project_id, hh_formids),
    )
    if not rows:
        return
    plain = INDIVIDUAL_PLAIN_COLS
    placeholders = ", ".join(["%s"] * len(plain))
    sql = f"INSERT INTO mda_individuals ({', '.join(plain)}) VALUES ({placeholders})"
    psycopg2.extras.execute_batch(tgt_cur, sql, rows, page_size=1000)


def _upsert_baseline(tgt_cur, rows: List[Tuple]) -> None:
    """Baseline gets re-inserted by natural key. Project_id+lga+ward+settlement
    is treated as the natural key — same row in the same project is replaced."""
    if not rows:
        return
    plain = BASELINE_PLAIN_COLS
    placeholders = ", ".join(["%s"] * len(plain))
    # The on-prem mda_baseline may not have a unique constraint matching this
    # tuple, so use a manual delete-then-insert scoped to the natural key.
    for row in rows:
        # row order matches BASELINE_PLAIN_COLS
        project_id, _state, lga, ward, settlement, *_rest = row
        tgt_cur.execute(
            "DELETE FROM mda_baseline "
            "WHERE project_id = %s "
            "  AND COALESCE(lga, '') = COALESCE(%s, '') "
            "  AND COALESCE(ward, '') = COALESCE(%s, '') "
            "  AND COALESCE(settlement, '') = COALESCE(%s, '')",
            (project_id, lga, ward, settlement),
        )
    sql = f"INSERT INTO mda_baseline ({', '.join(plain)}) VALUES ({placeholders})"
    psycopg2.extras.execute_batch(tgt_cur, sql, rows, page_size=500)


def run_mirror(project_id: int) -> Dict[str, Any]:
    """Mirror this project's MDA data from the source DB to on-prem.

    Returns a summary dict the route can hand back to the UI. Raises if
    the mirror target is not configured.
    """
    _check_available()

    summary: Dict[str, Any] = {
        "project_id": project_id,
        "households": 0,
        "individuals": 0,
        "baseline": 0,
        "watermark_before": None,
        "watermark_after": None,
    }

    src = psycopg2.connect(DATABASE_URL_SYNC)
    src.autocommit = False
    tgt = None  # opened later after the running-state row is committed
    try:
        src_cur = src.cursor()

        # Reserve the slot first (and commit so the UI can see 'running')
        _set_state_running(src_cur, project_id)
        _set_progress(src_cur, project_id, 1, _MIRROR_STEPS[0])  # Connecting…
        src.commit()

        # Step 1: open the target connection. Done after the running-state
        # row exists so a connect failure is recorded as an error, not a hang.
        tgt = psycopg2.connect(ONPREM_BACKUP_DATABASE_URL, connect_timeout=20)
        tgt.autocommit = False
        tgt_cur = tgt.cursor()

        # Step 2: bootstrap the target schema (no-op once tables exist).
        _set_progress(src_cur, project_id, 2, _MIRROR_STEPS[1]); src.commit()
        _ensure_target_schema(tgt)

        since, _prev_rows = _get_state(src_cur, project_id)
        summary["watermark_before"] = since.isoformat() if since else None

        # Steps 3-5: pull source rows
        _set_progress(src_cur, project_id, 3, _MIRROR_STEPS[2]); src.commit()
        households = _select_households_since(src_cur, project_id, since)
        hh_formids = [r[HOUSEHOLD_PLAIN_COLS.index("formid")] for r in households]

        _set_progress(src_cur, project_id, 4, _MIRROR_STEPS[3]); src.commit()
        individuals = _select_individuals_for_households(src_cur, project_id, hh_formids)

        _set_progress(src_cur, project_id, 5, _MIRROR_STEPS[4]); src.commit()
        baseline = _select_baseline_since(src_cur, project_id, since)

        # Steps 6-8: write to target
        _set_progress(src_cur, project_id, 6,
                      f"{_MIRROR_STEPS[5]} ({len(households)} rows)"); src.commit()
        _upsert_households(tgt_cur, households)

        _set_progress(src_cur, project_id, 7,
                      f"{_MIRROR_STEPS[6]} ({len(individuals)} rows)"); src.commit()
        _replace_individuals(tgt_cur, project_id, hh_formids, individuals)

        _set_progress(src_cur, project_id, 8,
                      f"{_MIRROR_STEPS[7]} ({len(baseline)} rows)"); src.commit()
        _upsert_baseline(tgt_cur, baseline)

        tgt.commit()

        # Step 9: advance watermark + mark ok
        _set_progress(src_cur, project_id, 9, _MIRROR_STEPS[8]); src.commit()

        # New watermark = max(uploaded_at) of what we just sent.
        new_watermark = since
        for row_set in (households, baseline):
            if not row_set:
                continue
            idx = (
                HOUSEHOLD_PLAIN_COLS.index("uploaded_at")
                if row_set is households else
                BASELINE_PLAIN_COLS.index("uploaded_at")
            )
            for r in row_set:
                ts = r[idx]
                if ts is not None and (new_watermark is None or ts > new_watermark):
                    new_watermark = ts

        rows_total = len(households) + len(individuals) + len(baseline)
        _set_state_done(
            src_cur, project_id,
            status="ok",
            error=None,
            rows=rows_total,
            new_watermark=new_watermark,
        )
        src.commit()

        summary["households"] = len(households)
        summary["individuals"] = len(individuals)
        summary["baseline"] = len(baseline)
        summary["watermark_after"] = (
            new_watermark.isoformat() if new_watermark else None
        )
        return summary

    except Exception as e:
        logger.exception("On-prem mirror failed for project %s", project_id)
        try:
            if tgt is not None:
                tgt.rollback()
        except Exception:
            pass
        try:
            # New connection-state cursor in case the previous one was poisoned
            src.rollback()
            err_cur = src.cursor()
            _set_state_done(
                err_cur, project_id,
                status="error",
                error=str(e)[:500],
                rows=0,
                new_watermark=None,
            )
            src.commit()
        except Exception:
            logger.exception("Also failed to record mirror error state")
        raise
    finally:
        try:
            src.close()
        except Exception:
            pass
        try:
            if tgt is not None:
                tgt.close()
        except Exception:
            pass


def is_available() -> bool:
    return bool(ONPREM_BACKUP_DATABASE_URL)


def get_state_for_ui(project_id: int) -> Dict[str, Any]:
    """Read the mirror state row for the admin panel."""
    if not ONPREM_BACKUP_DATABASE_URL:
        return {
            "available": False,
            "last_mirror_at": None,
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "last_row_count": 0,
        }
    src = psycopg2.connect(DATABASE_URL_SYNC)
    try:
        cur = src.cursor()
        cur.execute(
            "SELECT last_mirror_at, last_run_at, last_status, last_error, "
            "       COALESCE(last_row_count, 0), "
            "       last_progress_step, last_progress_total, last_progress_label "
            "FROM onprem_mirror_state WHERE project_id = %s",
            (project_id,),
        )
        row = cur.fetchone()
        if not row:
            return {
                "available": True,
                "last_mirror_at": None,
                "last_run_at": None,
                "last_status": None,
                "last_error": None,
                "last_row_count": 0,
                "last_progress_step": None,
                "last_progress_total": None,
                "last_progress_label": None,
            }
        return {
            "available": True,
            "last_mirror_at":      row[0].isoformat() if row[0] else None,
            "last_run_at":         row[1].isoformat() if row[1] else None,
            "last_status":         row[2],
            "last_error":          row[3],
            "last_row_count":      row[4],
            "last_progress_step":  row[5],
            "last_progress_total": row[6],
            "last_progress_label": row[7],
        }
    finally:
        src.close()
