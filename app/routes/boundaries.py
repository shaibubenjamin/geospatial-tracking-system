import asyncio
import io
import os
import tempfile
import zipfile
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import GeoProject, User
from app.routes.auth import require_admin, require_superadmin
from app.services.boundary_importer import (
    import_lga_shapefile,
    import_ward_shapefile,
    import_settlement_shapefile,
    import_grid_shapefile,
)
from app.services.commcare_sync import recompute_spatial_for_project
from app.services.spatial_engine import (
    get_lga_geojson,
    get_ward_geojson,
    get_settlement_geojson,
    get_grid_geojson,
)

router = APIRouter(prefix="/projects/{project_id}/boundaries", tags=["boundaries"])


async def _get_project(project_id: int, db: AsyncSession) -> GeoProject:
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/lga")
async def upload_lga_boundary(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    await _get_project(project_id, db)
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a ZIP file containing the shapefile")
    data = await file.read()
    result = await import_lga_shapefile(data, project_id, db)
    return {"message": "LGA boundaries uploaded", **result}


@router.post("/ward")
async def upload_ward_boundary(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    await _get_project(project_id, db)
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a ZIP file containing the shapefile")
    data = await file.read()
    result = await import_ward_shapefile(data, project_id, db)
    return {"message": "Ward boundaries uploaded", **result}


@router.post("/settlement")
async def upload_settlement_boundary(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    await _get_project(project_id, db)
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a ZIP file containing the shapefile")
    data = await file.read()
    result = await import_settlement_shapefile(data, project_id, db)
    return {"message": "Settlement boundaries uploaded", **result}


@router.post("/grid")
async def upload_grid_boundary(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    await _get_project(project_id, db)
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a ZIP file containing the shapefile")
    data = await file.read()
    result = await import_grid_shapefile(data, project_id, db)
    return {"message": "Grid boundaries uploaded", **result}


# ── Bundle upload: one ZIP, all four levels ───────────────────────────────────


# Shapefile companion extensions that should travel together with the .shp.
# .shp.xml is included for ESRI metadata; pyshp / pyproj don't need it but
# keeping it preserves the original sidecar.
_SHAPEFILE_COMPANION_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg",
                             ".sbn", ".sbx", ".shp.xml")


def _classify_shp_basename(basename: str) -> Optional[str]:
    """Return the boundary level this .shp belongs to, or None.

    Matched case-insensitively against substrings; the order of checks is
    intentional — "settlement" must beat "ward"/"lga" if a name contains
    multiple tokens, "grid"/"gridded" beats "ward"/"lga" similarly.
    """
    name = basename.lower()
    if "settlement" in name:
        return "settlement"
    if "grid" in name:           # matches "grid", "grids", "gridded"
        return "grid"
    if "ward" in name:
        return "ward"
    if "lga" in name:
        return "lga"
    return None


def _zip_shapefile_set(shp_path: str) -> bytes:
    """Re-zip a shapefile + every companion sidecar at ``shp_path``'s basename.

    The downstream importers (``import_lga_shapefile`` etc.) expect raw ZIP
    bytes that contain at least the .shp/.shx/.dbf/.prj quartet. We package
    everything we can find at the same basename so any optional sidecar
    (e.g. .cpg encoding hint) survives.
    """
    base_no_ext = os.path.splitext(shp_path)[0]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zw:
        for ext in _SHAPEFILE_COMPANION_EXTS:
            companion = base_no_ext + ext
            if os.path.exists(companion):
                zw.write(companion, arcname=os.path.basename(companion))
    buf.seek(0)
    return buf.getvalue()


@router.post("/bundle")
async def upload_boundary_bundle(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Upload all four boundary levels in a single ZIP.

    The ZIP must contain shapefile sets for the four levels — LGA, Ward,
    Settlement, Grid. Each set is identified case-insensitively by the
    basename of its ``.shp`` file (e.g. ``lga.shp`` / ``ward.shp`` /
    ``Settlement.shp`` / ``Gridded.shp`` all work). Companion sidecars
    (``.shx``, ``.dbf``, ``.prj``, optionally ``.cpg``/``.sbn``/``.sbx``/
    ``.shp.xml``) ride along under the same basename.

    Layers not found in the bundle are skipped (not an error). Each level
    found is fed into the same per-level importer used by the legacy
    ``/lga``, ``/ward``, ``/settlement``, ``/grid`` endpoints, so the
    behaviour and validation is unchanged — this endpoint is just a
    convenience wrapper that splits one upload into four.
    """
    await _get_project(project_id, db)
    # Size + extension guardrails. ZIP only for the bundle endpoint;
    # 100 MB ceiling matches the helper default.
    from app.services.uploads import validate_upload, read_with_cap
    validate_upload(file, allowed={".zip"})
    raw = await read_with_cap(file)
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Could not read the uploaded file as a ZIP")

        # Find every .shp inside (recursively — bundles may wrap a folder)
        shp_paths: list[str] = []
        for root, _, files in os.walk(tmp_dir):
            for f in files:
                if f.lower().endswith(".shp"):
                    shp_paths.append(os.path.join(root, f))
        if not shp_paths:
            raise HTTPException(400, "No .shp files found inside the ZIP")

        # Classify each .shp into a level. First-match wins per level so a
        # weirdly-named extra file can't override the canonical one.
        level_to_shp: dict[str, str] = {}
        unclassified: list[str] = []
        for shp in shp_paths:
            lvl = _classify_shp_basename(os.path.basename(shp))
            if lvl is None:
                unclassified.append(os.path.basename(shp))
                continue
            level_to_shp.setdefault(lvl, shp)

        importers = {
            "lga":        import_lga_shapefile,
            "ward":       import_ward_shapefile,
            "settlement": import_settlement_shapefile,
            "grid":       import_grid_shapefile,
        }
        # Process in dependency order — ward import may reference lgas; settlement
        # may reference wards; grid may reference settlements (the importers
        # don't strictly require this, but ordering keeps logs intuitive).
        order = ["lga", "ward", "settlement", "grid"]

        results: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for lvl in order:
            if lvl not in level_to_shp:
                continue
            zip_bytes = _zip_shapefile_set(level_to_shp[lvl])
            try:
                results[lvl] = await importers[lvl](zip_bytes, project_id, db)
            except Exception as e:  # noqa: BLE001 — surface per-level failure to UI
                errors[lvl] = str(e)[:300]

        levels_found   = list(results.keys())
        levels_missing = [lvl for lvl in order if lvl not in level_to_shp]
        return {
            "message":          "Boundary bundle processed",
            "levels_uploaded":  levels_found,
            "levels_missing":   levels_missing,
            "levels_failed":    list(errors.keys()),
            "unclassified_shp": unclassified,
            "results":          results,
            "errors":           errors,
        }


# ─── Maintenance: rebuild spatial QC + settlement_analytics ───────────────────


@router.post("/recompute")
async def recompute_spatial(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Recompute spatial QC flags + settlement_analytics for this project.

    Use this after importing or re-importing boundaries when household forms
    are already in place — without it, the spatial flags reflect a stale
    boundary state (everything flagged "outside LGA") and the Geographic
    View shows 0% completeness for every settlement.

    The operation is run in a thread pool because it uses the sync psycopg2
    connection (matching the sync code's transaction semantics) and can take
    30–60 seconds on a state-sized grid. The HTTP call blocks until it
    finishes — there's no progress wire — but it's bounded by the SQL.
    """
    await _get_project(project_id, db)
    try:
        result = await asyncio.to_thread(recompute_spatial_for_project, project_id)
    except Exception as e:  # noqa: BLE001 — surface SQL/connect errors to the operator
        raise HTTPException(500, f"Spatial recompute failed: {e}")
    return {"message": "Spatial recompute completed", **result}


# ─── GeoJSON endpoints ────────────────────────────────────────────────────────

@router.get("/lga/geojson")
async def lga_geojson(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await get_lga_geojson(project_id, db)


@router.get("/ward/geojson")
async def ward_geojson(
    project_id: int,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_ward_geojson(project_id, db, lgacode=lgacode)


@router.get("/settlement/geojson")
async def settlement_geojson(
    project_id: int,
    lgacode: Optional[str] = None,
    wardcode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_settlement_geojson(project_id, db, lgacode=lgacode, wardcode=wardcode)


@router.get("/grid/geojson")
async def grid_geojson(
    project_id: int,
    unique_cod: str,
    db: AsyncSession = Depends(get_db),
):
    return await get_grid_geojson(project_id, db, unique_cod=unique_cod)
