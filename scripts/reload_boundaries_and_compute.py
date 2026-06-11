"""
scripts/reload_boundaries_and_compute.py
=========================================
Load MDA boundary shapefiles for ANY state/round into PostGIS, run the spatial
analytics pipeline (grid visitation → settlement → ward → LGA), and print a
validation report. Project-parameterised — nothing is pinned to one state.

Examples:
    # New state/round (created if missing); all shapefiles in one folder:
    python scripts/reload_boundaries_and_compute.py --state Kano --round 1 \\
        --shapefiles "/data/kano-mda" --activate

    # Reload an existing project by id, with a different source CRS:
    python scripts/reload_boundaries_and_compute.py --project-id 3 \\
        --shapefiles "/data/kano-mda" --source-crs EPSG:32632

    # Re-run only the validation report (no loading):
    python scripts/reload_boundaries_and_compute.py --project-id 3 --validate-only

Shapefile names default to lga.shp / ward.shp / Settlement.shp / Gridded_Ta.shp
inside --shapefiles; override any of them with --lga/--ward/--settlement/--grid.

Steps: clear boundaries for the project → load LGA/Ward/Settlement/Grid →
populate mda_households.geom → compute settlement analytics → summary + validate.
"""

import os, sys, time, argparse, re
import shapefile as pyshp
from pyproj import Transformer
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import transform as shapely_transform
import functools, json
import psycopg2
import psycopg2.extras

# ── DB connection (override with --db-url) ───────────────────────────────────
DB_URL_DEFAULT = os.environ.get(
    "DATABASE_URL_SYNC",
    "postgresql://geotracker:geotracker@localhost:5433/geotracker",
)

# Reprojection transformer — rebuilt in main() from --source-crs (default 3857).
_tfm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

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


# ── CLI / project resolution / validation ────────────────────────────────────
def build_args():
    p = argparse.ArgumentParser(description="Load MDA boundaries + compute analytics for any state/round.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--project-id", type=int, help="Existing geo_projects.id to (re)load")
    g.add_argument("--state", help="State name, e.g. 'Kano' (use with --round; created if missing)")
    p.add_argument("--round", type=int, help="Round number, e.g. 1 (required with --state)")
    p.add_argument("--shapefiles", help="Folder holding the LGA/Ward/Settlement/Grid shapefiles")
    p.add_argument("--lga"); p.add_argument("--ward")
    p.add_argument("--settlement"); p.add_argument("--grid")
    p.add_argument("--source-crs", default="EPSG:3857", help="Shapefile CRS (default EPSG:3857)")
    p.add_argument("--visit-threshold", type=int, default=70)
    p.add_argument("--db-url", default=DB_URL_DEFAULT)
    p.add_argument("--activate", action="store_true",
                   help="Set this project active (deactivates the state's other rounds)")
    p.add_argument("--validate-only", action="store_true", help="Skip loading; print the validation report only")
    a = p.parse_args()
    if a.state and a.round is None:
        p.error("--round is required with --state")
    if not a.validate_only and not (a.shapefiles or all([a.lga, a.ward, a.settlement, a.grid])):
        p.error("provide --shapefiles DIR (or all of --lga/--ward/--settlement/--grid), unless --validate-only")
    return a


