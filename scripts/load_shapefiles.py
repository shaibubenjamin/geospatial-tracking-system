"""
load_shapefiles.py — Ingest Sokoto shapefiles into PostGIS.

Run inside the api container:
    docker compose exec api python scripts/load_shapefiles.py
"""
import logging
import sys

import psycopg2
from psycopg2.extras import execute_values
import shapefile
from shapely.geometry import shape as shp_shape, MultiPolygon
from shapely.ops import transform as shp_transform
from shapely import make_valid

from pyproj import Transformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = "postgresql://geouser:geopass@db:5432/geospatial_tracker"
DATA_DIR = "/app/sokoto_data"
PROJECT_SLUG = "sokoto"
BATCH = 2000

_PROJ = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


# ── geometry helpers ──────────────────────────────────────────────────────────

def reproj(geom):
    return shp_transform(_PROJ.transform, geom)


def to_multipolygon(geom):
    geom = make_valid(geom)
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    # GeometryCollection — extract polygon parts
    polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
    if not polys:
        return None
    from shapely.ops import unary_union
    merged = make_valid(unary_union(polys))
    if merged.geom_type == "Polygon":
        return MultiPolygon([merged])
    return merged


def ewkt(geom):
    return f"SRID=4326;{geom.wkt}"


# ── database helpers ──────────────────────────────────────────────────────────

def get_project_id(cur):
    cur.execute("SELECT id FROM geo_projects WHERE slug = %s", (PROJECT_SLUG,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO geo_projects(name, slug, description, is_active) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("Sokoto", PROJECT_SLUG, "Sokoto State geospatial coverage monitoring", True),
    )
    return cur.fetchone()[0]


def create_spatial_indexes(cur):
    log.info("Creating spatial indexes...")
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_lgas_geom ON lgas USING GIST(geom)",
        "CREATE INDEX IF NOT EXISTS idx_wards_geom ON wards USING GIST(geom)",
        "CREATE INDEX IF NOT EXISTS idx_settlements_geom ON settlements USING GIST(geom)",
        "CREATE INDEX IF NOT EXISTS idx_settlements_lgacode ON settlements(lgacode)",
        "CREATE INDEX IF NOT EXISTS idx_settlements_wardcode ON settlements(wardcode)",
        "CREATE INDEX IF NOT EXISTS idx_grids_geom ON grids USING GIST(geom)",
        "CREATE INDEX IF NOT EXISTS idx_grids_lgacode ON grids(lgacode)",
        "CREATE INDEX IF NOT EXISTS idx_grids_wardcode ON grids(wardcode)",
        "CREATE INDEX IF NOT EXISTS idx_grids_unique_cod ON grids(unique_cod)",
        "CREATE INDEX IF NOT EXISTS idx_points_geom ON points_raw USING GIST(geom)",
        "CREATE INDEX IF NOT EXISTS idx_points_lga ON points_raw(lga_name)",
        "CREATE INDEX IF NOT EXISTS idx_points_ward ON points_raw(ward_name)",
        "CREATE INDEX IF NOT EXISTS idx_points_settlement ON points_raw(settlement_name)",
    ]
    for sql in stmts:
        cur.execute(sql)
    log.info("Indexes created.")


# ── layer loaders ─────────────────────────────────────────────────────────────

def load_lgas(cur, pid):
    cur.execute("SELECT COUNT(*) FROM lgas WHERE project_id = %s", (pid,))
    if cur.fetchone()[0]:
        log.info("LGAs already loaded — skipping.")
        return

    log.info("Loading LGAs...")
    sf = shapefile.Reader(f"{DATA_DIR}/LGA.shp")
    fields = [f[0] for f in sf.fields[1:]]
    rows = []
    skipped = 0

    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            geom = to_multipolygon(reproj(shp_shape(sr.shape.__geo_interface__)))
        except Exception as exc:
            log.warning(f"LGA geometry error ({rec.get('lganame')}): {exc}")
            skipped += 1
            continue
        if not geom:
            skipped += 1
            continue
        rows.append((pid, str(int(rec["lgacode_"])), rec["lganame"], ewkt(geom)))

    execute_values(
        cur,
        "INSERT INTO lgas(project_id, lgacode, lga_name, geom) VALUES %s "
        "ON CONFLICT (project_id, lgacode) DO NOTHING",
        rows,
        template="(%s, %s, %s, ST_GeomFromEWKT(%s))",
    )
    log.info(f"  Loaded {len(rows)} LGAs (skipped {skipped})")


