"""Sync configuration & CommCare sync trigger endpoints.

All endpoints are superadmin-only. Passwords are encrypted at rest using
``app.services.crypto`` (Fernet) and never returned by the GET endpoint.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.database import get_db
from app.models import User, SyncConfig, GeoProject
from app.routes.auth import require_superadmin
from app.services.crypto import encrypt, CryptoNotConfigured
from app.services.commcare_sync import run_sync, test_connection

router = APIRouter(prefix="/sync", tags=["sync"])


class FormEntry(BaseModel):
    set_name: str
    form_id: str


class SyncConfigIn(BaseModel):
    commcare_base_url: Optional[str] = None
    commcare_app_slug: Optional[str] = None
    commcare_username: Optional[str] = None
    commcare_password: Optional[str] = None   # set to None to keep existing; '' to clear
    form_ids: Optional[List[FormEntry]] = None


class SyncConfigOut(BaseModel):
    project_id: int
    commcare_base_url: Optional[str]
    commcare_app_slug: Optional[str]
    commcare_username: Optional[str]
    has_password: bool
    form_ids: List[FormEntry]
    last_synced_at: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]
    last_row_count: Optional[int]
    last_progress_step: Optional[int] = None
    last_progress_total: Optional[int] = None


async def _get_config(project_id: int, db: AsyncSession) -> Optional[SyncConfig]:
    res = await db.execute(select(SyncConfig).where(SyncConfig.project_id == project_id))
    return res.scalar_one_or_none()


@router.get("/config", response_model=SyncConfigOut)
async def get_config(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Read sync config for a project. Password never returned, only its presence."""
    cfg = await _get_config(project_id, db)
    if not cfg:
        # Return an empty config so the UI can render a "first-time setup" form
        return SyncConfigOut(
            project_id=project_id,
            commcare_base_url="https://www.commcarehq.org",
            commcare_app_slug=None,
            commcare_username=None,
            has_password=False,
            form_ids=[],
            last_synced_at=None, last_status=None, last_error=None, last_row_count=None,
        )
    return SyncConfigOut(
        project_id=cfg.project_id,
        commcare_base_url=cfg.commcare_base_url,
        commcare_app_slug=cfg.commcare_app_slug,
        commcare_username=cfg.commcare_username,
        has_password=bool(cfg.commcare_password_encrypted),
        form_ids=[FormEntry(**e) for e in (cfg.form_ids or [])],
        last_synced_at=cfg.last_synced_at,
        last_status=cfg.last_status,
        last_error=cfg.last_error,
        last_row_count=cfg.last_row_count,
        last_progress_step=cfg.last_progress_step,
        last_progress_total=cfg.last_progress_total,
    )


@router.get("/history")
async def get_history(
    project_id: int,
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Most-recent N sync runs for this project (default 5)."""
    limit = max(1, min(limit, 50))  # safety cap
    res = await db.execute(text("""
        SELECT id, started_at, ended_at, status, rows_fetched, error_message
        FROM sync_history
        WHERE project_id = :pid
        ORDER BY started_at DESC
        LIMIT :limit
    """), {"pid": project_id, "limit": limit})
    return [dict(r._mapping) for r in res.fetchall()]


@router.put("/config")
async def put_config(
    project_id: int,
    data: SyncConfigIn,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Upsert sync config for a project. Sending `commcare_password=null` keeps
    the existing password; sending `commcare_password=""` clears it."""
    # Verify the project exists
    proj_res = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    if not proj_res.scalar_one_or_none():
        raise HTTPException(404, "Project not found")

    cfg = await _get_config(project_id, db)
    if cfg is None:
        cfg = SyncConfig(project_id=project_id, commcare_base_url="https://www.commcarehq.org", form_ids=[])
        db.add(cfg)

    if data.commcare_base_url is not None:
        cfg.commcare_base_url = data.commcare_base_url
    if data.commcare_app_slug is not None:
        cfg.commcare_app_slug = data.commcare_app_slug
    if data.commcare_username is not None:
        cfg.commcare_username = data.commcare_username
    if data.commcare_password is not None:
        if data.commcare_password == "":
            cfg.commcare_password_encrypted = None
        else:
            try:
                cfg.commcare_password_encrypted = encrypt(data.commcare_password)
            except CryptoNotConfigured as e:
                raise HTTPException(500, str(e))
    if data.form_ids is not None:
        cfg.form_ids = [e.model_dump() for e in data.form_ids]

    await db.commit()
    return {"ok": True}


@router.post("/test")
async def test_sync(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Hit one feed with the saved creds to confirm CommCare accepts them."""
    cfg = await _get_config(project_id, db)
    if not cfg or not cfg.commcare_password_encrypted:
        raise HTTPException(400, "No credentials configured for this project")
    if not cfg.form_ids:
        raise HTTPException(400, "No form IDs configured")
    from app.services.crypto import decrypt
    try:
        password = decrypt(cfg.commcare_password_encrypted)
    except Exception as e:
        raise HTTPException(500, f"Cannot decrypt password: {e}")
    first_form = cfg.form_ids[0].get("form_id")
    return await test_connection(
        cfg.commcare_base_url,
        cfg.commcare_app_slug,
        cfg.commcare_username,
        password,
        first_form,
    )


@router.post("/run")
async def trigger_sync(
    project_id: int,
    _super: User = Depends(require_superadmin),
):
    """Run a full incremental sync for the project (synchronous; large pulls
    may take a few minutes)."""
    try:
        return await run_sync(project_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Sync failed: {e}")


@router.get("/feeds")
async def list_feed_state(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Return the current watermark for each (form, record_type) in this project."""
    res = await db.execute(text("""
        SELECT form_id, record_type, last_received_on, last_synced_at, last_row_count
        FROM sync_feed_state
        WHERE project_id = :pid
        ORDER BY form_id, record_type
    """), {"pid": project_id})
    return [dict(r._mapping) for r in res.fetchall()]
