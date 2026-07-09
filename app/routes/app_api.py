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

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import GeoProject, User
from app.routes.auth import get_current_user, allowed_states_of
from typing import Optional as _Optional

from app.routes.mda import (
    resolve_pid, mda_overview, mda_coverage_lga, mda_coverage_ward,
    geo_completeness, geo_coverage_summary, mda_trends_daily,
    geo_lgas_coverage, geo_wards_coverage, geo_settlements_coverage,
)
from app.routes import mda as mda_route
from app.services.spatial_engine import get_points_geojson
from app.services import geo_cache

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
    # State scope: a non-superadmin only sees their assigned state(s).
    allowed = allowed_states_of(_u)
    if allowed is not None:
        projects = [p for p in projects if (p.state_name or "").strip().lower() in allowed]
    # Non-admins (LGA field logins) only see the IN-WINDOW campaign: started and
    # not yet ended. Old/not-started/ended rounds (e.g. a pilot / Round 1) are
    # hidden so the app selector shows only the active campaign. Admins and
    # superadmins still see every round.
    if not (bool(getattr(_u, "is_superadmin", False)) or bool(getattr(_u, "is_admin", False))):
        from datetime import date as _date
        _t = _date.today()
        def _in_window(p):
            # Visible while running OR in mop-up: started and not explicitly
            # ended by an admin. A PASSED campaign_end_date only means mop-up
            # (still collecting) — NOT hidden. Keying off campaign_end_date here
            # was hiding the live round from field users the day after its planned
            # end, even though mop-up data was still coming in.
            s = p.campaign_start_date
            return bool(s and s <= _t and not bool(getattr(p, "campaign_ended", False)))
        projects = [p for p in projects if _in_window(p)]
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
# GET /api/app/trends/daily — daily forms/treated/teams (for the trend chart).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/trends/daily")
async def app_trends_daily(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-day forms/treated/teams for the selected project — the app derives
    cumulative coverage over campaign days from this."""
    return await mda_trends_daily(pid=pid, db=db, _u=user)


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
# GET /api/app/coverage/ward — ward coverage, optionally within one LGA.
# Drives the LGA → ward drill-down in the app.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/coverage/ward")
async def app_coverage_ward(
    lga: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-ward coverage for the selected project, filtered to one LGA when
    ``lga`` is given. Reuses the web dashboard's ``mda_coverage_ward``.

    Fallback: some LGAs have treatment data but their households' ``ward_name``
    was never populated by the GPS→ward spatial join, so the household-based
    query returns no wards — and the user can't drill to settlements. When that
    happens (and an LGA is specified), derive the ward list from
    ``settlement_analytics`` (which DOES carry ward_name), reporting visitation
    coverage (% of settlements visited) so the drill still works.
    """
    rows = await mda_coverage_ward(lga=lga, pid=pid, db=db, _u=user)
    if rows or not lga:
        return rows
    params: dict = {"pid": pid, "lga": lga}
    lga_sql = mda_route._lga_and(mda_route.allowed_lgas_of(user), "sa.lga_name", params)
    res = await db.execute(text(f"""
        SELECT sa.ward_name,
               COUNT(*) AS total,
               SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END) AS visited
        FROM settlement_analytics sa
        WHERE sa.project_id = :pid AND sa.lga_name = :lga AND sa.ward_name IS NOT NULL{lga_sql}
        GROUP BY sa.ward_name
        ORDER BY sa.ward_name
    """), params)
    out = []
    for r in res.fetchall():
        total = int(r.total or 0)
        visited = int(r.visited or 0)
        out.append({
            "ward_name": r.ward_name,
            "lga": lga,
            "forms": 0,
            "actual_treated": 0,
            "baseline_total": 0,
            # Visitation coverage (settlements visited ÷ total) — NOT treatment
            # coverage, since these wards have no household-linked treatment data.
            "coverage_pct": round(100.0 * visited / total, 1) if total else 0.0,
            "teams": 0,
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/coverage/settlement — per-settlement coverage within a ward.
# Drives the ward → settlement drill (the third level under LGA → ward).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/coverage/settlement")
async def app_coverage_settlement(
    lga: Optional[str] = None,
    ward: Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Settlements within a ward, each with visited flag + completeness %.

    Lightweight JSON (no geometry, unlike /geo/settlements) straight from
    ``settlement_analytics``. Filtered by LGA and ward NAME — the app drills down
    by the names it already has from /coverage/{lga,ward}.
    """
    clauses = ["sa.project_id = :pid"]
    params: dict = {"pid": pid}
    if lga:
        clauses.append("sa.lga_name = :lga")
        params["lga"] = lga
    if ward:
        clauses.append("sa.ward_name = :ward")
        params["ward"] = ward
    where = " AND ".join(clauses) + mda_route._lga_and(mda_route.allowed_lgas_of(user), "sa.lga_name", params)
    res = await db.execute(text(f"""
        SELECT sa.settlement_name, sa.ward_name, sa.lga_name,
               COALESCE(sa.is_visited, FALSE) AS is_visited,
               COALESCE(sa.completeness_pct, 0)::float AS completeness_pct,
               COALESCE(sa.point_count, 0) AS point_count
        FROM settlement_analytics sa
        WHERE {where}
        ORDER BY sa.completeness_pct DESC, sa.settlement_name
    """), params)
    return [
        {
            "settlement_name": r.settlement_name,
            "ward_name": r.ward_name,
            "lga_name": r.lga_name,
            "is_visited": bool(r.is_visited),
            "completeness_pct": round(float(r.completeness_pct or 0), 1),
            "point_count": int(r.point_count or 0),
        }
        for r in res.fetchall()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/geo/wards — ward polygons + coverage in one call (map layer).
# ─────────────────────────────────────────────────────────────────────────────
async def _boundary_pid(pid: int, db: AsyncSession) -> int:
    """Resolve which project holds the boundary polygons for ``pid``'s state.

    Delegates to the shared ``_resolve_boundary_pid`` (own-boundaries-first) so
    the app resolves the SAME boundary as the web analytics and the sync
    recompute. A round with its own boundary set (e.g. Kano R3, project 4) is
    served against its own polygons; a round without (Sokoto R5) reuses the
    state's canonical set. Previously this returned the lowest-id sibling, which
    made the app read a different boundary than settlement_analytics was keyed to
    (wardcodes didn't match → 0 coverage everywhere).
    """
    from app.services.spatial_engine import _resolve_boundary_pid
    return await _resolve_boundary_pid(pid, db)


@router.get("/geo/wards")
async def app_geo_wards(
    request: Request,
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

    Cached per (project, LGA scope) so repeated map loads during a campaign
    don't re-run the aggregation; served via the shared geo_cache (ETag + 304).
    """
    scope = mda_route.allowed_lgas_of(_u)

    async def _produce() -> dict:
        bpid = await _boundary_pid(pid, db)
        params: dict = {"pid": pid, "bpid": bpid}
        lga_sql = mda_route._lga_and(scope, "w.lga_name", params)
        res = await db.execute(
            text(
                f"""
                WITH cov AS (
                  SELECT wardcode,
                         -- visitation = share of settlements with >=1 GPS point.
                         AVG(CASE WHEN COALESCE(point_count, 0) > 0 THEN 1.0 ELSE 0.0 END) AS visit_frac,
                         COUNT(*) AS settlements,
                         COUNT(*) FILTER (WHERE COALESCE(point_count, 0) > 0) AS settlements_visited
                  FROM settlement_analytics
                  WHERE project_id = :pid
                  GROUP BY wardcode
                )
                SELECT w.ward_name, w.lga_name, w.wardcode,
                       ST_AsGeoJSON(ST_SimplifyPreserveTopology(w.geom, 0.0005)) AS geom,
                       COALESCE(cov.visit_frac, 0)::float   AS visit_frac,
                       COALESCE(cov.settlements, 0)         AS settlements,
                       COALESCE(cov.settlements_visited, 0) AS settlements_visited
                FROM wards w
                LEFT JOIN cov ON cov.wardcode = w.wardcode
                WHERE w.project_id = :bpid{lga_sql}
                """
            ),
            params,
        )
        import json as _json

        features = []
        for r in res.fetchall():
            if not r.geom:
                continue
            vfrac = float(r.visit_frac or 0)
            features.append(
                {
                    "type": "Feature",
                    "geometry": _json.loads(r.geom),
                    "properties": {
                        "ward_name": r.ward_name,
                        "lga_name": r.lga_name,
                        "wardcode": r.wardcode,
                        "visitation_pct": round(100.0 * vfrac, 1),
                        "settlements": int(r.settlements or 0),
                        "settlements_visited": int(r.settlements_visited or 0),
                        "is_at_target": vfrac >= 0.7,
                    },
                }
            )
        return {"type": "FeatureCollection", "project_id": pid, "features": features}

    key = geo_cache.make_key("app_wards", pid, scope)
    return await geo_cache.respond(request, key, _produce)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/geo/{lgas,settlements} — gated coverage geojson for the map.
# (wards already exists above.) These require a token, so the geographic data
# is only reachable from within the authenticated app.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/geo/lgas")
async def app_geo_lgas(
    request: Request,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    scope = mda_route.allowed_lgas_of(user)
    key = geo_cache.make_key("app_lgas", pid, scope)
    return await geo_cache.respond(request, key, lambda: geo_lgas_coverage(pid=pid, db=db, _u=user))


@router.get("/geo/settlements")
async def app_geo_settlements(
    request: Request,
    lgacode: _Optional[str] = None,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    scope = mda_route.allowed_lgas_of(user)
    key = geo_cache.make_key("app_settlements", pid, scope, lgacode=lgacode)
    return await geo_cache.respond(
        request, key, lambda: geo_settlements_coverage(lgacode=lgacode, pid=pid, db=db, _u=user)
    )


@router.get("/geo/points")
async def app_geo_points(
    lgacode: _Optional[str] = None,
    wardcode: _Optional[str] = None,
    unique_cod: _Optional[str] = None,
    limit: int = 4000,
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """GPS submission points for the map, filtered to the drilled LGA/ward/
    settlement. Green = inside a settlement or grid cell (``in_bounds``), red =
    outside. Slimmed to geometry + in_bounds (no PII, small payload) since the
    app only needs to plot dots — the web dashboard keeps the full detail."""
    fc = await get_points_geojson(
        pid, db, unique_cod=unique_cod, wardcode=wardcode, lgacode=lgacode, limit=limit,
        allowed_lgas=mda_route.allowed_lgas_of(user),
    )
    features = [
        {
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {"in_bounds": bool((f.get("properties") or {}).get("in_bounds"))},
        }
        for f in fc.get("features", [])
        if f.get("geometry")
    ]
    return {"type": "FeatureCollection", "features": features}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/app/geo/summary — geographic-view summary (mirrors the web).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/geo/summary")
async def app_geo_summary(
    pid: int = Depends(resolve_pid),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Overall grid completeness + LGA/ward/settlement at-threshold counts —
    the headline numbers from the web dashboard's Geographic View."""
    completeness = await geo_completeness(pid=pid, db=db, _u=user)
    coverage = await geo_coverage_summary(pid=pid, db=db, _u=user)
    return {"completeness": completeness, "coverage_summary": coverage}


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
    lgas = mda_route.allowed_lgas_of(_u)

    cur_params: dict = {"pid": pid, "lat": lat, "lon": lon}
    cur_lga_sql = mda_route._lga_and(lgas, "sa.lga_name", cur_params)
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
              AND ST_Contains(s.geom, {point}){cur_lga_sql}
            ORDER BY ST_Area(s.geom) ASC
            LIMIT 1
            """
        ),
        cur_params,
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

    # Ranked "where to cover next" list: the nearest settlements not yet
    # covered (no GPS point AND grid completeness < 70%), closest first — a
    # practical to-do list for the team standing at (lat, lon).
    near_params: dict = {"pid": pid, "lat": lat, "lon": lon}
    near_lga_sql = mda_route._lga_and(lgas, "sa.lga_name", near_params)
    near_res = await db.execute(
        text(
            f"""
            SELECT sa.settlement_name, sa.ward_name, sa.lga_name,
                   COALESCE(sa.completeness_pct, 0)::float AS completeness_pct,
                   COALESCE(sa.is_visited, FALSE) AS is_visited,
                   ST_Distance(s.geom::geography, {point}::geography) AS dist_m,
                   ST_Y(ST_Centroid(s.geom)) AS lat,
                   ST_X(ST_Centroid(s.geom)) AS lon
            FROM settlement_analytics sa
            JOIN settlements s ON s.id = sa.settlement_id
            WHERE sa.project_id = :pid
              AND NOT (sa.is_visited OR COALESCE(sa.completeness_pct, 0) >= 70){near_lga_sql}
            ORDER BY s.geom <-> {point}
            LIMIT 12
            """
        ),
        near_params,
    )
    recommendations = [
        {
            "settlement_name": r.settlement_name,
            "ward_name": r.ward_name,
            "lga_name": r.lga_name,
            "completeness_pct": round(float(r.completeness_pct or 0), 1),
            "is_visited": bool(r.is_visited),
            "distance_m": round(float(r.dist_m or 0), 0),
            "lat": float(r.lat) if r.lat is not None else None,
            "lon": float(r.lon) if r.lon is not None else None,
        }
        for r in near_res.fetchall()
    ]

    return {
        "project_id": pid,
        "query": {"lat": lat, "lon": lon},
        "current": current,
        # Kept for older clients; equals recommendations[0].
        "nearest_uncovered": recommendations[0] if recommendations else None,
        "recommendations": recommendations,
    }