def resolve_project(cur, conn, args) -> int:
    """Return the geo_projects.id to load — by explicit id, or by (state, round),
    creating the project row if it doesn't exist yet."""
    if args.project_id is not None:
        cur.execute("SELECT id, name FROM geo_projects WHERE id = %s", (args.project_id,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"  No geo_projects row with id={args.project_id}")
        print(f"  Project #{row[0]}: {row[1]}")
        pid = row[0]
    else:
        cur.execute(
            "SELECT id, name FROM geo_projects WHERE lower(state_name)=lower(%s) AND round_number=%s",
            (args.state, args.round),
        )
        row = cur.fetchone()
        if row:
            pid = row[0]
            print(f"  Found project #{pid}: {row[1]}")
        else:
            name = f"{args.state} Round {args.round}"
            slug = re.sub(r"[^a-z0-9]+", "-", f"{args.state}-r{args.round}".lower()).strip("-")
            cur.execute(
                """INSERT INTO geo_projects (name, slug, description, state_name, round_number, is_active)
                   VALUES (%s, %s, %s, %s, %s, FALSE) RETURNING id""",
                (name, slug, f"{args.state} State — Round {args.round}", args.state, args.round),
            )
            pid = cur.fetchone()[0]
            conn.commit()
            print(f"  Created project #{pid}: {name}")
    if args.activate:
        cur.execute(
            "UPDATE geo_projects SET is_active = (id = %s) "
            "WHERE lower(state_name) = (SELECT lower(state_name) FROM geo_projects WHERE id = %s)",
            (pid, pid),
        )
        conn.commit()
        print(f"  Set project #{pid} active for its state")
    return pid


def validate(cur, pid: int):
    """Counts + name/code consistency so mismatches are caught at load time, not
    discovered later as a blank LGA/ward in the app."""
    print("\n── Validation report ──────────────────────────────────────────────")
    def one(q, *a):
        cur.execute(q, a or (pid,)); return cur.fetchone()[0]
    lgas = one("SELECT COUNT(*) FROM lgas WHERE project_id=%s")
    wards = one("SELECT COUNT(*) FROM wards WHERE project_id=%s")
    setts = one("SELECT COUNT(*) FROM settlements WHERE project_id=%s")
    grids = one("SELECT COUNT(*) FROM grids WHERE project_id=%s")
    hh = one("SELECT COUNT(*) FROM mda_households WHERE project_id=%s")
    hh_geom = one("SELECT COUNT(*) FROM mda_households WHERE project_id=%s AND geom IS NOT NULL")
    hh_ward = one("SELECT COUNT(*) FROM mda_households WHERE project_id=%s AND ward_name IS NOT NULL AND ward_name<>''")
    sa = one("SELECT COUNT(*) FROM settlement_analytics WHERE project_id=%s")
    print(f"  Boundaries : {lgas} LGAs · {wards} wards · {setts} settlements · {grids} grids")
    print(f"  Households  : {hh} total · {hh_geom} with GPS · {hh_ward} with ward_name")
    print(f"  Analytics   : {sa} settlement_analytics rows")

    def flag(label, q, hint=""):
        cur.execute(q, (pid,))
        rows = [str(r[0]) for r in cur.fetchall() if r[0] is not None]
        if rows:
            print(f"  ⚠ {label}: {len(rows)} — e.g. {rows[:6]}" + (f"  · {hint}" if hint else ""))
        else:
            print(f"  ✓ {label}: none")

    flag("wards whose lgacode isn't in lgas",
         "SELECT DISTINCT w.lgacode FROM wards w LEFT JOIN lgas l "
         "ON l.project_id=w.project_id AND l.lgacode=w.lgacode "
         "WHERE w.project_id=%s AND l.lgacode IS NULL")
    flag("settlements whose wardcode isn't in wards",
         "SELECT DISTINCT s.wardcode FROM settlements s LEFT JOIN wards w "
         "ON w.project_id=s.project_id AND w.wardcode=s.wardcode "
         "WHERE s.project_id=%s AND w.wardcode IS NULL")
    flag("household LGA names not matching any boundary LGA",
         "SELECT DISTINCT mh.lga FROM mda_households mh WHERE mh.project_id=%s AND mh.lga IS NOT NULL "
         "AND NOT EXISTS (SELECT 1 FROM lgas l WHERE l.project_id=mh.project_id "
         "AND lower(trim(l.lga_name))=lower(trim(mh.lga)))",
         "these LGAs' treatment data won't match a boundary")
    nw = one("SELECT COUNT(*) FROM mda_households WHERE project_id=%s AND (ward_name IS NULL OR ward_name='')")
    print((f"  ⚠ households with no ward_name: {nw}  · won't roll up to a ward "
           "(app falls back to settlement_analytics)") if nw else "  ✓ households with no ward_name: none")
    flag("boundary LGAs with NO settlement_analytics (out of campaign / no plan)",
         "SELECT l.lga_name FROM lgas l WHERE l.project_id=%s AND NOT EXISTS "
         "(SELECT 1 FROM settlement_analytics sa WHERE sa.project_id=l.project_id AND sa.lgacode=l.lgacode)",
         "these render blank on the map by design (in_campaign=false)")
    print("────────────────────────────────────────────────────────────────────")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    global _tfm
    args = build_args()
    print(f"Connecting to {args.db_url} …")
    conn = psycopg2.connect(args.db_url)
    cur  = conn.cursor()

    PROJECT_ID = resolve_project(cur, conn, args)
    if args.validate_only:
        validate(cur, PROJECT_ID)
        cur.close(); conn.close(); return

    _tfm = Transformer.from_crs(args.source_crs, "EPSG:4326", always_xy=True)
    VISIT_THRESHOLD = args.visit_threshold
    base = args.shapefiles
    SHP = {
        "lga":        args.lga or os.path.join(base, "lga.shp"),
        "ward":       args.ward or os.path.join(base, "ward.shp"),
        "settlement": args.settlement or os.path.join(base, "Settlement.shp"),
        "grid":       args.grid or os.path.join(base, "Gridded_Ta.shp"),
    }

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

    validate(cur, PROJECT_ID)

    cur.close()
    conn.close()
    print("\n✓ Done.")


if __name__ == "__main__":
    main()
