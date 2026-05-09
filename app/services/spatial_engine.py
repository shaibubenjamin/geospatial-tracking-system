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
    """Return GeoJSON for grid cells of a specific settlement."""
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
                    SELECT 1 FROM points_raw p
                    WHERE p.project_id = g.project_id
                      AND ST_DWithin(
                            p.geom::geography,
                            ST_Centroid(g.geom)::geography,
                            20
                          )
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
    """Return GeoJSON for GPS points, filtered by settlement/ward/LGA."""
    params: Dict[str, Any] = {"project_id": project_id, "limit": limit}
    join_clause = ""
    filters = []

    if unique_cod:
        join_clause = """
            JOIN settlements s ON s.project_id = p.project_id
              AND ST_Within(p.geom, s.geom)
              AND s.unique_cod = :unique_cod
        """
        params["unique_cod"] = unique_cod
    elif wardcode:
        join_clause = """
            JOIN wards w ON w.project_id = p.project_id
              AND ST_Within(p.geom, w.geom)
              AND w.wardcode = :wardcode
        """
        params["wardcode"] = wardcode
    elif lgacode:
        join_clause = """
            JOIN lgas l ON l.project_id = p.project_id
              AND ST_Within(p.geom, l.geom)
              AND l.lgacode = :lgacode
        """
        params["lgacode"] = lgacode

    result = await db.execute(
        text(f"""
            SELECT
                p.id,
                p.latitude,
                p.longitude,
                p.collection_date,
                p.timestamp,
                p.research_assistant,
                p.lga_name,
                p.ward_name,
                p.settlement_name,
                ST_AsGeoJSON(p.geom)::json AS geometry
            FROM points_raw p
            {join_clause}
            WHERE p.project_id = :project_id
            ORDER BY p.timestamp DESC NULLS LAST
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
                "id": row.id,
                "latitude": row.latitude,
                "longitude": row.longitude,
                "collection_date": str(row.collection_date) if row.collection_date else None,
                "timestamp": str(row.timestamp) if row.timestamp else None,
                "research_assistant": row.research_assistant,
                "lga_name": row.lga_name,
                "ward_name": row.ward_name,
                "settlement_name": row.settlement_name,
            },
        })
    return {"type": "FeatureCollection", "features": features}


async def spatial_join_points_to_grids(
    project_id: int,
    point_ids: List[int],
    db: AsyncSession,
) -> List[str]:
    """
    Find all grids intersected by new points (with 20m buffer).
    Returns list of affected unique_cods.
    """
    if not point_ids:
        return []

    result = await db.execute(
        text("""
            SELECT DISTINCT g.unique_cod
            FROM grids g
            JOIN points_raw p ON p.project_id = g.project_id
              AND ST_DWithin(
                    p.geom::geography,
                    ST_Centroid(g.geom)::geography,
                    20
                  )
            WHERE g.project_id = :project_id
              AND p.id = ANY(:point_ids)
        """),
        {"project_id": project_id, "point_ids": point_ids},
    )
    return [row.unique_cod for row in result.fetchall()]


async def compute_settlement_analytics(
    project_id: int,
    unique_cods: Optional[List[str]],
    db: AsyncSession,
) -> int:
    """
    Recompute settlement_analytics for affected settlements.
    If unique_cods is None, recompute ALL settlements.
    Returns count of updated rows.
    """
    params: Dict[str, Any] = {"project_id": project_id}
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
                s.id AS settlement_id,
                s.unique_cod,
                s.lgacode,
                s.wardcode,
                s.settlement_name,
                s.lga_name,
                s.ward_name,
                COUNT(DISTINCT g.id) AS total_grids,
                COUNT(DISTINCT CASE
                    WHEN EXISTS (
                        SELECT 1 FROM points_raw p
                        WHERE p.project_id = s.project_id
                          AND ST_DWithin(
                                p.geom::geography,
                                ST_Centroid(g.geom)::geography,
                                20
                              )
                    ) THEN g.id END
                ) AS visited_grids,
                CASE WHEN COUNT(DISTINCT g.id) > 0
                     THEN ROUND(100.0 * COUNT(DISTINCT CASE
                          WHEN EXISTS (
                              SELECT 1 FROM points_raw p
                              WHERE p.project_id = s.project_id
                                AND ST_DWithin(
                                      p.geom::geography,
                                      ST_Centroid(g.geom)::geography,
                                      20
                                    )
                          ) THEN g.id END
                     ) / NULLIF(COUNT(DISTINCT g.id), 0), 2)
                     ELSE 0 END AS completeness_pct,
                COUNT(DISTINCT p2.id) > 0 AS is_visited,
                COUNT(DISTINCT p2.id) AS point_count,
                NOW() AS last_computed
            FROM settlements s
            LEFT JOIN grids g ON g.unique_cod = s.unique_cod AND g.project_id = s.project_id
            LEFT JOIN points_raw p2 ON p2.project_id = s.project_id
              AND ST_Within(p2.geom, s.geom)
            WHERE s.project_id = :project_id {filter_clause}
            GROUP BY s.project_id, s.id, s.unique_cod, s.lgacode, s.wardcode,
                     s.settlement_name, s.lga_name, s.ward_name
            ON CONFLICT (project_id, settlement_id)
            DO UPDATE SET
                total_grids = EXCLUDED.total_grids,
                visited_grids = EXCLUDED.visited_grids,
                completeness_pct = EXCLUDED.completeness_pct,
                is_visited = EXCLUDED.is_visited,
                point_count = EXCLUDED.point_count,
                last_computed = EXCLUDED.last_computed
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
