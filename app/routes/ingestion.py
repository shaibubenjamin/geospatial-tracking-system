"""
ingestion.py
CSV upload, validation, deduplication, and incremental processing pipeline.
"""
import io
import uuid
from datetime import datetime, date
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.database import get_db
from app.models import GeoProject, UploadBatch, PointRaw, User
from app.schemas import ValidationSummary, UploadBatchOut
from app.routes.auth import get_current_user, require_superadmin
from app.services.spatial_engine import (
    spatial_join_points_to_grids,
    compute_settlement_analytics,
)
from app.services.qc_engine import (
    run_out_of_bound_check,
    run_time_violation_check,
    run_stacked_point_check,
)

router = APIRouter(prefix="/projects/{project_id}/ingest", tags=["ingestion"])

REQUIRED_COLUMNS = {"latitude", "longitude"}
OPTIONAL_COLUMNS = {"timestamp", "collection_date", "research_assistant",
                    "lga_name", "ward_name", "settlement_name"}


def _parse_csv(content: bytes) -> pd.DataFrame:
    """Parse CSV bytes to DataFrame, normalising column names."""
    df = pd.read_csv(io.BytesIO(content), low_memory=False)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _validate_df(df: pd.DataFrame) -> ValidationSummary:
    """Validate rows and return summary."""
    errors = []
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        errors.append(f"Missing required columns: {missing}")
        return ValidationSummary(
            total_rows=len(df),
            valid_rows=0,
            duplicate_rows=0,
            invalid_rows=len(df),
            errors=errors,
            sample_valid=[],
        )

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    invalid_mask = df["latitude"].isna() | df["longitude"].isna()
    invalid_mask |= (df["latitude"] < -90) | (df["latitude"] > 90)
    invalid_mask |= (df["longitude"] < -180) | (df["longitude"] > 180)

    invalid_count = int(invalid_mask.sum())
    valid_df = df[~invalid_mask].copy()

    # Deduplication within the CSV itself
    dup_mask = valid_df.duplicated(subset=["latitude", "longitude", "timestamp"], keep="first")
    dup_count = int(dup_mask.sum())
    valid_df = valid_df[~dup_mask]

    sample = valid_df.head(5).to_dict(orient="records")
    # Convert NaN to None in sample
    sample = [{k: (None if (isinstance(v, float) and pd.isna(v)) else v)
               for k, v in row.items()} for row in sample]

    return ValidationSummary(
        total_rows=len(df),
        valid_rows=len(valid_df),
        duplicate_rows=dup_count,
        invalid_rows=invalid_count,
        errors=errors,
        sample_valid=sample,
    )


