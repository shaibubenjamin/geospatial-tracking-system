"""
scripts/reload_boundaries_and_compute.py
=========================================
Loads Sokoto MDA boundary shapefiles directly from local paths into PostGIS,
then runs the full spatial analytics pipeline using mda_households GPS points.

Usage:
    python scripts/reload_boundaries_and_compute.py

Steps:
    1. Clear existing boundary data for the Sokoto project (project_id=1)
    2. Load LGA / Ward / Settlement / Grid shapefiles (EPSG:3857 → EPSG:4326)
    3. Ensure mda_households.geom is populated from lat/lon
    4. Compute settlement analytics:
       - Visited grids = grids that contain ≥1 mda_household GPS point (ST_Within)
       - Completeness % = visited_grids / total_grids × 100
       - is_visited = completeness_pct >= 70 (but map still shows all grid statuses)
    5. Aggregate: Settlement → Ward → LGA for the dashboard queries
"""

import os, sys, time
import shapefile as pyshp
from pyproj import Transformer
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import transform as shapely_transform
import functools, json
import psycopg2
import psycopg2.extras

# ── DB connection ────────────────────────────────────────────────────────────
DB_URL = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql://geotracker:geotracker@localhost:5433/geotracker",
)
PROJECT_ID = 1          # Sokoto project
VISIT_THRESHOLD = 70    # % grid completeness → settlement is "visited"

# ── Shapefile paths ──────────────────────────────────────────────────────────
BASE = r"C:\Users\Benjamin.shaibu\Downloads\SOKOTO MDA RESOURCE\SOKOTO MDA RESOURCE"
SHP = {
    "lga":        os.path.join(BASE, "lga.shp"),
    "ward":       os.path.join(BASE, "ward.shp"),
    "settlement": os.path.join(BASE, "Settlement.shp"),
    "grid":       os.path.join(BASE, "Gridded_Ta.shp"),
}

# ── Reproject EPSG:3857 → EPSG:4326 ─────────────────────────────────────────
_tfm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

def _reproject(geom_dict: dict) -> dict:
    geom = shape(geom_dict)
    proj = functools.partial(_tfm.transform)
    reprojected = shapely_transform(proj, geom)
    if isinstance(reprojected, Polygon):
        reprojected = MultiPolygon([reprojected])
    elif not isinstance(reprojected, MultiPolygon):
        try:
            reprojected = MultiPolygon(list(reprojected.geoms))
        except Exception:
            reprojected = MultiPolygon([reprojected])
    return mapping(reprojected)


def geom_to_wkt(geom_dict: dict) -> str:
    geom = shape(geom_dict)
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    return geom.wkt


def read_shp(path: str):
    r = pyshp.Reader(path)
    fields = [f[0] for f in r.fields[1:]]
    return fields, r.records(), r.shapes()


