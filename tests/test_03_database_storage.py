"""Domain 03 — Database & Storage.

Engine configuration, upload directory, and live-DB CRUD / integrity /
rollback / schema checks for the SARMAAN / ERITAS MDA dashboard.

Tests that touch real data depend on the `require_db` fixture (param) so they
SKIP cleanly when PostgreSQL is unreachable. All `app.*`/sqlalchemy imports are
done lazily inside the test bodies.
"""
import uuid as _uuid

import pytest


class TestEngineConfig:
    """Async engine is configured for asyncpg with a healthy pool."""

    def test_engine_url_uses_asyncpg(self):
        import app.database as db

        url = db.engine.url
        assert url.get_backend_name() == "postgresql", f"backend was {url.get_backend_name()!r}"
        assert url.get_driver_name() == "asyncpg", f"driver was {url.get_driver_name()!r}"

    def test_pool_size_at_least_10(self):
        import app.database as db

        size = db.engine.pool.size()
        assert size >= 10, f"pool_size should be >= 10, got {size}"

    def test_max_overflow_at_least_20(self):
        import app.database as db

        overflow = db.engine.pool._max_overflow
        assert overflow >= 20, f"max_overflow should be >= 20, got {overflow}"

    def test_pool_pre_ping_enabled(self):
        import app.database as db

        assert db.engine.pool._pre_ping is True, "pool_pre_ping should be True"


class TestUploadDir:
    """UPLOAD_DIR is configured and created on import."""

    def test_upload_dir_is_set(self):
        import app.config as config

        assert config.UPLOAD_DIR, "UPLOAD_DIR should be a non-empty path"

    def test_upload_dir_exists(self):
        import os

        import app.config as config

        assert os.path.isdir(config.UPLOAD_DIR), (
            f"UPLOAD_DIR should exist as a directory: {config.UPLOAD_DIR}"
        )


class TestGeoprojectCRUD:
    """Create / read / delete a GeoProject against the live DB."""

    async def test_create_read_delete(self, require_db):
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models import GeoProject

        slug = f"qa-test-{_uuid.uuid4().hex[:12]}"
        async with AsyncSessionLocal() as session:
            proj = GeoProject(name="QA CRUD Project", slug=slug)
            session.add(proj)
            await session.commit()
            await session.refresh(proj)
            created_id = proj.id
            assert created_id is not None, "GeoProject should get a primary key"

            # read it back
            fetched = (
                await session.execute(select(GeoProject).where(GeoProject.slug == slug))
            ).scalar_one()
            assert fetched.id == created_id
            assert fetched.name == "QA CRUD Project"

            # delete it
            await session.delete(fetched)
            await session.commit()

            gone = (
                await session.execute(select(GeoProject).where(GeoProject.slug == slug))
            ).scalar_one_or_none()
            assert gone is None, "GeoProject should be deleted"

    async def test_duplicate_slug_raises_integrity_error(self, require_db):
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from app.database import AsyncSessionLocal
        from app.models import GeoProject

        slug = f"qa-uniq-{_uuid.uuid4().hex[:12]}"
        created_id = None
        try:
            async with AsyncSessionLocal() as session:
                session.add(GeoProject(name="First", slug=slug))
                await session.commit()
                created_id = (
                    await session.execute(
                        select(GeoProject.id).where(GeoProject.slug == slug)
                    )
                ).scalar_one()

            async with AsyncSessionLocal() as session:
                session.add(GeoProject(name="Second", slug=slug))
                with pytest.raises(IntegrityError):
                    await session.commit()
        finally:
            # cleanup the first row regardless of outcome
            if created_id is not None:
                async with AsyncSessionLocal() as session:
                    obj = await session.get(GeoProject, created_id)
                    if obj is not None:
                        await session.delete(obj)
                        await session.commit()

    async def test_read_by_id(self, require_db):
        from app.database import AsyncSessionLocal
        from app.models import GeoProject

        slug = f"qa-byid-{_uuid.uuid4().hex[:12]}"
        async with AsyncSessionLocal() as session:
            proj = GeoProject(name="By Id", slug=slug)
            session.add(proj)
            await session.commit()
            await session.refresh(proj)
            pid = proj.id
            try:
                fetched = await session.get(GeoProject, pid)
                assert fetched is not None, "should read GeoProject by id"
                assert fetched.slug == slug
            finally:
                obj = await session.get(GeoProject, pid)
                if obj is not None:
                    await session.delete(obj)
                    await session.commit()


