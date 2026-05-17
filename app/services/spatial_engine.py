"""
spatial_engine.py
All PostGIS spatial operations for the geospatial tracker.
"""
import json
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def get_lga_geojson(project_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Return GeoJSON FeatureCollection for all LGAs in a project."""
    result = await db.execute(
        text("""
            SELECT
                l.id,
                l.lgacode,
                l.lga_name,
                ST_AsGeoJSON(l.geom)::json AS geometry,
                COUNT(DISTINCT sa.id) AS total_settlements,
                SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END) AS visited_settlements,
                COALESCE(SUM(sa.point_count), 0) AS point_count,
                CASE WHEN COUNT(DISTINCT sa.id) > 0
                     THEN ROUND(100.0 * SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(DISTINCT sa.id), 0), 1)
                     ELSE 0 END AS visitation_pct
            FROM lgas l
            LEFT JOIN settlements s ON s.lgacode = l.lgacode AND s.project_id = l.project_id
            LEFT JOIN settlement_analytics sa ON sa.settlement_id = s.id AND sa.project_id = l.project_id
            WHERE l.project_id = :project_id
            GROUP BY l.id, l.lgacode, l.lga_name, l.geom
            ORDER BY l.lga_name
        """),
        {"project_id": project_id},
    )
    rows = result.fetchall()
    features = []
    for row in rows:
        features.append({
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "id": row.id,
                "lgacode": row.lgacode,
                "lga_name": row.lga_name,
                "total_settlements": row.total_settlements or 0,
                "visited_settlements": row.visited_settlements or 0,
                "point_count": row.point_count or 0,
                "visitation_pct": float(row.visitation_pct or 0),
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def get_ward_geojson(
    project_id: int,
    db: AsyncSession,
    lgacode: Optional[str] = None,
) -> Dict[str, Any]:
    """Return GeoJSON FeatureCollection for wards, optionally filtered by LGA."""
    params: Dict[str, Any] = {"project_id": project_id}
    where_extra = ""
    if lgacode:
        where_extra = "AND w.lgacode = :lgacode"
        params["lgacode"] = lgacode

    result = await db.execute(
        text(f"""
            SELECT
                w.id,
                w.wardcode,
                w.lgacode,
                w.ward_name,
                w.lga_name,
                ST_AsGeoJSON(w.geom)::json AS geometry,
                COUNT(DISTINCT sa.id) AS total_settlements,
                SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END) AS visited_settlements,
                COALESCE(SUM(sa.point_count), 0) AS point_count,
                CASE WHEN COUNT(DISTINCT sa.id) > 0
                     THEN ROUND(100.0 * SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(DISTINCT sa.id), 0), 1)
                     ELSE 0 END AS visitation_pct
            FROM wards w
            LEFT JOIN settlements s ON s.wardcode = w.wardcode AND s.project_id = w.project_id
            LEFT JOIN settlement_analytics sa ON sa.settlement_id = s.id AND sa.project_id = w.project_id
            WHERE w.project_id = :project_id {where_extra}
            GROUP BY w.id, w.wardcode, w.lgacode, w.ward_name, w.lga_name, w.geom
            ORDER BY w.ward_name
        """),
        params,
    )
    rows = result.fetchall()
    features = []
    for row in rows:
        features.append({
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "id": row.id,
                "wardcode": row.wardcode,
                "lgacode": row.lgacode,
                "ward_name": row.ward_name,
                "lga_name": row.lga_name,
                "total_settlements": row.total_settlements or 0,
                "visited_settlements": row.visited_settlements or 0,
                "point_count": row.point_count or 0,
                "visitation_pct": float(row.visitation_pct or 0),
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def get_settlement_geojson(
    project_id: int,
    db: AsyncSession,
    lgacode: Optional[str] = None,
    wardcode: Optional[str] = None,
) -> Dict[str, Any]:
    """Return GeoJSON for settlements with analytics, filtered by LGA or ward."""
    params: Dict[str, Any] = {"project_id": project_id}
    filters = []
    if lgacode:
        filters.append("s.lgacode = :lgacode")
        params["lgacode"] = lgacode
    if wardcode:
        filters.append("s.wardcode = :wardcode")
        params["wardcode"] = wardcode

    where_extra = ("AND " + " AND ".join(filters)) if filters else ""

    result = await db.execute(
        text(f"""
            SELECT
                s.id,
                s.unique_cod,
                s.lgacode,
                s.wardcode,
                s.settlement_name,
                s.lga_name,
                s.ward_name,
                ST_AsGeoJSON(s.geom)::json AS geometry,
                COALESCE(sa.total_grids, 0) AS total_grids,
                COALESCE(sa.visited_grids, 0) AS visited_grids,
                COALESCE(sa.completeness_pct, 0) AS completeness_pct,
                COALESCE(sa.is_visited, FALSE) AS is_visited,
                COALESCE(sa.point_count, 0) AS point_count
            FROM settlements s
            LEFT JOIN settlement_analytics sa
                   ON sa.settlement_id = s.id AND sa.project_id = s.project_id
            WHERE s.project_id = :project_id {where_extra}
            ORDER BY s.settlement_name
        """),
        params,
    )
    rows = result.fetchall()
    features = []
    for row in rows:
        features.append({
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "id": row.id,
                "unique_cod": row.unique_cod,
                "lgacode": row.lgacode,
                "wardcode": row.wardcode,
                "settlement_name": row.settlement_name,
                "lga_name": row.lga_name,
                "ward_name": row.ward_name,
                "total_grids": row.total_grids,
                "visited_grids": row.visited_grids,
                "completeness_pct": float(row.completeness_pct),
                "is_visited": row.is_visited,
                "point_count": row.point_count,
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def get_grid_geojson(
    project_id: int,
    db: AsyncSession,
    unique_cod: str,
) -> Dict[str, Any]:
    """Return GeoJSON for grid cells of a specific settlement.
    has_point = TRUE when ≥1 mda_household GPS point falls within the grid polygon.
    """
    result = await db.execute(
        text("""
            SELECT
                g.id,
                g.unique_cod,
                g.lgacode,
                g.wardcode,
                g.settlement_name,
                ST_AsGeoJSON(g.geom)::json AS geometry,
                CASE WHEN EXISTS (
                    SELECT 1 FROM mda_households h
                    WHERE h.geom IS NOT NULL
                      AND ST_Within(h.geom, g.geom)
                ) THEN TRUE ELSE FALSE END AS has_point
            FROM grids g
            WHERE g.project_id = :project_id
              AND g.unique_cod = :unique_cod
            ORDER BY g.id
        """),
        {"project_id": project_id, "unique_cod": unique_cod},
    )
    rows = result.fetchall()
    features = []
    for row in rows:
        features.append({
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "id": row.id,
                "unique_cod": row.unique_cod,
                "lgacode": row.lgacode,
                "wardcode": row.wardcode,
                "settlement_name": row.settlement_name,
                "has_point": row.has_point,
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def get_points_geojson(
    project_id: int,
    db: AsyncSession,
    unique_cod: Optional[str] = None,
    wardcode: Optional[str] = None,
    lgacode: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """
    Return GeoJSON for MDA household GPS points, coloured by grid intersection.
    Each feature has in_grid=true (green) if the point falls inside any grid cell,
    or in_grid=false (red) if it does not intersect any grid.
    All layers are in EPSG:4326 — no reprojection needed.
    """
    params: Dict[str, Any] = {"project_id": project_id, "limit": limit}
    extra_join = ""
    extra_filter = "h.geom IS NOT NULL"

    if unique_cod:
        # Spatially filter to points within the named settlement polygon
        extra_join = """
            JOIN settlements s
              ON s.project_id = :project_id
             AND s.unique_cod  = :unique_cod
             AND ST_Within(h.geom, s.geom)
        """
        params["unique_cod"] = unique_cod
    elif wardcode:
        extra_join = """
            JOIN wards w
              ON w.project_id = :project_id
             AND w.wardcode    = :wardcode
             AND ST_Within(h.geom, w.geom)
        """
        params["wardcode"] = wardcode
    elif lgacode:
        extra_join = """
            JOIN lgas l
              ON l.project_id = :project_id
             AND l.lgacode     = :lgacode
             AND ST_Within(h.geom, l.geom)
        """
        params["lgacode"] = lgacode

    result = await db.execute(
        text(f"""
            SELECT
                h.id,
                h.latitude,
                h.longitude,
                h.date_trt          AS collection_date,
                h.started_time      AS timestamp,
                h.hq_user           AS research_assistant,
                h.lga               AS lga_name,
                h.ward_name,
                -- in_grid: pre-computed flag (TRUE = point inside a grid cell = green dot)
                h.in_grid,
                ST_AsGeoJSON(h.geom, 6)::json AS geometry
            FROM mda_households h
            {extra_join}
            WHERE {extra_filter}
            ORDER BY h.started_time DESC NULLS LAST
            LIMIT :limit
        """),
        params,
    )
    rows = result.fetchall()
    features = []
    for row in rows:
        features.append({
            "type": "Feature",
            "geometry": row.geometry,
            "properties": {
                "id":                  row.id,
                "latitude":            row.latitude,
                "longitude":           row.longitude,
                "collection_date":     str(row.collection_date) if row.collection_date else None,
                "timestamp":           str(row.timestamp)       if row.timestamp       else None,
                "research_assistant":  row.research_assistant,
                "lga_name":            row.lga_name,
                "ward_name":           row.ward_name,
                "in_grid":             bool(row.in_grid),
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def spatial_join_points_to_grids(
    project_id: int,
    point_ids: List[int],
    db: AsyncSession,
) -> List[str]:
    """
    Find all grids intersected by new MDA household GPS points (by household id).
    Returns list of affected settlement unique_cods.
    """
    if not point_ids:
        return []

    result = await db.execute(
        text("""
            SELECT DISTINCT g.unique_cod
            FROM grids g
            JOIN mda_households h
              ON h.geom IS NOT NULL
             AND ST_Within(h.geom, g.geom)
            WHERE g.project_id = :project_id
              AND h.id = ANY(:point_ids)
        """),
        {"project_id": project_id, "point_ids": point_ids},
    )
    return [row.unique_cod for row in result.fetchall()]


VISIT_THRESHOLD_PCT = 70  # Settlement is "visited" when completeness >= this %


async def compute_settlement_analytics(
    project_id: int,
    unique_cods: Optional[List[str]],
    db: AsyncSession,
) -> int:
    """
    Recompute settlement_analytics using MDA household GPS points (mda_households.geom).

    - Visited grid  = grid cell that contains ≥1 mda_household point (ST_Within)
    - Completeness  = visited_grids / total_grids × 100
    - is_visited    = completeness_pct >= VISIT_THRESHOLD_PCT (70%)
                      The map still renders each grid cell's individual green/red status.

    If unique_cods is None, recompute ALL settlements.
    """
    params: Dict[str, Any] = {"project_id": project_id,
                               "visit_threshold": VISIT_THRESHOLD_PCT}
    filter_clause = ""
    if unique_cods:
        filter_clause = "AND s.unique_cod = ANY(:unique_cods)"
        params["unique_cods"] = unique_cods

    result = await db.execute(
        text(f"""
            INSERT INTO settlement_analytics
              (project_id, settlement_id, unique_cod, lgacode, wardcode,
               settlement_name, lga_name, ward_name,
               total_grids, visited_grids, completeness_pct,
               is_visited, point_count, last_computed)
            SELECT
                s.project_id,
                s.id                 AS settlement_id,
                s.unique_cod,
                s.lgacode,
                s.wardcode,
                s.settlement_name,
                s.lga_name,
                s.ward_name,
                COUNT(DISTINCT g.id) AS total_grids,

                -- Visited grids: each grid cell that contains ≥1 MDA household GPS point
                COUNT(DISTINCT CASE
                    WHEN EXISTS (
                        SELECT 1 FROM mda_households h
                        WHERE h.geom IS NOT NULL
                          AND ST_Within(h.geom, g.geom)
                    ) THEN g.id END
                ) AS visited_grids,

                -- Completeness %
                CASE WHEN COUNT(DISTINCT g.id) > 0
                     THEN ROUND(100.0 * COUNT(DISTINCT CASE
                          WHEN EXISTS (
                              SELECT 1 FROM mda_households h
                              WHERE h.geom IS NOT NULL
                                AND ST_Within(h.geom, g.geom)
                          ) THEN g.id END
                     ) / NULLIF(COUNT(DISTINCT g.id), 0), 2)
                     ELSE 0 END AS completeness_pct,

                -- is_visited: completeness >= threshold OR any point within settlement polygon
                CASE
                    WHEN COUNT(DISTINCT g.id) > 0
                    THEN (
                        COUNT(DISTINCT CASE WHEN EXISTS (
                            SELECT 1 FROM mda_households h
                            WHERE h.geom IS NOT NULL AND ST_Within(h.geom, g.geom)
                        ) THEN g.id END) * 100.0
                        / NULLIF(COUNT(DISTINCT g.id), 0)
                    ) >= :visit_threshold
                    ELSE EXISTS (
                        SELECT 1 FROM mda_households h2
                        WHERE h2.geom IS NOT NULL AND ST_Within(h2.geom, s.geom)
                    )
                END AS is_visited,

                -- Total household GPS points inside the settlement polygon
                (SELECT COUNT(*) FROM mda_households h3
                 WHERE h3.geom IS NOT NULL AND ST_Within(h3.geom, s.geom)
                ) AS point_count,

                NOW() AS last_computed
            FROM settlements s
            LEFT JOIN grids g
                   ON g.unique_cod  = s.unique_cod
                  AND g.project_id  = s.project_id
            WHERE s.project_id = :project_id {filter_clause}
            GROUP BY s.project_id, s.id, s.unique_cod, s.lgacode, s.wardcode,
                     s.settlement_name, s.lga_name, s.ward_name
            ON CONFLICT (project_id, settlement_id) DO UPDATE SET
                total_grids      = EXCLUDED.total_grids,
                visited_grids    = EXCLUDED.visited_grids,
                completeness_pct = EXCLUDED.completeness_pct,
                is_visited       = EXCLUDED.is_visited,
                point_count      = EXCLUDED.point_count,
                last_computed    = EXCLUDED.last_computed
        """),
        params,
    )
    await db.commit()
    return result.rowcount


async def get_coverage_timeline(
    project_id: int,
    db: AsyncSession,
) -> List[Dict[str, Any]]:
    """Coverage over time: daily cumulative visited settlements and point count."""
    result = await db.execute(
        text("""
            WITH daily_points AS (
                SELECT
                    COALESCE(collection_date, uploaded_at::date) AS day,
                    COUNT(*) AS point_count
                FROM points_raw
                WHERE project_id = :project_id
                GROUP BY 1
            ),
            cumulative AS (
                SELECT
                    day,
                    point_count,
                    SUM(point_count) OVER (ORDER BY day) AS cumulative_points
                FROM daily_points
            ),
            total_settlements AS (
                SELECT COUNT(*) AS total FROM settlements WHERE project_id = :project_id
            )
            SELECT
                c.day AS date,
                c.point_count,
                c.cumulative_points,
                ts.total AS total_settlements
            FROM cumulative c, total_settlements ts
            ORDER BY c.day
        """),
        {"project_id": project_id},
    )
    rows = result.fetchall()
    return [
        {
            "date": str(row.date),
            "point_count": row.point_count,
            "cumulative_points": row.cumulative_points,
            "total_settlements": row.total_settlements,
        }
        for row in rows
    ]
