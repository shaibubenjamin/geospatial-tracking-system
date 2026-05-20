from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import GeoProject, User
from app.schemas import ProjectCreate, ProjectUpdate, ProjectOut
from app.routes.auth import get_current_user, get_current_user_optional, require_admin, require_superadmin

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=List[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    result = await db.execute(select(GeoProject).order_by(GeoProject.name))
    return result.scalars().all()


@router.post("", response_model=ProjectOut)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    existing = await db.execute(select(GeoProject).where(GeoProject.slug == data.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Project slug already exists")

    project = GeoProject(
        name=data.name,
        slug=data.slug,
        description=data.description or "",
        state_name=data.state_name,
        round_number=data.round_number,
        campaign_start_date=data.campaign_start_date,
        campaign_end_date=data.campaign_end_date,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    if data.state_name is not None:
        project.state_name = data.state_name
    if data.round_number is not None:
        project.round_number = data.round_number
    if data.campaign_start_date is not None:
        project.campaign_start_date = data.campaign_start_date
    if data.campaign_end_date is not None:
        project.campaign_end_date = data.campaign_end_date
    if data.is_active is not None:
        # Deactivate all others if activating this one
        if data.is_active:
            await db.execute(
                __import__("sqlalchemy").text(
                    "UPDATE geo_projects SET is_active = FALSE WHERE id != :pid"
                ).bindparams(pid=project_id)
            )
        project.is_active = data.is_active

    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.delete(project)
    await db.commit()
    return {"message": "Project deleted"}
