"""
boundary_importer.py
Read shapefiles (ZIP or .shp) using pyshp + pyproj, reproject EPSG:3857 → EPSG:4326,
and insert boundary records into PostGIS.
"""
import io
import os
import zipfile
import tempfile
import json
from typing import List, Dict, Any, Tuple

import shapefile as pyshp          # pyshp package
from pyproj import Transformer
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import transform as shapely_transform
import functools

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

# Reproject function: EPSG:3857 → EPSG:4326
_transformer_3857_to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _pick_field(row: dict, *candidates: str) -> str:
    """Case-insensitive flexible lookup for boundary attribute fields.

    Different states' shapefiles use different DBF column conventions
    (``lgacode_`` vs ``LGACODE`` vs ``lga_code`` vs ``Code``). For each
    candidate name we try:
      1. Exact case-insensitive match against any key in ``row``.
      2. Substring match (``"lgacode" in "stateLGACODE2024"``).
    Returns the stringified value of the first match, stripped, or
    empty string when nothing matches.

    The first candidate that resolves wins, so callers should list
    candidates from most-specific to least-specific.
    """
    # Build a normalised lookup once per row.
    keys_lower = {str(k).strip().lower(): k for k in row.keys()}
    for cand in candidates:
        cand_l = cand.lower()
        # Pass 1: exact case-insensitive
        if cand_l in keys_lower:
            v = row[keys_lower[cand_l]]
            return "" if v is None else str(v).strip()
        # Pass 2: substring (e.g. "lgacode" in "lgacode_" or "lga_code")
        for key_l, orig in keys_lower.items():
            if cand_l in key_l:
                v = row[orig]
                return "" if v is None else str(v).strip()
    return ""


def _reproject_geom(geom_dict: dict) -> dict:
    """Reproject a GeoJSON geometry dict from EPSG:3857 to EPSG:4326."""
    geom = shape(geom_dict)
    proj_func = functools.partial(_transformer_3857_to_4326.transform)
    reprojected = shapely_transform(proj_func, geom)

    # Ensure MultiPolygon
    if isinstance(reprojected, Polygon):
        reprojected = MultiPolygon([reprojected])
    elif not isinstance(reprojected, MultiPolygon):
        try:
            reprojected = MultiPolygon(list(reprojected.geoms))
        except Exception:
            reprojected = MultiPolygon([reprojected])

    return mapping(reprojected)


def _extract_shapefile_from_zip(zip_bytes: bytes, tmp_dir: str) -> str:
    """Extract ZIP and return path to the .shp file."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(tmp_dir)
    for root, _, files in os.walk(tmp_dir):
        for f in files:
            if f.lower().endswith(".shp"):
                return os.path.join(root, f)
    raise ValueError("No .shp file found in uploaded ZIP")


def _read_shapefile(shp_path: str) -> Tuple[List[str], List[Any], List[Any]]:
    """Read a shapefile and return (field_names, records, shapes)."""
    reader = pyshp.Reader(shp_path)
    fields = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
    records = reader.records()
    shapes = reader.shapes()
    return fields, records, shapes


def _geom_to_wkt_multipolygon(geom_dict: dict) -> str:
    """Convert reprojected GeoJSON geometry dict to WKT MULTIPOLYGON."""
    geom = shape(geom_dict)
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    return geom.wkt


async def import_lga_shapefile(
    zip_bytes: bytes,
    project_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Import LGA shapefile (ZIP) into the lgas table."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        shp_path = _extract_shapefile_from_zip(zip_bytes, tmp_dir)
        fields, records, shapes = _read_shapefile(shp_path)

        inserted = 0
        skipped = 0
        errors = []

        for i, (rec, shp) in enumerate(zip(records, shapes)):
            row = dict(zip(fields, rec))
            # Most specific first — "lgacode" usually wins. Fall back to
            # generic "code" only if no LGA-specific code column exists.
            lgacode  = _pick_field(row, "lgacode", "lga_code", "adm2_pcode", "adm2code", "code") or f"LGA_{i}"
            lga_name = _pick_field(row, "lga_name", "lganame", "adm2_en", "adm2name", "name") or f"LGA_{i}"

            if not lgacode or lgacode.startswith("LGA_"):
                errors.append(f"Row {i}: no LGA code column matched. Available fields: {fields[:10]}")
                skipped += 1
                continue

            try:
                geom_dict = shp.__geo_interface__
                reprojected = _reproject_geom(geom_dict)
                wkt = _geom_to_wkt_multipolygon(reprojected)

                await db.execute(
                    text("""
                        INSERT INTO lgas (project_id, lgacode, lga_name, geom)
                        VALUES (:project_id, :lgacode, :lga_name, ST_GeomFromText(:wkt, 4326))
                        ON CONFLICT (project_id, lgacode) DO UPDATE
                          SET lga_name = EXCLUDED.lga_name,
                              geom = EXCLUDED.geom
                    """),
                    {"project_id": project_id, "lgacode": lgacode, "lga_name": lga_name, "wkt": wkt},
                )
                inserted += 1
            except Exception as e:
                errors.append(f"Row {i} ({lgacode}): {str(e)}")
                skipped += 1

        await db.commit()
        return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}


async def import_ward_shapefile(
    zip_bytes: bytes,
    project_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Import Ward shapefile (ZIP) into the wards table."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        shp_path = _extract_shapefile_from_zip(zip_bytes, tmp_dir)
        fields, records, shapes = _read_shapefile(shp_path)

        inserted = 0
        skipped = 0
        errors = []

        for i, (rec, shp) in enumerate(zip(records, shapes)):
            row = dict(zip(fields, rec))
            wardcode  = _pick_field(row, "wardcode", "ward_code", "adm3_pcode", "adm3code") or f"WARD_{i}"
            ward_name = _pick_field(row, "ward_name", "wardname", "adm3_en", "adm3name", "name") or f"Ward_{i}"
            lgacode   = _pick_field(row, "lgacode", "lga_code", "adm2_pcode", "adm2code") or None
            lga_name  = _pick_field(row, "lga_name", "lganame", "adm2_en", "adm2name") or None

            if not wardcode or wardcode.startswith("WARD_"):
                errors.append(f"Row {i}: no ward code column matched. Available fields: {fields[:10]}")
                skipped += 1
                continue

            try:
                geom_dict = shp.__geo_interface__
                reprojected = _reproject_geom(geom_dict)
                wkt = _geom_to_wkt_multipolygon(reprojected)

                await db.execute(
                    text("""
                        INSERT INTO wards (project_id, wardcode, lgacode, ward_name, lga_name, geom)
                        VALUES (:project_id, :wardcode, :lgacode, :ward_name, :lga_name,
                                ST_GeomFromText(:wkt, 4326))
                        ON CONFLICT (project_id, wardcode) DO UPDATE
                          SET lgacode = EXCLUDED.lgacode,
                              ward_name = EXCLUDED.ward_name,
                              lga_name = EXCLUDED.lga_name,
                              geom = EXCLUDED.geom
                    """),
                    {
                        "project_id": project_id, "wardcode": wardcode, "lgacode": lgacode,
                        "ward_name": ward_name, "lga_name": lga_name, "wkt": wkt,
                    },
                )
                inserted += 1
            except Exception as e:
                errors.append(f"Row {i} ({wardcode}): {str(e)}")
                skipped += 1

        await db.commit()
        return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}


async def import_settlement_shapefile(
    zip_bytes: bytes,
    project_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Import Settlement shapefile (ZIP) into the settlements table."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        shp_path = _extract_shapefile_from_zip(zip_bytes, tmp_dir)
        fields, records, shapes = _read_shapefile(shp_path)

        inserted = 0
        skipped = 0
        errors = []

        for i, (rec, shp) in enumerate(zip(records, shapes)):
            row = dict(zip(fields, rec))
            # Most-specific first. unique_cod is the canonical settlement ID
            # used downstream by household-to-settlement spatial joins, but
            # if a state's shapefile uses a different name we fall back.
            unique_cod      = _pick_field(row, "unique_cod", "unique_code", "settlement_code", "sett_code") or ""
            lgacode         = _pick_field(row, "lgacode", "lga_code", "adm2_pcode") or ""
            wardcode        = _pick_field(row, "wardcode", "ward_code", "adm3_pcode") or ""
            settlement_name = _pick_field(row, "settlement_name", "settlemen", "settlement", "name") or None
            lga_name        = _pick_field(row, "lga_name", "lganame", "adm2_en") or None
            ward_name       = _pick_field(row, "ward_name", "wardname", "adm3_en") or None

            if not unique_cod:
                errors.append(f"Row {i}: no settlement code (unique_cod/code/settlement_code). Available fields: {fields[:10]}")
                skipped += 1
                continue

            try:
                geom_dict = shp.__geo_interface__
                reprojected = _reproject_geom(geom_dict)
                wkt = _geom_to_wkt_multipolygon(reprojected)

                await db.execute(
                    text("""
                        INSERT INTO settlements
                          (project_id, unique_cod, lgacode, wardcode, settlement_name, lga_name, ward_name, geom)
                        VALUES
                          (:project_id, :unique_cod, :lgacode, :wardcode, :settlement_name,
                           :lga_name, :ward_name, ST_GeomFromText(:wkt, 4326))
                        ON CONFLICT (project_id, unique_cod) DO UPDATE
                          SET lgacode = EXCLUDED.lgacode,
                              wardcode = EXCLUDED.wardcode,
                              settlement_name = EXCLUDED.settlement_name,
                              lga_name = EXCLUDED.lga_name,
                              ward_name = EXCLUDED.ward_name,
                              geom = EXCLUDED.geom
                    """),
                    {
                        "project_id": project_id, "unique_cod": unique_cod,
                        "lgacode": lgacode, "wardcode": wardcode,
                        "settlement_name": settlement_name,
                        "lga_name": lga_name, "ward_name": ward_name, "wkt": wkt,
                    },
                )
                inserted += 1
            except Exception as e:
                errors.append(f"Row {i} ({unique_cod}): {str(e)}")
                skipped += 1

        await db.commit()
        return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}


async def import_grid_shapefile(
    zip_bytes: bytes,
    project_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Import Grid shapefile (ZIP) into the grids table."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        shp_path = _extract_shapefile_from_zip(zip_bytes, tmp_dir)
        fields, records, shapes = _read_shapefile(shp_path)

        inserted = 0
        skipped = 0
        errors = []

        for i, (rec, shp) in enumerate(zip(records, shapes)):
            row = dict(zip(fields, rec))
            unique_cod      = _pick_field(row, "unique_cod", "unique_code", "grid_code", "grid_id") or ""
            lgacode         = _pick_field(row, "lgacode", "lga_code", "adm2_pcode") or ""
            wardcode        = _pick_field(row, "wardcode", "ward_code", "adm3_pcode") or ""
            settlement_name = _pick_field(row, "settlement_name", "settlemen", "settlement", "name") or None

            if not unique_cod:
                errors.append(f"Row {i}: no grid code (unique_cod/grid_code/grid_id). Available fields: {fields[:10]}")
                skipped += 1
                continue

            try:
                geom_dict = shp.__geo_interface__
                geom = shape(geom_dict)

                # Reproject geometry
                proj_func = functools.partial(_transformer_3857_to_4326.transform)
                reprojected = shapely_transform(proj_func, geom)

                # Ensure Polygon (grids are single polygons)
                if isinstance(reprojected, MultiPolygon):
                    reprojected = reprojected.geoms[0]
                wkt = reprojected.wkt

                await db.execute(
                    text("""
                        INSERT INTO grids
                          (project_id, unique_cod, lgacode, wardcode, settlement_name, geom)
                        VALUES
                          (:project_id, :unique_cod, :lgacode, :wardcode, :settlement_name,
                           ST_GeomFromText(:wkt, 4326))
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "project_id": project_id, "unique_cod": unique_cod,
                        "lgacode": lgacode, "wardcode": wardcode,
                        "settlement_name": settlement_name, "wkt": wkt,
                    },
                )
                inserted += 1
            except Exception as e:
                errors.append(f"Row {i} ({unique_cod}): {str(e)}")
                skipped += 1

            # Batch commit every 1000
            if i % 1000 == 0 and i > 0:
                await db.commit()

        await db.commit()
        return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}
