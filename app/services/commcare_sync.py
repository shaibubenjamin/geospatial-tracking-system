"""CommCare HQ → Postgres sync service.

Pulls Household + Individual OData feeds from CommCare for each configured form
and upserts the rows into ``mda_households`` / ``mda_individuals`` scoped to a
specific project (state + round).

Design notes
------------
- **Incremental.** Each (form, record_type) feed has a per-project watermark in
  ``sync_feed_state.last_received_on``. Subsequent syncs use OData
  ``$filter=received_on gt <watermark>`` so we only pull new rows.

- **Per-set commits with tolerant skip.** Each configured form set
  (``form_ids[i]``) is fetched, persisted, and committed as its own
  transaction. A failure inside one set is logged and recorded in
  ``sets_failed`` — the loop moves on to the next set. The successful sets'
  rows and watermarks are already on disk. Subsequent syncs only re-fetch the
  failed sets (because watermarks for successful sets advanced).

- **Sync status is tri-state.** ``ok`` (all sets succeeded), ``partial`` (at
  least one set succeeded and at least one failed), or ``error`` (every set
  failed). ``partial`` is a successful sync from the platform's perspective —
  the data on the dashboard is valid for the sets that came through.

- **Upsert on formid.** Households use ``ON CONFLICT (formid) DO UPDATE`` so a
  record edited in CommCare overwrites the local copy. Individuals are
  replaced en-bloc per household (delete + insert by hh_formid) — handles
  add/remove/edit of children within a form without needing a per-row natural
  key.

- **Spatial QC + settlement_analytics run once at the end** of the sync, over
  all rows for the project (not just the sets that just synced). Runs only if
  at least one set succeeded.

- **State boundaries.** Spatial QC and ward-name population reference the
  state's canonical boundary project (the lowest-id project for the same
  state), not the round being synced. Sokoto R5 households use Sokoto's
  permanent ward polygons.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psycopg2
import psycopg2.extras

from app.config import DATABASE_URL_SYNC
from app.services.crypto import decrypt

logger = logging.getLogger(__name__)


# ── HTTP layer ────────────────────────────────────────────────────────────────


def _feed_url(base_url: str, app_slug: str, form_id: str, record_type: str) -> str:
    """Construct the OData feed URL for a CommCare form's records.

    Households use ``/feed``; the (first) repeat group with individuals uses ``/1/feed``.
    """
    base = base_url.rstrip("/")
    suffix = "1/feed" if record_type == "individual" else "feed"
    return f"{base}/a/{app_slug}/api/odata/forms/v1/{form_id}/{suffix}"


async def _fetch_all_pages(
    url: str,
    auth: Tuple[str, str],
    since: Optional[datetime],
    client: httpx.AsyncClient,
) -> List[Dict[str, Any]]:
    """Walk every page of an OData feed via ``@odata.nextLink``, returning all rows.

    CommCare's OData implementation gates query options (``$top``, ``$filter``,
    etc.) behind a separate feature flag from basic feed access — for our
    account these come back "Feature flag not enabled." So we don't pass them.
    The ``since`` watermark is used for reporting only; deduplication relies on
    the ``ON CONFLICT (formid) DO UPDATE`` upsert downstream.
    """
    rows: List[Dict[str, Any]] = []
    next_url: Optional[str] = url
    page = 0
    while next_url:
        page += 1
        resp = await client.get(next_url, auth=auth, timeout=120.0)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("value", []) or [])
        next_url = payload.get("@odata.nextLink")
        if page > 200:  # safety belt against runaway pagination
            logger.warning("Pagination capped at 200 pages for %s", url)
            break
    return rows


# ── Helpers ───────────────────────────────────────────────────────────────────


def _s(v) -> Optional[str]:
    """Stringify CommCare values; treat '---' (CommCare's empty marker) as null."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "---":
        return None
    return s


def _i(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return None


def _dt(v) -> Optional[datetime]:
    """Parse an ISO8601 timestamp. Returns timezone-aware UTC datetime or None.

    We keep tzinfo throughout the sync because Postgres TIMESTAMPTZ columns
    return aware datetimes on read; mixing aware and naive (e.g. comparing a
    DB-read watermark to a freshly parsed received_on) raises
    "can't compare offset-naive and offset-aware datetimes".
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        # CommCare returns '2026-04-29T12:10:12.280258Z' style
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _date(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "---":
        return None
    return s.split("T")[0].split(" ")[0]


def _gps(s: Optional[str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Parse 'lat lon alt accuracy' (CommCare) → (lat, lon, accuracy)."""
    if not s:
        return None, None, None
    parts = s.split()
    try:
        lat = float(parts[0]) if len(parts) > 0 else None
        lon = float(parts[1]) if len(parts) > 1 else None
        acc = float(parts[3]) if len(parts) > 3 else None
        return lat, lon, acc
    except (ValueError, IndexError):
        return None, None, None


# ── Column mapping ────────────────────────────────────────────────────────────

# CommCare OData → mda_households. Keys here are the EXACT field names the
# CommCare OData feed returns (with the leading "form " path segments). Values
# are dicts we'll bulk-insert.

HOUSEHOLD_COMMCARE_FIELDS = {
    "formid":                       "formid",
    "username":                     "username",
    "form interviewer teamcode":    "teamcode",
    "form data_type":               "data_type",
    "form data_entry_persons":      "data_entry_persons",
    "form phone_number_data":       "phone_number_data",
    "form village_location admin2": "lga",
    "form village_location ward_name admin3_code":     "admin3_code",
    "form village_location settlement_name admin5_code": "admin5_code",
    "form trt_day":                 "trt_day",
    "form consent_trt":             "consent_trt",
    "form reasons_for_refusal":     "reasons_for_refusal",
    "form others_reasons_for_refusal": "others_reasons_for_refusal",
    "form hh_num":                  "hh_num",
    "form hh_seq":                  "hh_seq",
    "form serial_number_hh_id":     "serial_number_hh_id",
    "form Housemarking_code":       "housemarking_code",
    "form gps":                     "gps_raw",
    "hq_user":                      "hq_user",
}


def _map_household(row: Dict[str, Any], set_name: str) -> Dict[str, Any]:
    """Transform one CommCare OData household record into an mda_households row dict."""
    out: Dict[str, Any] = {}

    # Direct field copies
    for src, dst in HOUSEHOLD_COMMCARE_FIELDS.items():
        out[dst] = _s(row.get(src))

    # Numeric fields
    out["number_of_treated"] = _i(row.get("form number_of_treated"))

    # Dates
    out["date_trt"] = _date(row.get("form date_trt"))
    out["check_treatment_date"] = _date(row.get("form check_treatment_date_calc"))

    # Timestamps
    started   = _dt(row.get("started_time"))
    completed = _dt(row.get("completed_time"))
    received  = _dt(row.get("received_on"))
    out["started_time"]   = started
    out["completed_time"] = completed
    out["received_on"]    = received

    # Derived: durations
    out["form_duration_min"] = None
    if started and completed:
        out["form_duration_min"] = round((completed - started).total_seconds() / 60.0, 2)
    out["sync_lag_hours"] = None
    if completed and received:
        out["sync_lag_hours"] = round((received - completed).total_seconds() / 3600.0, 2)

    # Normalise LGA name to Title Case
    if out["lga"]:
        out["lga"] = out["lga"].title()

    # RA dedup key
    name = (out.get("data_entry_persons") or "").lower().strip()
    phone = out.get("phone_number_data") or ""
    out["data_entry_persons_norm"] = name or None
    out["ra_key"] = f"{name}|{phone}" if name and phone else None

    # GPS parsing
    lat, lon, acc = _gps(out["gps_raw"])
    out["latitude"]     = lat
    out["longitude"]    = lon
    out["gps_accuracy"] = acc

    # Flags computed locally (spatial flags come from a separate UPDATE pass)
    out["flag_gps_zero"]        = bool(lat == 0.0 and lon == 0.0) if (lat is not None and lon is not None) else False
    # GPS accuracy threshold set to 20 m (R5 calibration). At 10 m the flag
    # fired on ~8 % of forms even though most were within usable range; at
    # 14 m it still over-flagged. 20 m is the campaign-team's agreed cutoff
    # for "usable for grid assignment" given Sokoto's 50-200 m settlement grids.
    out["flag_gps_poor_accuracy"] = bool(acc is not None and acc > 20)
    out["flag_after_hours"]     = False
    if started:
        local_hour = (started + timedelta(hours=1)).hour
        out["flag_after_hours"] = local_hour < 6 or local_hour >= 19
    fdm = out["form_duration_min"]
    # Fast-form threshold: 5 min (campaign-team agreed cutoff). Forms
    # completed under 5 min are reviewed as potentially rushed.
    out["flag_fast_form"] = bool(fdm is not None and fdm < 5)
    out["flag_slow_form"] = bool(fdm is not None and fdm > 60)
    sl = out["sync_lag_hours"]
    out["flag_sync_lag"]  = bool(sl is not None and sl > 48)
    out["flag_refusal"]   = (out.get("consent_trt") == "0")
    # Duplicate-formid is implicit (we upsert on formid, so duplicates merge)
    out["flag_duplicate"] = False
    # Spatial flags filled by the UPDATE pass after insert
    out["flag_duplicate_gps"]      = False
    out["flag_gps_outside_lga"]    = False
    out["flag_gps_outside_ward"]   = False
    out["flag_gps_outside_state"]  = False

    # ward_name is populated by the post-insert spatial join

    # Geometry WKT (None when GPS is zero/null)
    if lat is not None and lon is not None and not out["flag_gps_zero"]:
        out["geom_wkt"] = f"SRID=4326;POINT({lon} {lat})"
    else:
        out["geom_wkt"] = None

    return out


def _map_individual(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform one CommCare OData individual record into an mda_individuals row dict."""
    return {
        "hh_formid":         _s(row.get("form consent_survey group_indv hh_formid")),
        "mother_name":       _s(row.get("form consent_survey group_indv mother_name")),
        "child_name":        _s(row.get("form consent_survey group_indv child_name")),
        "dob":               _date(row.get("form consent_survey group_indv dob")),
        "dob_checknote":     _s(row.get("form consent_survey group_indv DOB_Checknote")),
        "sex":               _s(row.get("form consent_survey group_indv sex")),
        "height_cm":         _s(row.get("form consent_survey group_indv height_cm")),
        "age_in_months":     _i(row.get("form consent_survey group_indv age_in_months")),
        "treatment_status":  _s(row.get("form consent_survey group_indv treatment_status")),
        "not_treated":       _s(row.get("form consent_survey group_indv not_treated")),
        "vomit_spill_azt":   _s(row.get("form consent_survey group_indv vomitsplit_medecine vomit_spill_")),
        "child_id_r2":       _s(row.get("form consent_survey group_indv child_ID_R2")),
        "respondent_hh_id":  _s(row.get("form consent_survey group_indv respondent_hh_id")),
        "individual_id":     _s(row.get("form consent_survey group_indv individual_id")),
    }


# ── Public entry points ──────────────────────────────────────────────────────


async def test_connection(
    base_url: str,
    app_slug: str,
    username: str,
    password: str,
    form_id: str,
) -> Dict[str, Any]:
    """Hit one feed (the household OData URL of the given form) and report status."""
    url = _feed_url(base_url, app_slug, form_id, "household")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, auth=(username, password), params={"$top": 1})
        if resp.status_code == 200:
            payload = resp.json()
            count = len(payload.get("value", []))
            return {"ok": True, "status": 200, "sample_rows": count}
        # Try to surface CommCare's structured JSON error if present
        detail = resp.text.strip()[:500]
        try:
            err_body = resp.json()
            if isinstance(err_body, dict):
                detail = err_body.get("error") or err_body.get("detail") or err_body.get("message") or detail
        except Exception:
            pass
        return {"ok": False, "status": resp.status_code, "detail": detail, "url": url}
    except Exception as e:
        return {"ok": False, "detail": str(e), "url": url}


def _decrypt_config(row) -> Dict[str, Any]:
    """Build a dict from a sync_config row, decrypting the password."""
    return {
        "project_id": row["project_id"],
        "base_url":   row["commcare_base_url"],
        "app_slug":   row["commcare_app_slug"],
        "username":   row["commcare_username"],
        "password":   decrypt(row["commcare_password_encrypted"]) if row["commcare_password_encrypted"] else None,
        "form_ids":   row["form_ids"] or [],
    }


async def run_sync(project_id: int) -> Dict[str, Any]:
    """Pull every configured form set for ``project_id`` incrementally and upsert.

    Each form set is fetched, persisted and committed as its own transaction.
    If a set fails (e.g. CommCare returns 404 for its OData feed) the failure
    is logged in ``sets_failed`` and the loop moves on to the next set —
    successfully synced sets stay on disk. Spatial QC + settlement_analytics
    run once at the end over the whole project if at least one set succeeded.

    Returns a dict with the tri-state status (``ok``/``partial``/``error``),
    the lists of succeeded and failed sets, and per-feed fetch counts. Only
    raises on hard errors (config missing, DB connection lost, etc.).
    """
    conn = psycopg2.connect(DATABASE_URL_SYNC)
    conn.autocommit = False
    history_id: Optional[int] = None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 1. Load config
        cur.execute("SELECT * FROM sync_config WHERE project_id = %s", (project_id,))
        cfg_row = cur.fetchone()
        if not cfg_row:
            raise RuntimeError(f"No sync_config for project {project_id}. Set credentials first.")
        cfg = _decrypt_config(cfg_row)
        if not (cfg["base_url"] and cfg["app_slug"] and cfg["username"] and cfg["password"]):
            raise RuntimeError("CommCare credentials are incomplete for this project.")
        if not cfg["form_ids"]:
            raise RuntimeError("No form IDs configured for this project.")

        # 2. Identify the state's canonical boundary project for spatial QC
        cur.execute("""
            SELECT MIN(p2.id) FROM geo_projects p1
            JOIN geo_projects p2 ON p2.state_name = p1.state_name
            WHERE p1.id = %s
              AND EXISTS (SELECT 1 FROM lgas l WHERE l.project_id = p2.id)
        """, (project_id,))
        bp_row = cur.fetchone()
        boundary_pid = (bp_row["min"] if bp_row and bp_row.get("min") else project_id)

        # 3. Mark sync as running + open a history row + reset progress.
        # Also clear any stale cancel_requested flag from a previous run so
        # this fresh sync isn't immediately stopped by an old stop signal.
        total_feeds = len([e for e in cfg["form_ids"] if e.get("form_id")]) * 2
        cur.execute(
            """UPDATE sync_config SET
               last_status='running', last_synced_at=NOW(), last_error=NULL,
               last_progress_step=0, last_progress_total=%s,
               cancel_requested=FALSE
               WHERE project_id=%s""",
            (total_feeds, project_id),
        )
        cur.execute(
            "INSERT INTO sync_history (project_id, status, started_at) VALUES (%s, 'running', NOW()) RETURNING id",
            (project_id,),
        )
        history_id = cur.fetchone()["id"]
        conn.commit()

        # 4. Per-set loop: fetch -> persist -> commit. Failures are tolerated; the
        #    loop continues to the next set so a single broken form ID doesn't
        #    wipe out the rest of the sync.
        sets_succeeded: List[str] = []
        sets_failed: List[Dict[str, Any]] = []
        per_feed: List[Dict[str, Any]] = []
        step = 0
        ph_cur = conn.cursor()  # tuple-cursor for bulk insert

        auth = (cfg["username"], cfg["password"])
        cancelled = False
        async with httpx.AsyncClient() as client:
            for entry in cfg["form_ids"]:
                # Cooperative stop. Poll the sync_config flag at the start of
                # every set so a "Stop sync" button click takes effect
                # between sets — preserves the integrity of in-flight DB
                # writes (we never abort mid-transaction).
                try:
                    cur.execute(
                        "SELECT cancel_requested FROM sync_config WHERE project_id=%s",
                        (project_id,),
                    )
                    cancel_row = cur.fetchone()
                    if cancel_row and cancel_row["cancel_requested"]:
                        cancelled = True
                        logger.info("Sync for project %s stopped by user request after %d sets",
                                    project_id, len(sets_succeeded))
                        break
                except Exception:  # noqa: BLE001
                    pass  # never fail the sync because the cancel-check itself errored

                set_name = entry.get("set_name") or "SET ?"
                form_id  = entry.get("form_id")
                if not form_id:
                    continue

                set_households: List[Dict[str, Any]] = []
                set_individuals: List[Dict[str, Any]] = []
                set_feeds: List[Dict[str, Any]] = []

                try:
                    # 4a. Fetch both record types for this set
                    for rt in ("household", "individual"):
                        cur.execute("""
                            SELECT last_received_on FROM sync_feed_state
                            WHERE project_id=%s AND form_id=%s AND record_type=%s
                        """, (project_id, form_id, rt))
                        wm_row = cur.fetchone()
                        since = wm_row["last_received_on"] if wm_row else None

                        url = _feed_url(cfg["base_url"], cfg["app_slug"], form_id, rt)
                        try:
                            rows = await _fetch_all_pages(url, auth, since, client)
                        except httpx.HTTPStatusError as e:
                            # Convert to RuntimeError tagged with set/rt so the outer
                            # set-level except catches it and we record per-set failure.
                            raise RuntimeError(
                                f"{set_name}/{rt} HTTP {e.response.status_code}: {e.response.text[:200]}"
                            ) from e

                        # Client-side incremental filter.
                        #
                        # CommCare's OData implementation for this account
                        # doesn't honour ``$filter=received_on gt …`` (the
                        # query option is gated behind a feature flag we
                        # don't have), so ``_fetch_all_pages`` returns every
                        # row each sync. We post-filter HERE: rows whose
                        # received_on is older than the watermark are
                        # discarded before the upsert phase, so subsequent
                        # syncs only spend DB time on NEW or EDITED rows.
                        # Without this, sync #N runs in roughly the same
                        # wall-time as sync #1 because the DB still rewrites
                        # 200k+ rows even when nothing changed.
                        max_received: Optional[datetime] = since
                        if rt == "household":
                            skipped_older = 0
                            for r in rows:
                                ts = _dt(r.get("received_on"))
                                if since is not None and ts is not None and ts <= since:
                                    skipped_older += 1
                                    continue
                                if ts and (max_received is None or ts > max_received):
                                    max_received = ts
                                set_households.append({"_set": set_name, "_form": form_id, **_map_household(r, set_name)})
                            if skipped_older:
                                logger.info(
                                    "Set %s household: filtered %d rows already at watermark (kept %d new)",
                                    set_name, skipped_older, len(rows) - skipped_older,
                                )
                        else:
                            # Individual rows don't carry received_on of their
                            # own; the parent household's received_on is the
                            # only timestamp. We DON'T pre-filter individuals
                            # because the persist phase already prunes them by
                            # joining on the set of household formids that
                            # ACTUALLY got upserted this run — so if a
                            # household is filtered out above, none of its
                            # children touch the DB either.
                            max_received = max_received or datetime.now(timezone.utc)
                            kept_hh = {h["formid"] for h in set_households}
                            kept_individuals = 0
                            skipped_individuals = 0
                            for r in rows:
                                mapped = _map_individual(r)
                                if not mapped["hh_formid"]:
                                    continue
                                if since is not None and mapped["hh_formid"] not in kept_hh:
                                    # parent household was filtered out
                                    skipped_individuals += 1
                                    continue
                                set_individuals.append({"_set": set_name, "_form": form_id, **mapped})
                                kept_individuals += 1
                            if skipped_individuals:
                                logger.info(
                                    "Set %s individual: filtered %d rows belonging to households already at watermark (kept %d new)",
                                    set_name, skipped_individuals, kept_individuals,
                                )

                        set_feeds.append({
                            "set_name": set_name,
                            "form_id": form_id,
                            "record_type": rt,
                            "fetched": len(rows),
                            "next_watermark": max_received.isoformat() if max_received else None,
                        })

                        # Advance progress counter + commit so the UI sees the bar fill
                        step += 1
                        cur.execute(
                            "UPDATE sync_config SET last_progress_step=%s WHERE project_id=%s",
                            (step, project_id),
                        )
                        conn.commit()

                    # 4b. Persist this set's rows in a single transaction (households,
                    #     individuals, and watermarks all commit together).
                    _persist_set(
                        ph_cur,
                        project_id=project_id,
                        set_households=set_households,
                        set_individuals=set_individuals,
                        set_feeds=set_feeds,
                    )
                    conn.commit()
                    sets_succeeded.append(set_name)
                    per_feed.extend(set_feeds)
                    logger.info(
                        "Set %s persisted: %d households + %d individuals",
                        set_name, len(set_households), len(set_individuals),
                    )

                except Exception as set_err:
                    # Roll back any partial work on this set's transaction; the
                    # previous sets' commits stay on disk. Record the failure and
                    # advance the progress step counter past the slots we skipped
                    # so the UI bar doesn't appear stuck.
                    conn.rollback()
                    logger.warning("Set %s failed, continuing: %s", set_name, set_err)
                    sets_failed.append({
                        "set_name": set_name,
                        "form_id": form_id,
                        "error": str(set_err)[:500],
                    })
                    # Make sure step counter reflects the slots we skipped (2 feeds per set)
                    while step % 2 != 0:
                        step += 1
                    expected_step_after_set = (
                        ([e.get("form_id") for e in cfg["form_ids"]].index(form_id) + 1) * 2
                    )
                    if step < expected_step_after_set:
                        step = expected_step_after_set
                    try:
                        cur.execute(
                            "UPDATE sync_config SET last_progress_step=%s WHERE project_id=%s",
                            (step, project_id),
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    continue

        # 5. Spatial QC + settlement_analytics — only if at least one set succeeded.
        #    Runs across the whole project so the dashboard reflects all rows
        #    (including from successful sets in earlier syncs).
        if sets_succeeded:
            _run_spatial_qc(ph_cur, project_id=project_id, boundary_pid=boundary_pid)
            _recompute_settlement_analytics(ph_cur, project_id=project_id, boundary_pid=boundary_pid)
            conn.commit()

        # 6. Final status: ok / partial / error / cancelled. "cancelled"
        #    wins when the user pressed Stop — even if some sets succeeded,
        #    we report that the run was cut short so the UI can show the
        #    right chip + tooltip.
        total_fetched = sum(f["fetched"] for f in per_feed)
        if cancelled:
            final_status = "cancelled"
            final_error  = f"Stopped by user after {len(sets_succeeded)} set(s) completed."
        elif sets_failed and sets_succeeded:
            final_status = "partial"
            final_error = " | ".join(f"{f['set_name']}: {f['error'][:100]}" for f in sets_failed)
        elif sets_failed and not sets_succeeded:
            final_status = "error"
            final_error = " | ".join(f"{f['set_name']}: {f['error'][:100]}" for f in sets_failed)
        else:
            final_status = "ok"
            final_error = None

        ph_cur.execute("""
            UPDATE sync_config SET
              last_status=%s, last_synced_at=NOW(),
              last_row_count=%s, last_error=%s,
              last_progress_step=last_progress_total
            WHERE project_id=%s
        """, (final_status, total_fetched, final_error, project_id))
        ph_cur.execute("""
            UPDATE sync_history SET
              status=%s, ended_at=NOW(), rows_fetched=%s, error_message=%s
            WHERE id=%s
        """, (final_status, total_fetched, final_error, history_id))
        conn.commit()

        return {
            "ok": final_status in ("ok", "partial"),
            "status": final_status,
            "sets_succeeded": sets_succeeded,
            "sets_failed": sets_failed,
            "households_fetched":  sum(f["fetched"] for f in per_feed if f["record_type"] == "household"),
            "individuals_fetched": sum(f["fetched"] for f in per_feed if f["record_type"] == "individual"),
            "per_feed": per_feed,
        }
    except Exception as e:
        # Only reached for hard failures BEFORE the per-set loop (config load, DB,
        # the boundary-pid query) or for spatial-QC failures. Per-set failures are
        # caught and recorded inside the loop, never re-raised.
        conn.rollback()
        logger.exception("CommCare sync failed for project %s", project_id)
        try:
            cur2 = conn.cursor()
            cur2.execute(
                """UPDATE sync_config SET
                   last_status='error', last_error=%s,
                   last_progress_step=NULL, last_progress_total=NULL
                   WHERE project_id=%s""",
                (str(e)[:1000], project_id),
            )
            if history_id is not None:
                cur2.execute(
                    """UPDATE sync_history SET
                       status='error', ended_at=NOW(), error_message=%s
                       WHERE id=%s""",
                    (str(e)[:1000], history_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    finally:
        conn.close()


# ── Per-set persist + project-wide spatial QC helpers ─────────────────────────


def _persist_set(
    ph_cur,
    *,
    project_id: int,
    set_households: List[Dict[str, Any]],
    set_individuals: List[Dict[str, Any]],
    set_feeds: List[Dict[str, Any]],
) -> None:
    """Persist one form set's rows and advance its watermarks.

    Caller must hold the connection's transaction open; this function does NOT
    commit. The caller commits after all three steps (household upsert,
    individual replace, watermark advance) succeed so they land atomically.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Household upsert for this set
    if set_households:
        cols = [
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
        values = []
        for h in set_households:
            values.append((
                project_id,
                h["formid"], h["username"], h["teamcode"], h["data_type"],
                h["data_entry_persons"], h["data_entry_persons_norm"],
                h["phone_number_data"], h["ra_key"], h["lga"],
                h["admin3_code"], h["admin5_code"], h["trt_day"], h["date_trt"],
                h["consent_trt"], h["reasons_for_refusal"], h["others_reasons_for_refusal"],
                h["hh_num"], h["hh_seq"], h["serial_number_hh_id"], h["number_of_treated"],
                h["housemarking_code"], h["gps_raw"], h["latitude"], h["longitude"],
                h["gps_accuracy"], h["geom_wkt"],
                h["started_time"], h["completed_time"], h["received_on"],
                h["form_duration_min"], h["sync_lag_hours"],
                h["flag_duplicate"], h["flag_duplicate_gps"],
                h["flag_gps_outside_lga"], h["flag_gps_outside_ward"], h["flag_gps_outside_state"],
                h["flag_gps_poor_accuracy"], h["flag_gps_zero"], h["flag_after_hours"],
                h["flag_fast_form"], h["flag_slow_form"], h["flag_sync_lag"], h["flag_refusal"],
                h["check_treatment_date"], h["hq_user"],
                now_iso,
            ))
        col_list = ", ".join(cols)
        ph_list = ", ".join("ST_GeomFromEWKT(%s)" if c == "geom" else "%s" for c in cols)
        update_cols = [c for c in cols if c != "formid"]
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        psycopg2.extras.execute_values(
            ph_cur,
            f"""INSERT INTO mda_households ({col_list}) VALUES %s
                ON CONFLICT (formid) DO UPDATE SET {update_set}""",
            values,
            template=f"({ph_list})",
            page_size=500,
        )

    # 2. Individuals — replace per-household (delete then insert)
    if set_individuals:
        affected_hh_formids = list({i["hh_formid"] for i in set_individuals})
        ph_cur.execute(
            "DELETE FROM mda_individuals WHERE project_id = %s AND hh_formid = ANY(%s)",
            (project_id, affected_hh_formids),
        )
        indv_cols = [
            "project_id", "hh_formid", "mother_name", "child_name", "dob", "dob_checknote",
            "sex", "height_cm", "age_in_months", "treatment_status",
            "not_treated", "vomit_spill_azt", "child_id_r2",
            "respondent_hh_id", "individual_id", "flag_orphan", "uploaded_at",
        ]
        valid_hh = {h["formid"] for h in set_households}
        indv_values = [
            (
                project_id, i["hh_formid"], i["mother_name"], i["child_name"],
                i["dob"], i["dob_checknote"], i["sex"], i["height_cm"],
                i["age_in_months"], i["treatment_status"], i["not_treated"],
                i["vomit_spill_azt"], i["child_id_r2"], i["respondent_hh_id"],
                i["individual_id"],
                bool(i["hh_formid"] and i["hh_formid"] not in valid_hh),
                now_iso,
            )
            for i in set_individuals
        ]
        psycopg2.extras.execute_values(
            ph_cur,
            f"INSERT INTO mda_individuals ({', '.join(indv_cols)}) VALUES %s",
            indv_values,
            page_size=1000,
        )

    # 3. Advance watermarks for this set's feeds
    for feed in set_feeds:
        if feed["next_watermark"]:
            ph_cur.execute("""
                INSERT INTO sync_feed_state (project_id, form_id, record_type,
                                             last_received_on, last_synced_at, last_row_count)
                VALUES (%s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (project_id, form_id, record_type) DO UPDATE
                SET last_received_on = EXCLUDED.last_received_on,
                    last_synced_at   = EXCLUDED.last_synced_at,
                    last_row_count   = EXCLUDED.last_row_count
            """, (project_id, feed["form_id"], feed["record_type"],
                  feed["next_watermark"], feed["fetched"]))


def _run_spatial_qc(ph_cur, *, project_id: int, boundary_pid: int) -> None:
    """Apply spatial QC flags across all rows for this project.

    Run after all per-set persists so flags reflect the full row set on the
    dashboard (e.g. duplicate-GPS needs to see every form to flag duplicates
    that span two form sets).
    """
    ph_cur.execute("""
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
    """, (project_id, boundary_pid))
    ph_cur.execute("""
        UPDATE mda_households h
        SET flag_gps_outside_state = TRUE
        WHERE h.project_id = %s
          AND h.geom IS NOT NULL AND h.flag_gps_zero = FALSE
          AND NOT EXISTS (
              SELECT 1 FROM lgas l
              WHERE l.project_id = %s AND ST_Within(h.geom, l.geom)
          )
    """, (project_id, boundary_pid))
    ph_cur.execute("""
        UPDATE mda_households h
        SET flag_gps_outside_ward = TRUE
        WHERE h.project_id = %s
          AND h.geom IS NOT NULL AND h.flag_gps_zero = FALSE
          AND NOT EXISTS (
              SELECT 1 FROM wards w
              WHERE w.project_id = %s AND ST_Within(h.geom, w.geom)
          )
    """, (project_id, boundary_pid))
    ph_cur.execute("""
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
    """, (project_id, project_id))
    ph_cur.execute("""
        UPDATE mda_households h
        SET ward_name = w.ward_name
        FROM wards w
        WHERE h.project_id = %s AND w.project_id = %s
          AND ST_Within(h.geom, w.geom)
          AND h.geom IS NOT NULL AND h.flag_gps_zero = FALSE
    """, (project_id, boundary_pid))


def _recompute_settlement_analytics(ph_cur, *, project_id: int, boundary_pid: int) -> None:
    """Recompute the settlement_analytics rollup for this project.

    Settlements/grids come from the state's boundary project; household
    EXISTS checks are scoped to the round being synced.
    """
    ph_cur.execute(
        """
        INSERT INTO settlement_analytics
          (project_id, settlement_id, unique_cod, lgacode, wardcode,
           settlement_name, lga_name, ward_name,
           total_grids, visited_grids, completeness_pct,
           is_visited, point_count, last_computed)
        SELECT
            %(mda_pid)s          AS project_id,
            s.id                 AS settlement_id,
            s.unique_cod, s.lgacode, s.wardcode,
            s.settlement_name, s.lga_name, s.ward_name,
            COUNT(DISTINCT g.id) AS total_grids,
            COUNT(DISTINCT CASE
                WHEN EXISTS (
                    SELECT 1 FROM mda_households h
                    WHERE h.project_id = %(mda_pid)s
                      AND h.geom IS NOT NULL
                      AND ST_Within(h.geom, g.geom)
                ) THEN g.id END
            ) AS visited_grids,
            CASE WHEN COUNT(DISTINCT g.id) > 0
                 THEN ROUND(100.0 * COUNT(DISTINCT CASE
                      WHEN EXISTS (
                          SELECT 1 FROM mda_households h
                          WHERE h.project_id = %(mda_pid)s
                            AND h.geom IS NOT NULL
                            AND ST_Within(h.geom, g.geom)
                      ) THEN g.id END
                 ) / NULLIF(COUNT(DISTINCT g.id), 0), 2)
                 ELSE 0 END AS completeness_pct,
            CASE
                WHEN COUNT(DISTINCT g.id) > 0
                THEN (
                    COUNT(DISTINCT CASE WHEN EXISTS (
                        SELECT 1 FROM mda_households h
                        WHERE h.project_id = %(mda_pid)s
                          AND h.geom IS NOT NULL AND ST_Within(h.geom, g.geom)
                    ) THEN g.id END) * 100.0
                    / NULLIF(COUNT(DISTINCT g.id), 0)
                ) >= %(visit_threshold)s
                ELSE EXISTS (
                    SELECT 1 FROM mda_households h2
                    WHERE h2.project_id = %(mda_pid)s
                      AND h2.geom IS NOT NULL AND ST_Within(h2.geom, s.geom)
                )
            END AS is_visited,
            (SELECT COUNT(*) FROM mda_households h3
             WHERE h3.project_id = %(mda_pid)s
               AND h3.geom IS NOT NULL AND ST_Within(h3.geom, s.geom)
            ) AS point_count,
            NOW() AS last_computed
        FROM settlements s
        LEFT JOIN grids g
               ON g.unique_cod  = s.unique_cod
              AND g.project_id  = s.project_id
        WHERE s.project_id = %(boundary_pid)s
        GROUP BY s.id, s.unique_cod, s.lgacode, s.wardcode,
                 s.settlement_name, s.lga_name, s.ward_name
        ON CONFLICT (project_id, settlement_id) DO UPDATE SET
            total_grids      = EXCLUDED.total_grids,
            visited_grids    = EXCLUDED.visited_grids,
            completeness_pct = EXCLUDED.completeness_pct,
            is_visited       = EXCLUDED.is_visited,
            point_count      = EXCLUDED.point_count,
            last_computed    = EXCLUDED.last_computed
        """,
        {"mda_pid": project_id, "boundary_pid": boundary_pid, "visit_threshold": 70},
    )
