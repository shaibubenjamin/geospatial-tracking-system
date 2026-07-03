"""One-shot bootstrap for the Kano Round 3 project.

Idempotent — safe to re-run. Creates the geo_projects row, loads the
Kano_R2 Target baseline (LGA / Ward / Total Treated), and registers the
sync_config with the seven CommCare form-set IDs. Reuses the existing
CommCare-encrypted password from the Kano Pilot (project_id=3) row so the
Fernet blob decrypts identically at sync time.

Boundaries are intentionally left empty — Kano R3 shares Kano Pilot's
via the same state-match pattern Sokoto R5 uses with Sokoto R4.

Usage inside the API container:
    docker exec geo_tracker_api python /app/scripts/create_kano_r3.py
"""

import asyncio
import json
import sys

import openpyxl
from sqlalchemy import text

from app.database import AsyncSessionLocal


TARGET_XLSX = "/tmp/Kano_R3_Target.xlsx"

FORM_IDS = [
    ("SET 1", "6a236491a2fc5f592aeecbd3ff2be6a6"),
    ("SET 2", "6a236491a2fc5f592aeecbd3ff2c090b"),
    ("SET 3", "af1399d9ec2e4d5a20dd8af3b41b611f"),
    ("SET 4", "af1399d9ec2e4d5a20dd8af3b41b6de0"),
    ("SET 5", "6a236491a2fc5f592aeecbd3ff2cab47"),
    ("SET 6", "6a236491a2fc5f592aeecbd3ff2cbdd7"),
    ("SET 7", "6a236491a2fc5f592aeecbd3ff2f891d"),
]


def parse_target_workbook(path: str):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]

    def col(name):
        for i, h in enumerate(header):
            if name in h:
                return i
        return -1

    i_lga = col("lga")
    i_ward = col("ward")
    i_total = col("total")
    assert i_lga >= 0 and i_ward >= 0 and i_total >= 0, f"unexpected header: {header}"

    out = []
    for r in rows[1:]:
        if r is None:
            continue
        lga = r[i_lga]
        ward = r[i_ward]
        tot = r[i_total]
        if lga is None or ward is None or tot is None:
            continue
        try:
            tot = int(float(tot))
        except (TypeError, ValueError):
            continue
        # Normalise to Title Case to align with how boundaries and household
        # rows are stored (mda_households.lga is normalised the same way).
        out.append((str(lga).strip().title(), str(ward).strip().title(), tot))
    return out


async def main():
    baseline_rows = parse_target_workbook(TARGET_XLSX)
    print(f"Parsed {len(baseline_rows)} baseline rows", flush=True)
    print(f"  sample: {baseline_rows[:3]}", flush=True)

    async with AsyncSessionLocal() as db:
        # ── 1) Insert / upsert the project row ────────────────────────────────
        r = await db.execute(
            text(
                """
                INSERT INTO geo_projects (
                    name, slug, description, is_active, is_public,
                    state_name, round_number, created_at
                ) VALUES (
                    'Kano Round 3', 'kano-r3', 'Kano Round 3 MDA campaign',
                    FALSE, FALSE, 'Kano', 3, NOW()
                )
                ON CONFLICT (slug) DO UPDATE
                  SET name         = EXCLUDED.name,
                      state_name   = EXCLUDED.state_name,
                      round_number = EXCLUDED.round_number
                RETURNING id
                """
            )
        )
        pid = r.fetchone()[0]
        await db.commit()
        print(f"[1/3] geo_projects — Kano R3 project_id = {pid}", flush=True)

        # ── 2) Replace baseline rows for this project ─────────────────────────
        await db.execute(
            text("DELETE FROM mda_baseline WHERE project_id = :pid"),
            {"pid": pid},
        )
        for lga, ward, tot in baseline_rows:
            await db.execute(
                text(
                    """
                    INSERT INTO mda_baseline
                        (project_id, state, lga, ward, settlement, total_treated, uploaded_at)
                    VALUES (:pid, 'Kano', :lga, :ward, NULL, :total, NOW())
                    """
                ),
                {"pid": pid, "lga": lga, "ward": ward, "total": tot},
            )
        await db.commit()

        r = await db.execute(
            text(
                """
                SELECT COUNT(*), COUNT(DISTINCT lga), COUNT(DISTINCT ward), SUM(total_treated)
                FROM mda_baseline WHERE project_id = :pid
                """
            ),
            {"pid": pid},
        )
        n, nl, nw, tot = r.fetchone()
        print(
            f"[2/3] mda_baseline — {n} rows · {nl} LGAs · {nw} wards · {int(tot or 0):,} total_treated",
            flush=True,
        )

        # ── 3) sync_config with the seven form sets ───────────────────────────
        # Copy Kano Pilot's (id=3) encrypted CommCare password — same
        # SYNC_ENCRYPTION_KEY across projects, so the blob decrypts identically.
        r = await db.execute(
            text(
                """
                SELECT commcare_base_url, commcare_app_slug,
                       commcare_username, commcare_password_encrypted
                FROM sync_config WHERE project_id = 3
                """
            )
        )
        src = r.fetchone()
        if src is None:
            print("[3/3] SKIPPED — Kano Pilot sync_config (id=3) not found; cannot copy credentials.", flush=True)
            return

        form_ids_json = json.dumps(
            [{"set_name": name, "form_id": fid} for (name, fid) in FORM_IDS]
        )
        await db.execute(
            text(
                """
                INSERT INTO sync_config (
                    project_id, commcare_base_url, commcare_app_slug,
                    commcare_username, commcare_password_encrypted,
                    form_ids, auto_sync_enabled, auto_sync_interval_minutes,
                    updated_at
                ) VALUES (
                    :pid, :base, :slug, :user, :pw, CAST(:form_ids AS JSONB),
                    FALSE, 60, NOW()
                )
                ON CONFLICT (project_id) DO UPDATE
                  SET commcare_base_url            = EXCLUDED.commcare_base_url,
                      commcare_app_slug            = EXCLUDED.commcare_app_slug,
                      commcare_username            = EXCLUDED.commcare_username,
                      commcare_password_encrypted  = EXCLUDED.commcare_password_encrypted,
                      form_ids                     = EXCLUDED.form_ids,
                      updated_at                   = NOW()
                """
            ),
            {
                "pid": pid,
                "base": src[0],
                "slug": src[1],
                "user": src[2],
                "pw": src[3],
                "form_ids": form_ids_json,
            },
        )
        await db.commit()

        r = await db.execute(
            text(
                """
                SELECT commcare_username, jsonb_array_length(form_ids),
                       auto_sync_enabled, last_status
                FROM sync_config WHERE project_id = :pid
                """
            ),
            {"pid": pid},
        )
        row = r.fetchone()
        print(
            f"[3/3] sync_config — user={row[0]} · {row[1]} form sets · auto_sync={row[2]} · last_status={row[3]}",
            flush=True,
        )
        print(f"\nDone. Kano R3 is live at project_id = {pid}. Boundaries share via state-match with Kano Pilot.", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
