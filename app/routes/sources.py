"""Data-source connections for the Data Sources gallery.

CommCare keeps its dedicated, fully-wired config under ``/api/sync`` and is
surfaced here only as a read-only "native" entry. These endpoints persist the
*configuration* for the additional gallery sources (Kobo, ODK, Google Drive) so
a connection is registered — there is deliberately no sync engine consuming
them yet (a later phase). Secret fields are Fernet-encrypted at rest (same key
as the CommCare password) and are never returned.
"""
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, SourceConnection, SyncConfig, GeoProject
from app.routes.auth import require_admin, require_superadmin
from app.services.crypto import encrypt, CryptoNotConfigured

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])

# Configurable (non-CommCare) source types and which of their fields are secret.
# Secrets are stripped out of the stored ``config`` JSON and encrypted instead,
# so a mislabelled field can never be persisted in the clear.
SECRET_FIELDS: Dict[str, set] = {
    "kobo":      {"api_token"},
    "odk":       {"password"},
    "dhis2":     {"password"},
    "surveycto": {"password"},
    "gsheets":   {"service_account_json"},
    "gdrive":    {"service_account_json"},
    "databricks": {"access_token"},
    "rest":      {"auth_token"},
}
ALLOWED_TYPES = set(SECRET_FIELDS.keys())


class ConnectionUpsert(BaseModel):
    display_name: Optional[str] = None
    config: Dict[str, Any] = {}
    credentials: Dict[str, Any] = {}   # secret fields; encrypted, never returned


def _split(source_type: str, config: Dict[str, Any], creds: Dict[str, Any]):
    """Keep secrets out of ``config`` regardless of how the client labelled them."""
    secret_keys = SECRET_FIELDS.get(source_type, set())
    clean_config = {k: v for k, v in (config or {}).items() if k not in secret_keys}
    clean_creds = {k: v for k, v in (creds or {}).items() if k in secret_keys and v not in (None, "")}
    return clean_config, clean_creds


@router.get("/connections")
async def list_connections(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_admin),
):
    """Connections configured for a project: CommCare (native, from sync_config)
    plus any saved gallery sources. Secrets are never included."""
    out = []
    cc = (await db.execute(
        select(SyncConfig).where(SyncConfig.project_id == project_id)
    )).scalar_one_or_none()
    if cc:
        out.append({
            "source_type": "commcare",
            "native": True,
            "display_name": "CommCare HQ",
            "status": cc.last_status or "configured",
            "has_credentials": bool(cc.commcare_password_encrypted),
            "config": {
                "base_url": cc.commcare_base_url,
                "app_slug": cc.commcare_app_slug,
                "username": cc.commcare_username,
            },
            "last_synced_at": cc.last_synced_at.isoformat() if cc.last_synced_at else None,
        })
    rows = (await db.execute(
        select(SourceConnection).where(SourceConnection.project_id == project_id)
    )).scalars().all()
    for r in rows:
        out.append({
            "source_type": r.source_type,
            "native": False,
            "display_name": r.display_name,
            "status": r.status,
            "has_credentials": bool(r.credentials_encrypted),
            "config": r.config or {},
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return {"connections": out}


@router.get("/connections/{source_type}")
async def get_connection(
    source_type: str,
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_admin),
):
    """Non-secret config for one gallery source, for pre-filling its form.
    ``has_credentials`` lets the UI show 'leave blank to keep' for secrets."""
    if source_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unknown or non-configurable source type")
    r = (await db.execute(
        select(SourceConnection).where(
            SourceConnection.project_id == project_id,
            SourceConnection.source_type == source_type,
        )
    )).scalar_one_or_none()
    if not r:
        return {"source_type": source_type, "exists": False, "config": {}, "has_credentials": False}
    return {
        "source_type": source_type,
        "exists": True,
        "display_name": r.display_name,
        "config": r.config or {},
        "has_credentials": bool(r.credentials_encrypted),
        "status": r.status,
    }


@router.put("/connections/{source_type}")
async def upsert_connection(
    source_type: str,
    project_id: int,
    data: ConnectionUpsert,
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_superadmin),
):
    """Create/update a gallery source's saved config. Secrets are encrypted;
    omitting a secret on update keeps the existing one. No sync is run."""
    if source_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unknown or non-configurable source type")
    proj = (await db.execute(
        select(GeoProject).where(GeoProject.id == project_id)
    )).scalar_one_or_none()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    config, creds = _split(source_type, data.config, data.credentials)
    enc = None
    if creds:
        try:
            enc = encrypt(json.dumps(creds))
        except CryptoNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))

    r = (await db.execute(
        select(SourceConnection).where(
            SourceConnection.project_id == project_id,
            SourceConnection.source_type == source_type,
        )
    )).scalar_one_or_none()
    if r is None:
        r = SourceConnection(
            project_id=project_id,
            source_type=source_type,
            display_name=data.display_name,
            config=config,
            credentials_encrypted=enc,
            status="configured",
        )
        db.add(r)
    else:
        if data.display_name is not None:
            r.display_name = data.display_name
        r.config = config
        if enc is not None:           # only replace secrets when supplied
            r.credentials_encrypted = enc
        r.status = "configured"
    await db.commit()
    await db.refresh(r)
    return {"ok": True, "source_type": source_type, "status": r.status, "has_credentials": bool(r.credentials_encrypted)}


@router.delete("/connections/{source_type}")
async def delete_connection(
    source_type: str,
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_superadmin),
):
    """Remove a gallery source's saved config (CommCare can't be deleted here)."""
    if source_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Unknown or non-configurable source type")
    r = (await db.execute(
        select(SourceConnection).where(
            SourceConnection.project_id == project_id,
            SourceConnection.source_type == source_type,
        )
    )).scalar_one_or_none()
    if r:
        await db.delete(r)
        await db.commit()
    return {"ok": True}
