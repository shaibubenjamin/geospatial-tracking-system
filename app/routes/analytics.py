from typing import Optional, List
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.routes.auth import get_current_user
from app.services.aggregation_engine import (
    get_lga_metrics,
    get_ward_metrics,
    get_settlement_metrics,
    get_project_summary,
)
from app.services.spatial_engine import (
    compute_settlement_analytics,
    get_coverage_timeline,
    get_points_geojson,
)
from app.services.qc_engine import run_stacked_point_check

router = APIRouter(prefix="/projects/{project_id}/analytics", tags=["analytics"])


@router.get("/summary")
async def project_summary(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_project_summary(project_id, db)


@router.get("/lgas")
async def lga_metrics(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_lga_metrics(project_id, db)


@router.get("/wards")
async def ward_metrics(
    project_id: int,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_ward_metrics(project_id, db, lgacode=lgacode)


@router.get("/settlements")
async def settlement_metrics(
    project_id: int,
    wardcode: Optional[str] = None,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_settlement_metrics(project_id, db, wardcode=wardcode, lgacode=lgacode)


@router.get("/timeline")
async def coverage_timeline(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_coverage_timeline(project_id, db)


@router.get("/points/geojson")
async def points_geojson(
    project_id: int,
    unique_cod: Optional[str] = None,
    wardcode: Optional[str] = None,
    lgacode: Optional[str] = None,
    limit: int = 5000,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return await get_points_geojson(
        project_id, db,
        unique_cod=unique_cod,
        wardcode=wardcode,
        lgacode=lgacode,
        limit=limit,
    )


@router.post("/compute")
async def trigger_compute(
    project_id: int,
    background_tasks: BackgroundTasks,
    full_recompute: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Trigger full or incremental analytics computation."""
    unique_cods = None  # full recompute

    background_tasks.add_task(
        _run_full_compute, project_id=project_id
    )
    return {"message": "Computation triggered", "full_recompute": True}


async def _run_full_compute(project_id: int):
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await compute_settlement_analytics(project_id, None, db)
        await run_stacked_point_check(project_id, db)
