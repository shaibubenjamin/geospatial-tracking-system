import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean,
    Float, DateTime, Date, ForeignKey, UniqueConstraint, PrimaryKeyConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from app.database import Base


class GeoProject(Base):
    __tablename__ = "geo_projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(Text, nullable=False)
    slug = Column(Text, unique=True, nullable=False)
    description = Column(Text, default="")
    is_active = Column(Boolean, default=False)
    # Public-dashboard opt-in: when TRUE, this project (state+round) is viewable
    # without login; anonymous access to any non-public project is refused.
    is_public = Column(Boolean, default=False)
    state_name = Column(Text)
    round_number = Column(Integer)
    # Official campaign start date. When set, every received_on-based query
    # hides rows received before this date so pre-campaign test submissions
    # don't inflate Days Active / pace metrics. Raw data is preserved.
    campaign_start_date = Column(Date)
    # Official campaign end date. Used with campaign_start_date to compute the
    # planned campaign length so cards can show "Day 2 of 5" rather than just
    # the count of days for which data has been received.
    campaign_end_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    lgas = relationship("LGA", back_populates="project", cascade="all, delete-orphan")
    wards = relationship("Ward", back_populates="project", cascade="all, delete-orphan")
    settlements = relationship("Settlement", back_populates="project", cascade="all, delete-orphan")
    grids = relationship("Grid", back_populates="project", cascade="all, delete-orphan")
    points = relationship("PointRaw", back_populates="project", cascade="all, delete-orphan")
    upload_batches = relationship("UploadBatch", back_populates="project", cascade="all, delete-orphan")


