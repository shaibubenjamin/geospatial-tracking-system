import re
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.database import get_db
from app.models import GeoProject, User
from app.schemas import ProjectCreate, ProjectUpdate, ProjectOut
from app.routes.auth import get_current_user, get_current_user_optional, require_admin, require_superadmin, allowed_states_of

router = APIRouter(prefix="/projects", tags=["projects"])


def _in_window(p, today) -> bool:
    """True when a round is in its campaign window: started and not yet ended.

    A paused round is still in-window (it's the current campaign, just halted) —
    pausing stops auto-sync and the "live" tag, not visibility. Used to scope
    what non-admins (LGA logins + public) may see: only the active campaign.
    """
    s = getattr(p, "campaign_start_date", None)
    e = getattr(p, "campaign_end_date", None)
    return bool(s and s <= today and (e is None or e >= today))


def _slugify(name: str) -> str:
    """Lowercase, drop non-alphanumerics, collapse to single dashes."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "project"


async def _unique_slug(base: str, db: AsyncSession) -> str:
    """Return ``base`` if free, else ``base-2``, ``base-3``, …"""
    candidate = base
    n = 2
    while True:
        existing = await db.execute(select(GeoProject).where(GeoProject.slug == candidate))
        if not existing.scalar_one_or_none():
            return candidate
        candidate = f"{base}-{n}"
        n += 1


@router.get("", response_model=List[ProjectOut])
async def list_projects(
    dashboard: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: Optional[User] = Depends(get_current_user_optional),
):
    # ``?dashboard=true`` → only rounds flagged "Show on dashboard" (the
    # dashboard project switcher). Without it, every project is returned (the
    # admin Projects management table needs to see and manage all of them).
    # Active project first so default selections land on the live round.
    # Within the same state, newest round (highest round_number) before
    # older ones. NULL round_numbers sort last.
    result = await db.execute(
        select(GeoProject).order_by(
            GeoProject.is_active.desc(),
            GeoProject.state_name.asc(),
            GeoProject.round_number.desc().nullslast(),
            GeoProject.id.asc(),
        )
    )
    projects = result.scalars().all()
    is_admin = _user is not None and (
        bool(getattr(_user, "is_superadmin", False)) or bool(getattr(_user, "is_admin", False))
    )

    # Audience + state scope.
    if _user is None:
        # Anonymous (public dashboard) → only projects opted in as public.
        projects = [p for p in projects if bool(getattr(p, "is_public", False))]
    else:
        # A non-(super)admin only sees their assigned state(s).
        allowed = allowed_states_of(_user)
        if allowed is not None:
            projects = [p for p in projects if (p.state_name or "").strip().lower() in allowed]

    if is_admin:
        # Admins/superadmins see every round. ``dashboard`` narrows to the rounds
        # they've chosen to surface in the switcher (their view preference).
        if dashboard:
            projects = [p for p in projects if bool(getattr(p, "show_on_dashboard", False))]
    else:
        # Everyone else (LGA logins + public) only ever sees the IN-WINDOW
        # campaign: started and not yet ended. Old/not-started/ended rounds are
        # hidden — "only the active campaign can be seen".
        today = date.today()
        projects = [p for p in projects if _in_window(p, today)]
    return projects


@router.post("", response_model=ProjectOut)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    # Slug: caller-supplied or server-derived from name. Either way, we
    # disambiguate against existing rows by appending -2, -3 … as needed
    # rather than failing the create.
    if data.slug:
        slug = await _unique_slug(_slugify(data.slug), db)
    else:
        slug = await _unique_slug(_slugify(data.name), db)

    project = GeoProject(
        name=data.name,
        slug=slug,
        description=data.description or "",
        state_name=data.state_name,
        round_number=data.round_number,
        campaign_start_date=data.campaign_start_date,
        campaign_end_date=data.campaign_end_date,
        show_on_dashboard=bool(data.show_on_dashboard),
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
    if data.campaign_paused is not None:
        project.campaign_paused = data.campaign_paused
    if data.is_public is not None:
        project.is_public = data.is_public
    if data.is_active is not None:
        # Deactivate all others if activating this one
        if data.is_active:
            await db.execute(
                __import__("sqlalchemy").text(
                    "UPDATE geo_projects SET is_active = FALSE WHERE id != :pid"
                ).bindparams(pid=project_id)
            )
        project.is_active = data.is_active
    if data.show_on_dashboard is not None:
        # Additive: showing a round on the dashboard does NOT hide the others.
        project.show_on_dashboard = data.show_on_dashboard
        # If a round is removed from the dashboard while it's the default one
        # the dashboard opens to (is_active), promote another shown round so
        # there's still a sensible default.
        if not data.show_on_dashboard and project.is_active:
            project.is_active = False
            other = (await db.execute(text(
                "SELECT id FROM geo_projects "
                "WHERE COALESCE(show_on_dashboard, FALSE) = TRUE AND id != :pid "
                "ORDER BY round_number DESC NULLS LAST, id DESC LIMIT 1"
            ).bindparams(pid=project_id))).fetchone()
            if other:
                await db.execute(text(
                    "UPDATE geo_projects SET is_active = TRUE WHERE id = :oid"
                ).bindparams(oid=other[0]))

    await db.commit()
    await db.refresh(project)
    return project


@router.post("/{project_id}/start-campaign", response_model=ProjectOut)
async def start_campaign(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Start a round's campaign.

    Stamps today as the start date, clears any stale end date (so a round that
    was mis-stamped as ended reads Running again), and shows the round on the
    dashboard. Auto-sync picks it up automatically because it now falls inside
    the running window. This is separate from "Show on dashboard" (view focus)
    and from Public/Private (visibility).
    """
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    today = date.today()
    project.campaign_start_date = today
    # Drop a stale/past end date so the round is Running, not Ended.
    if project.campaign_end_date is not None and project.campaign_end_date <= today:
        project.campaign_end_date = None
    # Starting a campaign shows it on the dashboard and focuses on it (the
    # single default round the dashboard opens to). Showing it does not hide
    # any other rounds already on the dashboard.
    project.show_on_dashboard = True
    # Starting (or restarting) clears any prior pause so the round is Running.
    project.campaign_paused = False
    await db.execute(
        text("UPDATE geo_projects SET is_active = FALSE WHERE id != :pid").bindparams(pid=project_id)
    )
    project.is_active = True
    await db.commit()
    await db.refresh(project)
    return project


@router.post("/{project_id}/end-campaign", response_model=ProjectOut)
async def end_campaign(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """End a round's campaign.

    Stamps today as the end date. Auto-sync stops because the round is no longer
    in the running window. The round stays on the dashboard so results remain
    viewable after the round wraps up.
    """
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.campaign_end_date = date.today()
    await db.commit()
    await db.refresh(project)
    return project


@router.post("/{project_id}/pause-campaign", response_model=ProjectOut)
async def pause_campaign(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Pause a running campaign — a temporary, resumable halt.

    The round stays in-window (start reached, not ended), so it remains the
    current campaign, but auto-sync stops and it reads "Paused" instead of
    "live". Resume clears the flag. Distinct from End (which finishes the round).
    """
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.campaign_paused = True
    await db.commit()
    await db.refresh(project)
    return project


@router.post("/{project_id}/resume-campaign", response_model=ProjectOut)
async def resume_campaign(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Resume a paused campaign — clears the pause so it's Running again and
    auto-sync picks it back up (it's still inside the campaign window)."""
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.campaign_paused = False
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