@router.post("/validate", response_model=ValidationSummary)
async def validate_csv(
    project_id: int,
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """Validate CSV without inserting anything."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")
    content = await file.read()
    df = _parse_csv(content)
    return _validate_df(df)


@router.post("/upload", response_model=UploadBatchOut)
async def upload_csv(
    project_id: int,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    _super: User = Depends(require_superadmin),
):
    """Upload and persist GPS point data from CSV."""
    result = await db.execute(select(GeoProject).where(GeoProject.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    content = await file.read()
    df = _parse_csv(content)
    summary = _validate_df(df)

    batch_id = uuid.uuid4()

    # Create upload batch record
    batch = UploadBatch(
        id=batch_id,
        project_id=project_id,
        filename=file.filename,
        row_count=summary.total_rows,
        valid_count=summary.valid_rows,
        duplicate_count=summary.duplicate_rows,
        status="processing",
    )
    db.add(batch)
    await db.commit()

    # Insert valid rows
    valid_df = df.copy()
    valid_df["latitude"] = pd.to_numeric(valid_df["latitude"], errors="coerce")
    valid_df["longitude"] = pd.to_numeric(valid_df["longitude"], errors="coerce")
    valid_df = valid_df[valid_df["latitude"].notna() & valid_df["longitude"].notna()]
    valid_df = valid_df[
        (valid_df["latitude"].between(-90, 90)) &
        (valid_df["longitude"].between(-180, 180))
    ]
    valid_df = valid_df.drop_duplicates(subset=["latitude", "longitude", "timestamp"], keep="first")

    new_point_ids: List[int] = []

    for _, row in valid_df.iterrows():
        ts = None
        if "timestamp" in row and pd.notna(row.get("timestamp")):
            try:
                ts = pd.to_datetime(row["timestamp"], utc=True).to_pydatetime()
            except Exception:
                ts = None

        col_date = None
        if "collection_date" in row and pd.notna(row.get("collection_date")):
            try:
                col_date = pd.to_datetime(row["collection_date"]).date()
            except Exception:
                col_date = None
        elif ts:
            col_date = ts.date()

        lat = float(row["latitude"])
        lon = float(row["longitude"])
        wkt = f"SRID=4326;POINT({lon} {lat})"

        try:
            ins_result = await db.execute(
                text("""
                    INSERT INTO points_raw
                      (project_id, geom, latitude, longitude, collection_date, timestamp,
                       research_assistant, lga_name, ward_name, settlement_name, upload_batch_id)
                    VALUES
                      (:project_id, ST_GeomFromText(:wkt, 4326), :lat, :lon,
                       :col_date, :ts, :ra, :lga, :ward, :settlement, :batch_id)
                    ON CONFLICT (project_id, latitude, longitude, timestamp)
                    DO NOTHING
                    RETURNING id
                """),
                {
                    "project_id": project_id,
                    "wkt": f"POINT({lon} {lat})",
                    "lat": lat,
                    "lon": lon,
                    "col_date": col_date,
                    "ts": ts,
                    "ra": row.get("research_assistant") if pd.notna(row.get("research_assistant", None)) else None,
                    "lga": row.get("lga_name") if pd.notna(row.get("lga_name", None)) else None,
                    "ward": row.get("ward_name") if pd.notna(row.get("ward_name", None)) else None,
                    "settlement": row.get("settlement_name") if pd.notna(row.get("settlement_name", None)) else None,
                    "batch_id": batch_id,
                },
            )
            inserted_row = ins_result.fetchone()
            if inserted_row:
                new_point_ids.append(inserted_row[0])
        except Exception:
            continue

    await db.commit()

    # Update batch status
    await db.execute(
        text("UPDATE upload_batches SET status = 'complete', valid_count = :vc WHERE id = :bid"),
        {"vc": len(new_point_ids), "bid": batch_id},
    )
    await db.commit()

    # Trigger background processing
    background_tasks.add_task(
        _process_upload_background,
        batch_id=batch_id,
        project_id=project_id,
        new_point_ids=new_point_ids,
    )

    await db.refresh(batch)
    return batch


async def _process_upload_background(
    batch_id: uuid.UUID,
    project_id: int,
    new_point_ids: List[int],
):
    """
    Background processing pipeline:
    1. Spatial join points → grids
    2. Mark visited grids
    3. Recompute settlement analytics for affected settlements
    4. Run QC checks
    """
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            # 1. Find affected unique_cods
            affected_unique_cods = await spatial_join_points_to_grids(
                project_id, new_point_ids, db
            )

            # 2 & 3. Recompute settlement analytics for affected settlements
            await compute_settlement_analytics(
                project_id,
                affected_unique_cods if affected_unique_cods else None,
                db,
            )

            # 4. QC checks
            await run_out_of_bound_check(project_id, new_point_ids, db)
            await run_time_violation_check(project_id, new_point_ids, db)
            await run_stacked_point_check(project_id, db)

            # Update batch status
            await db.execute(
                text("UPDATE upload_batches SET status = 'processed' WHERE id = :bid"),
                {"bid": batch_id},
            )
            await db.commit()
        except Exception as e:
            await db.execute(
                text("UPDATE upload_batches SET status = 'error' WHERE id = :bid"),
                {"bid": batch_id},
            )
            await db.commit()
            raise


@router.get("/batches", response_model=List[UploadBatchOut])
async def list_batches(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(UploadBatch)
        .where(UploadBatch.project_id == project_id)
        .order_by(UploadBatch.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


@router.get("/batches/{batch_id}", response_model=UploadBatchOut)
async def get_batch(
    project_id: int,
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(UploadBatch).where(
            UploadBatch.project_id == project_id,
            UploadBatch.id == batch_id,
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch
