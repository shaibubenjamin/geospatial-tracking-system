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
