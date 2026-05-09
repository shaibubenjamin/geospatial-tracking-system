"""
aggregation_engine.py
Settlement → Ward → LGA metric rollups.
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def get_lga_metrics(
    project_id: int,
    db: AsyncSession,
) -> List[Dict[str, Any]]:
    """Roll up settlement analytics to LGA level."""
    result = await db.execute(
        text("""
            SELECT
                l.lgacode,
                l.lga_name,
                COUNT(DISTINCT s.id) AS total_settlements,
                SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END) AS visited_settlements,
                CASE WHEN COUNT(DISTINCT s.id) > 0
                     THEN ROUND(100.0 * SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(DISTINCT s.id), 0), 1)
                     ELSE 0 END AS visitation_pct,
                COALESCE(SUM(sa.total_grids), 0) AS total_grids,
                COALESCE(SUM(sa.visited_grids), 0) AS visited_grids,
                CASE WHEN COALESCE(SUM(sa.total_grids), 0) > 0
                     THEN ROUND(100.0 * COALESCE(SUM(sa.visited_grids), 0)
                          / NULLIF(COALESCE(SUM(sa.total_grids), 0), 0), 1)
                     ELSE 0 END AS completeness_pct,
                COALESCE(SUM(sa.point_count), 0) AS point_count
            FROM lgas l
            LEFT JOIN settlements s ON s.lgacode = l.lgacode AND s.project_id = l.project_id
            LEFT JOIN settlement_analytics sa ON sa.settlement_id = s.id
            WHERE l.project_id = :project_id
            GROUP BY l.lgacode, l.lga_name
            ORDER BY l.lga_name
        """),
        {"project_id": project_id},
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def get_ward_metrics(
    project_id: int,
    db: AsyncSession,
    lgacode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Roll up settlement analytics to ward level."""
    params: Dict[str, Any] = {"project_id": project_id}
    filter_extra = ""
    if lgacode:
        filter_extra = "AND w.lgacode = :lgacode"
        params["lgacode"] = lgacode

    result = await db.execute(
        text(f"""
            SELECT
                w.wardcode,
                w.ward_name,
                w.lgacode,
                w.lga_name,
                COUNT(DISTINCT s.id) AS total_settlements,
                SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END) AS visited_settlements,
                CASE WHEN COUNT(DISTINCT s.id) > 0
                     THEN ROUND(100.0 * SUM(CASE WHEN sa.is_visited THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(DISTINCT s.id), 0), 1)
                     ELSE 0 END AS visitation_pct,
                COALESCE(SUM(sa.total_grids), 0) AS total_grids,
                COALESCE(SUM(sa.visited_grids), 0) AS visited_grids,
                CASE WHEN COALESCE(SUM(sa.total_grids), 0) > 0
                     THEN ROUND(100.0 * COALESCE(SUM(sa.visited_grids), 0)
                          / NULLIF(COALESCE(SUM(sa.total_grids), 0), 0), 1)
                     ELSE 0 END AS completeness_pct,
                COALESCE(SUM(sa.point_count), 0) AS point_count
            FROM wards w
            LEFT JOIN settlements s ON s.wardcode = w.wardcode AND s.project_id = w.project_id
            LEFT JOIN settlement_analytics sa ON sa.settlement_id = s.id
            WHERE w.project_id = :project_id {filter_extra}
            GROUP BY w.wardcode, w.ward_name, w.lgacode, w.lga_name
            ORDER BY w.ward_name
        """),
        params,
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def get_settlement_metrics(
    project_id: int,
    db: AsyncSession,
    wardcode: Optional[str] = None,
    lgacode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return settlement-level metrics with analytics."""
    params: Dict[str, Any] = {"project_id": project_id}
    filters = []
    if wardcode:
        filters.append("sa.wardcode = :wardcode")
        params["wardcode"] = wardcode
    if lgacode:
        filters.append("sa.lgacode = :lgacode")
        params["lgacode"] = lgacode
    where_extra = ("AND " + " AND ".join(filters)) if filters else ""

    result = await db.execute(
        text(f"""
            SELECT
                sa.unique_cod,
                sa.lgacode,
                sa.wardcode,
                sa.settlement_name,
                sa.lga_name,
                sa.ward_name,
                sa.total_grids,
                sa.visited_grids,
                sa.completeness_pct,
                sa.is_visited,
                sa.point_count,
                sa.last_computed
            FROM settlement_analytics sa
            WHERE sa.project_id = :project_id {where_extra}
            ORDER BY sa.settlement_name
        """),
        params,
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def get_project_summary(
    project_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Get high-level project summary statistics."""
    result = await db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM lgas WHERE project_id = :pid) AS total_lgas,
                (SELECT COUNT(*) FROM wards WHERE project_id = :pid) AS total_wards,
                (SELECT COUNT(*) FROM settlements WHERE project_id = :pid) AS total_settlements,
                (SELECT COUNT(*) FROM settlement_analytics WHERE project_id = :pid AND is_visited = TRUE) AS visited_settlements,
                (SELECT COALESCE(SUM(total_grids), 0) FROM settlement_analytics WHERE project_id = :pid) AS total_grids,
                (SELECT COALESCE(SUM(visited_grids), 0) FROM settlement_analytics WHERE project_id = :pid) AS visited_grids,
                (SELECT COUNT(*) FROM points_raw WHERE project_id = :pid) AS total_points,
                (SELECT COUNT(*) FROM qc_flags WHERE project_id = :pid AND flag_type = 'out_of_bound') AS qc_out_of_bound,
                (SELECT COUNT(*) FROM qc_flags WHERE project_id = :pid AND flag_type = 'time_violation') AS qc_time_violations,
                (SELECT COUNT(*) FROM qc_flags WHERE project_id = :pid AND flag_type = 'stacked_point') AS qc_stacked_points
        """),
        {"pid": project_id},
    )
    row = result.fetchone()
    total_s = row.total_settlements or 0
    visited_s = row.visited_settlements or 0
    total_g = row.total_grids or 0
    visited_g = row.visited_grids or 0
    return {
        "total_lgas": row.total_lgas or 0,
        "total_wards": row.total_wards or 0,
        "total_settlements": total_s,
        "visited_settlements": visited_s,
        "visitation_pct": round(100.0 * visited_s / total_s, 1) if total_s > 0 else 0,
        "total_grids": total_g,
        "visited_grids": visited_g,
        "completeness_pct": round(100.0 * visited_g / total_g, 1) if total_g > 0 else 0,
        "total_points": row.total_points or 0,
        "qc_out_of_bound": row.qc_out_of_bound or 0,
        "qc_time_violations": row.qc_time_violations or 0,
        "qc_stacked_points": row.qc_stacked_points or 0,
    }
