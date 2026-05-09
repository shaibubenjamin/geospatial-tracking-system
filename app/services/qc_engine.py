"""
qc_engine.py
Quality control checks for GPS points.
"""
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def run_out_of_bound_check(
    project_id: int,
    point_ids: List[int],
    db: AsyncSession,
) -> int:
    """
    Flag points that claim to be in an LGA but don't spatially fall there.
    Also flag points that fall completely outside all project LGAs.
    """
    if not point_ids:
        return 0

    result = await db.execute(
        text("""
            INSERT INTO qc_flags (project_id, point_id, flag_type, flag_detail)
            SELECT DISTINCT
                p.project_id,
                p.id AS point_id,
                'out_of_bound' AS flag_type,
                CASE
                  WHEN p.lga_name IS NOT NULL AND l_named.id IS NULL
                    THEN 'Point claims LGA "' || p.lga_name || '" but does not intersect it'
                  ELSE 'Point falls outside all project LGA boundaries'
                END AS flag_detail
            FROM points_raw p
            LEFT JOIN lgas l_named
                   ON l_named.project_id = p.project_id
                  AND LOWER(l_named.lga_name) = LOWER(COALESCE(p.lga_name, ''))
                  AND ST_Within(p.geom, l_named.geom)
            LEFT JOIN lgas l_any
                   ON l_any.project_id = p.project_id
                  AND ST_Within(p.geom, l_any.geom)
            WHERE p.project_id = :project_id
              AND p.id = ANY(:point_ids)
              AND (
                  (p.lga_name IS NOT NULL AND l_named.id IS NULL)
                  OR (l_any.id IS NULL)
              )
              AND NOT EXISTS (
                  SELECT 1 FROM qc_flags qf
                  WHERE qf.point_id = p.id AND qf.flag_type = 'out_of_bound'
              )
        """),
        {"project_id": project_id, "point_ids": point_ids},
    )
    await db.commit()
    return result.rowcount


async def run_time_violation_check(
    project_id: int,
    point_ids: List[int],
    db: AsyncSession,
) -> int:
    """
    Flag points collected outside working hours (before 07:00 or after 19:00).
    """
    if not point_ids:
        return 0

    result = await db.execute(
        text("""
            INSERT INTO qc_flags (project_id, point_id, flag_type, flag_detail)
            SELECT
                p.project_id,
                p.id AS point_id,
                'time_violation' AS flag_type,
                'Collection time ' || TO_CHAR(p.timestamp AT TIME ZONE 'UTC', 'HH24:MI')
                  || ' is outside allowed window (07:00-19:00 UTC)' AS flag_detail
            FROM points_raw p
            WHERE p.project_id = :project_id
              AND p.id = ANY(:point_ids)
              AND p.timestamp IS NOT NULL
              AND (EXTRACT(HOUR FROM p.timestamp AT TIME ZONE 'UTC') < 7
                   OR EXTRACT(HOUR FROM p.timestamp AT TIME ZONE 'UTC') >= 19)
              AND NOT EXISTS (
                  SELECT 1 FROM qc_flags qf
                  WHERE qf.point_id = p.id AND qf.flag_type = 'time_violation'
              )
        """),
        {"project_id": project_id, "point_ids": point_ids},
    )
    await db.commit()
    return result.rowcount


async def run_stacked_point_check(
    project_id: int,
    db: AsyncSession,
) -> int:
    """
    Flag points that are suspiciously stacked (clusters of > 5 points within 5m radius).
    Uses ST_ClusterDBSCAN.
    """
    result = await db.execute(
        text("""
            INSERT INTO qc_flags (project_id, point_id, flag_type, flag_detail)
            WITH clustered AS (
                SELECT
                    id,
                    project_id,
                    ST_ClusterDBSCAN(geom, eps := 0.00005, minpoints := 3)
                      OVER (PARTITION BY project_id) AS cluster_id
                FROM points_raw
                WHERE project_id = :project_id
            ),
            large_clusters AS (
                SELECT cluster_id, COUNT(*) AS cluster_size
                FROM clustered
                WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id
                HAVING COUNT(*) > 5
            )
            SELECT
                c.project_id,
                c.id AS point_id,
                'stacked_point' AS flag_type,
                'Part of suspicious cluster (cluster_id=' || c.cluster_id::text
                  || ', size=' || lc.cluster_size::text || ')' AS flag_detail
            FROM clustered c
            JOIN large_clusters lc ON lc.cluster_id = c.cluster_id
            WHERE NOT EXISTS (
                SELECT 1 FROM qc_flags qf
                WHERE qf.point_id = c.id AND qf.flag_type = 'stacked_point'
            )
        """),
        {"project_id": project_id},
    )
    await db.commit()
    return result.rowcount


async def get_qc_summary(project_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Get summary counts of QC flags by type for a project."""
    result = await db.execute(
        text("""
            SELECT
                flag_type,
                COUNT(*) AS cnt
            FROM qc_flags
            WHERE project_id = :project_id
            GROUP BY flag_type
        """),
        {"project_id": project_id},
    )
    rows = result.fetchall()
    summary = {
        "out_of_bound": 0,
        "time_violations": 0,
        "stacked_points": 0,
        "duplicates": 0,
        "total_flags": 0,
    }
    for row in rows:
        if row.flag_type == "out_of_bound":
            summary["out_of_bound"] = row.cnt
        elif row.flag_type == "time_violation":
            summary["time_violations"] = row.cnt
        elif row.flag_type == "stacked_point":
            summary["stacked_points"] = row.cnt
        elif row.flag_type == "duplicate":
            summary["duplicates"] = row.cnt
        summary["total_flags"] += row.cnt
    return summary
