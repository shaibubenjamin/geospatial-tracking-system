from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import DATABASE_URL


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_all_tables():
    """Create all tables in the database.

    Extension creation is best-effort and runs in its own short-lived
    connection so a permission error doesn't abort the whole startup
    transaction. On managed Postgres (e.g. AWS RDS) the app's role doesn't
    have CREATE-EXTENSION privilege; extensions are pre-installed by the
    DBA at provisioning time, so the IF NOT EXISTS path is harmless when
    a superuser runs it and tolerable to skip otherwise.
    """
    import logging
    from sqlalchemy import text
    log = logging.getLogger(__name__)

    def _first_line(e: Exception) -> str:
        s = str(e).strip()
        return s.splitlines()[0][:160] if s else type(e).__name__

    for ext in ("postgis", '"uuid-ossp"'):
        try:
            async with engine.begin() as ext_conn:
                await ext_conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
        except Exception as e:
            log.info("Skipping CREATE EXTENSION %s (likely pre-installed by DBA): %s",
                     ext, _first_line(e))

    # Best-effort, like the extensions above. On managed Postgres (AWS RDS) the
    # app role is DML-only — it has no DDL/REFERENCES privilege — so existing
    # tables are a no-op here but a NEW model would raise "permission denied".
    # New tables are added out-of-band by a privileged migration (server_admin),
    # so a permission error must NOT crash startup. Catch OUTSIDE the begin()
    # block so the failed transaction is rolled back cleanly first.
    from app import models  # noqa: F401
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        log.warning("metadata.create_all skipped (app role likely lacks DDL; "
                    "new tables need a privileged migration): %s", _first_line(e))