def load_wards(cur, pid):
    cur.execute("SELECT COUNT(*) FROM wards WHERE project_id = %s", (pid,))
    if cur.fetchone()[0]:
        log.info("Wards already loaded — skipping.")
        return

    log.info("Loading Wards...")
    sf = shapefile.Reader(f"{DATA_DIR}/Ward.shp")
    fields = [f[0] for f in sf.fields[1:]]
    rows = []
    skipped = 0

    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            geom = to_multipolygon(reproj(shp_shape(sr.shape.__geo_interface__)))
        except Exception as exc:
            log.warning(f"Ward geometry error ({rec.get('wardname')}): {exc}")
            skipped += 1
            continue
        if not geom:
            skipped += 1
            continue
        rows.append((
            pid,
            str(rec["wardcode"]),
            str(rec["lgacode"]),
            rec["wardname"],
            rec["lganame"],
            ewkt(geom),
        ))

    execute_values(
        cur,
        "INSERT INTO wards(project_id, wardcode, lgacode, ward_name, lga_name, geom) "
        "VALUES %s ON CONFLICT (project_id, wardcode) DO NOTHING",
        rows,
        template="(%s, %s, %s, %s, %s, ST_GeomFromEWKT(%s))",
    )
    log.info(f"  Loaded {len(rows)} Wards (skipped {skipped})")


def load_settlements(cur, pid):
    cur.execute("SELECT COUNT(*) FROM settlements WHERE project_id = %s", (pid,))
    if cur.fetchone()[0]:
        log.info("Settlements already loaded — skipping.")
        return

    log.info("Loading Settlements (9,602 records)...")
    sf = shapefile.Reader(f"{DATA_DIR}/Settlement.shp")
    fields = [f[0] for f in sf.fields[1:]]
    rows = []
    total = skipped = 0

    def flush():
        nonlocal rows
        if not rows:
            return
        execute_values(
            cur,
            "INSERT INTO settlements"
            "(project_id, unique_cod, lgacode, wardcode, settlement_name, lga_name, ward_name, geom) "
            "VALUES %s ON CONFLICT (project_id, unique_cod) DO NOTHING",
            rows,
            template="(%s, %s, %s, %s, %s, %s, %s, ST_GeomFromEWKT(%s))",
        )
        rows = []

    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            geom = to_multipolygon(reproj(shp_shape(sr.shape.__geo_interface__)))
        except Exception as exc:
            log.warning(f"Settlement geometry error: {exc}")
            skipped += 1
            continue
        if not geom:
            skipped += 1
            continue
        rows.append((
            pid,
            str(rec["unique_cod"]),
            str(rec["lgacode"]),
            str(rec["wardcode"]),
            rec["Settlement"],
            rec["lganame"],
            rec["wardname"],
            ewkt(geom),
        ))
        total += 1
        if len(rows) >= BATCH:
            flush()
            log.info(f"  {total} settlements inserted...")

    flush()
    log.info(f"  Loaded {total} Settlements (skipped {skipped})")


