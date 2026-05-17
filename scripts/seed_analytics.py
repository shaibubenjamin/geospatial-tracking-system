"""
seed_analytics.py — Seed settlement_analytics from shapefile pre-computed attributes.

The Settlement shapefile already carries Total_Grid, Visit_Grid, Com_percen and
visitation ('V' / 'NV') computed by the source system.  Reading these directly is
far faster than running a full PostGIS spatial join.

GPS point counts are derived from the points_raw.settlement_name attribute column.

Run inside the api container:
    docker compose exec api python scripts/seed_analytics.py
"""
import logging
import psycopg2
from psycopg2.extras import execute_values
import shapefile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = "postgresql://geouser:geopass@db:5432/geospatial_tracker"
DATA_DIR = "/app/sokoto_data"
PROJECT_SLUG = "sokoto"
BATCH = 500


def _flush(cur, rows):
    execute_values(
        cur,
        """
        INSERT INTO settlement_analytics
          (project_id, settlement_id, unique_cod, lgacode, wardcode,
           settlement_name, lga_name, ward_name,
           total_grids, visited_grids, completeness_pct,
           is_visited, point_count, last_computed)
        VALUES %s
        ON CONFLICT (project_id, settlement_id) DO UPDATE SET
            total_grids      = EXCLUDED.total_grids,
            visited_grids    = EXCLUDED.visited_grids,
            completeness_pct = EXCLUDED.completeness_pct,
            is_visited       = EXCLUDED.is_visited,
            point_count      = EXCLUDED.point_count,
            last_computed    = NOW()
        """,
        rows,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
    )


def main():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    cur.execute("SELECT id FROM geo_projects WHERE slug = %s", (PROJECT_SLUG,))
    row = cur.fetchone()
    if not row:
        log.error(f"Project '{PROJECT_SLUG}' not found in DB")
        return
    pid = row[0]
    log.info(f"Project: {PROJECT_SLUG} (id={pid})")

    # GPS point counts per settlement_name (attribute join — instant)
    log.info("Counting GPS points per settlement name from points_raw...")
    cur.execute(
        """
        SELECT settlement_name, COUNT(*) AS cnt
        FROM points_raw
        WHERE project_id = %s
          AND settlement_name IS NOT NULL AND settlement_name <> ''
        GROUP BY settlement_name
        """,
        (pid,),
    )
    point_counts = {r[0]: int(r[1]) for r in cur.fetchall()}
    log.info(f"  {len(point_counts)} unique settlement names have GPS points")

    # Load DB settlement index keyed by unique_cod
    cur.execute(
        """
        SELECT id, unique_cod, lgacode, wardcode,
               settlement_name, lga_name, ward_name
        FROM settlements WHERE project_id = %s
        """,
        (pid,),
    )
    db_setts = {r[1]: r for r in cur.fetchall()}
    log.info(f"  {len(db_setts)} settlements in DB")

    # Stream shapefile records — no geometry needed here
    log.info("Reading Settlement shapefile analytics attributes...")
    sf = shapefile.Reader(f"{DATA_DIR}/Settlement.shp")
    fields = [f[0] for f in sf.fields[1:]]

    rows = []
    total = missing = 0

    for rec in sf.records():
        d = dict(zip(fields, rec))
        ucode = str(d["unique_cod"])
        db_row = db_setts.get(ucode)
        if not db_row:
            missing += 1
            continue

        sid, _, lgacode, wardcode, sett_name, lga_name, ward_name = db_row

        total_grids   = int(d.get("Total_Grid") or 0)
        visited_grids = int(d.get("Visit_Grid") or 0)
        completeness  = float(d.get("Com_percen") or 0.0)
        is_visited    = str(d.get("visitation") or "").strip().upper() == "V"
        point_count   = point_counts.get(sett_name, 0)

        rows.append((
            pid, sid, ucode, lgacode, wardcode,
            sett_name, lga_name, ward_name,
            total_grids, visited_grids, completeness,
            is_visited, point_count,
        ))
        total += 1

        if len(rows) >= BATCH:
            _flush(cur, rows)
            rows = []
            log.info(f"  {total} settlements processed...")

    if rows:
        _flush(cur, rows)

    conn.commit()
    log.info(f"Inserted/updated {total} settlement_analytics rows (skipped {missing} unmatched)")

    # Summary
    cur.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN is_visited THEN 1 ELSE 0 END) AS visited,
            ROUND(100.0 * SUM(CASE WHEN is_visited THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1) AS visitation_pct,
            ROUND(AVG(completeness_pct), 1) AS avg_completeness
        FROM settlement_analytics WHERE project_id = %s
        """,
        (pid,),
    )
    t, v, vp, ac = cur.fetchone()
    log.info(f"Summary: {t} settlements | {v} visited ({vp}%) | avg grid completeness {ac}%")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
