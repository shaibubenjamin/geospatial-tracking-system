from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.database import get_db
from app.models import QCFlag, User
from app.schemas import QCFlagOut, QCSummary
from app.routes.auth import get_current_user, get_current_user_optional
from app.services.qc_engine import get_qc_summary

router = APIRouter(prefix="/projects/{project_id}/qc", tags=["qc"])


@router.get("/summary", response_model=QCSummary)
async def qc_summary(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    data = await get_qc_summary(project_id, db)
    return QCSummary(**data)


@router.get("/flags", response_model=List[QCFlagOut])
async def list_flags(
    project_id: int,
    flag_type: Optional[str] = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    query = select(QCFlag).where(QCFlag.project_id == project_id)
    if flag_type:
        query = query.where(QCFlag.flag_type == flag_type)
    query = query.order_by(QCFlag.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/field-checks")
async def field_qc_checks(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Run four field data quality checks against the raw points table:
      1. Points whose LGA is not in the project's LGA reference (outside state).
      2. Points where the ward/LGA combo is invalid (outside stated LGA).
      3. Points where the settlement/ward combo is invalid (outside stated ward).
      4. Points collected before 06:00 or after 20:00 local time (time violations).
    """
    result = await db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM points_raw p
           WHERE p.project_id = :pid
           AND (p.lga_name IS NULL OR p.lga_name = ''
                OR p.lga_name NOT IN (SELECT lga_name FROM lgas WHERE project_id = :pid))
          ) AS outside_state,

          (SELECT COUNT(*) FROM points_raw p
           WHERE p.project_id = :pid
           AND p.lga_name IS NOT NULL AND p.ward_name IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM wards w
               WHERE w.project_id = :pid
               AND w.ward_name = p.ward_name
               AND w.lga_name = p.lga_name
           )
          ) AS outside_lga,

          (SELECT COUNT(*) FROM points_raw p
           WHERE p.project_id = :pid
           AND p.ward_name IS NOT NULL AND p.settlement_name IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM settlements s
               WHERE s.project_id = :pid
               AND s.settlement_name = p.settlement_name
               AND s.ward_name = p.ward_name
           )
          ) AS outside_ward,

          (SELECT COUNT(*) FROM points_raw
           WHERE project_id = :pid
           AND timestamp IS NOT NULL
           AND (EXTRACT(HOUR FROM timestamp) < 6 OR EXTRACT(HOUR FROM timestamp) >= 20)
          ) AS time_violations
    """), {"pid": project_id})
    row = result.fetchone()
    return {
        "outside_state":   int(row[0] or 0),
        "outside_lga":     int(row[1] or 0),
        "outside_ward":    int(row[2] or 0),
        "time_violations": int(row[3] or 0),
    }
