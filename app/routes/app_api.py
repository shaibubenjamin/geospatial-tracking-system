"""
app_api.py — App-facing API surface for the ERITAS Android companion app.

Everything here lives under the ``/api/app`` prefix, which is the version-gated
surface (see the ``enforce_app_version`` middleware in app/main.py and
docs/apk-app-blueprint.md). An outdated/tampered client is rejected with
HTTP 426 before reaching these handlers, so this is the real force-update
enforcement point — not just the client-side update wall.

These endpoints are deliberately thin: they reuse the existing MDA query logic
where it already exists (overview, ward coverage) and add only the genuinely
new piece the app needs — a point-in-polygon "where am I / what's left to
cover" locator scoped to a selected project (state + round).

All endpoints take an optional ``project_id`` and otherwise default to the
active project via ``resolve_pid`` — so the app works for *any* state/round.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import GeoProject, User
from app.routes.auth import get_current_user
from app.routes.mda import resolve_pid, mda_overview, mda_coverage_lga
from app.routes import mda as mda_route

router = APIRouter(prefix="/app", tags=["app"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/projects — the state/round selector source.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/projects")
async def app_projects(
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(get_current_user),
):
    """Every project (state + round) the app can scope to.

    Ordered active-first, then newest round, so the app can default its
    selector to the live campaign. The app groups these by ``state_name``
    then ``round_number``.
    """
    res = await db.execute(
        select(GeoProject).order_by(
            GeoProject.is_active.desc(),
            GeoProject.state_name.asc(),
            GeoProject.round_number.desc().nullslast(),
            GeoProject.id.desc(),
        )
    )
    projects = res.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "state_name": p.state_name,
            "round_number": p.round_number,
            "is_active": bool(p.is_active),
        }
        for p in projects
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/overview — dashboard KPIs (delegates to the web overview logic).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/overview")
async def app_overview(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Same numbers as the web dashboard's overview tiles, project-scoped.

    Reuses ``mda_overview`` directly so the app never drifts from the web
    definitions of coverage_pct / qc flags / campaign day.
    """
    return await mda_overview(pid=pid, db=db, _u=user)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/coverage/lga — per-LGA coverage (mirrors the web overview).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/coverage/lga")
