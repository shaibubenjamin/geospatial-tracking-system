"""
scripts/import_kano_r3_boundaries.py — Kano Round 3 boundary bootstrap.

Loads four shapefiles into the (lgas / wards / settlements / grids) tables
for `project_id = 4` (Kano Round 3), following the linkage the operator
specified:

    lga    ↔ ward         :  lgacode  (both shapefiles have it)
    ward   ↔ settlement   :  standardise(lga_name + '|' + ward_name)
                             — settlement + grid shapefiles have no
                             ward/lga *code* columns, only names.
    settlement ↔ grid     :  Unique_cod  (both have it; on the grid
                             shapefile the code is REPEATED for every
                             grid inside that settlement, which is
                             expected — grids are keyed by (unique_cod, id))

Design choices (deliberate, per operator sign-off 2026-07-02):

* CRS handling — the general importer hard-codes 3857→4326. All four Kano
  shapefiles are already **EPSG:4326** (`GCS_WGS_1984`). This script skips
  reprojection so we don't corrupt coordinates.
* Only project_id=4 is touched. Kano Pilot (id=3) keeps its existing
  boundaries.
* Sanity-check dry-run first (in the operator-requested order: grid →
  settlement → ward → LGA). The actual insert only fires with --commit.

Usage:
    # inside the API container (docker exec -w /app):
    python -m scripts.import_kano_r3_boundaries                # dry-run
    python -m scripts.import_kano_r3_boundaries --commit       # do the writes

The four .shp/.dbf/.shx/.prj sets must be staged at /tmp/kano_r3/*.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Iterable

import psycopg2
import psycopg2.extras
import shapefile as pyshp   # pyshp
from shapely.geometry import shape as shp_shape, MultiPolygon, Polygon


PROJECT_ID = 4
BASE_DIR   = "/tmp/kano_r3"

FILES = {
    "lga":        os.path.join(BASE_DIR, "kano_lga.shp"),
    "ward":       os.path.join(BASE_DIR, "kano_wards.shp"),
    "settlement": os.path.join(BASE_DIR, "kano_settlement_extent.shp"),
    "grid":       os.path.join(BASE_DIR, "kano_grided_extent.shp"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


# Known one-character transliteration variants between the settlement/grid
# shapefiles and the ward shapefile. Same underlying Hausa place, different
# spelling. Discovered by the dry-run's diff step; codifying here so the
# import is deterministic instead of silently dropping 218 settlements.
# Format: (lga_norm, settlement's_ward_norm)  →  ward_shapefile's_wardname
_WARD_ALIASES: dict[tuple[str, str], str] = {
    ("madobi",     "kubarachi"):        "Kubaraci",
    ("tudun wada", "burum-burum"):      "Burun-Burun",
    ("warawa",     "tamburawar gabas"): "Tamburawan Gabas",
    ("kabo",       "hawadin galadima"): "Hawaden Galadima",
}


def _norm_str(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def standardise_name(*parts: str) -> str:
    """Pipeline for name-based joins.

    lower-case, strip, collapse runs of whitespace, then join with a single
    ``|``. Handles the trailing / doubled-space typos we see in the wild
    (e.g. ``"Unguwa Uku Kauyen  Alu "`` matches ``"Unguwa Uku Kauyen Alu"``).
    """
    cleaned = [_norm_str(p) for p in parts]
    return " | ".join(cleaned)


def resolve_ward_key(lga: str, ward: str) -> str:
    """Return the concat key to look up in the ward shapefile, applying
    the known one-off transliteration aliases first."""
    lga_n  = _norm_str(lga)
    ward_n = _norm_str(ward)
    canonical = _WARD_ALIASES.get((lga_n, ward_n))
    if canonical:
        return standardise_name(lga, canonical)
    return standardise_name(lga, ward)


def _pick(row: dict, *candidates: str) -> str:
    """Case-insensitive lookup — first candidate that resolves wins."""
    lower_keys = {k.lower(): k for k in row.keys()}
    for c in candidates:
        lk = c.lower()
        if lk in lower_keys:
            v = row[lower_keys[lk]]
            return "" if v is None else str(v).strip()
    return ""


def _to_wkt_multipolygon(geom_dict: dict) -> str:
    """Coerce a GeoJSON dict into WKT MULTIPOLYGON. Grids may be single
    polygons — those get wrapped so the target column (MULTIPOLYGON) stays
    happy. For the grids table (Polygon), we return WKT of a single Polygon.
    """
    g = shp_shape(geom_dict)
    if isinstance(g, Polygon):
        g = MultiPolygon([g])
    return g.wkt


def _to_wkt_polygon(geom_dict: dict) -> str:
    """Single-polygon WKT for the grids table. If the shape happens to be a
    MultiPolygon with one part, unwrap it; else take the first part (grids
    are cadastral 100m squares — there is no realistic case for a multi-part
    grid).
    """
    g = shp_shape(geom_dict)
    if isinstance(g, MultiPolygon):
        g = list(g.geoms)[0]
    return g.wkt


# ─────────────────────────────────────────────────────────────────────────────
# Readers
# ─────────────────────────────────────────────────────────────────────────────


def read_lgas():
    """Return list of dicts (lgacode, lga_name, geom_wkt)."""
    reader = pyshp.Reader(FILES["lga"])
    fields = [f[0] for f in reader.fields[1:]]
    out = []
    for i, sr in enumerate(reader.iterShapeRecords()):
        row = dict(zip(fields, sr.record))
        lgacode  = _pick(row, "lgacode", "lga_code", "adm2_pcode")
        lga_name = _pick(row, "lganame", "lga_name", "adm2_en", "name")
        if not lgacode:
            print(f"  ! LGA row {i}: no lgacode — skipping ({row})", file=sys.stderr)
            continue
        out.append({
            "lgacode":  lgacode,
            "lga_name": lga_name or f"LGA_{lgacode}",
            "geom_wkt": _to_wkt_multipolygon(sr.shape.__geo_interface__),
        })
    return out


def read_wards():
    """Return list of dicts + a concat→(wardcode, lgacode) lookup."""
    reader = pyshp.Reader(FILES["ward"])
    fields = [f[0] for f in reader.fields[1:]]
    out = []
    concat_map: dict[str, tuple[str, str]] = {}
    dupes: list[str] = []
    for i, sr in enumerate(reader.iterShapeRecords()):
        row = dict(zip(fields, sr.record))
        wardcode = _pick(row, "wardcode", "ward_code", "adm3_pcode")
        wardname = _pick(row, "wardname", "ward_name", "adm3_en", "name")
        lgacode  = _pick(row, "lgacode", "lga_code", "adm2_pcode")
        lganame  = _pick(row, "lganame", "lga_name", "adm2_en")
        if not wardcode:
            print(f"  ! Ward row {i}: no wardcode — skipping ({row})", file=sys.stderr)
            continue
        key = standardise_name(lganame, wardname)
        if key in concat_map:
            dupes.append(f"{key}  →  existing {concat_map[key][0]} vs new {wardcode}")
        else:
            concat_map[key] = (wardcode, lgacode)
        out.append({
            "wardcode":  wardcode,
            "lgacode":   lgacode,
            "ward_name": wardname or f"Ward_{wardcode}",
            "lga_name":  lganame or None,
            "concat":    key,
            "geom_wkt":  _to_wkt_multipolygon(sr.shape.__geo_interface__),
        })
    if dupes:
        print(f"  !! {len(dupes)} duplicate concat keys in wards shapefile — first 5:", file=sys.stderr)
        for d in dupes[:5]:
            print(f"     {d}", file=sys.stderr)
    return out, concat_map


def read_settlements(concat_to_ward: dict[str, tuple[str, str]]):
    """Stream settlements, look up (wardcode, lgacode) via concat.

    Returns (rows, unique_cod_to_ward_lga_map, orphans, unmatched_count).
    """
    reader = pyshp.Reader(FILES["settlement"])
    fields = [f[0] for f in reader.fields[1:]]
    out = []
    unique_to_wl: dict[str, tuple[str, str]] = {}
    orphans_examples: list[str] = []
    matched = 0
    for i, sr in enumerate(reader.iterShapeRecords()):
        row = dict(zip(fields, sr.record))
        unique_cod = _pick(row, "unique_cod", "unique_code", "settlement_code")
        if not unique_cod:
            continue  # can't do anything without one
        lganame  = _pick(row, "lga_name", "lganame")
        wardname = _pick(row, "ward_name", "wardname")
        settlement_name = _pick(row, "settlement", "settlement_name", "name") or None

        # resolve_ward_key applies the transliteration-alias table for the
        # four known one-character-different ward names, then falls back to
        # the plain standardised concat.
        key = resolve_ward_key(lganame, wardname)
        wardcode = lgacode = ""
        if key in concat_to_ward:
            wardcode, lgacode = concat_to_ward[key]
            matched += 1
        else:
            if len(orphans_examples) < 5:
                orphans_examples.append(f"unique_cod={unique_cod}  concat='{key}'")

        out.append({
            "unique_cod":      unique_cod,
            "lgacode":         lgacode,
            "wardcode":        wardcode,
            "settlement_name": settlement_name,
            "lga_name":        lganame or None,
            "ward_name":       wardname or None,
            "geom_wkt":        _to_wkt_multipolygon(sr.shape.__geo_interface__),
        })
        unique_to_wl[unique_cod] = (wardcode, lgacode)

    return out, unique_to_wl, orphans_examples, matched


def stream_grids(unique_to_wl: dict[str, tuple[str, str]]):
    """Yield grid rows one at a time (200k rows — don't hold them all).

    ``unique_to_wl`` is the settlement.Unique_cod → (wardcode, lgacode) map so
    each grid inherits its parent settlement's codes without a second SQL
    join.
    """
    reader = pyshp.Reader(FILES["grid"])
    fields = [f[0] for f in reader.fields[1:]]
    orphans = []
    matched = 0
    total = 0
    for i, sr in enumerate(reader.iterShapeRecords()):
        row = dict(zip(fields, sr.record))
        unique_cod = _pick(row, "unique_cod", "unique_code", "grid_code")
        if not unique_cod:
            continue
        total += 1
        settlement_name = _pick(row, "settlement", "settlement_name") or None
        wardcode = lgacode = ""
        if unique_cod in unique_to_wl:
            wardcode, lgacode = unique_to_wl[unique_cod]
            matched += 1
        else:
            if len(orphans) < 5:
                orphans.append(unique_cod)
        yield {
            "unique_cod":      unique_cod,
            "lgacode":         lgacode,
            "wardcode":        wardcode,
            "settlement_name": settlement_name,
            "geom_wkt":        _to_wkt_polygon(sr.shape.__geo_interface__),
        }, matched, total, orphans


# ─────────────────────────────────────────────────────────────────────────────
# Sanity check (dry run) — in the operator-requested order
# ─────────────────────────────────────────────────────────────────────────────


def dry_run() -> tuple[list, list, dict, list, dict]:
    """Read all four shapefiles, compute linkage, report in reverse order.

    Returns (lgas, wards, concat_map, settlements, unique_to_wl) so caller
    can reuse them for the actual insert without re-parsing.
    """
    print("── DRY RUN ────────────────────────────────────────────────────────────")

    t0 = time.time()
    lgas = read_lgas()
    lga_codes = {r["lgacode"] for r in lgas}
    print(f"  LGA shapefile:        {len(lgas):>7,} rows read  ({time.time()-t0:.1f}s)")

    t0 = time.time()
    wards, concat_map = read_wards()
    ward_lgacode_matches = sum(1 for w in wards if w["lgacode"] in lga_codes)
    print(f"  Ward shapefile:       {len(wards):>7,} rows read  ({time.time()-t0:.1f}s)")

    t0 = time.time()
    settlements, unique_to_wl, sett_orphans, sett_matched = read_settlements(concat_map)
    print(f"  Settlement shapefile: {len(settlements):>7,} rows read  ({time.time()-t0:.1f}s)")

    # Grids: stream-count only for the sanity check — cheap.
    t0 = time.time()
    grid_matched = grid_total = 0
    grid_orphans = []
    for _row, m, t, orphans in stream_grids(unique_to_wl):
        grid_matched = m
        grid_total   = t
        grid_orphans = orphans
    print(f"  Grid shapefile:       {grid_total:>7,} rows read  ({time.time()-t0:.1f}s)")

    print()
    print("── LINKAGE (in operator-requested order) ──────────────────────────────")
    print(f"  Grid       → Settlement : {grid_matched:>7,} of {grid_total:>7,} matched via Unique_cod   (orphans: {grid_total - grid_matched:,})")
    print(f"  Settlement → Ward       : {sett_matched:>7,} of {len(settlements):>7,} matched via concat(lganame|wardname)   (orphans: {len(settlements) - sett_matched:,})")
    print(f"  Ward       → LGA        : {ward_lgacode_matches:>7,} of {len(wards):>7,} matched via lgacode   (orphans: {len(wards) - ward_lgacode_matches:,})")

    if grid_orphans:
        print(f"\n  Grid orphan examples (first {len(grid_orphans)}):")
        for u in grid_orphans:
            print(f"    Unique_cod = '{u}'")
    if sett_orphans:
        print(f"\n  Settlement orphan examples (first {len(sett_orphans)}):")
        for s in sett_orphans:
            print(f"    {s}")
    unmatched_wards = [w for w in wards if w["lgacode"] not in lga_codes]
    if unmatched_wards:
        print(f"\n  Ward-to-LGA orphan examples (first 5):")
        for w in unmatched_wards[:5]:
            print(f"    wardcode={w['wardcode']}  concat='{w['concat']}'  claimed lgacode='{w['lgacode']}'")

    return lgas, wards, concat_map, settlements, unique_to_wl


# ─────────────────────────────────────────────────────────────────────────────
# Actual DB writes (synchronous psycopg2 for grid throughput)
# ─────────────────────────────────────────────────────────────────────────────


def _connect_sync():
    url = os.getenv("DATABASE_URL_SYNC")
    if not url:
        raise RuntimeError("DATABASE_URL_SYNC is not set — cannot connect to Postgres")
    return psycopg2.connect(url)


def _wipe_project(cur, pid: int) -> None:
    """Clear any existing boundaries for this project — cascade order matters:
    grids → settlements → wards → lgas (no FKs between them technically, but
    the mental model is child → parent)."""
    for tbl in ("grids", "settlements", "wards", "lgas"):
        cur.execute(f"DELETE FROM {tbl} WHERE project_id = %s", (pid,))
        print(f"    cleared {tbl}: {cur.rowcount:,} rows")


def commit_load(lgas, wards, settlements, unique_to_wl) -> None:
    """Blocking bulk load.  Wraps everything in a single transaction so an
    error mid-load doesn't leave partial data.
    """
    conn = _connect_sync()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        print("\n── COMMIT ─────────────────────────────────────────────────────────────")
        print(f"  Wiping any existing boundaries on project_id={PROJECT_ID}...")
        _wipe_project(cur, PROJECT_ID)

        # LGAs — 44 rows, straightforward.
        t0 = time.time()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO lgas (project_id, lgacode, lga_name, geom)
               VALUES %s""",
            [
                (PROJECT_ID, r["lgacode"], r["lga_name"], r["geom_wkt"])
                for r in lgas
            ],
            template="(%s, %s, %s, ST_GeomFromText(%s, 4326))",
            page_size=200,
        )
        print(f"  LGAs loaded:        {len(lgas):>7,}  ({time.time()-t0:.1f}s)")

        # Wards — 484 rows.
        t0 = time.time()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO wards
                 (project_id, wardcode, lgacode, ward_name, lga_name, geom)
               VALUES %s""",
            [
                (
                    PROJECT_ID, w["wardcode"], w["lgacode"],
                    w["ward_name"], w["lga_name"], w["geom_wkt"],
                )
                for w in wards
            ],
            template="(%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
            page_size=500,
        )
        print(f"  Wards loaded:       {len(wards):>7,}  ({time.time()-t0:.1f}s)")

        # Settlements — 28k rows.
        t0 = time.time()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO settlements
                 (project_id, unique_cod, lgacode, wardcode,
                  settlement_name, lga_name, ward_name, geom)
               VALUES %s""",
            [
                (
                    PROJECT_ID, s["unique_cod"], s["lgacode"], s["wardcode"],
                    s["settlement_name"], s["lga_name"], s["ward_name"],
                    s["geom_wkt"],
                )
                for s in settlements
            ],
            template="(%s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
            page_size=500,
        )
        print(f"  Settlements loaded: {len(settlements):>7,}  ({time.time()-t0:.1f}s)")

        # Grids — stream in chunks (batches of ~2000 rows go into one SQL
        # statement so the round-trip overhead through the SSM tunnel doesn't
        # kill throughput). 200k rows in ~2000-row batches = ~100 INSERTs.
        t0 = time.time()
        BATCH = 2000
        buf: list = []
        total_grids = 0
        for row_pack in stream_grids(unique_to_wl):
            row = row_pack[0]
            buf.append((
                PROJECT_ID, row["unique_cod"], row["lgacode"], row["wardcode"],
                row["settlement_name"], row["geom_wkt"],
            ))
            if len(buf) >= BATCH:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO grids
                         (project_id, unique_cod, lgacode, wardcode,
                          settlement_name, geom)
                       VALUES %s
                       ON CONFLICT DO NOTHING""",
                    buf,
                    template="(%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
                    page_size=BATCH,
                )
                total_grids += len(buf)
                buf.clear()
                if total_grids % 10000 == 0:
                    print(f"    grids inserted so far: {total_grids:,} ({time.time()-t0:.1f}s)")
        if buf:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO grids
                     (project_id, unique_cod, lgacode, wardcode,
                      settlement_name, geom)
                   VALUES %s
                   ON CONFLICT DO NOTHING""",
                buf,
                template="(%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
                page_size=BATCH,
            )
            total_grids += len(buf)
        print(f"  Grids loaded:       {total_grids:>7,}  ({time.time()-t0:.1f}s)")

        conn.commit()
        print("\n  ✓ transaction committed")

        # Verify counts landed
        for tbl in ("lgas", "wards", "settlements", "grids"):
            cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE project_id = %s", (PROJECT_ID,))
            print(f"    {tbl:<12} row count in DB: {cur.fetchone()[0]:,}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="Actually write to the DB (default: dry run)")
    args = ap.parse_args()

    # Fast fail if any shapefile is missing.
    missing = [k for k, p in FILES.items() if not os.path.exists(p)]
    if missing:
        print(f"FAIL — missing shapefile(s): {missing}", file=sys.stderr)
        print(f"       expected at: {BASE_DIR}", file=sys.stderr)
        sys.exit(1)

    lgas, wards, concat_map, settlements, unique_to_wl = dry_run()

    if not args.commit:
        print("\n(Dry run only — pass --commit to actually write to the DB.)")
        return

    commit_load(lgas, wards, settlements, unique_to_wl)


if __name__ == "__main__":
    main()
