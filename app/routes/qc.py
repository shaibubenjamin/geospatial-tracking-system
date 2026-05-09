from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import QCFlag, User
from app.schemas import QCFlagOut, QCSummary
from app.routes.auth import get_current_user
from app.services.qc_engine import get_qc_summary

router = APIRouter(prefix="/projects/{project_id}/qc", tags=["qc"])


@router.get("/summary", response_model=QCSummary)
async def qc_summary(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
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
    _user: User = Depends(get_current_user),
):
    query = select(QCFlag).where(QCFlag.project_id == project_id)
    if flag_type:
        query = query.where(QCFlag.flag_type == flag_type)
    query = query.order_by(QCFlag.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