def load_grids(cur, pid):
    cur.execute("SELECT COUNT(*) FROM grids WHERE project_id = %s", (pid,))
    if cur.fetchone()[0]:
        log.info("Grids already loaded — skipping.")
        return

    log.info("Loading Grids (98,866 records)...")
    sf = shapefile.Reader(f"{DATA_DIR}/Gridded.shp")
    fields = [f[0] for f in sf.fields[1:]]
    rows = []
    total = skipped = 0

    def flush():
        nonlocal rows
        if not rows:
            return
        execute_values(
            cur,
            "INSERT INTO grids(project_id, unique_cod, lgacode, wardcode, settlement_name, geom) "
            "VALUES %s",
            rows,
            template="(%s, %s, %s, %s, %s, ST_GeomFromEWKT(%s))",
        )
        rows = []

    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            raw = reproj(shp_shape(sr.shape.__geo_interface__))
            geom = make_valid(raw)
        except Exception as exc:
            log.warning(f"Grid geometry error: {exc}")
            skipped += 1
            continue
        # Grid column is POLYGON — flatten MultiPolygon/Collection to largest part
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda g: g.area)
        elif geom.geom_type == "GeometryCollection":
            polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                skipped += 1
                continue
            candidate = max(polys, key=lambda g: g.area)
            geom = (max(candidate.geoms, key=lambda g: g.area)
                    if candidate.geom_type == "MultiPolygon" else candidate)
        if geom.geom_type != "Polygon":
            skipped += 1
            continue
        rows.append((
            pid,
            str(rec["unique_cod"]),
            str(rec["lgacode"]),
            str(rec["wardcode"]),
            rec["Settlement"],
            f"SRID=4326;{geom.wkt}",
        ))
        total += 1
        if len(rows) >= BATCH:
            flush()
            log.info(f"  {total} grids inserted...")

    flush()
    log.info(f"  Loaded {total} Grids (skipped {skipped})")


def load_points(cur, pid):
    cur.execute("SELECT COUNT(*) FROM points_raw WHERE project_id = %s", (pid,))
    existing = cur.fetchone()[0]
    if existing:
        log.info(f"Points already loaded ({existing:,} rows) — skipping.")
        return

    log.info("Loading GPS Points (445,983 records)...")
    sf = shapefile.Reader(f"{DATA_DIR}/so_point.shp")
    fields = [f[0] for f in sf.fields[1:]]
    rows = []
    total = skipped = 0

    def flush():
        nonlocal rows
        if not rows:
            return
        execute_values(
            cur,
            "INSERT INTO points_raw"
            "(project_id, latitude, longitude, lga_name, ward_name, settlement_name, geom) "
            "VALUES %s ON CONFLICT DO NOTHING",
            rows,
            template="(%s, %s, %s, %s, %s, %s, ST_GeomFromEWKT(%s))",
        )
        rows = []

    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        try:
            lat = float(rec["lat"])
            lon = float(rec["long"])
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skipped += 1
            continue
        rows.append((
            pid,
            lat,
            lon,
            rec.get("lga_name") or rec.get("lganame_NM") or "",
            rec.get("ward_name") or rec.get("wardname_N") or "",
            rec.get("settlement") or "",
            f"SRID=4326;POINT({lon} {lat})",
        ))
        total += 1
        if len(rows) >= 5000:
            flush()
            log.info(f"  {total:,} points inserted...")

    flush()
    log.info(f"  Loaded {total:,} GPS Points (skipped {skipped})")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Connecting to PostGIS...")
    try:
        conn = psycopg2.connect(DB_DSN)
    except Exception as exc:
        log.error(f"DB connection failed: {exc}")
        sys.exit(1)

    conn.autocommit = False
    cur = conn.cursor()

    try:
        pid = get_project_id(cur)
        log.info(f"Using project_id={pid} (slug={PROJECT_SLUG})")

        load_lgas(cur, pid)
        conn.commit()

        load_wards(cur, pid)
        conn.commit()

        load_settlements(cur, pid)
        conn.commit()

        load_grids(cur, pid)
        conn.commit()

        load_points(cur, pid)
        conn.commit()

        create_spatial_indexes(cur)
        conn.commit()

        log.info("All layers loaded and indexed successfully.")

    except Exception as exc:
        conn.rollback()
        log.error(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