class LGA(Base):
    __tablename__ = "lgas"
    __table_args__ = (UniqueConstraint("project_id", "lgacode", name="uq_lga_project_code"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    lgacode = Column(Text, nullable=False)
    lga_name = Column(Text, nullable=False)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)

    project = relationship("GeoProject", back_populates="lgas")


class Ward(Base):
    __tablename__ = "wards"
    __table_args__ = (UniqueConstraint("project_id", "wardcode", name="uq_ward_project_code"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    wardcode = Column(Text, nullable=False)
    lgacode = Column(Text, nullable=False)
    ward_name = Column(Text, nullable=False)
    lga_name = Column(Text)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)

    project = relationship("GeoProject", back_populates="wards")


class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("project_id", "unique_cod", name="uq_settlement_project_code"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    unique_cod = Column(Text, nullable=False)
    lgacode = Column(Text, nullable=False)
    wardcode = Column(Text, nullable=False)
    settlement_name = Column(Text)
    lga_name = Column(Text)
    ward_name = Column(Text)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)

    project = relationship("GeoProject", back_populates="settlements")
    analytics = relationship("SettlementAnalytics", back_populates="settlement", uselist=False, cascade="all, delete-orphan")


class Grid(Base):
    __tablename__ = "grids"
    __table_args__ = (UniqueConstraint("project_id", "unique_cod", "id", name="uq_grid_project_code_id"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    unique_cod = Column(Text, nullable=False)
    lgacode = Column(Text, nullable=False)
    wardcode = Column(Text, nullable=False)
    settlement_name = Column(Text)
    geom = Column(Geometry("POLYGON", srid=4326), nullable=False)

    project = relationship("GeoProject", back_populates="grids")


class PointRaw(Base):
    __tablename__ = "points_raw"
    __table_args__ = (
        UniqueConstraint("project_id", "latitude", "longitude", "timestamp", name="uq_point_dedup"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    geom = Column(Geometry("POINT", srid=4326), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    collection_date = Column(Date)
    timestamp = Column(DateTime(timezone=True))
    research_assistant = Column(Text)
    lga_name = Column(Text)
    ward_name = Column(Text)
    settlement_name = Column(Text)
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    upload_batch_id = Column(UUID(as_uuid=True))

    project = relationship("GeoProject", back_populates="points")
    qc_flags = relationship("QCFlag", back_populates="point", cascade="all, delete-orphan")


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    filename = Column(Text)
    row_count = Column(Integer)
    valid_count = Column(Integer)
    duplicate_count = Column(Integer)
    status = Column(Text, default="pending")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    project = relationship("GeoProject", back_populates="upload_batches")


class SettlementAnalytics(Base):
    __tablename__ = "settlement_analytics"
    __table_args__ = (UniqueConstraint("project_id", "settlement_id", name="uq_analytics_project_settlement"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    settlement_id = Column(Integer, ForeignKey("settlements.id"), nullable=False)
    unique_cod = Column(Text)
    lgacode = Column(Text)
    wardcode = Column(Text)
    settlement_name = Column(Text)
    lga_name = Column(Text)
    ward_name = Column(Text)
    total_grids = Column(Integer, default=0)
    visited_grids = Column(Integer, default=0)
    completeness_pct = Column(Float, default=0.0)
    is_visited = Column(Boolean, default=False)
    point_count = Column(Integer, default=0)
    last_computed = Column(DateTime(timezone=True), default=datetime.utcnow)

    settlement = relationship("Settlement", back_populates="analytics")


class QCFlag(Base):
    __tablename__ = "qc_flags"

    id = Column(BigInteger, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    point_id = Column(BigInteger, ForeignKey("points_raw.id"), nullable=False)
    flag_type = Column(Text, nullable=False)  # out_of_bound, time_violation, stacked_point, duplicate
    flag_detail = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    point = relationship("PointRaw", back_populates="qc_flags")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(Text, unique=True, nullable=False, index=True)
    email = Column(Text, unique=True)
    hashed_password = Column(Text, nullable=False)
    is_admin = Column(Boolean, default=False)
    is_superadmin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    # CSV of state names this account may access (e.g. "Sokoto,Kano"). NULL/empty
    # = no implicit state access; superadmins always see every state regardless.
    allowed_states = Column(Text)
    # CSV of LGA names this account is further restricted to (e.g. "Binji,Wamako").
    # NULL/empty = no LGA restriction (sees every LGA in its allowed state[s]).
    # When set, every coverage/quality/geo query is filtered to these LGAs.
    allowed_lgas = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class MdaHousehold(Base):
    __tablename__ = "mda_households"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), index=True)
    formid = Column(Text, unique=True, nullable=False, index=True)
    username = Column(Text)
    teamcode = Column(Text)
    data_type = Column(Text)
    data_entry_persons = Column(Text)
    data_entry_persons_norm = Column(Text)   # lower().strip()
    phone_number_data = Column(Text)
    ra_key = Column(Text, index=True)        # f"{name_norm}|{phone}"
    lga = Column(Text, index=True)           # admin2 value
    admin3_code = Column(Text)
    admin5_code = Column(Text)
    trt_day = Column(Text)
    date_trt = Column(Date, index=True)
    consent_trt = Column(Text)               # '0'=refusal '1'=consent
    reasons_for_refusal = Column(Text)
    others_reasons_for_refusal = Column(Text)
    hh_num = Column(Text)
    hh_seq = Column(Text)
    serial_number_hh_id = Column(Text)
    number_of_treated = Column(Integer)
    housemarking_code = Column(Text)
    gps_raw = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    gps_accuracy = Column(Float)
    geom = Column(Geometry("POINT", srid=4326))
    started_time = Column(DateTime(timezone=True))
    completed_time = Column(DateTime(timezone=True))
    received_on = Column(DateTime(timezone=True))
    form_duration_min = Column(Float)        # completed - started in minutes
    sync_lag_hours = Column(Float)           # received - completed in hours
    # QC flags
    flag_duplicate = Column(Boolean, default=False)
    flag_duplicate_gps = Column(Boolean, default=False)       # same lat/lon as another record
    flag_gps_outside_lga = Column(Boolean, default=False)     # GPS not within stated LGA polygon
    flag_gps_outside_ward = Column(Boolean, default=False)    # GPS not within any ward polygon
    flag_gps_outside_state = Column(Boolean, default=False)   # GPS not within any Sokoto LGA
    flag_gps_poor_accuracy = Column(Boolean, default=False)   # accuracy > 20m
    flag_gps_zero = Column(Boolean, default=False)            # lat==0 & lon==0
    flag_after_hours = Column(Boolean, default=False)         # outside 06:00-19:00
    flag_fast_form = Column(Boolean, default=False)           # < 3 min
    flag_slow_form = Column(Boolean, default=False)           # > 60 min
    flag_sync_lag = Column(Boolean, default=False)            # > 48 h
    flag_refusal = Column(Boolean, default=False)
    check_treatment_date = Column(Date, index=True)   # form check_treatment_date_calc col 21
    hq_user = Column(Text, index=True)                # col 37
    ward_name = Column(Text, index=True)              # populated via spatial join post-upload
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    individuals = relationship(
        "MdaIndividual", back_populates="household",
        primaryjoin="MdaHousehold.formid == foreign(MdaIndividual.hh_formid)",
    )


class MdaBaseline(Base):
    __tablename__ = "mda_baseline"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), index=True)
    state = Column(Text)
    lga = Column(Text, index=True)
    ward = Column(Text, index=True)
    settlement = Column(Text)
    total_treated = Column(Integer)
    # Age/sex breakdown — populated by R5+ uploads. Nullable for R4 historical rows.
    target_1_11_f = Column(Integer)
    target_1_11_m = Column(Integer)
    target_12_59_f = Column(Integer)
    target_12_59_m = Column(Integer)
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class MdaIndividual(Base):
    __tablename__ = "mda_individuals"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), index=True)
    hh_formid = Column(Text, index=True)
    mother_name = Column(Text)
    child_name = Column(Text)
    dob = Column(Date)
    dob_checknote = Column(Text)
    sex = Column(Text)
    height_cm = Column(Text)
    age_in_months = Column(Integer)
    treatment_status = Column(Text)    # '1'=treated '2'=not treated
    not_treated = Column(Text)
    vomit_spill_azt = Column(Text)
    child_id_r2 = Column(Text)
    respondent_hh_id = Column(Text)
    individual_id = Column(Text)
    flag_orphan = Column(Boolean, default=False)
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    household = relationship(
        "MdaHousehold", back_populates="individuals",
        primaryjoin="foreign(MdaIndividual.hh_formid) == MdaHousehold.formid",
    )


class MlosSettlement(Base):
    __tablename__ = "mlos_settlements"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), index=True)
    state_name = Column(Text)
    lga_name = Column(Text, index=True)
    ward_name_raw = Column(Text)        # ward_name as written in MLOS file
    ward_name = Column(Text, index=True)  # ward_name from spatial join with wards boundary
    settlement_name = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    source = Column(Text)
    geom = Column(Geometry("POINT", srid=4326))
    uploaded_at = Column(DateTime(timezone=True), default=datetime.utcnow)


# ── CommCare sync configuration & state ──────────────────────────────────────

class SyncConfig(Base):
    """Per-project CommCare credentials + form IDs to pull from.

    One row per geo_project. Owned by superadmins via the admin panel.
    The password is encrypted at rest using app.services.crypto.
    """
    __tablename__ = "sync_config"

    project_id = Column(Integer, ForeignKey("geo_projects.id"), primary_key=True)
    commcare_base_url = Column(Text, default="https://www.commcarehq.org")
    commcare_app_slug = Column(Text)  # e.g. 'sarmaan'
    commcare_username = Column(Text)
    commcare_password_encrypted = Column(Text)
    form_ids = Column(JSONB, default=list)  # [{"set_name": "SET 1", "form_id": "..."}, ...]
    last_synced_at = Column(DateTime(timezone=True))
    last_status = Column(Text)        # 'ok' / 'partial' / 'error' / 'running'
    last_error = Column(Text)
    last_row_count = Column(Integer, default=0)
    # Live progress for the currently-running sync (polled by the admin panel)
    last_progress_step = Column(Integer)   # feeds processed so far in this run
    last_progress_total = Column(Integer)  # total feeds for this run (= len(form_ids) * 2)
    # Auto-sync scheduler. When auto_sync_enabled is TRUE, sync_worker's
    # scheduler_loop enqueues a job for this project every
    # auto_sync_interval_minutes (measured from last_synced_at). Opt-in.
    auto_sync_enabled          = Column(Boolean, default=False, server_default='false', nullable=False)
    auto_sync_interval_minutes = Column(Integer, default=60,    server_default='60',    nullable=False)
    # Cooperative stop signal — POST /api/sync/stop flips this true; the
    # per-set loop in run_sync polls it between sets and exits early.
    cancel_requested           = Column(Boolean, default=False, server_default='false', nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class SyncHistory(Base):
    """One row per CommCare sync run for audit / history display."""
    __tablename__ = "sync_history"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), index=True)
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    ended_at = Column(DateTime(timezone=True))
    status = Column(Text, default="running")  # 'running' / 'ok' / 'partial' / 'error' / 'cancelled'
    rows_fetched = Column(Integer, default=0)    # raw rows pulled from CommCare
    rows_new     = Column(Integer, default=0)    # rows actually written after watermark filter
    error_message = Column(Text)


class SyncFeedState(Base):
    """Per-feed watermark for incremental CommCare syncs.

    Composite PK (project_id, form_id, record_type). After each successful
    pull from a feed, ``last_received_on`` is advanced to the MAX(received_on)
    of the freshly-ingested rows so the next sync only pulls newer records.
    """
    __tablename__ = "sync_feed_state"
    __table_args__ = (
        PrimaryKeyConstraint("project_id", "form_id", "record_type", name="pk_sync_feed_state"),
    )

    project_id = Column(Integer, ForeignKey("geo_projects.id"))
    form_id = Column(Text)
    record_type = Column(Text)  # 'household' | 'individual'
    last_received_on = Column(DateTime(timezone=True))
    last_synced_at = Column(DateTime(timezone=True))
    last_row_count = Column(Integer, default=0)


class OnpremMirrorState(Base):
    """Watermark + status for the AWS RDS → on-prem reverse mirror.

    One row per project. ``last_mirror_at`` is the high-water mark of
    ``uploaded_at`` from the last successful mirror run — the next run only
    sends rows whose uploaded_at is strictly greater. Status fields are
    surfaced in the admin panel.
    """
    __tablename__ = "onprem_mirror_state"

    project_id = Column(Integer, ForeignKey("geo_projects.id"), primary_key=True)
    last_mirror_at = Column(DateTime(timezone=True))
    last_run_at = Column(DateTime(timezone=True))
    last_status = Column(Text)        # 'ok' / 'error' / 'running'
    last_error = Column(Text)
    last_row_count = Column(Integer, default=0)
    # Live progress for the currently-running mirror (polled by the admin panel).
    last_progress_step  = Column(Integer)
    last_progress_total = Column(Integer)
    last_progress_label = Column(Text)


class SourceConnection(Base):
    """A configured data-source connection other than CommCare.

    CommCare keeps its dedicated, fully-wired config in ``sync_config`` and is
    NOT represented here. This table holds the saved configuration for the
    additional sources surfaced in the Data Sources gallery (Kobo, ODK, Google
    Drive, …). The config is persisted so the connection is "registered", but no
    sync engine consumes it yet — that's a later phase. Non-secret fields live in
    ``config`` (JSONB); secrets (tokens, passwords, service-account keys) are
    Fernet-encrypted as a JSON blob in ``credentials_encrypted`` (same key as
    the CommCare password).
    """
    __tablename__ = "source_connections"
    __table_args__ = (UniqueConstraint("project_id", "source_type", name="uq_source_project_type"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("geo_projects.id"), nullable=False)
    source_type = Column(Text, nullable=False)   # 'kobo' | 'odk' | 'gdrive'
    display_name = Column(Text)
    config = Column(JSONB, default=dict)          # non-secret fields only
    credentials_encrypted = Column(Text)          # Fernet(JSON) of secret fields
    status = Column(Text, default="configured")   # 'configured' | 'draft'
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class UserReport(Base):
    """Operator concerns / issue reports filed from the dashboard "Report a Concern" widget.

    Anyone (public, LGA-viewer, admin) can submit a report — no auth required, but the
    user's role / username (if known) is captured for context. Admins read these in the
    admin portal.
    """
    __tablename__ = "user_reports"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(Text, nullable=False, default="general")   # 'bug' | 'data-issue' | 'feature' | 'general'
    subject = Column(Text)
    message = Column(Text, nullable=False)
    reporter_email = Column(Text)
    reporter_name = Column(Text)
    reporter_role = Column(Text)               # snapshot at submit time: 'public' | 'analyst' | 'admin' | …
    page_url = Column(Text)                    # where the user was when they reported
    user_agent = Column(Text)
    status = Column(Text, default="open")      # 'open' | 'in-progress' | 'resolved' | 'dismissed'
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)
