from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError
import bcrypt as _bcrypt
import jwt

from app.database import get_db
from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from app.schemas import LoginRequest, TokenResponse, UserCreate, UserOut, UserStatesUpdate
from app.models import User


def allowed_states_of(user: "Optional[User]") -> "Optional[set]":
    """Lowercased set of state names an account may access. None = ALL (a
    superadmin, or the anonymous/public path which is gated separately). A set
    (possibly empty → no access) for a state-scoped account. Single enforcement
    point shared by the web platform and the Android app."""
    # Admins AND superadmins see every state (HQ view); only non-admin accounts
    # are state-scoped.
    if user is None or getattr(user, "is_superadmin", False) or getattr(user, "is_admin", False):
        return None
    raw = (getattr(user, "allowed_states", None) or "").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def allowed_lgas_of(user: "Optional[User]") -> "Optional[set]":
    """Lowercased set of LGA names an account is restricted to, or None for NO
    LGA restriction. None covers superadmins/admins, the anonymous/public path,
    AND a state-scoped user who isn't pinned to specific LGAs (they see every LGA
    in their state). A set means the account is LGA-scoped — every coverage /
    quality / geo query must filter to these LGAs. A NON-empty allowed_lgas that
    parses to an empty set returns the empty set (→ no rows), never None, so a
    malformed value fails closed rather than leaking everything."""
    if user is None or getattr(user, "is_superadmin", False) or getattr(user, "is_admin", False):
        return None
    raw = (getattr(user, "allowed_lgas", None) or "").strip()
    if not raw:
        return None  # state-scoped but not LGA-scoped → all LGAs in their state(s)
    return {s.strip().lower() for s in raw.split(",") if s.strip()}

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Return the authenticated user if a valid token is provided, otherwise None.

    Used by endpoints that serve both the public dashboard and authenticated views.
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except HTTPException:
        return None
    username = payload.get("sub")
    if not username:
        return None
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not (user.is_admin or user.is_superadmin):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return user


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token({
        "sub": user.username,
        "is_admin": user.is_admin,
        "is_superadmin": user.is_superadmin,
    })
    return TokenResponse(
        access_token=token,
        username=user.username,
        is_admin=user.is_admin,
        is_superadmin=user.is_superadmin,
    )


