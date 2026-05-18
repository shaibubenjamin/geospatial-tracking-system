from typing import Optional, List
import csv, io
from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.routes.auth import get_current_user, get_current_user_optional
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
    _user: Optional[User] = Depends(get_current_user_optional),
):
    return await get_project_summary(project_id, db)


@router.get("/lgas")
async def lga_metrics(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    return await get_lga_metrics(project_id, db)


@router.get("/wards")
async def ward_metrics(
    project_id: int,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    return await get_ward_metrics(project_id, db, lgacode=lgacode)


@router.get("/settlements")
async def settlement_metrics(
    project_id: int,
    wardcode: Optional[str] = None,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    return await get_settlement_metrics(project_id, db, wardcode=wardcode, lgacode=lgacode)


@router.get("/timeline")
async def coverage_timeline(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
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
    _user: Optional[User] = Depends(get_current_user_optional),
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
    _user: Optional[User] = Depends(get_current_user_optional),
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


def _csv_response(rows: List[dict], filename: str) -> StreamingResponse:
    if not rows:
        rows = [{}]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/lgas/csv")
async def lga_metrics_csv(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    data = await get_lga_metrics(project_id, db)
    rows = [
        {
            "lga_name": r["lga_name"],
            "lgacode": r["lgacode"],
            "total_settlements": r["total_settlements"],
            "visited_settlements": r["visited_settlements"],
            "visitation_pct": r["visitation_pct"],
            "total_grids": r["total_grids"],
            "visited_grids": r["visited_grids"],
            "point_count": r["point_count"],
        }
        for r in data
    ]
    return _csv_response(rows, f"lga_coverage_project{project_id}.csv")


@router.get("/wards/csv")
async def ward_metrics_csv(
    project_id: int,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    data = await get_ward_metrics(project_id, db, lgacode=lgacode)
    rows = [
        {
            "ward_name": r["ward_name"],
            "wardcode": r["wardcode"],
            "lga_name": r["lga_name"],
            "lgacode": r["lgacode"],
            "total_settlements": r["total_settlements"],
            "visited_settlements": r["visited_settlements"],
            "visitation_pct": r["visitation_pct"],
            "point_count": r["point_count"],
        }
        for r in data
    ]
    lga_tag = f"_lga{lgacode}" if lgacode else ""
    return _csv_response(rows, f"ward_coverage_project{project_id}{lga_tag}.csv")


@router.get("/settlements/csv")
async def settlement_metrics_csv(
    project_id: int,
    wardcode: Optional[str] = None,
    lgacode: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    data = await get_settlement_metrics(project_id, db, wardcode=wardcode, lgacode=lgacode)
    rows = [
        {
            "settlement_name": r["settlement_name"],
            "unique_cod": r["unique_cod"],
            "ward_name": r["ward_name"],
            "wardcode": r["wardcode"],
            "lga_name": r["lga_name"],
            "lgacode": r["lgacode"],
            "is_visited": r["is_visited"],
            "total_grids": r["total_grids"],
            "visited_grids": r["visited_grids"],
            "completeness_pct": r["completeness_pct"],
            "point_count": r["point_count"],
        }
        for r in data
    ]
    tag = f"_ward{wardcode}" if wardcode else (f"_lga{lgacode}" if lgacode else "")
    return _csv_response(rows, f"settlement_coverage_project{project_id}{tag}.csv")
