"""
app/routes/reports.py — User-submitted concerns / issue reports.

A lightweight "Report a Concern" inbox. Surfaced as a floating widget on the
dashboard so anyone (public visitor, LGA viewer, admin) can flag a problem,
data discrepancy, or feature request without leaving the page.

Submit endpoint is unauthenticated by design — public viewers need it. We
record whatever auth context we have (username, role) for triage but never
require a login.
"""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.routes.auth import get_current_user_optional, require_admin
from app.services.notifier import notify_new_report


router = APIRouter(prefix="/reports", tags=["reports"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class ReportIn(BaseModel):
    category: str = Field("general", max_length=32)   # 'bug' | 'data-issue' | 'feature' | 'general'
    subject:  Optional[str] = Field(None, max_length=200)
    message:  str = Field(..., min_length=5, max_length=4000)
    reporter_email: Optional[str] = Field(None, max_length=200)
    reporter_name:  Optional[str] = Field(None, max_length=100)
    page_url: Optional[str] = Field(None, max_length=500)


class ReportOut(BaseModel):
    id: int
    category: str
    subject: Optional[str]
    message: str
    reporter_email: Optional[str]
    reporter_name: Optional[str]
    reporter_role: Optional[str]
    page_url: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────────────────────────
# Ensure the table exists. Run lazily on first POST so deployments that
# haven't run the migration yet don't fail with "relation does not exist".
# CREATE TABLE IF NOT EXISTS is idempotent and harmless.
# ─────────────────────────────────────────────────────────────────────────────

# asyncpg refuses multi-statement strings ("cannot insert multiple commands
# into a prepared statement"), so each DDL is issued separately. All
# CREATE … IF NOT EXISTS so this is idempotent across restarts.
_TABLE_DDL_STMTS = (
    """
    CREATE TABLE IF NOT EXISTS user_reports (
        id              SERIAL PRIMARY KEY,
        category        TEXT NOT NULL DEFAULT 'general',
        subject         TEXT,
        message         TEXT NOT NULL,
        reporter_email  TEXT,
        reporter_name   TEXT,
        reporter_role   TEXT,
        page_url        TEXT,
        user_agent      TEXT,
        status          TEXT DEFAULT 'open',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_reports_created_at ON user_reports (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_user_reports_status     ON user_reports (status)",
)

_table_ensured = False


async def _ensure_table(db: AsyncSession) -> None:
    global _table_ensured
    if _table_ensured:
        return
    for stmt in _TABLE_DDL_STMTS:
        try:
            await db.execute(text(stmt))
            await db.commit()
        except Exception:
            await db.rollback()
            # If the app role lacks DDL, surface a clear error rather than 500.
            raise HTTPException(
                status_code=503,
                detail="Reports inbox is not initialised on this database. Ask an administrator to create the user_reports table.",
            )
    _table_ensured = True


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/reports — anyone can file a report
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=ReportOut)
async def create_report(
    body: ReportIn,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    await _ensure_table(db)

    # Snapshot the reporter context so admins triaging this later have something
    # to work with. None of these are required at submit time.
    role = None
    name = body.reporter_name
    email = body.reporter_email
    if user is not None:
        role  = getattr(user, "access_level", None) or ("admin" if getattr(user, "is_admin", False) else "viewer")
        name  = name  or getattr(user, "username", None)
        email = email or getattr(user, "email", None)
    else:
        role = "public"

    ua = request.headers.get("user-agent")
    # Set created_at explicitly so the value is always present in RETURNING
    # — relying on the column DEFAULT was flaky in environments where the
    # table was created without DEFAULT NOW() persisted.
    res = await db.execute(
        text("""
            INSERT INTO user_reports (
                category, subject, message,
                reporter_email, reporter_name, reporter_role,
                page_url, user_agent, status, created_at
            ) VALUES (
                :category, :subject, :message,
                :reporter_email, :reporter_name, :reporter_role,
                :page_url, :user_agent, 'open', NOW()
            )
            RETURNING id, category, subject, message, reporter_email, reporter_name,
                      reporter_role, page_url, status, created_at
        """),
        {
            "category":       (body.category or "general").lower().strip()[:32],
            "subject":        (body.subject or "").strip()[:200] or None,
            "message":        body.message.strip()[:4000],
            "reporter_email": (email or None) and email.strip()[:200],
            "reporter_name":  (name  or None) and name.strip()[:100],
            "reporter_role":  role,
            "page_url":       (body.page_url or "")[:500] or None,
            "user_agent":     (ua or "")[:500] or None,
        },
    )
    row = res.fetchone()
    await db.commit()
    out = ReportOut(
        id=row[0], category=row[1], subject=row[2], message=row[3],
        reporter_email=row[4], reporter_name=row[5], reporter_role=row[6],
        page_url=row[7], status=row[8], created_at=row[9],
    )
    # Fire-and-forget email notification to the programme owners. If SMTP
    # isn't configured (or the send blows up) the notifier logs a warning
    # and swallows the error — the report is already persisted to Postgres
    # and the HTTP path must stay healthy.
    background_tasks.add_task(notify_new_report, out.model_dump())
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/reports — admin-only triage view
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=List[ReportOut])
async def list_reports(
    status: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    await _ensure_table(db)
    params: dict = {"limit": min(max(limit, 1), 500)}
    where = ""
    if status:
        where = "WHERE status = :status"
        params["status"] = status
    res = await db.execute(
        text(f"""
            SELECT id, category, subject, message, reporter_email, reporter_name,
                   reporter_role, page_url, status, created_at
            FROM user_reports {where}
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        params,
    )
    return [
        ReportOut(
            id=r[0], category=r[1], subject=r[2], message=r[3],
            reporter_email=r[4], reporter_name=r[5], reporter_role=r[6],
            page_url=r[7], status=r[8], created_at=r[9],
        )
        for r in res.fetchall()
    ]
