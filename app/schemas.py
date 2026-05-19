from __future__ import annotations
from typing import Optional, List, Any
from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, field_validator, model_validator


# ─── Auth ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool
    is_superadmin: bool = False


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    is_admin: bool = False
    is_superadmin: bool = False


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str]
    is_admin: bool
    is_superadmin: bool = False
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Projects ────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ProjectOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    is_active: bool
    state_name: Optional[str] = None
    round_number: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Boundaries ──────────────────────────────────────────────────────────────

class LGAOut(BaseModel):
    id: int
    lgacode: str
    lga_name: str

    model_config = {"from_attributes": True}


class WardOut(BaseModel):
    id: int
    wardcode: str
    lgacode: str
    ward_name: str
    lga_name: Optional[str]

    model_config = {"from_attributes": True}


class SettlementOut(BaseModel):
    id: int
    unique_cod: str
    lgacode: str
    wardcode: str
    settlement_name: Optional[str]
    lga_name: Optional[str]
    ward_name: Optional[str]

    model_config = {"from_attributes": True}


# ─── Analytics ───────────────────────────────────────────────────────────────

class SettlementAnalyticsOut(BaseModel):
    id: int
    unique_cod: Optional[str]
    lgacode: Optional[str]
    wardcode: Optional[str]
    settlement_name: Optional[str]
    lga_name: Optional[str]
    ward_name: Optional[str]
    total_grids: int
    visited_grids: int
    completeness_pct: float
    is_visited: bool
    point_count: int
    last_computed: datetime

    model_config = {"from_attributes": True}


class WardMetrics(BaseModel):
    wardcode: str
    ward_name: Optional[str]
    lga_name: Optional[str]
    total_settlements: int
    visited_settlements: int
    visitation_pct: float
    total_grids: int
    visited_grids: int
    completeness_pct: float
    point_count: int


class LGAMetrics(BaseModel):
    lgacode: str
    lga_name: str
    total_settlements: int
    visited_settlements: int
    visitation_pct: float
    total_grids: int
    visited_grids: int
    completeness_pct: float
    point_count: int


class ProjectSummary(BaseModel):
    project_id: int
    project_name: str
    total_lgas: int
    total_wards: int
    total_settlements: int
    visited_settlements: int
    visitation_pct: float
    total_grids: int
    visited_grids: int
    completeness_pct: float
    total_points: int
    qc_out_of_bound: int
    qc_time_violations: int
    qc_stacked_points: int


# ─── Upload / Ingestion ──────────────────────────────────────────────────────

class UploadBatchOut(BaseModel):
    id: UUID
    filename: Optional[str]
    row_count: Optional[int]
    valid_count: Optional[int]
    duplicate_count: Optional[int]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ValidationSummary(BaseModel):
    total_rows: int
    valid_rows: int
    duplicate_rows: int
    invalid_rows: int
    errors: List[str]
    sample_valid: List[dict]


# ─── QC ──────────────────────────────────────────────────────────────────────

class QCFlagOut(BaseModel):
    id: int
    point_id: int
    flag_type: str
    flag_detail: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class QCSummary(BaseModel):
    out_of_bound: int
    time_violations: int
    stacked_points: int
    duplicates: int
    total_flags: int


# ─── GeoJSON responses ───────────────────────────────────────────────────────

class GeoJSONFeature(BaseModel):
    type: str = "Feature"
    geometry: dict
    properties: dict


class GeoJSONCollection(BaseModel):
    type: str = "FeatureCollection"
    features: List[GeoJSONFeature]


# ─── Coverage timeline ───────────────────────────────────────────────────────

class CoverageTimelinePoint(BaseModel):
    date: date
    visited_settlements: int
    visitation_pct: float
    point_count: int


class CoverageTimeline(BaseModel):
    project_id: int
    data: List[CoverageTimelinePoint]
