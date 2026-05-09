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
            lgacode = str(row.get("lgacode_", row.get("LGACODE", row.get("lgacode", "")))).strip()
            lga_name = str(row.get("LGA_Name", row.get("lga_name", row.get("NAME", f"LGA_{i}")))).strip()

            if not lgacode:
                errors.append(f"Row {i}: missing lgacode")
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
            lgacode = str(row.get("lgacode_", row.get("LGACODE", row.get("lgacode", "")))).strip()
            wardcode = str(row.get("Wardcode", row.get("WARDCODE", row.get("wardcode", "")))).strip()
            ward_name = str(row.get("Ward_Name", row.get("ward_name", row.get("NAME", f"Ward_{i}")))).strip()
            lga_name = str(row.get("LGA_Name", row.get("lga_name", ""))).strip() or None

            if not wardcode:
                errors.append(f"Row {i}: missing wardcode")
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
            lgacode = str(row.get("lgacode_", row.get("LGACODE", row.get("lgacode", "")))).strip()
            wardcode = str(row.get("Wardcode", row.get("WARDCODE", row.get("wardcode", "")))).strip()
            unique_cod = str(row.get("unique_cod", row.get("UNIQUE_COD", row.get("unique_code", "")))).strip()
            settlement_name = str(row.get("Settlemen", row.get("settlement", row.get("NAME", f"Settlement_{i}")))).strip() or None
            lga_name = str(row.get("LGA_Name", row.get("lga_name", ""))).strip() or None
            ward_name = str(row.get("Ward_Name", row.get("ward_name", ""))).strip() or None

            if not unique_cod:
                errors.append(f"Row {i}: missing unique_cod")
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
            lgacode = str(row.get("lgacode_", row.get("LGACODE", row.get("lgacode", "")))).strip()
            wardcode = str(row.get("Wardcode", row.get("WARDCODE", row.get("wardcode", "")))).strip()
            unique_cod = str(row.get("unique_cod", row.get("UNIQUE_COD", row.get("unique_code", "")))).strip()
            settlement_name = str(row.get("Settlemen", row.get("settlement", row.get("NAME", "")))).strip() or None

            if not unique_cod:
                errors.append(f"Row {i}: missing unique_cod")
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
