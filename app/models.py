import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean,
    Float, DateTime, Date, ForeignKey, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
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
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
