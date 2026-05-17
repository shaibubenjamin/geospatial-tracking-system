"""
Pre-compute in_grid flag on mda_households so the points GeoJSON API
is fast (no correlated subquery per row).
"""
import psycopg2, os, time
DB = os.environ.get("DATABASE_URL_SYNC", "postgresql://geouser:geopass@localhost:5433/geospatial_tracker")

def main():
    conn = psycopg2.connect(DB)
    cur  = conn.cursor()

    # Add column if absent
    cur.execute("ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS in_grid boolean DEFAULT false")
    conn.commit()
    print("Column in_grid ensured")

    # Populate: TRUE if the household GPS point falls within ANY grid cell
    t0 = time.time()
    cur.execute("""
        UPDATE mda_households h
        SET in_grid = EXISTS (
            SELECT 1 FROM grids g
            WHERE g.project_id = 1
              AND ST_Within(h.geom, g.geom)
            LIMIT 1
        )
        WHERE h.geom IS NOT NULL
    """)
    updated = cur.rowcount
    conn.commit()
    print(f"Updated in_grid for {updated:,} rows in {time.time()-t0:.1f}s")

    cur.execute("SELECT COUNT(*) FROM mda_households WHERE in_grid = true")
    inside = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mda_households WHERE in_grid = false AND geom IS NOT NULL")
    outside = cur.fetchone()[0]
    print(f"  Inside  grid: {inside:,}")
    print(f"  Outside grid: {outside:,}")

    # Add index for fast filtering
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mda_in_grid ON mda_households(in_grid)")
    conn.commit()
    print("Index created")

    cur.close(); conn.close()
    print("Done.")

if __name__ == "__main__":
    main()