@router.post("/users", response_model=UserOut)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    username = (data.username or "").strip()
    # Empty email → NULL. `email` is UNIQUE, and storing "" for several users
    # would itself collide; NULLs don't, so blank stays blank.
    email = (data.email or "").strip() or None

    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f'Username "{username}" is already taken.')

    # Email is UNIQUE. Pre-check so the user gets a clear message instead of a
    # raw 500 from the DB constraint (which surfaces on the client as a
    # misleading "Network error").
    if email:
        dup = await db.execute(select(User).where(User.email == email))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f'Email "{email}" is already in use by another account.')

    # Only superadmins can mint admins or other superadmins.
    if (data.is_admin or data.is_superadmin) and not actor.is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="Only superadmins can create admin or superadmin accounts",
        )

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(data.password),
        is_admin=data.is_admin or data.is_superadmin,
        is_superadmin=data.is_superadmin,
        allowed_states=(data.allowed_states or None),
        allowed_lgas=(data.allowed_lgas or None),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Lost the race against a concurrent create (or any other unique
        # collision). Roll back so the session is reusable and report cleanly.
        await db.rollback()
        raise HTTPException(status_code=409, detail="That username or email is already in use.")
    await db.refresh(user)
    return user


@router.put("/users/{user_id}/states", response_model=UserOut)
async def set_user_states(
    user_id: int,
    data: UserStatesUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    """Set which state(s) a user may access (CSV, e.g. 'Sokoto,Kano') and,
    optionally, which LGA(s) within them (CSV). Clearing states (empty) grants
    unrestricted access and is superadmin-only. allowed_lgas is set whenever the
    field is present on the request (empty/omitted → no LGA restriction)."""
    res = await db.execute(select(User).where(User.id == user_id))
    target = res.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    states = (data.allowed_states or "").strip()
    if not states and not actor.is_superadmin:
        raise HTTPException(status_code=403, detail="Only a superadmin can clear a user's state restriction")
    target.allowed_states = states or None
    if data.allowed_lgas is not None:
        target.allowed_lgas = (data.allowed_lgas or "").strip() or None
    await db.commit()
    await db.refresh(target)
    return target


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.get("/scope-options")
async def scope_options(
    db: AsyncSession = Depends(get_db),
    _actor: User = Depends(require_admin),
):
    """Distinct states and LGAs known to the platform, for the user-scope
    pickers in the admin UI. LGAs carry their state so the UI can cascade the
    LGA list off the chosen state(s). Names are normalised (trim + title-case)
    and de-duplicated so the same LGA across rounds/projects appears once."""
    states_res = await db.execute(text("""
        SELECT DISTINCT INITCAP(TRIM(state_name)) AS s
        FROM geo_projects
        WHERE state_name IS NOT NULL AND TRIM(state_name) <> ''
        ORDER BY s
    """))
    states = [r[0] for r in states_res.fetchall()]

    # Union the LGA names from both boundary tables (lgas + wards) so the
    # picker stays complete even when only one of them is loaded for a project.
    lgas_res = await db.execute(text("""
        SELECT DISTINCT lga, st FROM (
            SELECT INITCAP(TRIM(l.lga_name)) AS lga, INITCAP(TRIM(g.state_name)) AS st
            FROM lgas l JOIN geo_projects g ON g.id = l.project_id
            WHERE l.lga_name IS NOT NULL AND TRIM(l.lga_name) <> ''
            UNION
            SELECT INITCAP(TRIM(w.lga_name)) AS lga, INITCAP(TRIM(g.state_name)) AS st
            FROM wards w JOIN geo_projects g ON g.id = w.project_id
            WHERE w.lga_name IS NOT NULL AND TRIM(w.lga_name) <> ''
        ) q
        WHERE st IS NOT NULL AND st <> ''
        ORDER BY st, lga
    """))
    lgas = [{"name": r[0], "state": r[1]} for r in lgas_res.fetchall()]

    return {"states": states, "lgas": lgas}


from pydantic import BaseModel, Field  # noqa: E402 — colocated with the route below


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password:     str = Field(min_length=8, max_length=200)


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/change-password", status_code=204)
async def change_password(
    data: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Self-service password change for the logged-in user.

    Requires the caller's current password — protects against an attacker
    abusing a stolen session token to lock the real user out. New password
    must be at least 8 chars; bcrypt-hashed before storage.
    """
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")

    user.hashed_password = hash_password(data.new_password)
    await db.commit()
    # 204 No Content — client should redirect to login or refresh
    return


@router.post("/users/{user_id}/reset-password", status_code=204)
async def admin_reset_password(
    user_id: int,
    data: AdminResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_superadmin),
):
    """Superadmin override: reset any user's password without their old one.

    Restricted to superadmins because it bypasses the current-password check.
    The target user is forced to use the new password on their next login —
    they don't get a session-invalidation, but the old password no longer
    works, so any active session of theirs is effectively dead on token
    refresh.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    # Defensive: prevent the actor from accidentally locking out the root
    # admin via this endpoint — they can still change it via self-service.
    if target.id == actor.id:
        raise HTTPException(
            status_code=400,
            detail="Use the self-service Change Password page for your own account.",
        )
    target.hashed_password = hash_password(data.new_password)
    await db.commit()
    return


@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    """List users. Admin (non-superadmin) callers do NOT see superadmin
    accounts — they have no way to act on them anyway (delete/promote/demote
    are all superadmin-gated), and surfacing them invites accidental UI
    confusion or a future click-handler bug. Superadmin callers see all.
    """
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    if not actor.is_superadmin:
        users = [u for u in users if not u.is_superadmin]
    return users


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.username == "admin":
        raise HTTPException(status_code=403, detail="Cannot delete the root admin user")
    # Admins can't delete superadmins; only superadmins can.
    if user.is_superadmin and not actor.is_superadmin:
        raise HTTPException(status_code=403, detail="Only superadmins can delete superadmin accounts")
    # Don't allow deleting the last remaining superadmin.
    if user.is_superadmin:
        count_result = await db.execute(
            select(User).where(User.is_superadmin == True, User.is_active == True)
        )
        active_supers = count_result.scalars().all()
        if len(active_supers) <= 1:
            raise HTTPException(status_code=403, detail="Cannot delete the last active superadmin")
    await db.delete(user)
    await db.commit()


@router.post("/users/{user_id}/promote", response_model=UserOut)
async def promote_to_superadmin(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Promote an existing user to superadmin. Superadmin-only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_admin = True
    user.is_superadmin = True
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/users/{user_id}/demote", response_model=UserOut)
async def demote_from_superadmin(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_superadmin),
):
    """Remove superadmin from a user, leaving them as admin. Cannot demote yourself or the last superadmin."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == actor.id:
        raise HTTPException(status_code=403, detail="You cannot demote yourself")
    if not user.is_superadmin:
        return user
    count_result = await db.execute(
        select(User).where(User.is_superadmin == True, User.is_active == True)
    )
    active_supers = count_result.scalars().all()
    if len(active_supers) <= 1:
        raise HTTPException(status_code=403, detail="Cannot demote the last active superadmin")
    user.is_superadmin = False
    await db.commit()
    await db.refresh(user)
    return user