class TestUploadBatchUUID:
    """UploadBatch primary keys are UUIDs and unique per row."""

    async def test_batch_pk_is_uuid(self, require_db):
        try:
            from app.models import GeoProject, UploadBatch
        except ImportError:
            pytest.skip("UploadBatch model not present")

        from app.database import AsyncSessionLocal

        slug = f"qa-batch-{_uuid.uuid4().hex[:12]}"
        async with AsyncSessionLocal() as session:
            proj = GeoProject(name="Batch Owner", slug=slug)
            session.add(proj)
            await session.commit()
            await session.refresh(proj)
            pid = proj.id
            try:
                batch = UploadBatch(project_id=pid, filename="a.csv")
                session.add(batch)
                await session.commit()
                await session.refresh(batch)
                assert isinstance(batch.id, _uuid.UUID), (
                    f"UploadBatch.id should be a UUID, got {type(batch.id)!r}"
                )
            finally:
                fresh = await session.get(GeoProject, pid)
                if fresh is not None:
                    await session.delete(fresh)  # cascade removes batches
                    await session.commit()

    async def test_two_batches_have_distinct_uuids(self, require_db):
        try:
            from app.models import GeoProject, UploadBatch
        except ImportError:
            pytest.skip("UploadBatch model not present")

        from app.database import AsyncSessionLocal

        slug = f"qa-batch2-{_uuid.uuid4().hex[:12]}"
        async with AsyncSessionLocal() as session:
            proj = GeoProject(name="Batch Owner 2", slug=slug)
            session.add(proj)
            await session.commit()
            await session.refresh(proj)
            pid = proj.id
            try:
                b1 = UploadBatch(project_id=pid, filename="b1.csv")
                b2 = UploadBatch(project_id=pid, filename="b2.csv")
                session.add_all([b1, b2])
                await session.commit()
                await session.refresh(b1)
                await session.refresh(b2)
                assert b1.id != b2.id, "two batches must get distinct UUID primary keys"
                assert isinstance(b1.id, _uuid.UUID) and isinstance(b2.id, _uuid.UUID)
            finally:
                fresh = await session.get(GeoProject, pid)
                if fresh is not None:
                    await session.delete(fresh)
                    await session.commit()


class TestSessionRollback:
    """Sessions roll back cleanly on errors, leaving no partial rows."""

    async def test_integrity_error_leaves_no_partial_row(self, require_db):
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from app.database import AsyncSessionLocal
        from app.models import GeoProject

        slug = f"qa-rb-{_uuid.uuid4().hex[:12]}"
        first_id = None
        try:
            async with AsyncSessionLocal() as session:
                session.add(GeoProject(name="RB First", slug=slug))
                await session.commit()
                first_id = (
                    await session.execute(
                        select(GeoProject.id).where(GeoProject.slug == slug)
                    )
                ).scalar_one()

            # second session: add a good row AND a duplicate; commit fails, rolls back
            async with AsyncSessionLocal() as session:
                good_slug = f"qa-rb-good-{_uuid.uuid4().hex[:12]}"
                session.add(GeoProject(name="RB Good", slug=good_slug))
                session.add(GeoProject(name="RB Dup", slug=slug))  # duplicate
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()

                # the "good" row must NOT have been persisted
                leftover = (
                    await session.execute(
                        select(GeoProject).where(GeoProject.slug == good_slug)
                    )
                ).scalar_one_or_none()
                assert leftover is None, "rollback should leave no partial row"
        finally:
            if first_id is not None:
                async with AsyncSessionLocal() as session:
                    obj = await session.get(GeoProject, first_id)
                    if obj is not None:
                        await session.delete(obj)
                        await session.commit()

    async def test_get_db_rolls_back_on_exception(self, require_db):
        from sqlalchemy import select

        from app.database import AsyncSessionLocal, get_db
        from app.models import GeoProject

        slug = f"qa-getdb-{_uuid.uuid4().hex[:12]}"
        agen = get_db()
        session = await agen.__anext__()
        session.add(GeoProject(name="GetDb RB", slug=slug))
        # Throwing into the generator triggers the except: rollback path in get_db.
        with pytest.raises(RuntimeError):
            await agen.athrow(RuntimeError("boom"))

        # nothing should have been committed
        async with AsyncSessionLocal() as verify:
            leftover = (
                await verify.execute(select(GeoProject).where(GeoProject.slug == slug))
            ).scalar_one_or_none()
            assert leftover is None, "get_db must roll back the uncommitted row on exception"


class TestDatabaseTables:
    """PostGIS extension and core tables are present in the live DB."""

    async def test_postgis_extension_present(self, require_db):
        from sqlalchemy import text

        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            names = (
                await session.execute(text("SELECT extname FROM pg_extension"))
            ).scalars().all()
        assert "postgis" in names, f"postgis extension should be installed; saw {names}"

    async def test_core_tables_exist(self, require_db):
        from sqlalchemy import text

        from app.database import AsyncSessionLocal

        expected = {"users", "geo_projects", "points_raw"}
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).scalars().all()
        present = set(rows)
        missing = expected - present
        assert not missing, f"core tables missing: {missing}"