async def app_coverage_lga(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Coverage % per LGA (treated vs baseline) for the selected project.

    Reuses the web dashboard's ``mda_coverage_lga`` so the app's LGA list
    matches the overview page exactly.
    """
    return await mda_coverage_lga(pid=pid, db=db, _u=user)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/geo/wards — ward polygons + coverage in one call (map layer).
# ─────────────────────────────────────────────────────────────────────────────
async def _boundary_pid(pid: int, db: AsyncSession) -> int:
    """Resolve which project holds the ward polygons for ``pid``'s state.

    Boundaries are typically attached to the canonical project for a state
    (e.g. Sokoto R4 holds Sokoto's polygons; R5 reuses them), while coverage
    analytics are computed per round. This returns the lowest-id project in
    the same state that actually has ward rows, falling back to ``pid``.
    """
    res = await db.execute(
        text(
            """
            SELECT MIN(p2.id)
            FROM geo_projects p1
            JOIN geo_projects p2 ON p2.state_name = p1.state_name
            WHERE p1.id = :pid
              AND EXISTS (SELECT 1 FROM wards w WHERE w.project_id = p2.id)
            """
        ),
        {"pid": pid},
    )
    row = res.fetchone()
    return int(row[0]) if row and row[0] is not None else pid


@router.get("/geo/wards")
async def app_geo_wards(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(get_current_user),
):
    """Ward polygons as GeoJSON, each tagged with its coverage fraction.

    Coverage = mean of per-settlement "covered" (is_visited OR completeness
    ≥ 70%) within the ward, from ``settlement_analytics`` for the selected
    project. Geometry comes from the state's boundary project, joined by the
    round-stable ``wardcode``. Ward granularity keeps the payload phone-sized
    (~hundreds of features) — settlement granularity stays server-side and is
    reached point-by-point via /api/app/near.
    """
    bpid = await _boundary_pid(pid, db)
    res = await db.execute(
        text(
            """
            WITH cov AS (
              SELECT wardcode,
                     AVG(CASE WHEN is_visited OR COALESCE(completeness_pct, 0) >= 70
                              THEN 1.0 ELSE 0.0 END) AS frac,
                     COUNT(*) AS settlements,
                     COUNT(*) FILTER (
                       WHERE is_visited OR COALESCE(completeness_pct, 0) >= 70
                     ) AS settlements_covered
              FROM settlement_analytics
              WHERE project_id = :pid
              GROUP BY wardcode
            )
            SELECT w.ward_name, w.lga_name, w.wardcode,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(w.geom, 0.0005)) AS geom,
                   COALESCE(cov.frac, 0)::float        AS frac,
                   COALESCE(cov.settlements, 0)        AS settlements,
                   COALESCE(cov.settlements_covered, 0) AS settlements_covered
            FROM wards w
            LEFT JOIN cov ON cov.wardcode = w.wardcode
            WHERE w.project_id = :bpid
            """
        ),
        {"pid": pid, "bpid": bpid},
    )
    import json as _json

    features = []
    for r in res.fetchall():
        if not r.geom:
            continue
        frac = float(r.frac or 0)
        features.append(
            {
                "type": "Feature",
                "geometry": _json.loads(r.geom),
                "properties": {
                    "ward_name": r.ward_name,
                    "lga_name": r.lga_name,
                    "wardcode": r.wardcode,
                    "coverage_pct": round(100.0 * frac, 1),
                    "settlements": int(r.settlements or 0),
                    "settlements_covered": int(r.settlements_covered or 0),
                    "is_at_target": frac >= 0.7,
                },
            }
        )
    return {"type": "FeatureCollection", "project_id": pid, "features": features}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/near — the core field aid: where am I, what's left to cover.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/near")
async def app_near(
    lat: float,
    lon: float,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(get_current_user),
):
    """Given the device's GPS, answer "which area am I in and where next?".

    Returns:
      * ``current`` — the settlement polygon containing the point (with its
        ward/LGA and coverage status for the selected project), or null if the
        point is outside every settlement boundary.
      * ``nearest_uncovered`` — the closest settlement still below the 70%
        coverage threshold, with great-circle distance and a bearing target,
        so the field user knows where to head next.
    """
    point = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"

    cur_res = await db.execute(
        text(
            f"""
            SELECT sa.settlement_name, sa.ward_name, sa.lga_name,
                   COALESCE(sa.completeness_pct, 0)::float AS completeness_pct,
                   COALESCE(sa.is_visited, FALSE) AS is_visited,
                   COALESCE(sa.point_count, 0) AS point_count
            FROM settlement_analytics sa
            JOIN settlements s ON s.id = sa.settlement_id
            WHERE sa.project_id = :pid
              AND ST_Contains(s.geom, {point})
            ORDER BY ST_Area(s.geom) ASC
            LIMIT 1
            """
        ),
        {"pid": pid, "lat": lat, "lon": lon},
    )
    cur = cur_res.fetchone()
    current = None
    if cur:
        completeness = float(cur.completeness_pct or 0)
        current = {
            "settlement_name": cur.settlement_name,
            "ward_name": cur.ward_name,
            "lga_name": cur.lga_name,
            "completeness_pct": round(completeness, 1),
            "is_covered": bool(cur.is_visited) or completeness >= 70,
            "point_count": int(cur.point_count or 0),
        }

    near_res = await db.execute(
        text(
            f"""
            SELECT sa.settlement_name, sa.ward_name, sa.lga_name,
                   COALESCE(sa.completeness_pct, 0)::float AS completeness_pct,
                   ST_Distance(s.geom::geography, {point}::geography) AS dist_m,
                   ST_Y(ST_Centroid(s.geom)) AS lat,
                   ST_X(ST_Centroid(s.geom)) AS lon
            FROM settlement_analytics sa
            JOIN settlements s ON s.id = sa.settlement_id
            WHERE sa.project_id = :pid
              AND NOT (sa.is_visited OR COALESCE(sa.completeness_pct, 0) >= 70)
            ORDER BY s.geom <-> {point}
            LIMIT 1
            """
        ),
        {"pid": pid, "lat": lat, "lon": lon},
    )
    nxt = near_res.fetchone()
    nearest_uncovered = None
    if nxt:
        nearest_uncovered = {
            "settlement_name": nxt.settlement_name,
            "ward_name": nxt.ward_name,
            "lga_name": nxt.lga_name,
            "completeness_pct": round(float(nxt.completeness_pct or 0), 1),
            "distance_m": round(float(nxt.dist_m or 0), 0),
            "lat": float(nxt.lat) if nxt.lat is not None else None,
            "lon": float(nxt.lon) if nxt.lon is not None else None,
        }

    return {
        "project_id": pid,
        "query": {"lat": lat, "lon": lon},
        "current": current,
        "nearest_uncovered": nearest_uncovered,
    }
