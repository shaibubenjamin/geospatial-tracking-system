from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
import bcrypt as _bcrypt
import jwt

from app.database import get_db
from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from app.schemas import LoginRequest, TokenResponse, UserCreate, UserOut
from app.models import User

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
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already exists")

    # Only superadmins can mint admins or other superadmins.
    if (data.is_admin or data.is_superadmin) and not actor.is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="Only superadmins can create admin or superadmin accounts",
        )

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        is_admin=data.is_admin or data.is_superadmin,
        is_superadmin=data.is_superadmin,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()


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