def s(val, default="") -> str:
    if val is None:
        return default
    return str(val).strip() or default


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Connecting to {DB_URL} …")
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()

    # ── 1. Clear existing boundary data ─────────────────────────────────────
    print("\n[1/5] Clearing existing boundaries for project", PROJECT_ID)
    for tbl in ("settlement_analytics", "grids", "settlements", "wards", "lgas"):
        cur.execute(f"DELETE FROM {tbl} WHERE project_id = %s", (PROJECT_ID,))
        print(f"       Cleared {tbl}: {cur.rowcount} rows")
    conn.commit()

    # ── 2. Load LGA ──────────────────────────────────────────────────────────
    print("\n[2a/5] Loading LGA boundaries …")
    fields, records, shapes = read_shp(SHP["lga"])
    inserted = skipped = 0
    for rec, shp in zip(records, shapes):
        row = dict(zip(fields, rec))
        lgacode  = s(row.get("lgacode_") or row.get("lgacode"))
        lga_name = s(row.get("lganame")  or row.get("lga_name") or row.get("LGA_Name"), "Unknown LGA")
        if not lgacode:
            skipped += 1; continue
        try:
            wkt = geom_to_wkt(_reproject(shp.__geo_interface__))
            cur.execute("""
                INSERT INTO lgas (project_id, lgacode, lga_name, geom)
                VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (project_id, lgacode) DO UPDATE
                  SET lga_name = EXCLUDED.lga_name, geom = EXCLUDED.geom
            """, (PROJECT_ID, lgacode, lga_name, wkt))
            inserted += 1
        except Exception as e:
            print(f"  LGA error ({lgacode}): {e}"); skipped += 1; conn.rollback()
    conn.commit()
    print(f"  LGA: {inserted} inserted, {skipped} skipped")

    # ── 2b. Load Ward ────────────────────────────────────────────────────────
    print("\n[2b/5] Loading Ward boundaries …")
    fields, records, shapes = read_shp(SHP["ward"])
    inserted = skipped = 0
    for rec, shp in zip(records, shapes):
        row      = dict(zip(fields, rec))
        lgacode  = s(row.get("lgacode")  or row.get("lgacode_"))
        wardcode = s(row.get("wardcode") or row.get("ward_codee"))
        ward_name= s(row.get("wardname") or row.get("ward_name") or row.get("Ward_Name"), "Unknown Ward")
        lga_name = s(row.get("lganame")  or row.get("lga_name")  or row.get("LGA_Name"))
        if not wardcode or not lgacode:
            skipped += 1; continue
        try:
            wkt = geom_to_wkt(_reproject(shp.__geo_interface__))
            cur.execute("""
                INSERT INTO wards (project_id, wardcode, lgacode, ward_name, lga_name, geom)
                VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (project_id, wardcode) DO UPDATE
                  SET lgacode = EXCLUDED.lgacode, ward_name = EXCLUDED.ward_name,
                      lga_name = EXCLUDED.lga_name, geom = EXCLUDED.geom
            """, (PROJECT_ID, wardcode, lgacode, ward_name, lga_name or None, wkt))
            inserted += 1
        except Exception as e:
            print(f"  Ward error ({wardcode}): {e}"); skipped += 1; conn.rollback()
    conn.commit()
    print(f"  Ward: {inserted} inserted, {skipped} skipped")

    # ── 2c. Load Settlement ──────────────────────────────────────────────────
    print("\n[2c/5] Loading Settlement boundaries …")
    fields, records, shapes = read_shp(SHP["settlement"])
    inserted = skipped = 0
    batch = []
    BATCH = 500
    for i, (rec, shp) in enumerate(zip(records, shapes)):
        row          = dict(zip(fields, rec))
        unique_cod   = s(row.get("unique_cod"))
        lgacode      = s(row.get("lgacode"))
        wardcode     = s(row.get("wardcode"))
        settlement_name = s(row.get("settlement") or row.get("settleme_1") or row.get("settlement_name"))
        lga_name     = s(row.get("lga_name")  or row.get("lganame"))
        ward_name    = s(row.get("ward_name") or row.get("wardname"))
        if not unique_cod or not lgacode or not wardcode:
            skipped += 1; continue
        try:
            wkt = geom_to_wkt(_reproject(shp.__geo_interface__))
            batch.append((PROJECT_ID, unique_cod, lgacode, wardcode,
                          settlement_name or None, lga_name or None, ward_name or None, wkt))
        except Exception as e:
            skipped += 1
        if len(batch) >= BATCH:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO settlements (project_id, unique_cod, lgacode, wardcode,
                                        settlement_name, lga_name, ward_name, geom)
                VALUES %s
                ON CONFLICT (project_id, unique_cod) DO UPDATE
                  SET lgacode=EXCLUDED.lgacode, wardcode=EXCLUDED.wardcode,
                      settlement_name=EXCLUDED.settlement_name,
                      lga_name=EXCLUDED.lga_name, ward_name=EXCLUDED.ward_name,
                      geom=EXCLUDED.geom
            """, batch,
                template="(%s,%s,%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))")
            inserted += len(batch); conn.commit(); batch.clear()
            if i % 1000 == 0:
                print(f"  Settlement progress: {i+1}/{len(records)} …")
    if batch:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO settlements (project_id, unique_cod, lgacode, wardcode,
                                    settlement_name, lga_name, ward_name, geom)
            VALUES %s
            ON CONFLICT (project_id, unique_cod) DO UPDATE
              SET lgacode=EXCLUDED.lgacode, wardcode=EXCLUDED.wardcode,
                  settlement_name=EXCLUDED.settlement_name,
                  lga_name=EXCLUDED.lga_name, ward_name=EXCLUDED.ward_name,
                  geom=EXCLUDED.geom
        """, batch,
            template="(%s,%s,%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))")
        inserted += len(batch); conn.commit()
    print(f"  Settlement: {inserted} inserted, {skipped} skipped")

    # ── 2d. Load Grid ────────────────────────────────────────────────────────
    print("\n[2d/5] Loading Grid cells …")
    fields, records, shapes = read_shp(SHP["grid"])
    inserted = skipped = 0
    batch = []
    for i, (rec, shp) in enumerate(zip(records, shapes)):
        row        = dict(zip(fields, rec))
        unique_cod = s(row.get("unique_cod"))
        lgacode    = s(row.get("lgacode"))
        wardcode   = s(row.get("wardcode"))
        sett_name  = s(row.get("settlement") or row.get("settleme_1"))
        if not unique_cod:
            skipped += 1; continue
        try:
            geom_dict = shp.__geo_interface__
            g = shape(geom_dict)
            proj = functools.partial(_tfm.transform)
            g4326 = shapely_transform(proj, g)
            wkt = g4326.wkt
            batch.append((PROJECT_ID, unique_cod, lgacode, wardcode, sett_name or None, wkt))
        except Exception as e:
            skipped += 1
        if len(batch) >= BATCH:
            for row_vals in batch:
                try:
                    cur.execute("""
                        INSERT INTO grids (project_id, unique_cod, lgacode, wardcode, settlement_name, geom)
                        VALUES (%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))
                        ON CONFLICT DO NOTHING
                    """, row_vals)
                except Exception:
                    skipped += 1
            inserted += len(batch); conn.commit(); batch.clear()
            if i % 5000 == 0:
                print(f"  Grid progress: {i+1}/{len(records)} …")
    if batch:
        for row_vals in batch:
            try:
                cur.execute("""
                    INSERT INTO grids (project_id, unique_cod, lgacode, wardcode, settlement_name, geom)
                    VALUES (%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))
                    ON CONFLICT DO NOTHING
                """, row_vals)
            except Exception:
                skipped += 1
        inserted += len(batch); conn.commit()
    print(f"  Grid: {inserted} inserted, {skipped} skipped")

    # ── 3. Ensure mda_households.geom is populated from lat/lon ─────────────
    print("\n[3/5] Populating mda_households.geom from latitude/longitude …")
    cur.execute("""
        UPDATE mda_households
        SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND latitude  BETWEEN -90  AND 90
          AND longitude BETWEEN -180 AND 180
          AND (geom IS NULL
               OR NOT ST_Equals(
                    geom,
                    ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                  ))
    """)
    print(f"  Updated {cur.rowcount} household geometry rows")
    conn.commit()

    # Also ensure the geom column has a GiST index
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mda_hh_geom
        ON mda_households USING GIST(geom)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_grids_geom
        ON grids USING GIST(geom)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_settlements_geom
        ON settlements USING GIST(geom)
    """)
    conn.commit()
    print("  GiST indexes ensured")

    # ── 4. Compute settlement analytics ─────────────────────────────────────
    print(f"\n[4/5] Computing settlement analytics (visit threshold = {VISIT_THRESHOLD}%) …")
    print("  This may take several minutes for 9k+ settlements …")
    t0 = time.time()

    # Process in batches of unique_cods to avoid OOM / lock timeouts
    cur.execute("SELECT DISTINCT unique_cod FROM settlements WHERE project_id = %s", (PROJECT_ID,))
    all_codes = [r[0] for r in cur.fetchall()]
    total = len(all_codes)
    print(f"  Total settlements to process: {total}")

    CHUNK = 200
    processed = 0
    for start in range(0, total, CHUNK):
        chunk = all_codes[start:start + CHUNK]
        sql = f"""
            INSERT INTO settlement_analytics
              (project_id, settlement_id, unique_cod, lgacode, wardcode,
               settlement_name, lga_name, ward_name,
               total_grids, visited_grids, completeness_pct,
               is_visited, point_count, last_computed)
            SELECT
                s.project_id,
                s.id                 AS settlement_id,
                s.unique_cod,
                s.lgacode,
                s.wardcode,
                s.settlement_name,
                s.lga_name,
                s.ward_name,
                COUNT(DISTINCT g.id) AS total_grids,

                COUNT(DISTINCT CASE
                    WHEN EXISTS (
                        SELECT 1 FROM mda_households mh
                        WHERE mh.geom IS NOT NULL
                          AND ST_Within(mh.geom, g.geom)
                    ) THEN g.id END
                ) AS visited_grids,

                CASE WHEN COUNT(DISTINCT g.id) > 0
                     THEN ROUND(
                         100.0
                         * COUNT(DISTINCT CASE WHEN EXISTS (
                               SELECT 1 FROM mda_households mh
                               WHERE mh.geom IS NOT NULL AND ST_Within(mh.geom, g.geom)
                           ) THEN g.id END)
                         / NULLIF(COUNT(DISTINCT g.id), 0),
                         2)
                     ELSE 0 END AS completeness_pct,

                CASE
                    WHEN COUNT(DISTINCT g.id) > 0
                    THEN (
                        COUNT(DISTINCT CASE WHEN EXISTS (
                            SELECT 1 FROM mda_households mh
                            WHERE mh.geom IS NOT NULL AND ST_Within(mh.geom, g.geom)
                        ) THEN g.id END) * 100.0
                        / NULLIF(COUNT(DISTINCT g.id), 0)
                    ) >= {VISIT_THRESHOLD}
                    ELSE (SELECT COUNT(*) > 0 FROM mda_households mh2
                          WHERE mh2.geom IS NOT NULL AND ST_Within(mh2.geom, s.geom))
                END AS is_visited,

                (SELECT COUNT(*) FROM mda_households mh3
                 WHERE mh3.geom IS NOT NULL AND ST_Within(mh3.geom, s.geom)
                ) AS point_count,

                NOW() AS last_computed
            FROM settlements s
            LEFT JOIN grids g
                   ON g.unique_cod = s.unique_cod
                  AND g.project_id = s.project_id
            WHERE s.project_id = %s
              AND s.unique_cod = ANY(%s)
            GROUP BY s.project_id, s.id, s.unique_cod, s.lgacode, s.wardcode,
                     s.settlement_name, s.lga_name, s.ward_name
            ON CONFLICT (project_id, settlement_id) DO UPDATE SET
                total_grids      = EXCLUDED.total_grids,
                visited_grids    = EXCLUDED.visited_grids,
                completeness_pct = EXCLUDED.completeness_pct,
                is_visited       = EXCLUDED.is_visited,
                point_count      = EXCLUDED.point_count,
                last_computed    = EXCLUDED.last_computed
        """
        cur.execute(sql, (PROJECT_ID, chunk))
        conn.commit()
        processed += len(chunk)
        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        eta  = (total - processed) / rate if rate > 0 else 0
        print(f"  {processed}/{total} settlements — {elapsed:.0f}s elapsed, "
              f"ETA {eta:.0f}s", end="\r", flush=True)

    elapsed_total = time.time() - t0
    print(f"\n  Settlement analytics complete in {elapsed_total:.1f}s")

    # ── 5. Summary stats ─────────────────────────────────────────────────────
    print("\n[5/5] Summary …")
    cur.execute("SELECT COUNT(*) FROM lgas WHERE project_id=%s", (PROJECT_ID,))
    print(f"  LGAs:         {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM wards WHERE project_id=%s", (PROJECT_ID,))
    print(f"  Wards:        {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM settlements WHERE project_id=%s", (PROJECT_ID,))
    print(f"  Settlements:  {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM grids WHERE project_id=%s", (PROJECT_ID,))
    print(f"  Grid cells:   {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM mda_households WHERE geom IS NOT NULL")
    print(f"  HH with geom: {cur.fetchone()[0]}")
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN is_visited THEN 1 END) AS visited,
            ROUND(AVG(completeness_pct)::numeric, 1) AS avg_completeness
        FROM settlement_analytics WHERE project_id=%s
    """, (PROJECT_ID,))
    row = cur.fetchone()
    print(f"  Analytics:    {row[0]} settlements — {row[1]} visited "
          f"({round(100*row[1]/row[0])}%) — avg completeness {row[2]}%")

    cur.close()
    conn.close()
    print("\n✓ Done.")


if __name__ == "__main__":
    main()
